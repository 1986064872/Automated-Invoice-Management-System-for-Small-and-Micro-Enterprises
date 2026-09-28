"""图片票脱敏：OCR 定位 → 涂掉 → 用系统字体重写（方案 B · 图片版）。

⚠️ 必须在装了 PaddleOCR 的解释器里跑（本机是 `.workbuddy` 下的 demo311 环境），
   后端那个精简 venv（3.13）里没有 paddleocr / PIL / cv2。

    <装了 PaddleOCR 的那个 python.exe>  backend\scripts\make_public_images.py

   例（换成你自己的路径）：
    %USERPROFILE%\.workbuddy\binaries\python\envs\demo311\Scripts\python.exe ^
        backend\scripts\make_public_images.py

为什么图片票不能照搬 PDF 的「重建」思路
--------------------------------------
PDF 有文字层，可以按 span 坐标重新画一遍；图片票是**栅格像素**，
文字已经烧进像素里，没有坐标可抄 —— 只能靠 OCR 反推文字框。

好消息：**栅格图的脱敏比 PDF 可靠得多**。
PDF 涂抹后原始文字可能还留在内容流里（别的库照样能读，踩过这个坑）；
而图片是覆写像素，**原像素直接没了**，不存在「隐藏层」问题。

流程
----
1. PaddleOCR 拿到「文字框 + 识别文本」（注意：它常把「名称：XX公司」连标签一起框住，正好）
2. 在识别文本里替换映射表里的真实值 → 得到假文本
3. 按框位置涂掉原像素（用取样出来的背景色，不是硬编码白）
4. 用系统宋体在同一个框里重写假文本，字号自动缩放到框宽以内
5. 校验：对输出图**再跑一次 OCR**，确认真实值一个都读不出来

真实值放 `anonymize_map.local.json`（gitignored），和 PDF 脚本共用同一份。
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from paddleocr import PaddleOCR

sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
SRC_DIR = PROJECT_DIR / "素材" / "测试发票"
OUT_DIR = BACKEND_DIR / "testdata" / "测试发票公开版"
MAP_LOCAL = Path(__file__).with_name("anonymize_map.local.json")

IMAGE_EXT = {".jpg", ".jpeg", ".png"}
FONT_PATH = Path(r"C:\Windows\Fonts\simsun.ttc")
FONT_PATH_ASCII = Path(r"C:\Windows\Fonts\simsun.ttc")

# 涂掉时向外扩几个像素，避免残留抗锯齿边缘；不能太大，会吃掉表格线
PAD_X, PAD_Y = 2, 2

NUMBER20_RE = re.compile(r"(?<!\d)(\d{20})(?!\d)")
# 税号：18 位；OCR 会把 X 读成全角 ×，所以字符集要放宽
TAXID_RE = re.compile(r"(?<![0-9A-Za-z×x])([0-9][0-9A-Za-z×x]{16}[0-9A-Za-z×x])(?![0-9A-Za-z×x])")


def load_rules() -> dict:
    if not MAP_LOCAL.exists():
        raise SystemExit(f"缺少映射表：{MAP_LOCAL}")
    return json.loads(MAP_LOCAL.read_text(encoding="utf-8"))


class NumberAllocator:
    """票号：前 12 位保留 + 后 8 位唯一序号（和 PDF 脚本同一套规则）。"""

    def __init__(self, cfg: dict):
        self.keep = int(cfg.get("keep_prefix", 12))
        self.digits = int(cfg.get("suffix_digits", 8))
        self.start = int(cfg.get("start_at", 1))
        self._map: dict[str, str] = {}

    def __call__(self, real: str) -> str:
        if real not in self._map:
            self._map[real] = f"{real[: self.keep]}{self.start + len(self._map):0{self.digits}d}"
        return self._map[real]

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._map)


def build_verifier(rules: dict, alloc: NumberAllocator):
    """返回一个函数：找出文本里还残留的真实值。

    ⚠️ 必须把「20 位票号」也算进来 —— 票号所在的那个框里没有公司名/税号，
       只按公司名/税号判断的话，整个票号框会被当成「无需处理」直接跳过。
    """
    reals = list(rules["company_name"]) + list(rules["tax_id"]) + list(rules["person_name"])

    def find_real(text: str) -> list[str]:
        hits = [r for r in reals if r in text]
        # 税号容错（X 可能被 OCR 读成 ×）
        for m in TAXID_RE.finditer(text):
            token = m.group(1)
            norm = token.replace("×", "X").replace("x", "X")
            if norm in rules["tax_id"]:
                hits.append(token)
        # 票号：只要不是本脚本自己分配出去的假号，就是真实值
        fakes = set(alloc.mapping.values())
        for m in NUMBER20_RE.finditer(text):
            if m.group(1) not in fakes:
                hits.append(m.group(1))
        return hits

    return find_real


def anonymize_text(text: str, rules: dict, alloc: NumberAllocator, find_real) -> str:
    if not find_real(text):
        return text
    out = text
    for bucket in ("company_name", "tax_id", "person_name"):
        for real, fake in rules[bucket].items():
            if real in out:
                out = out.replace(real, fake)
    # 税号容错：OCR 把 X 读成 × 的情况
    for m in list(TAXID_RE.finditer(out)):
        token = m.group(1)
        norm = token.replace("×", "X").replace("x", "X")
        fake = rules["tax_id"].get(norm)
        if fake:
            out = out.replace(token, fake)
    # 票号
    for m in list(NUMBER20_RE.finditer(out)):
        token = m.group(1)
        if token in set(alloc.mapping.values()):
            continue
        out = out.replace(token, alloc(token))
    return out


def fit_font(text: str, box_w: float, box_h: float) -> ImageFont.FreeTypeFont:
    """字号从「框高」起步往下试，直到文本宽度装得进框。"""
    size = max(10, int(round(box_h)))
    font = ImageFont.truetype(str(FONT_PATH), size)
    while size > 9 and font.getlength(text) > box_w * 0.98:
        size -= 1
        font = ImageFont.truetype(str(FONT_PATH), size)
    return font


def background_color(img: Image.Image, box) -> tuple[int, int, int]:
    """取框上方 3px 处的像素当背景色（比硬编码白色稳，截图可能是 #F8F8F8）。"""
    x0, y0, x1, y1 = box
    sample_y = max(0, int(y0) - 3)
    pixels = [img.getpixel(((int(x0) + int(x1)) // 2, sample_y))]
    pixels.append(img.getpixel((max(0, int(x0) - 3), (int(y0) + int(y1)) // 2)))
    # 取最亮的那个当背景
    return max(pixels, key=lambda p: sum(p[:3]))


def ocr_boxes(ocr, path: Path) -> list[tuple[int, int, int, int, str]]:
    result = ocr.predict(str(path))
    page = result[0]
    boxes = []
    for text, poly in zip(page.get("rec_texts"), page.get("rec_polys")):
        xs = [float(p[0]) for p in poly]
        ys = [float(p[1]) for p in poly]
        boxes.append((min(xs), min(ys), max(xs), max(ys), str(text)))
    return boxes


# ==========================================================================
# 二维码：图片上的真码必须一起去掉
# ==========================================================================
def find_qr_box(img: Image.Image):
    """用 OpenCV 的二维码检测器定位真码。

    一开始我写的是「在左上角找密集黑方块」那种启发式，实测**会误判**：
    它把 UI 截图左上角的深色元素当成二维码，却漏掉真正的码。
    既然本机装了 opencv（envs\\demo311 里有），就直接用真检测器，不再猜。
    """
    detector = cv2.QRCodeDetector()
    ok, points = detector.detect(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
    if not ok or points is None:
        return None
    xs = [float(p[0]) for p in points[0]]
    ys = [float(p[1]) for p in points[0]]
    return (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))


def draw_fake_qr_pil(draw: ImageDraw.ImageDraw, box, seed: str) -> None:
    """画一张「看着像二维码、但扫不出任何东西」的假码（和 PDF 脚本同一套画法）。"""
    x0, y0, x1, y1 = box
    n = 25
    cell = (x1 - x0) / n
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    bits: list[int] = []
    i = 0
    while len(bits) < n * n:
        byte = digest[i % len(digest)]
        bits.extend((byte >> k) & 1 for k in range(8))
        i += 1
    finders = [(0, 0), (0, n - 7), (n - 7, 0)]

    def is_finder(r: int, c: int) -> bool:
        return any(br <= r < br + 7 and bc <= c < bc + 7 for br, bc in finders)

    draw.rectangle(box, fill=(255, 255, 255))
    for row in range(n):
        for col in range(n):
            if is_finder(row, col) or not bits[row * n + col]:
                continue
            draw.rectangle(
                (x0 + col * cell, y0 + row * cell, x0 + (col + 1) * cell, y0 + (row + 1) * cell),
                fill=(0, 0, 0),
            )
    for br, bc in finders:
        draw.rectangle((x0 + bc * cell, y0 + br * cell,
                        x0 + (bc + 7) * cell, y0 + (br + 7) * cell), fill=(0, 0, 0))
        draw.rectangle((x0 + (bc + 1) * cell, y0 + (br + 1) * cell,
                        x0 + (bc + 6) * cell, y0 + (br + 6) * cell), fill=(255, 255, 255))
        draw.rectangle((x0 + (bc + 2) * cell, y0 + (br + 2) * cell,
                        x0 + (bc + 5) * cell, y0 + (br + 5) * cell), fill=(0, 0, 0))


def process_image(ocr, src: Path, dst: Path, rules: dict, alloc: NumberAllocator,
                  find_real) -> list[str]:
    img = Image.open(src).convert("RGB")
    draw = ImageDraw.Draw(img)
    notes: list[str] = []

    # 先把二维码换掉（真码里可能编码了票号与金额）；识别不到就跳过
    qr = find_qr_box(img)
    if qr is not None:
        draw_fake_qr_pil(draw, qr, seed="public-sample")
        notes.append(f"二维码 {qr} → 假码")

    for x0, y0, x1, y1, text in ocr_boxes(ocr, src):
        fake = anonymize_text(text, rules, alloc, find_real)
        if fake == text:
            continue
        rect = (max(0, int(x0) - PAD_X), max(0, int(y0) - PAD_Y),
                min(img.width, int(x1) + PAD_X), min(img.height, int(y1) + PAD_Y))
        draw.rectangle(rect, fill=background_color(img, (x0, y0, x1, y1)))
        font = fit_font(fake, rect[2] - rect[0], rect[3] - rect[1])
        draw.text((rect[0], (rect[1] + rect[3]) // 2), fake, font=font,
                  fill=(0, 0, 0), anchor="lm")
        notes.append(f"{text!r} → {fake!r}")

    if dst.suffix.lower() in (".jpg", ".jpeg"):
        img.save(dst, quality=95, subsampling=0)
    else:
        img.save(dst)
    return notes


def main() -> int:
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    rules = load_rules()
    alloc = NumberAllocator(rules["invoice_number"])
    find_real = build_verifier(rules, alloc)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in SRC_DIR.iterdir() if p.suffix.lower() in IMAGE_EXT)
    if limit:
        images = images[:limit]

    print(f"源目录：{SRC_DIR}")
    print(f"输出目录：{OUT_DIR}")
    print(f"待处理图片：{len(images)} 张")
    print("（首次会加载 PaddleOCR 模型，稍等）")
    print()
    ocr = PaddleOCR(lang="ch", use_doc_orientation_classify=False,
                    use_doc_unwarping=False, use_textline_orientation=False)

    produced: list[Path] = []
    for idx, src in enumerate(images, start=1):
        dst = OUT_DIR / f"图片_{idx:02d}{src.suffix.lower()}"
        notes = process_image(ocr, src, dst, rules, alloc, find_real)
        produced.append(dst)
        print(f"  [{idx:02d}] {src.name[:34]:36} → {dst.name}  （{len(notes)} 处替换, {dst.stat().st_size // 1024} KB）")
        for n in notes:
            print(f"        {n}")

    print()
    print("=" * 74)
    print("校验：对输出图片【重新 OCR】，确认真实值读不出来")
    print("=" * 74)
    bad = 0
    for path in produced:
        texts = " ".join(t for *_, t in ocr_boxes(ocr, path))
        hits = find_real(texts)
        if hits:
            bad += 1
            print(f"  ✘ {path.name}  仍能读出：{hits[:4]}")
        else:
            print(f"  ✔ {path.name}")
    print()
    if bad:
        print(f"❌ {bad}/{len(produced)} 张未通过")
        return 1
    print(f"✅ 全部 {len(produced)} 张通过（复核用的也是 OCR，最终还需肉眼过一遍）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
