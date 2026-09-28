"""本地 PaddleOCR Provider（GPU 加速）—— 处理 JPG/PNG 图片票与扫描件 PDF。

为什么走子进程？
    后端跑在 Python 3.13 的轻量 venv；PaddlePaddle-GPU + PaddleOCR 装在另一个
    Python 3.11 的解释器里（约 1GB+，含 CUDA 运行时）。跨版本不能 import，
    也不该把 GPU 框架塞进 Web 进程。所以启动一个**常驻**工作进程：
    模型只加载一次，之后每张图只花推理时间（20 张图批量识别不会反复初始化 GPU）。

    工作进程的协议与清场逻辑见 `paddle_worker.py`。

坐标处理：
    PaddleOCR 返回的是图像像素坐标（y 轴向下）。本项目的解析器用的是 PDF 约定
    （y 轴向上），所以这里要翻转 y，并把整体缩放到「约 800 单位高」的
    标准尺度 —— 这样解析器里的行聚拢阈值在 PDF 和图片上表现一致。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from ..config import WORKBUDDY_ENVS_DIR, settings
from .base import (
    OCRCode,
    InvoiceOCRProvider,
    InvoiceResult,
    OCRProviderError,
)
from .parser import parse_invoice_fragments

log = logging.getLogger(__name__)

WORKER_SCRIPT = Path(__file__).with_name("paddle_worker.py")
# 把图像坐标缩放到这个「标准高度」，与 PDF 点阵尺度可比，解析器阈值才能通用
CANONICAL_HEIGHT = 800.0

IMAGE_TYPES = {"jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff"}
PDF_TYPES = {"pdf"}


# ==========================================================================
# 找解释器
# ==========================================================================
def _site_packages_of(python_exe: Path) -> Path | None:
    """由解释器路径推出它的 site-packages。"""
    parent = python_exe.parent
    base = parent.parent if parent.name.lower() in ("scripts", "bin") else parent
    for candidate in (base / "Lib" / "site-packages", base / "lib" / "site-packages"):
        if candidate.is_dir():
            return candidate
    return None


def _has_paddleocr(python_exe: Path) -> bool:
    site = _site_packages_of(python_exe)
    if site is None:
        return False
    return (site / "paddleocr").is_dir()


def candidate_interpreters() -> list[Path]:
    """列出本机所有可能装了 paddleocr 的解释器。"""
    cands: list[Path] = []

    if WORKBUDDY_ENVS_DIR.is_dir():
        for env_dir in sorted(WORKBUDDY_ENVS_DIR.iterdir()):
            for rel in ("Scripts/python.exe", "bin/python"):
                exe = env_dir / rel
                if exe.is_file():
                    cands.append(exe)

    # 系统安装的 Python
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    for pattern in ("Programs/Python/Python3*/python.exe", "Programs/Python/Python3*/Scripts/python.exe"):
        cands.extend(sorted(local_appdata.glob(pattern)))

    # conda
    for name in ("anaconda3", "miniconda3", "Anaconda3", "Miniconda3"):
        for base in (Path.home() / name, Path("C:/ProgramData") / name):
            exe = base / "python.exe"
            if exe.is_file():
                cands.append(exe)
            envs = base / "envs"
            if envs.is_dir():
                for env_dir in sorted(envs.iterdir()):
                    if (env_dir / "python.exe").is_file():
                        cands.append(env_dir / "python.exe")

    # 去重，保持顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for path in cands:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def discover_paddle_python() -> tuple[str, str]:
    """定位带 paddleocr 的解释器，返回 (路径, 说明)。找不到就返回空路径 + 原因。"""
    configured = (settings.paddle_python or "").strip()
    if configured:
        path = Path(configured)
        if not path.is_file():
            return "", f"配置的 PADDLE_PYTHON 不存在：{configured}"
        if not _has_paddleocr(path):
            return "", f"{configured} 里没有 paddleocr，请改成装了 PaddleOCR 的环境"
        return str(path), "来自 PADDLE_PYTHON 配置"

    for exe in candidate_interpreters():
        if _has_paddleocr(exe):
            return str(exe), f"自动发现：{exe}"

    return (
        "",
        "本机没找到装了 paddleocr 的 Python 环境。可执行 "
        "pip install paddlepaddle-gpu paddleocr，或在 backend/.env 里指定 PADDLE_PYTHON。",
    )


def paddle_env_ready() -> tuple[bool, str]:
    python_exe, note = discover_paddle_python()
    return (bool(python_exe), note)


# ==========================================================================
# 常驻工作进程
# ==========================================================================
class _PaddleWorker:
    """一个 lazily 启动、可复用的 PaddleOCR 子进程。请求串行化（单 GPU 不做并发）。"""

    def __init__(self, python_exe: str):
        self.python_exe = python_exe
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.Lock()
        self._counter = 0
        self._last_used = 0.0
        self._info: dict = {}
        self._watchdog_started = False

    # ---------------- 生命周期 ----------------
    def _start_locked(self) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        if settings.paddle_device.strip():
            env["PADDLE_OCR_DEVICE"] = settings.paddle_device.strip()
        env["PADDLE_OCR_DOC_ORI"] = "1" if settings.paddle_doc_orientation else "0"
        env["PADDLE_OCR_UNWARP"] = "1" if settings.paddle_unwarping else "0"
        env["PADDLE_OCR_TEXTLINE_ORI"] = "1" if settings.paddle_textline_orientation else "0"

        log.info("启动 PaddleOCR 工作进程：%s", self.python_exe)
        self._queue = queue.Queue()
        self._proc = subprocess.Popen(
            [self.python_exe, "-u", str(WORKER_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            # cwd 特意放在 backend/ 而不是脚本所在目录：
            # 脚本目录一旦进 sys.path，`import paddle` 就会命中同目录的兄弟模块。
            # 工作进程内部还有一道 sys.path 清理，这里是第二层保险。
            cwd=str(WORKER_SCRIPT.parents[2]),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

        ready = self._read_json(settings.paddle_startup_timeout)
        if not ready or not ready.get("ready"):
            message = (ready or {}).get("error", "工作进程没有上报就绪状态")
            hint = (ready or {}).get("hint", "")
            self.stop()
            raise OCRProviderError(
                OCRCode.PROVIDER_NOT_CONFIGURED,
                f"PaddleOCR 启动失败：{message} {hint}".strip(),
            )
        self._info = ready
        self._last_used = time.time()
        self._start_watchdog()
        log.info(
            "PaddleOCR 就绪：model=%s device=%s cuda=%s",
            ready.get("model"),
            ready.get("device"),
            ready.get("cuda"),
        )

    def _pump_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._queue.put(line)
        self._queue.put(None)

    def _pump_stderr(self) -> None:
        """把子进程 stderr 转进 logger，顺便防止管道写满导致子进程卡死。"""
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            text = line.rstrip()
            if text:
                log.debug("[paddle] %s", text)

    def _read_json(self, timeout: float) -> dict | None:
        try:
            line = self._queue.get(timeout=max(timeout, 0.001))
        except queue.Empty:
            return None
        if line is None:
            return None
        line = line.strip()
        if not line:
            return {}
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            log.warning("工作进程输出非 JSON（已忽略）：%s", line[:200])
            return {}

    def _start_watchdog(self) -> None:
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def loop() -> None:
            while True:
                time.sleep(30)
                idle = time.time() - self._last_used
                if self._proc is not None and self._proc.poll() is None:
                    if settings.paddle_idle_shutdown > 0 and idle > settings.paddle_idle_shutdown:
                        log.info("PaddleOCR 空闲 %.0f 秒，退出子进程释放显存", idle)
                        self.stop()

        threading.Thread(target=loop, daemon=True).start()

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib_suppress():
            if proc.stdin:
                proc.stdin.close()
        with contextlib_suppress():
            proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            with contextlib_suppress():
                proc.kill()

    # ---------------- 请求 ----------------
    def run(self, path: str, timeout: float) -> dict:
        with self._lock:
            if not self.alive():
                self._start_locked()

            self._counter += 1
            req_id = str(self._counter)
            payload = json.dumps({"id": req_id, "path": path}, ensure_ascii=False)

            try:
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(payload + "\n")
                self._proc.stdin.flush()
            except Exception as exc:  # noqa: BLE001
                self.stop()
                raise OCRProviderError(
                    OCRCode.PROVIDER_ERROR, f"无法把任务发给 PaddleOCR 工作进程：{exc}"
                ) from exc

            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    # 超时说明工作进程已经卡住，杀掉重启，下次请求才是干净的
                    self.stop()
                    raise OCRProviderError(
                        OCRCode.TIMEOUT,
                        f"PaddleOCR 识别超时（超过 {timeout:.0f} 秒）。"
                        "首次识别需要加载模型，可稍后重试；若持续超时请看后端日志。",
                    )
                message = self._read_json(remaining)
                if message is None and not self.alive():
                    self.stop()
                    raise OCRProviderError(
                        OCRCode.PROVIDER_ERROR,
                        "PaddleOCR 工作进程意外退出，请查看后端日志中的 [paddle] 行。",
                    )
                if message and str(message.get("id")) == req_id:
                    self._last_used = time.time()
                    return message


class contextlib_suppress:
    """就地实现 contextlib.suppress(Exception)，少一个 import。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return True


