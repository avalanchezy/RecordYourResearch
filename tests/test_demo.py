# -*- coding: utf-8 -*-
"""演示模式（python run.py --demo）与 docs/demo/ 里的演示页面：保证 docs/DEMO.md 里说的都还成立。"""
import os
import sqlite3
import subprocess
import sys

from app import syspaste
from app.elsevier_counters import parse_reviewer_counters

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _page(name):
    with open(os.path.join(ROOT, "docs", "demo", name), encoding="utf-8") as f:
        return f.read()


def test_demo_seed_builds_a_fresh_demo_folder(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PAPER_TRACKER_")}
    env["PYTHONIOENCODING"] = "cp1252"          # 模拟英文 Windows 的管道编码：脚本输出中文也不能崩
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "demo_seed.py"), "--data-dir", str(tmp_path)],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="cp1252",
                       errors="replace", timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    con = sqlite3.connect(os.path.join(str(tmp_path), "tracker.db"))
    try:
        n_subs = con.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        backfilled = con.execute("SELECT COUNT(*) FROM status_events WHERE recorded_at > observed_at"
                                 " AND julianday(recorded_at) - julianday(observed_at) > 1").fetchone()[0]
        pending = con.execute("SELECT COUNT(*) FROM pending_imports WHERE dismissed = 0").fetchone()[0]
    finally:
        con.close()
    assert n_subs >= 7 and pending == 1
    assert backfilled == 1            # 只有一条「事后补录」，用来演示补录标记
    again = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "demo_seed.py"), "--data-dir", str(tmp_path)],
                           cwd=ROOT, env=env, capture_output=True, text=True, encoding="cp1252",
                           errors="replace", timeout=120)
    assert again.returncode != 0      # 不会往已有数据库里再灌一遍


def test_demo_pages_parse_as_the_demo_guide_says():
    s1 = syspaste.parse(_page("scholarone-author-dashboard.txt"))
    assert s1["system"] == "scholarone" and len(s1["items"]) == 3
    em = syspaste.parse(_page("editorial-manager-folders.txt"))
    assert em["system"] == "editorial_manager" and len(em["items"]) == 5
    r = parse_reviewer_counters(_page("elsevier-query-changed-counts.txt"),
                                {"invited": 4, "accepted": 2, "completed": 1})
    assert r["reviewers"] == {"invited": 5, "accepted": 3, "completed": 2}
    assert r["reviewers_prev"] == {"invited": 4, "accepted": 2, "completed": 1}
    assert not r["reviewers_ambiguous"]
