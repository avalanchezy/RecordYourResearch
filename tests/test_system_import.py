# -*- coding: utf-8 -*-
"""投稿系统页面导入（用户自己登录后整页复制粘贴）：解析 → 对到库里的稿件 → 记入时间线 / 新建。只用合成数据。"""
from app import syspaste as sp

EM_PAGE = """Editorial Manager
Author Main Menu
Submissions Being Processed for Author Alice Example, Ph.D.
Page: 1 of 1 (2 total submissions)\tDisplay 10 results per page.
Action\tManuscript Number\tTitle\tInitial Date Submitted\tStatus Date\tCurrent Status
Action Links\tSYNJ-D-25-01234\tSynthetic Study of Imaginary Graph Networks for Toy Data\tOct 02, 2025\tNov 10, 2025\tUnder Review
Action Links\tSYNJ-D-25-05678R1\tA Placeholder Benchmark for Fictional Segmentation Tasks\tAug 14, 2025\tNov 17, 2025\tRevision Submitted to Journal
"""

S1_PAGE = """ScholarOne Manuscripts
Author Dashboard
Submitted Manuscripts
STATUS\tID\tTITLE\tCREATED\tSUBMITTED
ADM: Example, Alice
• Awaiting Reviewer Scores
view submission (pdf)
contact journal\tSYNT-2025-P-12345\tSynthetic Networks for Imaginary Learning Systems
View Submission\t02-Oct-2025\t05-Oct-2025
EIC: Sample, Bob
• Major Revision (12-Nov-2025)
a revision has been submitted (SYNT-2025-P-02222.R1)
contact journal\tSYNT-2025-P-02222\tToy Models of Fictional Attention Mechanisms
View Submission\t01-Jun-2025\t03-Jun-2025
"""


def test_parse_editorial_manager_table():
    r = sp.parse(EM_PAGE)
    assert r["system"] == "editorial_manager"
    got = {i["manuscript_id"]: i for i in r["items"]}
    assert set(got) == {"SYNJ-D-25-01234", "SYNJ-D-25-05678R1"}
    a, b = got["SYNJ-D-25-01234"], got["SYNJ-D-25-05678R1"]
    assert a["stage"] == "review" and a["title"].startswith("Synthetic Study of Imaginary")
    assert (a["submitted_date"], a["status_date"]) == ("2025-10-02", "2025-11-10")
    assert b["stage"] == "editor" and sp.ms_base(b["manuscript_id"]) == "SYNJ-D-25-05678"


def test_parse_scholarone_dashboard():
    r = sp.parse(S1_PAGE)
    assert r["system"] == "scholarone"
    got = {i["manuscript_id"]: i for i in r["items"]}
    assert set(got) == {"SYNT-2025-P-12345", "SYNT-2025-P-02222"}      # 状态句里引用的 .R1 不单独成篇
    assert got["SYNT-2025-P-12345"]["stage"] == "review"
    assert got["SYNT-2025-P-12345"]["submitted_date"] == "2025-10-05"
    d = got["SYNT-2025-P-02222"]
    assert d["stage"] == "revise" and d["status_date"] == "2025-11-12" and d["title"].startswith("Toy Models")


def test_ms_base_and_title_key():
    assert sp.ms_base("ABC-D-25-00001R2") == sp.ms_base("abc-d-25-00001") == "ABC-D-25-00001"
    assert sp.ms_base("TNNLS-2025-P-1.R1") == "TNNLS-2025-P-1"
    assert sp.title_key("A  Study: of X!") == sp.title_key("a study of x")


def test_titles_with_status_words_are_not_statuses():
    assert sp._status_of("Accepted Methods for Synthetic Data Augmentation in Imaginary Domains") is None
    assert sp._status_of("Status: Under Review")[0] == "review"
    assert sp._status_of("Initial Date Submitted") is None


