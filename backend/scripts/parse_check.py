"""开发期自检脚本：拿真实票据跑一遍解析，看字段抽得准不准。

用法：
    python backend/scripts/parse_check.py "素材/示例项目_示例设备_12345元.pdf"
    python backend/scripts/parse_check.py            # 不传参数就扫 素材/ 目录下的所有票
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.ocr.base import OCRProviderError  # noqa: E402
from app.ocr.registry import get_provider  # noqa: E402

SAMPLE_DIR = PROJECT_DIR / "素材"
SAMPLE_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}


def sample_files() -> list[Path]:
    """递归找出素材目录下的所有票据文件。

    用户会自由整理素材目录（可能有子文件夹），所以不能写死路径。
    """
    if not SAMPLE_DIR.is_dir():
        return []
    return sorted(
        p for p in SAMPLE_DIR.rglob("*") if p.is_file() and p.suffix.lower() in SAMPLE_SUFFIXES
    )


def dump(path: Path) -> None:
    file_type = path.suffix.lower().lstrip(".")
    print("=" * 78)
    print("FILE:", path.name, f"({file_type})")
    try:
        provider = get_provider(file_type)
    except OCRProviderError as exc:
        print(f"  [跳过] {exc.code}: {exc.message}")
        return

    try:
        result = provider.recognize(path, file_type)
    except OCRProviderError as exc:
        print(f"  [失败] {exc.code}: {exc.message}")
        return

    print(f"  供应商      : {result.provider}")
    print(f"  票种        : {result.invoice_type}")
    print(f"  发票代码    : {result.invoice_code or '-'}")
    print(f"  发票号码    : {result.invoice_number or '-'}")
    print(f"  开票日期    : {result.invoice_date or '-'}")
    print(f"  购买方      : {result.buyer_name or '-'}  /  {result.buyer_tax_id or '-'}")
    print(f"  销售方      : {result.seller_name or '-'}  /  {result.seller_tax_id or '-'}")
    print(f"  不含税金额  : {result.amount_without_tax}")
    print(f"  税额        : {result.tax_amount}")
    print(f"  价税合计    : {result.total_amount}")
    print(f"  平均置信度  : {result.confidence}")
    print(f"  缺失必填    : {result.missing_required() or '无'}")
    if result.warnings:
        print(f"  提示        : {'; '.join(result.warnings)}")
    print(f"  明细 {len(result.items)} 行:")
    for idx, item in enumerate(result.items, 1):
        print(
            f"    {idx}. 名称={item.item_name!r} 规格={item.specification!r} "
            f"单位={item.unit!r} 数量={item.quantity} 单价={item.unit_price} "
            f"金额={item.amount} 税率={item.tax_rate} 税额={item.tax_amount}"
        )
    # 金额关系校验，验证「不含税 + 税额 = 价税合计」
    if None not in (result.amount_without_tax, result.tax_amount, result.total_amount):
        diff = round(
            result.amount_without_tax + result.tax_amount - result.total_amount, 2
        )
        print(f"  金额勾稽    : 差额 {diff:.2f} {'✔ 通过' if abs(diff) <= 0.01 else '✘ 不通过'}")


def main() -> None:
    args = sys.argv[1:]
    if args:
        targets = [Path(a) for a in args]
    else:
        targets = sample_files()
    if not targets:
        print(f"!! 在 {SAMPLE_DIR} 下没找到任何票据文件（支持 PDF/JPG/JPEG/PNG）")
    for target in targets:
        if not target.exists():
            print(f"!! 文件不存在: {target}")
            continue
        dump(target)
    print("=" * 78)


if __name__ == "__main__":
    main()