_workers: dict[str, _PaddleWorker] = {}
_workers_lock = threading.Lock()


def _worker_for(python_exe: str) -> _PaddleWorker:
    with _workers_lock:
        worker = _workers.get(python_exe)
        if worker is None:
            worker = _PaddleWorker(python_exe)
            _workers[python_exe] = worker
        return worker


def shutdown_paddle_workers() -> None:
    """进程退出时调用，别留孤儿进程占显存。"""
    with _workers_lock:
        for worker in _workers.values():
            worker.stop()
        _workers.clear()


# ==========================================================================
# Provider
# ==========================================================================
class PaddleOCRProvider(InvoiceOCRProvider):
    name = "paddleocr"
    display_name = "本地 PaddleOCR（GPU 加速）"

    def recognize(self, file_path: Path, file_type: str) -> InvoiceResult:
        ft = (file_type or "").lower().lstrip(".")
        if ft not in IMAGE_TYPES | PDF_TYPES:
            raise OCRProviderError(
                OCRCode.UNSUPPORTED_FORMAT, f"PaddleOCR 不处理这种格式：{file_type}"
            )

        python_exe, note = discover_paddle_python()
        if not python_exe:
            raise OCRProviderError(OCRCode.PROVIDER_NOT_CONFIGURED, note)

        worker = _worker_for(python_exe)
        # 必须传绝对路径：工作进程的 cwd 不在这个项目里，相对路径会找不到文件
        payload = worker.run(str(Path(file_path).resolve()), timeout=settings.paddle_timeout)

        if not payload.get("ok"):
            raise OCRProviderError(
                OCRCode.PROVIDER_ERROR,
                payload.get("error") or "PaddleOCR 识别失败",
            )

        lines = payload.get("lines") or []
        if not lines:
            raise OCRProviderError(
                OCRCode.NO_TEXT_LAYER,
                "PaddleOCR 没在这张图上检出任何文字，可能是空白图或分辨率太低。",
            )

        height = float(payload.get("height") or 0.0)
        scale = CANONICAL_HEIGHT / height if height > 0 else 1.0

        fragments: list[tuple[float, float, str]] = []
        scores: list[float] = []
        for line in lines:
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            x = float(line.get("x") or 0.0) * scale
            # 图像 y 轴向下，解析器要 PDF 约定（y 越大越靠上）
            y = (height - float(line.get("y") or 0.0)) * scale
            fragments.append((x, y, text))
            scores.append(float(line.get("score") or 0.0))

        result = parse_invoice_fragments(fragments, provider=self.name, tolerance=4.0)

        # OCR 本身有不确定性：把解析器的结构置信度按平均识别分往下压一点。
        # 平均分 1.0 → 不打折；平均分 0.6 → 打 0.8 折，低置信字段就会被复核工作台高亮出来。
        mean_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        factor = 0.5 + 0.5 * mean_score
        result.field_confidence = {
            key: round(value * factor, 4) for key, value in result.field_confidence.items()
        }
        result.field_confidence["_ocr_mean_score"] = mean_score

        result.warnings.append(
            f"由本地 PaddleOCR（GPU）识别，共 {len(lines)} 行文字，平均行置信度 {mean_score:.0%}。"
        )
        result.raw = {
            "provider": self.name,
            "device": worker._info.get("device"),
            "model": worker._info.get("model"),
            "mean_score": mean_score,
            "line_count": len(lines),
            "lines": [
                {"text": line.get("text"), "score": line.get("score")}
                for line in lines[:120]
            ],
        }
        return result

    def health_check(self) -> tuple[bool, str]:
        python_exe, note = discover_paddle_python()
        if not python_exe:
            return False, note
        worker = _worker_for(python_exe)
        if worker.alive():
            info = worker._info
            return True, (
                f"已预热 · 模型 {info.get('model')} · 设备 {info.get('device')} · "
                f"paddle {info.get('paddle')}"
            )
        return True, f"{note}（未预热，首次识别需加载模型，约 20 秒）"
