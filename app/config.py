# -*- coding: utf-8 -*-
"""运行配置。

- 数据目录：环境变量 PAPER_TRACKER_DATA_DIR（run.py 的 --data-dir 会设置它），默认项目下的 data/。
  这里存放你的全部私人数据（数据库、PDF、备份、分级库），已被 .gitignore 排除，切勿提交。
- 随项目发布的公开数据：data-sources/（CCF 推荐目录、中科院国际期刊预警名单），有各自的使用条款。
"""
import os

VERSION = "0.1.0"
REPO_URL = "https://github.com/avalanchezy/RecordYourResearch"
USER_AGENT = f"RecordYourResearch/{VERSION} (+{REPO_URL})"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.environ.get("PAPER_TRACKER_DATA_DIR") or os.path.join(BASE_DIR, "data"))
SOURCES_DIR = os.path.join(BASE_DIR, "data-sources")
RANKINGS_DB = os.path.join(DATA_DIR, "rankings", "rankings.db")
