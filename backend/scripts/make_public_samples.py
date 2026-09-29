"""生成完全合成的公开发票样本。

这份脚本不读取 ``素材/``、``data/`` 或任何真实票据，也不会保留原票的
项目名称、设备型号、数量、单价或金额。所有字段都由脚本内的固定模板生成。

用法：
    python backend/scripts/make_public_samples.py
    python backend/scripts/make_public_samples.py --images-only
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pymupdf

BACKEND_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BACKEND_DIR / "testdata" / "测试发票公开版"
FONT_FILE = Path(r"C:\Windows\Fonts\simsun.ttc")
PAGE_WIDTH = 842.0
PAGE_HEIGHT = 595.0


@dataclass(frozen=True)
class InvoiceData:
    invoice_number: str
    invoice_date: str
    buyer_name: str
    buyer_tax_id: str
    seller_name: str
    seller_tax_id: str
    item_name: str
    specification: str
    unit: str
    quantity: int
    unit_price: float
    tax_rate: int

    @property
    def amount(self) -> float:
        return round(self.quantity * self.unit_price, 2)

    @property
    def tax_amount(self) -> float:
        return round(self.amount * self.tax_rate / 100, 2)

    @property
    def total_amount(self) -> float:
        return round(self.amount + self.tax_amount, 2)


ITEM_NAMES = (
    "*信息技术服务*示例技术服务",
    "*办公用品*示例文具套装",
    "*软件服务*示例软件授权",
    "*维修服务*示例设备维护",
    "*咨询服务*示例咨询服务",
)
SPECS = ("DEMO-A01", "DEMO-B02", "DEMO-C03", "DEMO-D04", "DEMO-E05")


def sample_rows() -> list[InvoiceData]:
    rows: list[InvoiceData] = []
    for index in range(1, 21):
        quantity = index % 5 + 1
        unit_price = float(100 + index * 10)
        rows.append(
            InvoiceData(
                invoice_number=f"2600000000000000{index:04d}",
                invoice_date=f"2026-01-{index:02d}",
                buyer_name=f"示例购买方{chr(64 + ((index - 1) % 5) + 1)}有限公司",
                buyer_tax_id=f"91310000MA{index:08d}",
                seller_name=f"示例销售方{chr(64 + ((index - 1) % 5) + 1)}有限公司",
                seller_tax_id=f"91320000MA{index:08d}",
                item_name=ITEM_NAMES[(index - 1) % len(ITEM_NAMES)],
                specification=SPECS[(index - 1) % len(SPECS)],
                unit="件",
                quantity=quantity,
                unit_price=unit_price,
                tax_rate=1 if index == 20 else 13,
            )
        )
    return rows


def money(value: float) -> str:
    return f"{value:,.2f}"


def amount_upper(value: float) -> str:
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ("", "拾", "佰", "仟", "万", "拾万", "佰万", "仟万")
    yuan = int(round(value))
    if yuan == 0:
        return "零圆整"
    parts: list[str] = []
    for offset, char in enumerate(reversed(str(yuan))):
        digit = int(char)
        if digit:
            parts.append(digits[digit] + units[offset])
        elif parts and parts[-1] != "零":
            parts.append("零")
    return "".join(reversed(parts)).rstrip("零") + "圆整"


def write_text(
    page: pymupdf.Page,
    x: float,
    y: float,
    text: str,
    *,
    size: float = 10,
    color: tuple[float, float, float] = (0.1, 0.1, 0.1),
) -> None:
    page.insert_text(
        (x, y),
        text,
        fontsize=size,
        fontname="simsun",
        fontfile=str(FONT_FILE),
        color=color,
    )


def draw_line(page: pymupdf.Page, x0: float, y0: float, x1: float, y1: float) -> None:
    page.draw_line(
        pymupdf.Point(x0, y0),
        pymupdf.Point(x1, y1),
        color=(0.72, 0.48, 0.34),
        width=0.7,
    )


def draw_invoice_page(document: pymupdf.Document, data: InvoiceData) -> pymupdf.Page:
    page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.draw_rect(pymupdf.Rect(28, 30, 814, 565), color=(0.72, 0.48, 0.34), width=0.8)
    write_text(page, 285, 55, "电子发票（增值税专用发票）", size=18, color=(0.65, 0.2, 0.16))
    draw_line(page, 285, 63, 555, 63)
    draw_line(page, 285, 67, 555, 67)

    write_text(page, 585, 73, f"发票号码：{data.invoice_number}", size=9)
    write_text(page, 585, 92, f"开票日期：{data.invoice_date}", size=9)

    draw_line(page, 28, 112, 814, 112)
    draw_line(page, 28, 174, 814, 174)
    draw_line(page, 421, 112, 421, 174)
    draw_line(page, 28, 143, 814, 143)

    write_text(page, 72, 130, "名称：", size=10)
    write_text(page, 112, 130, data.buyer_name, size=10)
    write_text(page, 465, 130, "名称：", size=10)
    write_text(page, 505, 130, data.seller_name, size=10)

    tax_label = "统一社会信用代码/纳税人识别号："
    write_text(page, 72, 162, tax_label, size=8)
    write_text(page, 215, 162, data.buyer_tax_id, size=8)
    write_text(page, 465, 162, tax_label, size=8)
    write_text(page, 608, 162, data.seller_tax_id, size=8)

    headers = ("项目名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "税额")
    write_text(page, 42, 200, headers[0], size=9)
    write_text(page, 218, 200, headers[1], size=9)
    write_text(page, 350, 200, headers[2], size=9)
    write_text(page, 430, 200, headers[3], size=9)
    write_text(page, 515, 200, headers[4], size=9)
    write_text(page, 615, 200, headers[5], size=9)
    write_text(page, 698, 200, headers[6], size=8)
    write_text(page, 782, 200, headers[7], size=9)
    draw_line(page, 28, 210, 814, 210)

    write_text(page, 42, 245, data.item_name, size=9)
    write_text(page, 218, 245, data.specification, size=9)
    write_text(page, 350, 245, data.unit, size=9)
    write_text(page, 430, 245, str(data.quantity), size=9)
    write_text(page, 515, 245, f"{data.unit_price:.2f}", size=9)
    write_text(page, 615, 245, money(data.amount), size=9)
    write_text(page, 705, 245, f"{data.tax_rate}%", size=9)
    write_text(page, 780, 245, money(data.tax_amount), size=9)
    draw_line(page, 28, 410, 814, 410)

    write_text(page, 70, 430, "合计", size=10)
    write_text(page, 615, 430, f"¥{money(data.amount)}", size=9)
    write_text(page, 780, 430, f"¥{money(data.tax_amount)}", size=9)
    draw_line(page, 28, 442, 814, 442)
    draw_line(page, 28, 470, 814, 470)
    draw_line(page, 220, 442, 220, 470)

    write_text(page, 45, 462, "价税合计（大写）", size=9)
    write_text(page, 228, 462, amount_upper(data.total_amount), size=9)
    write_text(page, 620, 462, "（小写）", size=9)
    write_text(page, 665, 462, f"¥{money(data.total_amount)}", size=9)

    write_text(page, 45, 505, "备注：", size=9)
    write_text(page, 90, 505, "示例备注：合同编号 DEMO-CONTRACT-001", size=8)
    write_text(page, 45, 545, "开票人：示例经办人", size=9)
    write_text(page, 400, 545, "收款人：示例收款人", size=9)
    write_text(page, 650, 545, "复核人：示例复核人", size=9)
    return page


def set_metadata(document: pymupdf.Document) -> None:
    document.set_metadata(
        {
            "title": "完全合成电子发票样本",
            "author": "示例数据生成器",
            "subject": "不含真实票据信息",
            "keywords": "invoice, synthetic, demo",
            "creator": "示例数据生成器",
            "producer": "示例数据生成器",
        }
    )


def build_pdfs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.glob("*.pdf"):
        path.unlink()
    for index, data in enumerate(sample_rows(), start=1):
        document = pymupdf.open()
        draw_invoice_page(document, data)
        set_metadata(document)
        document.subset_fonts()
        path = OUT_DIR / f"{index:02d}_{data.invoice_number}.pdf"
        document.save(str(path), garbage=4, deflate=True)
        document.close()
        print(f"生成 {path.name}")


def build_images() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in OUT_DIR.glob("*.jpg"):
        path.unlink()
    for path in OUT_DIR.glob("*.png"):
        path.unlink()

    rows = sample_rows()
    for index in range(1, 5):
        document = pymupdf.open()
        page = draw_invoice_page(document, rows[index - 1])
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.7, 1.7), alpha=False)
        path = OUT_DIR / f"图片_{index:02d}.jpg"
        pixmap.save(str(path), jpg_quality=92)
        document.close()
        print(f"生成 {path.name}")

    for index in range(5, 8):
        document = pymupdf.open()
        page = draw_invoice_page(document, rows[index - 1])
        clip = pymupdf.Rect(20, 108, 822, 485)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), clip=clip, alpha=False)
        path = OUT_DIR / f"图片_{index:02d}.png"
        pixmap.save(str(path))
        document.close()
        print(f"生成 {path.name}")

    document = pymupdf.open()
    page = draw_invoice_page(document, rows[19])
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0), alpha=False)
    path = OUT_DIR / "图片_08.jpg"
    pixmap.save(str(path), jpg_quality=94)
    document.close()
    print(f"生成 {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成完全合成的公开发票样本")
    parser.add_argument("--images-only", action="store_true", help="只生成图片样本")
    parser.add_argument("--pdf-only", action="store_true", help="只生成 PDF 样本")
    args = parser.parse_args()

    if not FONT_FILE.exists():
        raise SystemExit(f"缺少中文字体：{FONT_FILE}")
    if args.images_only and args.pdf_only:
        raise SystemExit("--images-only 和 --pdf-only 不能同时使用")

    if args.images_only:
        build_images()
    elif args.pdf_only:
        build_pdfs()
    else:
        build_pdfs()
        build_images()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
