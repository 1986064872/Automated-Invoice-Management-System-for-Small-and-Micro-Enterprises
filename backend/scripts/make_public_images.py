"""兼容入口：生成完全合成的图片样本。"""

from __future__ import annotations

from make_public_samples import build_images


def main() -> int:
    build_images()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
