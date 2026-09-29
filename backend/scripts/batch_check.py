"""批量质检：把你实际上传过的所有票据重新解析一遍，列出一张健康度清单。

用途：解析器改动后，比 4 张样本更靠谱的回归测试 —— 直接扫 data/uploads 里的真实票据。

检查项：
    · 关键字段是否齐全
    · 金额勾稽（不含税 + 税额 == 价税合计）
    · 明细行金额相加是否等于票面不含税金额（负数折扣行最容易在这里出问题）
    · 有没有解析出明细

用法：
    python backend/scripts/batch_check.py              # 扫 data/uploads
    python backend/scripts/batch_check.py 某个目录      # 扫指定目录
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.ocr.base import OCRProviderError  # noqa: E402
from app.ocr.registry import get_provider  # noqa: E402

SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png"}
AMOUNT_TOLERANCE = 0.01


def self_test() -> list[str]:
    """内置回归用例（用合成坐标跑纯解析器，不依赖任何文件）。

    为什么放这里：data/uploads 是 gitignore 的，用户把票删了用例就没了。
    两个真实踩过的坑：
      ① 负数行（折扣 / 退货）：金额税额为负，曾经被整行丢掉
      ② 拉丁单位（PCS / SET）：曾经因为词表里只有汉字，
         被当成规格型号的一部分拼进去，单位栏反而空着
    """
    from app.ocr.parser import parse_invoice_fragments

    failures: list[str] = []

    # 表头行：这些 x 就是各列的权威位置
    header = [
        (43.9, 246.6, "项目名称"),
        (150.0, 246.6, "规格型号"),
        (320.0, 246.6, "单位"),
        (400.0, 246.6, "数量"),
        (470.0, 246.6, "单价"),
        (540.0, 246.6, "金额"),
        (600.0, 246.6, "税率/征收率"),
        (660.0, 246.6, "税额"),
    ]

    def row(y, name, spec, unit, qty, price, amount, rate, tax):
        return [
            (43.9, y, name),
            (150.0, y, spec),
            (320.0, y, unit),
            (400.0, y, qty),
            (470.0, y, price),
            (540.0, y, amount),
            (600.0, y, rate),
            (660.0, y, tax),
        ]

    # ---------- 用例一：负数折扣行 ----------
    frags = header + row(
        230.0, "*示例材料*示例管件甲", "SPEC-110", "只", "4", "100.00", "400.00", "13%", "52.00"
    ) + row(
        214.0, "*示例材料*示例管件乙", "SPEC-200", "只", "2", "200.00", "400.00", "13%", "52.00"
    ) + row(
        198.0, "*示例材料*示例管件乙", "SPEC-200", "只", "2", "100.00", "-200.00", "13%", "-26.00"
    ) + [
        (43.9, 150.0, "合"),
        (60.0, 150.0, "计"),
        (540.0, 150.0, "¥600.00"),
        (660.0, 150.0, "¥78.00"),
    ]
    result = parse_invoice_fragments(frags, provider="selftest")
    if len(result.items) != 3:
        failures.append(f"[负数行] 明细行数应为 3，实际 {len(result.items)}")
    else:
        last = result.items[-1]
        if last.amount != -200.00:
            failures.append(f"[负数行] 金额应为 -200.00，实际 {last.amount}")
        if last.tax_amount != -26.00:
            failures.append(f"[负数行] 税额应为 -26.00，实际 {last.tax_amount}")
        if result.items[1].item_name != "*示例材料*示例管件乙":
            failures.append(f"[负数行] 上一行名称被污染：{result.items[1].item_name!r}")

    # ---------- 用例二：拉丁单位 PCS ----------
    frags = header + row(
        230.0,
        "*示例设备*示例无线网络设备",
        "DEMO-AP-01",
        "PCS",
        "10",
        "100.00",
        "1000.00",
        "13%",
        "130.00",
    ) + [
        (43.9, 150.0, "合"),
        (60.0, 150.0, "计"),
        (540.0, 150.0, "¥1000.00"),
        (660.0, 150.0, "¥130.00"),
    ]
    result = parse_invoice_fragments(frags, provider="selftest")
    if len(result.items) != 1:
        failures.append(f"[拉丁单位] 明细行数应为 1，实际 {len(result.items)}")
    else:
        item = result.items[0]
        if item.unit != "PCS":
            failures.append(f"[拉丁单位] 单位应为 PCS，实际 {item.unit!r}")
        if item.specification != "DEMO-AP-01":
            failures.append(f"[拉丁单位] 规格应只含 DEMO-AP-01，实际 {item.specification!r}")
        if "PCS" in (item.specification or ""):
            failures.append("[拉丁单位] 单位被拼进规格型号了")
        if item.quantity != 10:
            failures.append(f"[拉丁单位] 数量应为 10，实际 {item.quantity}")

    # ---------- 用例三：表头标签被拆成单字 + 名称被拆成多块 ----------
    # 实测版式：表头是 `单`+`位`、`数`+`量`，名称是 `*`+`大类`+`*`+`尾部`，
    # 而规格数据紧贴在名称右边。这一组曾经把名称的前半段整块塞进规格里。
    header_split = [
        (45.4, 246.6, "项目名称"),
        (119.4, 246.6, "规格型号"),
        (190.0, 246.6, "单"),
        (208.0, 246.6, "位"),
        (263.7, 246.6, "数"),
        (281.7, 246.6, "量"),
        (334.6, 246.6, "单"),
        (352.6, 246.6, "价"),
        (406.8, 246.6, "金"),
        (424.8, 246.6, "额"),
        (446.5, 246.6, "税率"),
        (464.5, 246.6, "/"),
        (469.1, 246.6, "征收率"),
        (551.4, 246.6, "税"),
        (569.4, 246.6, "额"),
    ]
    frags = header_split + [
        (12.76, 230.0, "*"),
        (17.26, 230.0, "示例制品"),
        (80.25, 230.0, "*"),
        (84.75, 230.0, "示例台盆"),
        (119.05, 230.0, "DEMO-012"),
        (150.55, 230.0, "示例台盆"),
        (198.17, 230.0, "个"),
        (281.55, 230.0, "2"),
        (297.41, 230.0, "200.00"),
        (402.19, 230.0, "400.00"),
        (465.21, 230.0, "13%"),
        (555.51, 230.0, "52.00"),
        # 折行：名称的尾巴跑到下一行
        (12.76, 214.0, "支架"),
        (43.9, 150.0, "合"),
        (60.0, 150.0, "计"),
        (406.69, 150.0, "¥400.00"),
        (551.4, 150.0, "¥52.00"),
    ]
    result = parse_invoice_fragments(frags, provider="selftest")
    if len(result.items) != 1:
        failures.append(f"[拆字表头] 明细行数应为 1，实际 {len(result.items)}")
    else:
        item = result.items[0]
        if item.unit != "个":
            failures.append(f"[拆字表头] 单位应为 个，实际 {item.unit!r}")
        if item.item_name != "*示例制品*示例台盆支架":
            failures.append(f"[拆字表头] 名称应为 示例台盆+折行支架，实际 {item.item_name!r}")
        if item.specification != "DEMO-012 示例台盆":
            failures.append(f"[拆字表头] 规格应为 DEMO-012 示例台盆，实际 {item.specification!r}")
        if item.quantity != 2:
            failures.append(f"[拆字表头] 数量应为 2，实际 {item.quantity}")

    # ---------- 用例四：名称折行「跨」数值行（一段在上、一段在下） ----------
    # 实测一份图片票：单元格里的名称折了两行，数值列垂直居中，
    # 于是名称第一段落在数值行**上方**、第二段在**下方**。
    # 旧逻辑只看数值行下方，结果名称只剩一个「撞系」，整条名称面目全非。
    img_header = [
        (225.55, 600.0, "项目名称"),
        (416.35, 600.0, "规格型号"),
        (594.21, 600.0, "单位"),
        (799.32, 600.0, "数量"),
        (979.90, 600.0, "单价"),
        (1241.57, 600.0, "金额税率/征收率"),
        (1513.46, 600.0, "税额"),
    ]
    frags = img_header + [
        # 名称第一段：在数值行上方
        (222.83, 560.0, "*示例设备*示例设备总成防碰"),
        # 数值行（锚点）
        (409.54, 540.0, "DEMO-GZ-3"),
        (594.21, 540.0, "套"),
        (824.53, 540.0, "5"),
        (928.79, 540.0, "1000.00"),
        (1165.93, 540.0, "5000.00"),
        (1274.28, 540.0, "13%"),
        (1501.87, 540.0, "650.00"),
        # 名称第二段：在数值行下方
        (71.50, 520.0, "撞系统"),
        # 表尾噪声（不应被吸进名称）
        (1291.48, 420.0, "重选发票"),
        # 合计行
        (198.30, 380.0, "合计"),
        (1155.03, 380.0, "¥5000.00"),
        (1471.89, 380.0, "¥650.00"),
    ]
    result = parse_invoice_fragments(frags, provider="selftest")
    if len(result.items) != 1:
        failures.append(f"[名称跨行] 明细行数应为 1，实际 {len(result.items)}")
    else:
        item = result.items[0]
        want_name = "*示例设备*示例设备总成防碰撞系统"
        if item.item_name != want_name:
            failures.append(f"[名称跨行] 名称应为 {want_name!r}，实际 {item.item_name!r}")
        if item.specification != "DEMO-GZ-3":
            failures.append(f"[名称跨行] 规格应为 DEMO-GZ-3，实际 {item.specification!r}")
        if item.unit != "套":
            failures.append(f"[名称跨行] 单位应为 套，实际 {item.unit!r}")
        if item.quantity != 5:
            failures.append(f"[名称跨行] 数量应为 5，实际 {item.quantity}")

    # ---------- 用例五：名称和规格型号被 OCR 粘在同一块里 ----------
    # OCR 切块不稳定：同一批发票里，有时型号单独成块（落在规格列 x 上，按列位置就能分对），
    # 有时却和名称被识别成**同一块**。这时两者 x 完全相同，靠列位置分不开，
    # 只能按文字拆：最后一个空白之后、纯 ASCII 且含数字的尾巴当规格型号。
    glue_header = [
        (196.4, 600.0, "项目名称"),
        (350.2, 600.0, "规格型号"),
        (495.7, 600.0, "单位"),
        (662.8, 600.0, "数量"),
        (808.9, 600.0, "单价"),
        (1021.9, 600.0, "金额税率/征收率"),
        (1242.6, 600.0, "税额"),
    ]
    glue_rest = [
        (496.27, 540.0, "套"),
        (682.16, 540.0, "2"),
        (767.91, 540.0, "1000.00"),
        (960.44, 540.0, "2000.00"),
        (1048.41, 540.0, "13%"),
        (1233.20, 540.0, "260.00"),
        (174.83, 380.0, "合计"),
        (951.04, 380.0, "¥2000.00"),
        (1206.64, 380.0, "¥260.00"),
    ]
    frags = glue_header + [
        (230.15, 540.0, "*示例设备*示例设备总成 DEMO-QCB-7"),
    ] + glue_rest
    result = parse_invoice_fragments(frags, provider="selftest")
    if len(result.items) != 1:
        failures.append(f"[名称粘型号] 明细行数应为 1，实际 {len(result.items)}")
    else:
        item = result.items[0]
        if item.item_name != "*示例设备*示例设备总成":
            failures.append(f"[名称粘型号] 名称应为 示例设备总成，实际 {item.item_name!r}")
        if item.specification != "DEMO-QCB-7":
            failures.append(f"[名称粘型号] 规格应为 DEMO-QCB-7，实际 {item.specification!r}")

    # 反向用例：尾巴含中文时**不能**乱拆（宁可不拆也别拆错）
    frags = glue_header + [
        (230.15, 540.0, "*信息技术服务*软件开发 一期2阶段"),
    ] + glue_rest
    result = parse_invoice_fragments(frags, provider="selftest")
    if result.items and result.items[0].specification:
        failures.append(
            f"[名称粘型号] 尾巴含中文时不该拆，实际拆成了 {result.items[0].specification!r}"
        )

    return failures


def run_self_test() -> bool:
    print("=" * 100)
    print("内置回归用例：负数折扣行 / 拉丁单位 / 拆字表头 / 名称跨行 / 名称粘型号")
    print("=" * 100)
    failures = self_test()
    if failures:
        for msg in failures:
            print(f"  [✘] {msg}")
        print("  → 上面这些先修，再看批量结果")
    else:
        print("  [✔] 负数折扣行：被正确识别为明细行，金额为负、不影响上一行名称")
        print("  [✔] 拉丁单位：PCS 归到单位列，规格型号不再被污染")
        print("  [✔] 拆字表头：`单`+`位` 能还原成单位列，名称拆成多块也不再串进规格")
        print("  [✔] 名称跨行：名称折成上下两段（数值行夹在中间）也能拼回完整名称")
        print("  [✔] 名称粘型号：型号粘在名称尾巴上能拆回规格列，含中文的尾巴不乱拆")
    print()
    return not failures


def collect(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(
        p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES
    )


def main() -> int:
    self_test_ok = run_self_test()
    if "--self-test-only" in sys.argv:
        return 0 if self_test_ok else 2

    root = (
        Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else (PROJECT_DIR / "data" / "uploads")
    )
    if not root.exists():
        print(f"目录不存在：{root}")
        return 1

    files = collect(root)
    print("=" * 100)
    print(f"批量质检：{len(files)} 个文件   （根目录 {root}）")
    print("=" * 100)

    problems: list[tuple[str, str]] = []
    stat = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
    rows: list[tuple[str, str, str, str, str, str]] = []

    for path in files:
        file_type = path.suffix.lower().lstrip(".")
        short = path.name[:46]
        try:
            provider = get_provider(file_type)
        except OCRProviderError as exc:
            stat["skip"] += 1
            rows.append((short, file_type, "跳过", f"{exc.code}", "", ""))
            continue

        try:
            result = provider.recognize(path, file_type)
        except OCRProviderError as exc:
            stat["fail"] += 1
            problems.append((short, f"识别失败：{exc.code} {exc.message[:60]}"))
            rows.append((short, file_type, "失败", exc.code, "", ""))
            continue

        issues: list[str] = []
        missing = result.missing_required()
        if missing:
            issues.append(f"缺字段 {','.join(missing)}")
        if None not in (result.amount_without_tax, result.tax_amount, result.total_amount):
            diff = round(
                result.amount_without_tax + result.tax_amount - result.total_amount, 2
            )
            if abs(diff) > AMOUNT_TOLERANCE:
                issues.append(f"金额勾稽差 {diff:+.2f}")
        if not result.items:
            issues.append("无明细行")
        elif result.amount_without_tax is not None:
            item_sum = round(sum(it.amount or 0 for it in result.items), 2)
            diff = round(item_sum - result.amount_without_tax, 2)
            if abs(diff) > AMOUNT_TOLERANCE:
                issues.append(f"明细合计差 {diff:+.2f}")

        if issues:
            stat["warn"] += 1
            problems.append((short, "；".join(issues)))
            status = "注意"
        else:
            stat["ok"] += 1
            status = "OK"

        rows.append(
            (
                short,
                file_type,
                status,
                result.invoice_number or "-",
                result.invoice_date or "-",
                f"{len(result.items)}行",
            )
        )

    print()
    print(f"{'文件':<48}{'类型':<6}{'结果':<6}{'发票号码':<22}{'日期':<12}明细")
    print("-" * 100)
    for r in rows:
        print(f"{r[0]:<48}{r[1]:<6}{r[2]:<6}{r[3]:<22}{r[4]:<12}{r[5]}")

    print()
    print("=" * 100)
    print(f"结果：正常 {stat['ok']} · 需注意 {stat['warn']} · 失败 {stat['fail']} · 跳过 {stat['skip']}")
    print()
    print("提示：「需注意」不一定是程序错。图片票缺号码/日期，往往是源图本身就没有")
    print("      （比如「发票预览」截图，号码栏本身就显示 -），系统会正确地要求人工补全。")
    if problems:
        print()
        print("需要关注的问题：")
        for name, msg in problems:
            print(f"  · {name}\n      {msg}")
    print("=" * 100)
    return 0 if (not problems and self_test_ok) else 2


if __name__ == "__main__":
    sys.exit(main())