def test_preview_and_confirm_updates_and_creates(client):
    r = client.post("/api/submissions", json={"title": "Some Earlier Synthetic Paper", "manuscript_number": "SYNJ-D-25-01234",
                                               "venue": {"name": "Synthetic Journal", "type": "journal"}})
    sid = r.json()["id"]
    pv = client.post("/api/import/system-paste", json={"text": EM_PAGE}).json()
    items = {i["manuscript_id"]: (n, i) for n, i in enumerate(pv["items"])}
    n_upd, upd = items["SYNJ-D-25-01234"]
    n_new, new = items["SYNJ-D-25-05678R1"]
    assert upd["action"] == "update" and upd["match"]["id"] == sid and upd["match"]["by"] == "number"
    assert new["action"] == "create" and new["match"] is None

    w = client.post("/api/import/system-paste/confirm", json={"text": EM_PAGE, "picks": [
        {"index": n_upd, "action": "update"}, {"index": n_new, "action": "create", "venue_name": "Synthetic Journal"}]}).json()
    assert w["updated"] == 1 and w["created"] == 1
    d = client.get(f"/api/submissions/{sid}").json()
    assert d["current_stage"] == "review"
    ev = [e for e in d["events"] if e["source"] == "paste"]
    assert ev and ev[0]["raw_status"] == "Under Review" and ev[0]["observed_at"].startswith("2025-11-10")
    created = client.get(f"/api/submissions/{w['ids'][1]}").json()
    assert created["manuscript_number"] == "SYNJ-D-25-05678R1" and created["current_stage"] == "editor"
    assert created["submitted_at"] == "2025-08-14"

    # 同一页再粘贴一次：都已是最新，不会重复记录
    pv2 = client.post("/api/import/system-paste", json={"text": EM_PAGE}).json()
    assert {i["action"] for i in pv2["items"]} == {"same"}
    w2 = client.post("/api/import/system-paste/confirm", json={"text": EM_PAGE, "picks": [
        {"index": 0, "action": "update"}, {"index": 1, "action": "update"}]}).json()
    assert w2["updated"] == 0 and w2["unchanged"] == 2
    # 粘贴导入不算「自上次查看以来」的自动变化
    assert all(c["submission_id"] not in (sid, w["ids"][1]) for c in client.get("/api/changes").json()["changes"])


def test_confirm_rejects_bad_picks(client):
    assert client.post("/api/import/system-paste/confirm", json={"text": EM_PAGE, "picks": [{"index": 99, "action": "update"}]}).status_code == 400
    assert client.post("/api/import/system-paste/confirm", json={"text": "  ", "picks": []}).status_code == 400
    pv = client.post("/api/import/system-paste", json={"text": S1_PAGE}).json()
    n = next(k for k, i in enumerate(pv["items"]) if i["action"] == "create")
    r = client.post("/api/import/system-paste/confirm", json={"text": S1_PAGE, "picks": [{"index": n, "action": "create"}]})
    assert r.status_code == 400 and "期刊名" in r.json()["detail"]      # 新建必须有期刊名


def _em_row(num, title, status, status_date="Nov 10, 2025", sub_date="Oct 02, 2025"):
    return (f"Editorial Manager\nAuthor Main Menu\nSubmissions Being Processed for Author Alice Example\n"
            f"Action\tManuscript Number\tTitle\tInitial Date Submitted\tStatus Date\tCurrent Status\n"
            f"Action Links\t{num}\t{title}\t{sub_date}\t{status_date}\t{status}\n")


def _items(client, text):
    return client.post("/api/import/system-paste", json={"text": text}).json()["items"]


def test_title_is_not_used_when_the_row_has_a_number(client):
    """改投后同名的期刊稿（有编号）不能按标题对到会议原稿上。"""
    t = "Synthetic Study of Transferred Toy Graph Embeddings"
    client.post("/api/submissions", json={"title": t, "venue": {"name": "Imaginary Conference", "type": "conference"}})
    it = _items(client, _em_row("SYNJ-D-25-31001", t, "Under Review"))[0]
    assert it["match"] is None and it["action"] == "create"


def test_title_match_only_for_journals_and_not_onto_rejected_papers(client):
    t = "Synthetic Study of Placeholder Wavelet Denoisers"
    a = client.post("/api/submissions", json={"title": t, "venue": {"name": "Synthetic Journal A", "type": "journal"}}).json()["id"]
    client.post(f"/api/submissions/{a}/events", json={"stage": "rejected", "raw": "Reject"})
    page = ("My Submissions\nSynthetic Journal B\nTitle: " + t + "\nStatus: Under Review\nSubmitted: 2025-11-02\n")
    it = _items(client, page)[0]
    assert it["match"] is None          # 已拒的旧稿不接新的在审状态


