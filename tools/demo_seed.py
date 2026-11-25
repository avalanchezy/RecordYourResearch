# -*- coding: utf-8 -*-
"""往一个全新的数据目录里灌一批「虚构的」投稿，用来试用界面、截图或开发调试。

用法：
  python tools/demo_seed.py --data-dir ./demo-data
  python run.py --data-dir ./demo-data --no-daily --open

全部是编造的标题 / 作者 / 编号，不联网。不要对你自己的真实数据目录运行。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for _s in (sys.stdout, sys.stderr):      # 非中文 Windows 的控制台 / 管道（cp1252）不会因中文输出崩溃
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="生成演示数据（虚构）")
    ap.add_argument("--data-dir", required=True, help="一个新的、空的数据目录")
    a = ap.parse_args()
    data_dir = os.path.abspath(a.data_dir)
    if os.path.exists(os.path.join(data_dir, "tracker.db")):
        sys.exit(f"{data_dir} 里已经有 tracker.db：为防止混进真实数据，请换一个空目录")
    os.environ["PAPER_TRACKER_DATA_DIR"] = data_dir
    os.environ["PAPER_TRACKER_NO_DAILY"] = "1"
    os.environ["PAPER_TRACKER_TESTING"] = "1"
    sys.path.insert(0, ROOT)
    import warnings
    warnings.filterwarnings("ignore", message="Using .httpx. with .starlette.testclient")
    from fastapi.testclient import TestClient
    import server
    from app import db

    c = TestClient(server.app)
    day = lambda n: (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")
    ts = lambda n, h=10: (datetime.now() - timedelta(days=n)).strftime(f"%Y-%m-%d {h:02d}:00:00")

    def new(title, venue, **kw):
        body = {"title": title, "authors": kw.pop("authors", ["Alice Example", "Bob Sample"]),
                "abstract": "A fictional abstract used only for the demo. " * 3,
                "keywords": ["demo", "synthetic"], "venue": venue, **kw}
        r = c.post("/api/submissions", json=body)
        r.raise_for_status()
        return r.json()["id"]

    def ev(sid, stage, raw, days_ago, source="manual", payload=None):
        with db.get_db() as conn:
            db.add_event(conn, sid, stage, raw, source, payload or {}, observed_at=ts(days_ago))

    a1 = new("DemoNet: Learning to Track Fictional Submissions",
             {"name": "ICLR", "year": "2026", "type": "conference", "tracker": "openreview"},
             submitted_at=day(40), openreview_forum="DemoForum01")
    ev(a1, "review", "ICLR 2026 Conference Submission", 30, "openreview", {"sync": True})
    a2 = new("SampleFormer: Attention Over Imaginary Data",
             {"name": "NeurIPS", "year": "2025", "type": "conference", "tracker": "openreview"},
             submitted_at=day(120), openreview_forum="DemoForum02")
    ev(a2, "reviews_done", "NeurIPS 2025 Conference Submission · 4 reviews released", 60, "openreview", {"sync": True})
    ev(a2, "accepted", "Accept (poster)", 25, "openreview", {"sync": True, "decision": "Accept (poster)"})
    j1 = new("A Survey of Placeholder Methods for Pattern Recognition",
             {"name": "Pattern Recognition", "type": "journal", "tracker": "elsevier", "journal_acronym": "PR"},
             submitted_at=day(75), manuscript_number="PR-D-25-00000", corresponding_pinyin="ALICE EXAMPLE")
    ev(j1, "editor", "With Editor", 70)
    ev(j1, "review", "Under Review", 52, "elsevier", {"reviewers": {"invited": 4, "accepted": 2, "completed": 1}})
    j2 = new("Robust Toy Models with Synthetic Noise",
             {"name": "Neurocomputing", "type": "journal", "tracker": "elsevier", "journal_acronym": "NEUCOM"},
             submitted_at=day(160), manuscript_number="NEUCOM-D-25-00000", corresponding_pinyin="BOB SAMPLE")
    ev(j2, "review", "Under Review", 130)
    ev(j2, "revise", "Revise - Minor", 10)
    c.patch(f"/api/submissions/{j2}", json={"revise_due": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")})
    r1 = new("Imaginary Benchmarks Considered Harmful",
             {"name": "ICME", "year": "2025", "type": "conference", "tracker": "manual"}, submitted_at=day(150))
    ev(r1, "review", "Under Review", 140)
    ev(r1, "rejected", "Reject", 35)
    c.post(f"/api/submissions/{r1}/resubmit", json={"venue": {"name": "ICASSP", "year": "2026", "type": "conference"},
                                                    "relation": "转投", "submitted_at": day(20)})
    m1 = new("Notes on Entirely Made-Up Datasets",
             {"name": "IEEE Transactions on Cybernetics", "type": "journal", "tracker": "manual"}, submitted_at=day(45))
    ev(m1, "editor", "With Editor", 33)

    # 演示里的历史状态都按「当时就记下了」处理：写入时间 = 发生时间；
    # 只留一条「事后补录」（编辑处理晚了 6 天才登记），用来演示时间线上的来源与补录标记
    with db.get_db() as conn:
        conn.execute("UPDATE status_events SET recorded_at = observed_at")
        conn.execute("UPDATE status_events SET recorded_at = ? WHERE submission_id = ? AND stage = 'editor'",
                     (ts(64), j1))

    # 「自上次查看以来」：一条自动渠道检测到的新变化 + 一篇待建档
    c.post("/api/changes/seen", json={"until": c.get("/api/changes").json()["now"] or ""})
    with db.get_db() as conn:
        db.add_event(conn, a1, "reviews_done", "ICLR 2026 Conference Submission · 3 reviews released",
                     "openreview", {"sync": True})
        conn.execute("INSERT INTO pending_imports (forum, data, first_seen_at, last_seen_at, dismissed)"
                     " VALUES (?,?," + db.SQL_NOW_MS + "," + db.SQL_NOW_MS + ",0)",
                     ("DemoForum03", '{"forum": "DemoForum03", "title": "A Co-authored Demo Paper on Nothing",'
                      ' "venue_id": "ICML.cc/2026/Conference", "venue_name": "ICML 2026", "venue_base_name": "ICML",'
                      ' "year": "2026", "stage": "review", "raw": "Under review"}'))
    print(f"演示数据已写入 / demo data written to {data_dir}:", c.get("/api/categories/counts").json())
    print("可以粘贴的演示页面 / pages to paste during the demo: docs/demo/*.txt (see docs/DEMO.md)")


if __name__ == "__main__":
    main()
