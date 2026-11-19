# -*- coding: utf-8 -*-
"""（重新）构建本地分级库 rankings.db。

随项目附带、不带参数就会用上：CCF 推荐目录 2026、中科院国际期刊预警名单、
中科院分区表（升级版）2025、新锐期刊分区表 2026（出处与权利说明见 DATA_SOURCES.md）。
JCR 受 Clarivate 许可保护，本项目不附带；你有权使用的话可以用 --jcr 导入。

用法：
  python tools/build_rankings.py                           # 用随附数据重建
  python tools/build_rankings.py --jcr jcr.csv             # 再加上你自己的 JCR
  python tools/build_rankings.py --xr 新锐2027.csv          # 用你自己的 CSV 替换随附的新锐分区表
  python tools/build_rankings.py --data-dir D:/my-tracker-data --jcr jcr.csv --jcr-edition 2025

生成的 rankings.db 只放在你的数据目录里（已被 .gitignore 排除），不要提交或分享。
以后升级到附带了更新数据的版本时：没导入过自己的 CSV 会自动重建；导入过的话请重新运行本脚本，并带上原来的 CSV。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="构建本地分级库 rankings.db（默认使用随项目附带的数据）")
    ap.add_argument("--data-dir", help="数据目录（默认项目下 data/，或环境变量 PAPER_TRACKER_DATA_DIR）")
    ap.add_argument("--cas", help="中科院分区表（升级版）CSV：替换随附的 2025 版")
    ap.add_argument("--xr", help="新锐期刊分区表 CSV：替换随附的 2026 版")
    ap.add_argument("--jcr", help="JCR CSV：你有权使用的数据（本项目不附带 JCR）")
    ap.add_argument("--cas-edition", help="中科院分区表版本（如 2025），默认取 CSV 的「年份」列")
    ap.add_argument("--xr-edition", help="新锐分区表版本（如 2026），默认取 CSV 的「年份」列")
    ap.add_argument("--jcr-edition", help="JCR 版本（如 2025），默认取 IF(<年份>) 列名里的年份")
    a = ap.parse_args()
    if a.data_dir:
        os.environ["PAPER_TRACKER_DATA_DIR"] = os.path.abspath(a.data_dir)
    sys.path.insert(0, ROOT)
    from app import rankings_build

    editions = {k: v for k, v in (("cas", a.cas_edition), ("xr", a.xr_edition), ("jcr", a.jcr_edition)) if v}
    try:
        rankings_build.build(cas=a.cas, xr=a.xr, jcr=a.jcr, editions=editions)
    except (OSError, ValueError) as e:
        print(f"构建失败：{e}", file=sys.stderr)
        sys.exit(1)
    print("完成。刷新页面（或重启本系统）后，徽章会按新的分级库重新匹配。")


if __name__ == "__main__":
    main()
