"""PaddleOCR 常驻工作进程（在 demo311 那个装了 paddlepaddle-gpu 的解释器里运行）。

为什么是独立进程而不是直接 import？
    后端跑在 Python 3.13 的隔离 venv 里，paddle 装在 Python 3.11 的另一个 venv 里。
    跨环境 import 不可能，而且把 1GB+ 的 GPU 框架塞进 Web 进程也不合适。
    所以用「常驻子进程 + JSON 行协议」：模型只加载一次，之后每张图只花推理时间。

协议（父子都按行收发 JSON）：
    父 → 子  {"id": "1", "path": "C:\\...\\a.jpg"}
    子 → 父  {"ready": true, "model": "...", "device": "..."}        启动完成
             {"id": "1", "ok": true, "width": w, "height": h,
              "lines": [{"text": "...", "score": 0.98, "x": 100.5, "y": 60.2}]}
             {"id": "1", "ok": false, "error": "..."}

关键细节：启动时把真实 stdout 管道单独存起来，再把 fd 1 重定向到 stderr。
    因为 paddle / 某些依赖会往 stdout 打印噪音（实测有一行 "信息: 用提供的模式无法找到文件。"），
    不清场的话会污染 JSON 协议，父进程解析就会崩。
"""

from __future__ import annotations

import json
import os
import sys
import traceback

# ---------------------------------------------------------------------------
# 清场 1：把脚本自身所在目录从 sys.path 里摘掉。
#   否则 `import paddle` 会命中同目录下的兄弟模块（本项目的 provider），
#   报出 "attempted relative import with no known parent package" 这种莫名其妙
#   的错，而且很难看出是路径问题。
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [
    entry for entry in sys.path if entry and os.path.abspath(entry) != _HERE
]

# ---------------------------------------------------------------------------
# 清场 2：真实 stdout 留给 JSON 协议，其它一切输出（含第三方库的 print）走 stderr。
#   因为 paddle / 某些依赖会往 stdout 打印噪音（实测有一行 "信息: 用提供的模式无法找到文件。"），
#   不清场的话会污染 JSON 协议，父进程解析就会崩。
# ---------------------------------------------------------------------------
_PROTOCOL_OUT = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)          # 现在 fd1 指向 stderr
sys.stdout = sys.stderr  # Python 级 print 也一并转到 stderr


def emit(payload: dict) -> None:
    _PROTOCOL_OUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _PROTOCOL_OUT.flush()


def main() -> int:
    try:
        import paddle
        from paddleocr import PaddleOCR
    except Exception as exc:  # noqa: BLE001
        emit(
            {
                "ready": False,
                "error": f"无法导入 paddle/paddleocr：{exc}",
                "hint": "这个解释器里没有装 paddlepaddle-gpu / paddleocr",
            }
        )
        return 1

    device = os.environ.get("PADDLE_OCR_DEVICE", "").strip()
    kwargs = {
        "lang": os.environ.get("PADDLE_OCR_LANG", "ch"),
        # 这三个开关都会额外加载模型，按需开启：
        #   方向分类 → 处理旋转/倒置的照片；矫正 → 处理弯曲的扫描件，比较重
        "use_doc_orientation_classify": os.environ.get("PADDLE_OCR_DOC_ORI", "1") == "1",
        "use_doc_unwarping": os.environ.get("PADDLE_OCR_UNWARP", "0") == "1",
        "use_textline_orientation": os.environ.get("PADDLE_OCR_TEXTLINE_ORI", "0") == "1",
    }
    if device:
        kwargs["device"] = device

    try:
        engine = PaddleOCR(**kwargs)
        # 兼容性取出模型名，仅用于上报
        model_name = ""
        try:
            cfg = getattr(engine, "_model_settings", None) or {}
            model_name = str(cfg.get("text_recognition_model_name") or "")
        except Exception:  # noqa: BLE001
            pass
        emit(
            {
                "ready": True,
                "model": model_name or "PP-OCR",
                "device": str(paddle.device.get_device()),
                "cuda": bool(paddle.device.is_compiled_with_cuda()),
                "paddle": paddle.__version__,
            }
        )
    except Exception as exc:  # noqa: BLE001
        emit({"ready": False, "error": f"PaddleOCR 初始化失败：{exc}"})
        return 1

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            request = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        req_id = request.get("id", "")
        if request.get("command") == "shutdown":
            emit({"id": req_id, "ok": True, "bye": True})
            return 0

        path = request.get("path", "")
        try:
            pages = engine.predict(path)
        except Exception as exc:  # noqa: BLE001
            emit(
                {
                    "id": req_id,
                    "ok": False,
                    "error": f"识别失败：{exc}",
                    "trace": traceback.format_exc()[-800:],
                }
            )
            continue

        if not pages:
            emit({"id": req_id, "ok": True, "width": 0, "height": 0, "lines": []})
            continue

        page = pages[0]

        def get(key):
            if hasattr(page, "get"):
                return page.get(key)
            return getattr(page, key, None)

        texts = list(get("rec_texts") or [])
        scores = list(get("rec_scores") or [])
        polys = get("rec_polys")
        if polys is None:
            polys = get("dt_polys")
        polys = list(polys) if polys is not None else []

        # 用所有检测框推算图像尺寸（paddlex 不直接给原始宽高）
        max_x = max_y = 0.0
        for box in polys:
            for point in box:
                x, y = float(point[0]), float(point[1])
                max_x = max(max_x, x)
                max_y = max(max_y, y)

        lines = []
        for index, text in enumerate(texts):
            if not str(text).strip():
                continue
            box = polys[index] if index < len(polys) else None
            if box is None:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            lines.append(
                {
                    "text": str(text),
                    "score": float(scores[index]) if index < len(scores) else 0.0,
                    # 图像坐标：y 轴向下、原点在左上
                    "x": round(sum(xs) / len(xs), 2),
                    "y": round(sum(ys) / len(ys), 2),
                }
            )

        emit(
            {
                "id": req_id,
                "ok": True,
                "width": round(max_x, 2),
                "height": round(max_y, 2),
                "lines": lines,
            }
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
