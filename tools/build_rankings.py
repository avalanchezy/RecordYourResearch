# -*- coding: utf-8 -*-
"""（重新）构建本地分级库：CCF 推荐目录与中科院预警名单随项目发布；
中科院分区表 / 新锐期刊分区表 / JCR 需要你自己有权使用的 CSV 文件（本项目不附带，见 DATA_SOURCES.md）。

用法：
  python tools/build_rankings.py                         # 只用随项目发布的 CCF + 预警名单
  python tools/build_rankings.py --cas 中科院.csv --xr 新锐.csv --jcr jcr.csv
  python tools/build_rankings.py --data-dir D:/my-tracker-data --jcr jcr.csv --jcr-edition 2025

生成的 rankings.db 只放在你的数据目录里（已被 .gitignore 排除），不要提交或分享。
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser(description="构建本地分级库 rankings.db")
    ap.add_argument("--data-dir", help="数据目录（默认项目下 data/，或环境变量 PAPER_TRACKER_DATA_DIR）")
    ap.add_argument("--cas", help="中科院分区表（升级版）CSV")
    ap.add_argument("--xr", help="新锐期刊分区表 CSV")
    ap.add_argument("--jcr", help="JCR CSV")
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
    print("完成。重启本系统（或刷新页面）后，新导入的分级会显示在徽章里。")


if __name__ == "__main__":
    main()
