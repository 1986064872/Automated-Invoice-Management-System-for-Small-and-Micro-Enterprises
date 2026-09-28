"""批量导入一个文件夹里的所有票据。

用途：
    · 从微信收文件目录 / 素材目录一次性导入整批发票
    · 误删后用原始文件夹快速恢复数据

用法：
    python backend/scripts/bulk_upload.py "C:\\path\\to\\folder"
    python backend/scripts/bulk_upload.py                    # 默认导入 素材/测试发票
    python backend/scripts/bulk_upload.py "某个目录" --dry-run

说明：
    会自动递归子目录、按 50 个一批分组（后端单批上限），
    每批跑完后轮询处理进度并打印结果。重复票据由后端去重逻辑自动标红，这里不预筛。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
PROJECT_DIR = Path(__file__).resolve().parents[2]
SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
BATCH_SIZE = 50


def collect(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES
    )


def wait_job(client: httpx.Client, job_id: str, timeout: float = 600.0) -> dict:
    deadline = time.time() + timeout
    job: dict = {}
    while time.time() < deadline:
        job = client.get(f"{BASE}/jobs/{job_id}", params={"with_files": True}).json()
        if job.get("status") in ("done", "partial", "failed"):
            return job
        time.sleep(0.5)
    return job


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    folder = Path(args[0]).resolve() if args else (PROJECT_DIR / "素材" / "测试发票")
    if not folder.is_dir():
        print(f"目录不存在：{folder}")
        return 1

    files = collect(folder)
    if not files:
        print(f"{folder} 下没有 PDF / JPG / JPEG / PNG 文件")
        return 1

    print("=" * 88)
    print(f"批量导入：{len(files)} 个文件")
    print(f"来源目录：{folder}")
    print(f"目标后端：{BASE}")
    if dry_run:
        print("模式：--dry-run（只看不动）")
    print("=" * 88)
    for path in files:
        print(f"  {path.stat().st_size:>9}  {path.name}")

    if dry_run:
        print("\n（--dry-run，未实际上传）")
        return 0

    client = httpx.Client(timeout=600)
    try:
        client.get(f"{BASE}/health").raise_for_status()
    except Exception as exc:
        print(f"\n后端未运行（{exc}）。先启动 start.bat 或 dev_backend.bat。")
        return 1

    accepted_total = 0
    rejected_total = 0
    batches = [files[i : i + BATCH_SIZE] for i in range(0, len(files), BATCH_SIZE)]

    for index, batch in enumerate(batches, 1):
        print(f"\n--- 第 {index}/{len(batches)} 批（{len(batch)} 个文件）---")
        handles = [p.open("rb") for p in batch]
        try:
            payload = [
                ("files", (p.name, fh, "application/octet-stream"))
                for p, fh in zip(batch, handles)
            ]
            resp = client.post(f"{BASE}/invoices/upload", files=payload)
            resp.raise_for_status()
            result = resp.json()
        finally:
            for fh in handles:
                fh.close()

        accepted_total += len(result["accepted"])
        rejected_total += len(result["rejected"])
        for item in result["rejected"]:
            print(f"  [拒绝] {item['name']}：{item['reason']}")

        job_id = result.get("job_id")
        if not job_id:
            print("  没有生成批次")
            continue

        print(f"  批次 {job_id[:8]} 已入队，等待识别…")
        job = wait_job(client, job_id)
        print(
            f"  完成：状态 {job.get('status')} · 成功 {job.get('succeeded')} · "
            f"失败 {job.get('failed')} · 待复核 {job.get('pending_review')}"
        )
        for f in job.get("files", []):
            if f.get("status") == "failed" or f.get("error_message"):
                print(f"    · {f['original_name']}：{f.get('error_message')}")

    print()
    print("=" * 88)
    print(f"导入结束：接收 {accepted_total} 个 · 拒绝 {rejected_total} 个")
    summary = client.get(f"{BASE}/invoices", params={"page_size": 1}).json().get("summary", {})
    print(f"当前库内：{summary.get('count', 0)} 条 · 价税合计 ¥{summary.get('total_amount', 0)}")
    print("提示：票据会进「待复核」，请到复核工作台逐张核对后确认入账。")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())
