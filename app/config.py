# -*- coding: utf-8 -*-
"""运行配置。

- 数据目录：环境变量 PAPER_TRACKER_DATA_DIR（run.py 的 --data-dir 会设置它），默认项目下的 data/。
  这里存放你的全部私人数据（数据库、PDF、备份、分级库），已被 .gitignore 排除，切勿提交。
- 随项目附带的分级数据：data-sources/（CCF 推荐目录、中科院国际期刊预警名单、中科院分区表 2025、
  新锐期刊分区表 2026），权利归各自的发布方，见 DATA_SOURCES.md。
"""
import os

VERSION = "0.5.1"
REPO_URL = "https://github.com/avalanchezy/RecordYourResearch"
USER_AGENT = f"RecordYourResearch/{VERSION} (+{REPO_URL})"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.environ.get("PAPER_TRACKER_DATA_DIR") or os.path.join(BASE_DIR, "data"))
SOURCES_DIR = os.path.join(BASE_DIR, "data-sources")
RANKINGS_DB = os.path.join(DATA_DIR, "rankings", "rankings.db")
# 演示模式（python run.py --demo）：用 demo-data/ 里的虚构数据，界面上标出「演示数据」
DEMO = os.environ.get("PAPER_TRACKER_DEMO") == "1"