def test_ambiguous_and_trashed_rows_are_not_importable(client):
    for i in (1, 2):
        client.post("/api/submissions", json={"title": f"Twin Synthetic Paper {i}", "manuscript_number": "SYNJ-D-25-32002" + ("" if i == 1 else "R1"),
                                               "venue": {"name": "Synthetic Journal", "type": "journal"}})
    it = _items(client, _em_row("SYNJ-D-25-32002R2", "Twin Synthetic Paper", "Under Review"))[0]
    assert it["action"] == "ambiguous" and len(it["candidates"]) == 2
    exact = _items(client, _em_row("SYNJ-D-25-32002R1", "Twin Synthetic Paper 2", "Under Review"))[0]
    assert exact["action"] == "update" and exact["match"]["by"] == "number"      # 完全相同的编号优先
    tid = client.post("/api/submissions", json={"title": "Trashed Synthetic Paper", "manuscript_number": "SYNJ-D-25-33003",
                                                 "venue": {"name": "Synthetic Journal", "type": "journal"}}).json()["id"]
    client.delete(f"/api/submissions/{tid}")
    tr = _items(client, _em_row("SYNJ-D-25-33003", "Trashed Synthetic Paper", "Under Review"))[0]
    assert tr["action"] == "trashed"
    r = client.post("/api/import/system-paste/confirm", json={"text": _em_row("SYNJ-D-25-33003", "Trashed Synthetic Paper", "Under Review"),
                                                             "picks": [{"index": 0, "action": "create", "venue_name": "X"}]})
    assert r.status_code == 400


def test_earlier_official_date_becomes_current_status(client):
    """建档时没填投稿日期（登记事件在今天），粘贴的官方状态日期更早：它仍应成为当前状态。"""
    sid = client.post("/api/submissions", json={"title": "Late Registered Synthetic Paper", "manuscript_number": "SYNJ-D-25-34004",
                                                 "venue": {"name": "Synthetic Journal", "type": "journal"}}).json()["id"]
    text = _em_row("SYNJ-D-25-34004", "Late Registered Synthetic Paper", "Revise", status_date="Nov 10, 2025", sub_date="")
    w = client.post("/api/import/system-paste/confirm", json={"text": text, "picks": [{"index": 0, "action": "update"}]}).json()
    assert w["updated"] == 1 and w["backdated"] == 0
    assert client.get(f"/api/submissions/{sid}").json()["current_stage"] == "revise"
    created = client.post("/api/import/system-paste/confirm", json={
        "text": _em_row("SYNJ-D-25-35005", "Freshly Imported Synthetic Paper", "Revise", sub_date=""),
        "picks": [{"index": 0, "action": "create", "venue_name": "Synthetic Journal"}]}).json()
    assert client.get(f"/api/submissions/{created['ids'][0]}").json()["current_stage"] == "revise"


def test_numbered_rows_never_title_match_and_repeats_create_once(client):
    """两篇编号不同的同名稿不会靠标题揉到库里同一篇；同一页里重复出现的同一篇只建一次。"""
    t = "Shared Title Synthetic Paper Without Number"
    client.post("/api/submissions", json={"title": t, "venue": {"name": "Synthetic Journal", "type": "journal"}})
    page = _em_row("SYNJ-D-25-36006", t, "Under Review") + \
        f"Action Links\tSYNJ-D-25-36007\t{t}\tMay 02, 2025\tJul 10, 2025\tReject\n"
    assert all(i["match"] is None for i in _items(client, page))
    card = "Title: Brand New Synthetic Card Paper\nStatus: Under Review\nSubmitted: 2025-10-02\n\n"
    page2 = "My Submissions\n" + card + card
    its = _items(client, page2)
    picks = [{"index": n, "action": "create", "venue_name": "Synthetic Journal"} for n, i in enumerate(its) if i["action"] == "create"]
    assert picks
    w = client.post("/api/import/system-paste/confirm", json={"text": page2, "picks": picks}).json()
    assert w["created"] == 1
