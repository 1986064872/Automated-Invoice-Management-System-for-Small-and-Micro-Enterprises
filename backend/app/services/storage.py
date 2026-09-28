"""文件存储：落盘、算哈希、判断类型。V1 存本地 data/uploads（项目书第七章）。"""

from __future__ import annotations

import hashlib
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR, UPLOAD_DIR, settings

TYPE_MAP = {
    ".pdf": "pdf",
    ".jpg": "jpg",
    ".jpeg": "jpeg",
    ".png": "png",
}


class UploadRejected(Exception):
    """上传被拒（格式/大小/数量），带中文原因。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def detect_file_type(filename: str) -> str | None:
    return TYPE_MAP.get(Path(filename).suffix.lower())


def safe_filename(original: str) -> str:
    """清掉路径分隔符等危险字符，防止目录穿越。"""
    name = Path(original).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    return name.strip() or "unnamed"


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_upload(filename: str, content: bytes) -> str:
    """校验格式和大小，返回规范化后的 file_type。"""
    file_type = detect_file_type(filename)
    if file_type is None:
        allowed = "、".join(sorted(t.upper() for t in TYPE_MAP.values()))
        raise UploadRejected(
            f"「{safe_filename(filename)}」格式不支持。V1 只接受 {allowed}。"
        )
    if not content:
        raise UploadRejected(f"「{safe_filename(filename)}」是空文件。")
    if len(content) > settings.max_file_size_bytes:
        raise UploadRejected(
            f"「{safe_filename(filename)}」{len(content) / 1024 / 1024:.1f}MB，"
            f"超过单文件上限 {settings.max_file_size_mb:g}MB。"
        )
    return file_type


def save_upload(batch_dir: Path, filename: str, content: bytes) -> tuple[Path, str]:
    """保存文件，返回 (磁盘路径, 存储文件名)。"""
    batch_dir.mkdir(parents=True, exist_ok=True)
    original = safe_filename(filename)
    stored_name = f"{datetime.now():%H%M%S}_{uuid.uuid4().hex[:8]}_{original}"
    target = batch_dir / stored_name
    target.write_bytes(content)
    return target, stored_name


def new_batch_dir_stub() -> Path:
    return UPLOAD_DIR


def count_pages(path: Path, file_type: str) -> int:
    """PDF 拆页计数（项目书第三章「PDF 拆页」）。图片恒为 1。"""
    if file_type != "pdf":
        return 1
    try:
        from pypdf import PdfReader

        return max(1, len(PdfReader(str(path)).pages))
    except Exception:
        return 1


# --------------------------------------------------------------------------
# 删除要进回收站，不要硬删
# --------------------------------------------------------------------------
TRASH_DIR = DATA_DIR / "trash"


def move_to_trash(path: Path) -> Path | None:
    """把原票移进 data/trash/，而不是 unlink 掉。

    为什么：unlink 是**不可逆**的 —— 一旦因为误操作（脚本清库、点错删除）把用户
    的真实票据删了，就再也找不回来。移到回收站至少还能人工捞回来。
    同一文件名重复进来时自动加序号，不覆盖。
    """
    if not path.exists():
        return None
    folder = TRASH_DIR / datetime.now().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        stem, suffix = target.stem, target.suffix
        index = 1
        while target.exists():
            target = folder / f"{stem}__{index}{suffix}"
            index += 1
    try:
        shutil.move(str(path), str(target))
        return target
    except OSError:
        # 移动失败（跨盘/占用）时退一步：复制过去再删原文件
        try:
            shutil.copy2(str(path), str(target))
            path.unlink()
            return target
        except OSError:
            return None
