"""把「票面备注」回填到已入库的票据上。

背景：
    `invoice_remark`（票面备注栏原文）是后加的字段。之前入库的票据是用旧解析器
    处理的，这个字段是空的。重跑一次识别就能补上。

为什么单独写一个脚本，而不是用 reparse_pending.py：
    那个脚本是「整张票重解析」，会连带重算明细/金额/校验标记 —— 对**已入账**的票
    风险太大。这里只做一件事：从原票文件重新识别**备注栏**，
    且**只填 `invoice_remark` 为空的行**，绝不覆盖已有内容、绝不碰其他字段。

用法：
    python backend/scripts/backfill_invoice_remark.py            # 只看会改什么（dry-run）
    python backend/scripts/backfill_invoice_remark.py --apply    # 真的写库
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Invoice, InvoiceFile  # noqa: E402
from app.ocr.registry import get_provider  # noqa: E402


def _file_path(inv_file: InvoiceFile) -> Path | None:
    from app.config import settings

    raw = inv_file.storage_path or ""
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = Path(settings.data_dir).parent / raw
    return p if p.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(description="回填票面备注")
    parser.add_argument("--apply", action="store_true", help="真的写库（默认只预览）")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        invoices = db.query(Invoice).all()
        targets: list[tuple[Invoice, InvoiceFile]] = []
        for inv in invoices:
            inv_file = db.get(InvoiceFile, inv.file_id) if inv.file_id else None
            if inv_file is None:
                continue
            # 已有备注的不动
            if (inv.invoice_remark or "").strip():
                continue
            targets.append((inv, inv_file))

        print(f"库内共 {len(invoices)} 张票，其中「票面备注为空」的有 {len(targets)} 张")
        print()

        filled = 0
        for inv, inv_file in targets:
            path = _file_path(inv_file)
            if path is None:
                print(f"  - 跳过 {inv.invoice_number or inv.id[:8]}：找不到原票文件")
                continue
            file_type = path.suffix.lower().lstrip(".")
            try:
                result = get_provider(file_type).recognize(path, file_type)
            except Exception as exc:  # 识别失败不该中断整批
                print(f"  - 跳过 {inv.invoice_number or inv.id[:8]}：识别失败 {exc}")
                continue

            remark = (result.remark or "").strip()
            if not remark:
                print(f"  · {inv.invoice_number or inv.id[:8]:<22} 票面没有备注栏内容")
                continue

            print(f"  ✔ {inv.invoice_number or inv.id[:8]:<22} {remark[:64]!r}")
            if args.apply:
                inv.invoice_remark = remark
            filled += 1

        if args.apply:
            db.commit()
            print()
            print(f"已写入 {filled} 张票据的票面备注。")
        else:
            print()
            print(f"（预览模式，未写库）加 --apply 才会写入这 {filled} 张。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
