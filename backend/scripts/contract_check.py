"""前端契约检查：确认后端返回的字段与 frontend/src/types.ts 里前端真正用到的字段一致。

类型检查（tsc）只能保证前端内部自洽，管不了后端字段名。这个脚本堵住的就是
「后端改字段名 → 前端静默显示空值」这类最容易漏的集成 bug。

用法（后端需在 8000 运行）：
    python backend/scripts/contract_check.py
"""

from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000").rstrip("/") + "/api/v1"
results: list[tuple[bool, str]] = []


def check_keys(label: str, obj: dict, keys: list[str]) -> None:
    missing = [k for k in keys if k not in obj]
    results.append((not missing, f"{label} 字段齐全"))
    if missing:
        print(f"  [✘] {label} 缺少字段: {missing}")


def main() -> int:
    client = httpx.Client(timeout=30)
    try:
        client.get(f"{BASE}/health").raise_for_status()
    except Exception as exc:
        print(f"后端未运行：{exc}")
        return 1

    print("=" * 78)
    print("逐项核对前端用到的字段")

    # ---------- 概览 ----------
    dash = client.get(f"{BASE}/dashboard/summary", params={"month": "2026-01"}).json()
    check_keys(
        "Dashboard",
        dash,
        [
            "month", "invoice_count", "total_amount", "pending_review", "abnormal",
            "confirmed", "failed", "by_category", "recent_jobs", "provider",
        ],
    )
    check_keys(
        "Dashboard.provider",
        dash["provider"],
        [
            "configured",
            "baidu_ready",
            "paddle_ready",
            "paddle_detail",
            "rapidocr_ready",
            "daily_ocr_limit",
            "daily_ocr_used",
        ],
    )
    if dash["by_category"]:
        check_keys("Dashboard.by_category[]", dash["by_category"][0], ["category", "count", "total_amount"])
    if dash["recent_jobs"]:
        check_keys(
            "Dashboard.recent_jobs[]",
            dash["recent_jobs"][0],
            ["id", "status", "total", "succeeded", "failed", "pending_review", "created_at", "progress"],
        )

    # ---------- 票据列表 / 详情 ----------
    listing = client.get(f"{BASE}/invoices", params={"page_size": 100}).json()
    check_keys("InvoiceList", listing, ["items", "total", "page", "page_size", "summary"])
    check_keys("InvoiceList.summary", listing["summary"], ["count", "total_amount", "amount_without_tax", "tax_amount", "by_category"])

    if not listing["items"]:
        print("  （库内没有票据，先跑一次 e2e_check.py 造数据）")
        return 1

    invoice = listing["items"][0]
    check_keys(
        "Invoice",
        invoice,
        [
            "id", "file_id", "invoice_type", "invoice_code", "invoice_number", "invoice_date",
            "seller_name", "seller_tax_id", "buyer_name", "buyer_tax_id",
            "amount_without_tax", "tax_amount", "total_amount", "currency",
            "expense_category", "account_subject", "confidence", "field_confidence",
            "rule_source", "risk_flags", "dedupe_key", "status", "edit_history",
            "source_note", "invoice_remark", "confirmed_at", "items", "file",
            "error_count", "warning_count", "raw_url",
        ],
    )
    check_keys(
        "Invoice.file",
        invoice["file"],
        [
            "id", "job_id", "invoice_id", "original_name", "file_type", "file_size",
            "page_count", "status", "error_code", "error_message", "ocr_provider",
            "created_at", "updated_at", "raw_url",
        ],
    )

    # 这两项要挑「有内容的」那张票来查，否则第一张恰好是干净票就会被跳过，
    # 检查项数量随数据漂移，看着像覆盖率掉了
    with_items = next((i for i in listing["items"] if i["items"]), None)
    if with_items:
        check_keys(
            "Invoice.items[]",
            with_items["items"][0],
            [
                "id", "item_name", "specification", "unit", "quantity",
                "unit_price", "tax_rate", "amount", "tax_amount", "remark",
            ],
        )
    flagged = next((i for i in listing["items"] if i["risk_flags"]), None)
    if flagged:
        check_keys("Invoice.risk_flags[]", flagged["risk_flags"][0], ["code", "level", "message"])

    # ---------- 批次 ----------
    jobs = client.get(f"{BASE}/jobs", params={"limit": 3}).json()
    if jobs:
        check_keys("Job", jobs[0], ["id", "total", "succeeded", "failed", "pending_review", "completed", "status", "progress", "created_at", "files"])

    # ---------- 账本 ----------
    ledger = client.get(f"{BASE}/ledger", params={"status": "all", "page_size": 50}).json()
    check_keys("LedgerPage", ledger, ["items", "summary", "total", "page", "page_size"])
    if ledger["items"]:
        check_keys(
            "LedgerRow",
            ledger["items"][0],
            [
                "id", "invoice_id", "entry_date", "invoice_number", "seller_name", "item_name",
                "direction", "direction_text", "company_role", "counterparty_name",
                "counterparty_tax_id", "direction_reason",
                "amount_without_tax", "tax_amount", "total_amount", "expense_category",
                "account_subject", "original_name", "status", "error_count", "warning_count", "raw_url",
            ],
        )

    # ---------- 规则 ----------
    rules = client.get(f"{BASE}/rules").json()
    if rules:
        check_keys(
            "CategoryRule",
            rules[0],
            ["id", "rule_type", "match_field", "keyword", "expense_category", "account_subject", "priority", "enabled", "hits", "note"],
        )
    options = client.get(f"{BASE}/rules/options").json()
    check_keys("RuleOptions", options, ["categories", "pending_category", "match_fields", "rule_types"])

    # ---------- 系统 ----------
    config = client.get(f"{BASE}/system/config").json()
    check_keys("system/config", config, ["max_file_size_mb", "max_batch_files", "ocr_provider", "confidence_threshold"])
    providers = client.get(f"{BASE}/system/providers").json()
    check_keys("system/providers", providers, ["providers", "active"])
    if providers["providers"]:
        check_keys("providers[]", providers["providers"][0], ["name", "display_name", "ready", "note"])
    company = client.get(f"{BASE}/system/company")
    if company.status_code == 200 and company.json() is not None:
        check_keys("system/company", company.json(), ["name", "tax_id", "aliases", "updated_at"])
    suggestions = client.get(f"{BASE}/system/company/suggestions").json()
    if suggestions:
        check_keys(
            "system/company/suggestions[]",
            suggestions[0],
            ["name", "tax_id", "roles", "invoice_count", "buyer_count", "seller_count"],
        )

    # ---------- 导出预览 ----------
    preview = client.get(f"{BASE}/exports/preview", params={"month": "2026-01"}).json()
    check_keys(
        "exports/preview",
        preview,
        ["count", "invoice_count", "total_amount", "file_name", "company_ready", "by_direction"],
    )
    if preview["by_direction"]:
        check_keys(
            "exports/preview.by_direction[]",
            preview["by_direction"][0],
            ["direction", "label", "invoice_count", "item_count", "total_amount"],
        )

    # ---------- 原票读取 ----------
    raw = client.get(f"{BASE.removesuffix('/api/v1')}{invoice['raw_url']}")
    results.append((raw.status_code == 200, "原票可读取（复核工作台左栏依赖它）"))
    if raw.status_code != 200:
        print(f"  [✘] 原票读取失败 HTTP {raw.status_code}")

    total = len(results)
    passed = sum(1 for ok, _ in results if ok)
    print("=" * 78)
    for ok, label in results:
        print(f"  [{'✔' if ok else '✘'}] {label}")
    print("=" * 78)
    print(f"契约检查：{passed}/{total} 通过")
    return 0 if passed == total else 2


if __name__ == "__main__":
    sys.exit(main())
