"""开发期探测脚本：摸清 demo311 环境里 PaddleOCR 3.7 的真实 API 与返回结构。

不做任何下载：只使用模型缓存目录（默认 ~/.paddlex/official_models）里已有的模型。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 项目根 = 本脚本往上两级（backend/scripts/ -> backend/ -> 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = Path(os.environ.get("INVOICE_SAMPLE_DIR") or PROJECT_ROOT / "素材")
IMG = next(SAMPLE_DIR.rglob("invoice.jpg"), None)

print("=" * 78)
print("样本目录:", SAMPLE_DIR)
if IMG is None:
    raise SystemExit(f"在 {SAMPLE_DIR} 下没找到 invoice.jpg（可用 INVOICE_SAMPLE_DIR 指定样本目录）")
print("目标图片:", IMG)

print("\n--- paddleocr 顶层导出 ---")
import paddleocr  # noqa: E402

print("version:", getattr(paddleocr, "__version__", "?"))
print("顶层名字:", [n for n in dir(paddleocr) if not n.startswith("_")])

from paddleocr import PaddleOCR  # noqa: E402

print("\n--- PaddleOCR.__init__ 签名 ---")
import inspect  # noqa: E402

sig = inspect.signature(PaddleOCR.__init__)
params = [p for p in sig.parameters if p != "self"]
print(f"共 {len(params)} 个参数，挑关键几个：")
for key in (
    "lang", "use_doc_orientation_classify", "use_doc_unwarping",
    "use_textline_orientation", "device", "text_detection_model_name",
    "text_recognition_model_name", "ocr_version", "enable_mkldnn",
):
    if key in sig.parameters:
        print(f"  {key} = {sig.parameters[key].default}")

print("\n--- predict 签名 ---")
print(inspect.signature(PaddleOCR.predict))

print("\n--- 开始实例化（首次会初始化 GPU，可能较慢）---")
ocr = PaddleOCR(
    lang="ch",
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    use_textline_orientation=False,
)
print("实例化完成")

print("\n--- 推理 ---")
result = ocr.predict(str(IMG))
print("返回类型:", type(result), "长度:", len(result))

if result:
    page = result[0]
    print("单页类型:", type(page))
    keys = list(page.keys()) if hasattr(page, "keys") else dir(page)
    print("单页键:", [k for k in keys if not k.startswith("_")])
    for k in ("rec_texts", "rec_scores", "rec_polys", "dt_polys", "rec_boxes"):
        if hasattr(page, k):
            v = getattr(page, k)
            try:
                n = len(v)
            except TypeError:
                n = "?"
            print(f"  {k}: 长度 {n}")
            if n not in (0, "?"):
                print("    首个:", repr(v[0])[:160])
    texts = page.get("rec_texts") if hasattr(page, "get") else None
    if texts:
        print("\n前 25 行识别文本:")
        for t in texts[:25]:
            print("   ", t)
    print("\n可 json 化输出预览:")
    try:
        print(json.dumps(page, ensure_ascii=False, default=str)[:600])
    except Exception as exc:
        print("  json 化失败:", exc)
print("=" * 78)
