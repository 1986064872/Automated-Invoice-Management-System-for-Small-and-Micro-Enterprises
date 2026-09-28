"""检查仓库里有没有漏进的「真实数据」。

为什么需要它
------------
这个项目的原始票据是第三方商业数据。清理时我犯过两次错：
  ① 用 PyMuPDF 涂抹、又用 PyMuPDF 校验 —— **自证清白**，实际 14/20 个文件还能读出真名；
  ② 把校验写成 shell 命令链，前一步崩了被 `&&` 短路，**扫描压根没执行**，
     却被我读成「0 命中」。
所以校验必须是**独立、可见、可重复**的一步，而且要有阳性对照。

怎么用
------
    python backend/scripts/check_no_real_data.py              # 扫工作区
    python backend/scripts/check_no_real_data.py HEAD         # 扫某个提交
    python backend/scripts/check_no_real_data.py <某个ref>     # 阳性对照

真实值从哪来
------------
- `anonymize_map.local.json`（gitignored）：公司名 / 税号 / 人名
- `check_patterns.local.txt`（gitignored，可选）：票号、银行账号、工程名等，一行一个
两者都在仓库外，所以**这个脚本本身可以安全提交**。

⚠️ 阳性对照：干净的仓库扫出 0 命中是预期；但「扫不出」也可能是**脚本坏了**。
   找一个含真实数据的旧 ref 扫一遍，必须报出命中，否则说明扫描器失效。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parents[1]
MAP_LOCAL = SCRIPT_DIR / "anonymize_map.local.json"
EXTRA_LOCAL = SCRIPT_DIR / "check_patterns.local.txt"

# 兜底：本机绝对路径形态（和具体数据无关，永远该查）。
# ⚠️ **不要把 %USERPROFILE% 列进来** —— 那是正确、安全的占位符，
#    列进来会把三个 .bat 里的正常写法全报成「泄露」（实测踩过）。
PATH_PATTERNS = [r"C:\Users", r"C:/Users"]


def collect_patterns() -> list[str]:
    patterns: list[str] = []
    if MAP_LOCAL.exists():
        rules = json.loads(MAP_LOCAL.read_text(encoding="utf-8"))
        for bucket in ("company_name", "tax_id", "person_name"):
            patterns += list(rules.get(bucket, {}))
    else:
        print(f"⚠️ 没找到 {MAP_LOCAL.name}（真实值映射表），只能用兜底模式")
    if EXTRA_LOCAL.exists():
        patterns += [
            line.strip()
            for line in EXTRA_LOCAL.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    patterns += PATH_PATTERNS
    # 去重、去空、按长度倒序（长的先匹配，报告更可读）
    return sorted({p for p in patterns if p.strip()}, key=len, reverse=True)


def scan(patterns: list[str], rev: str | None) -> tuple[int, list[str]]:
    """用 git grep 扫。返回 (返回码, 输出行)。"""
    tmp = REPO_DIR / ".git" / "leak_patterns.tmp"
    tmp.write_text("\n".join(patterns), encoding="utf-8")
    try:
        cmd = ["git", "grep", "-n", "-I", "-f", str(tmp)]
        if rev:
            cmd.append(rev)
        cmd += ["--"]
        proc = subprocess.run(cmd, cwd=REPO_DIR, capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        return proc.returncode, [l for l in proc.stdout.splitlines() if l.strip()]
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    rev = sys.argv[1] if len(sys.argv) > 1 else None
    patterns = collect_patterns()
    print(f"扫描目标：{rev or '工作区'}")
    print(f"模式数量：{len(patterns)}")
    if not patterns:
        print("❌ 模式列表为空 —— 这是脚本坏了，不是「干净」")
        return 2

    code, hits = scan(patterns, rev)
    if code == 1:  # git grep: 1 = 无匹配
        print("\n✅ 0 命中")
        return 0
    if code != 0:
        print(f"\n❌ git grep 异常退出（code={code}）—— 扫描没跑成，别当成干净")
        return 2

    print(f"\n❌ 命中 {len(hits)} 行：")
    for line in hits:
        print("   ", line[:160])
    print("\n提醒：注释、docstring、脚本里的示例值都算数（我在这上面栽过两次）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
