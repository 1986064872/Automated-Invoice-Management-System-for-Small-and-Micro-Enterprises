"""全局配置：路径、上传限制、OCR 供应商开关、跨域。

安全约定（项目书第十三章）：
- 百度 API Key / Secret Key 只从环境变量或 backend/.env 读取；
  绝不写进代码、绝不返回给前端、绝不写进日志。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/ -> 项目根
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent

# 数据目录。默认放在项目下的 data/，可以用 APP_DATA_DIR 整体重定向
# （跑自检脚本 / 做演示时用临时目录，绝不碰正在用的真实票据）：
#     set APP_DATA_DIR=C:\temp\scratch
_data_override = os.environ.get("APP_DATA_DIR", "").strip()
DATA_DIR = Path(_data_override) if _data_override else (PROJECT_DIR / "data")
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"

# 本机可能存在的其它 Python 环境（PaddleOCR 这类重型框架装在单独 venv 里）
WORKBUDDY_ENVS_DIR = Path.home() / ".workbuddy" / "binaries" / "python" / "envs"

for _d in (DATA_DIR, UPLOAD_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "企业智能票据记账助手"
    api_prefix: str = "/api/v1"

    # ---------------- OCR ----------------
    # local = 本地文本层解析（零成本，只对「带文本层的 PDF」有效）
    # baidu = 百度智能云增值税发票识别
    # auto  = 有百度密钥走百度，否则回落本地
    ocr_provider: str = "auto"
    baidu_api_key: str = ""
    baidu_secret_key: str = ""
    baidu_timeout: float = 20.0
    # 服务端日调用上限，防止云 OCR 意外产生费用（项目书第八章）
    daily_ocr_limit: int = 200
    # 低于该分数视为「低置信字段」，产生黄色警告（项目书第十章）
    confidence_threshold: float = 0.85

    # ---------------- 本地图片 OCR（PaddleOCR，GPU 加速） ----------------
    # 本项目后端跑在轻量 venv 里，而 PaddleOCR 装在另一个装了 paddlepaddle-gpu 的解释器里。
    # 两者跨版本无法直接 import，所以用「常驻子进程 + JSON 行协议」对接。
    # 留空则自动扫描本机 .workbuddy\binaries\python\envs 下的所有环境，找带 paddleocr 的那个。
    paddle_python: str = ""
    paddle_device: str = ""              # 如 "gpu:0" / "cpu"；留空交给 paddle 自己选
    paddle_startup_timeout: float = 240.0  # 首次加载模型较慢
    paddle_timeout: float = 300.0          # 单张图的识别超时
    paddle_idle_shutdown: float = 600.0    # 空闲多久后退出子进程，把显存还给别的程序
    paddle_doc_orientation: bool = True    # 图片方向分类：处理旋转/倒置的照片
    paddle_unwarping: bool = False         # 文档矫正：处理弯曲扫描件，比较重，默认关
    paddle_textline_orientation: bool = False

    # ---------------- 上传 ----------------
    allowed_extensions: tuple[str, ...] = (".pdf", ".jpg", ".jpeg", ".png")
    max_file_size_mb: float = 20.0
    # 单批最多文件数，避免一次拖 200 张把浏览器和服务端拖死
    max_batch_files: int = 50

    # ---------------- 跨域 ----------------
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173"
    )

    # ---------------- 派生属性 ----------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)

    @property
    def resolved_ocr_provider(self) -> str:
        """把 auto 解析成真正的供应商名。"""
        name = (self.ocr_provider or "auto").strip().lower()
        if name == "auto":
            has_key = bool(self.baidu_api_key.strip()) and bool(
                self.baidu_secret_key.strip()
            )
            return "baidu" if has_key else "local"
        return name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
