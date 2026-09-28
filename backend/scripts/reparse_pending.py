"""把「待复核」的票据用当前版本的解析器重跑一遍。

什么时候用：解析器修了 bug（比如支持负数折扣行）之后，
库里已经入库的票据还是旧结果，需要重跑才能应用新逻辑。

安全设计：
    · 只处理「待复核」的票据 —— 已入账的一律不碰（接口本身也会拒绝）
    · 默认**跳过有手工修改痕迹的票**，避免覆盖你人工录入的内容
    · 想知道哪些被跳过，看输出的「跳过」列；确实要重跑就加 --force

用法：
    python backend/scripts/reparse_pending.py            # 安全模式
    python backend/scripts/reparse_pending.py --force     # 连改过的也重跑
    python backend/scripts/reparse_pending.py --dry-run   # 只看会动哪些，不实际执行
"""

from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8000/api/v1"


def content_signature(invoice: dict) -> tuple:
    """内容的指纹，用来判断重跑后到底有没有变化。

    只比「明细行数 + 错误数」是不够的 —— 解析器修了字段取值（比如单位从空变成 PCS、
    金额从正变负）时行数和错误数可能完全不变，会被误报成「无变化」。
    """
    items = []
    for it in invoice.get("items") or []:
        items.append(
            (
                it.get("item_name") or "",
                it.get("specification") or "",
                it.get("unit") or "",
                it.get("quantity"),
                it.get("unit_price"),
                it.get("amount"),
                it.get("tax_amount"),
            )
        )
    return (
        invoice.get("invoice_number") or "",
        invoice.get("seller_name") or "",
        invoice.get("amount_without_tax"),
        invoice.get("tax_amount"),
        invoice.get("total_amount"),
        tuple(items),
    )


def main() -> int:
    args = sys.argv[1:]
    force = "--force" in args
    dry_run = "--dry-run" in args

    client = httpx.Client(timeout=300)
    try:
        client.get(f"{BASE}/health").raise_for_status()
    except Exception as exc:
        print(f"后端未运行（{exc}）。先双击 start.bat 或 dev_backend.bat。")
        return 1

    listing = client.get(f"{BASE}/invoices", params={"status": "pending_review", "page_size": 200}).json()
    items = listing.get("items", [])
    print("=" * 96)
    print(f"待复核票据 {len(items)} 张")
    if force:
        print("模式：--force（连有手工修改痕迹的也重跑）")
    elif dry_run:
        print("模式：--dry-run（只看不动）")
    else:
        print("模式：安全（跳过有手工修改痕迹的）")
    print("=" * 96)

    changed_count = 0
    skipped = 0
    failed = 0

    for inv in items:
        number = inv.get("invoice_number") or "（无号码）"
        edits = len(inv.get("edit_history") or [])
        before_items = len(inv.get("items") or [])
        before_err = inv.get("error_count", 0)
        before_warn = inv.get("warning_count", 0)
        before_sig = content_signature(inv)

        if edits and not force:
            skipped += 1
            print(f"  跳过  {number:<22} 有 {edits} 条人工修改，保护你的录入")
            continue

        if dry_run:
            print(f"  会重跑 {number:<22} 当前 明细{before_items}行 错{before_err} 警{before_warn}")
            continue

        try:
            resp = client.post(f"{BASE}/invoices/{inv['id']}/retry")
            if resp.status_code != 200:
                failed += 1
                print(f"  失败  {number:<22} HTTP {resp.status_code} {resp.text[:80]}")
                continue
            after = resp.json()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  失败  {number:<22} {exc}")
            continue

        after_items = len(after.get("items") or [])
        after_err = after.get("error_count", 0)
        after_warn = after.get("warning_count", 0)
        # 内容指纹 + 计数一起比，字段取值的变化也要能报出来
        changed = content_signature(after) != before_sig
        if changed:
            changed_count += 1
        mark = "有变化" if changed else "无变化"
        print(
            f"  {mark}  {number:<22} "
            f"明细 {before_items}→{after_items}  错 {before_err}→{after_err}  警 {before_warn}→{after_warn}"
        )

    print("=" * 96)
    print(f"完成：{changed_count} 张有变化 · {skipped} 张跳过 · {failed} 张失败")
    if skipped and not force:
        print("被跳过的如果想一起重跑，加 --force（会覆盖那些票上的人工修改）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
