# -*- coding: utf-8 -*-
"""智能功能：编辑来信、状态词规则、自然语言提问。模型用假的（monkeypatch ai.chat_json），只用合成数据。"""
from datetime import date, timedelta

import pytest

from app import ai, db, smart, stages


@pytest.fixture(autouse=True, scope="module")
def _db():
    db.init_db()                 # 不经过 client 的用例也要有表
    smart.load_status_rules()

# ---------------------------------------------------------------- 规则部分
LETTERS = {
    "major": ("Date: 12 Nov 2026\nRef: PR-D-26-00001\nDear Dr. Example,\nBased on the reviewers' comments, your "
              "manuscript requires major revision before it can be considered further.\n"
              "Please submit your revised manuscript by 15 Jan 2027.\nYours sincerely"),
    "accept": "Dear Author, we are pleased to inform you that your manuscript PR-D-26-00002 has been accepted "
              "for publication in Pattern Recognition.",
    "desk": "Dear Author, I regret to inform you that we are unable to accept your manuscript. The editor "
            "decided not to send it out for external review.",
    "minor_zh": "尊敬的作者：您的稿件经审稿人评审，需要小修。请在30天内提交修改稿。",
    "cond": "The decision on your paper is Accept with minor revisions. Please revise within 3 weeks.",
    "rr": "We regret to inform you that your paper is rejected in its current form, but we encourage you to "
          "revise and resubmit.",
    "notacc": "After careful consideration your manuscript cannot be accepted for publication. Regards.",
}


@pytest.mark.parametrize("key,want", [("major", ["major_revision"]), ("accept", ["accept"]),
                                      ("desk", ["desk_reject"]), ("minor_zh", ["minor_revision"]),
                                      ("cond", ["minor_revision"]), ("rr", ["revise_resubmit"]),
                                      ("notacc", ["reject"])])
def test_rule_decisions(key, want):
    assert smart.rule_decisions(LETTERS[key]) == want


def test_dates_and_deadlines():
    iso = {d for _s, _e, d in smart.find_dates("by 15 Jan 2027, 2027年1月15日前, Jan 15, 2027, 2027-01-15, 15-Jan-2027")}
    assert iso == {"2027-01-15"}
    assert smart.letter_date(LETTERS["major"]) == "2026-11-12"
    dl = smart.rule_deadlines(LETTERS["major"], "2026-11-12")
    assert dl[0] == {"date": "2027-01-15", "how": "stated", "text": dl[0]["text"]}
    rel = smart.rule_deadlines("Please revise within 60 days.", "2026-11-01")
    assert rel[0]["date"] == "2026-12-31" and rel[0]["how"] == "relative"
    zh = smart.rule_deadlines(LETTERS["minor_zh"])
    assert zh[0]["date"] == (date.today() + timedelta(days=30)).isoformat()


def test_letter_rules_only():
    r = smart.analyze_letter(LETTERS["major"], {"id": 1, "manuscript_number": "PR-D-26-00001"}, use_model=False)
    assert (r["decision"], r["stage"], r["deadline"], r["agreement"]) == ("major_revision", "revise", "2027-01-15", "rules_only")
    assert r["manuscript_number"] == "PR-D-26-00001" and not r["warning"]
    r = smart.analyze_letter(LETTERS["major"], {"id": 1, "manuscript_number": "XX-9"}, use_model=False)
    assert "不一致" in r["warning"]
    r = smart.analyze_letter(LETTERS["accept"], None, use_model=False)
    assert r["stage"] == "accepted" and r["deadline"] == ""


# ---------------------------------------------------------------- 假模型
@pytest.fixture()
def fake_model(monkeypatch):
    calls = []

    def install(reply):
        monkeypatch.setattr(ai, "enabled", lambda: True)

        def chat(system, user, schema, max_tokens=256):
            calls.append(user)
            if isinstance(reply, Exception):
                raise reply
            return {**(reply(user) if callable(reply) else reply), "_ms": 42}
        monkeypatch.setattr(ai, "chat_json", chat)
        return calls
    return install


def test_letter_model_agrees(fake_model):
    fake_model({"decision": "major_revision", "deadline": "2027-01-15", "manuscript_number": "PR-D-26-00001",
                "quote": "your manuscript requires major revision before it can be considered further."})
    r = smart.analyze_letter(LETTERS["major"])
    assert r["agreement"] == "agree" and r["via"] == "rules+model"
    assert r["deadline"] == "2027-01-15" and r["deadline_how"] == "model" and r["quote"]


def test_letter_model_claims_are_checked_against_the_text(fake_model):
    fake_model({"decision": "major_revision", "deadline": "2027-02-01", "manuscript_number": "PR-D-99-99999",
                "quote": "This sentence is not in the letter."})
    r = smart.analyze_letter(LETTERS["major"])
    assert r["deadline"] == "2027-01-15" and r["deadline_how"] == "stated"     # 回到规则找到的日期
    assert r["quote"] == "" and r["manuscript_number"] == "PR-D-26-00001"
    assert any("2027-02-01" in n for n in r["notes"]) and any("引文" in n for n in r["notes"])


def test_letter_disagreement_keeps_rules(fake_model):
    fake_model({"decision": "accept", "deadline": "", "manuscript_number": "", "quote": ""})
    r = smart.analyze_letter(LETTERS["desk"])
    assert r["decision"] == "desk_reject" and r["agreement"] == "disagree"


def test_letter_model_decides_when_rules_cannot(fake_model):
    text = "Dear author, the handling editor has reached a decision on your paper; see the attached file."
    fake_model({"decision": "other", "deadline": "", "manuscript_number": "", "quote": ""})
    r = smart.analyze_letter(text)
    assert r["decision"] == "other" and r["agreement"] == "model_only" and r["stage"] is None


def test_letter_model_failure_falls_back(fake_model):
    fake_model(ai.AiError("本地模型没有响应（timeout）"))
    r = smart.analyze_letter(LETTERS["accept"])
    assert r["decision"] == "accept" and r["model"]["error"]


# ---------------------------------------------------------------- 状态词规则
def test_status_rules_feed_normalize():
    raw = "Out for Refereeing"
    assert stages.normalize(raw) is None
    smart.save_status_rule(raw, "review", "model")
    try:
        assert stages.normalize("  out for refereeing ") == "review"
        assert smart.suggest_stage(raw)["source"] == "user_rule"
        assert any(r["key"] == "out for refereeing" and r["source"] == "model" for r in smart.list_status_rules())
    finally:
        smart.delete_status_rule("out for refereeing")
    assert stages.normalize(raw) is None


def test_suggest_stage(fake_model, monkeypatch):
    assert smart.suggest_stage("Under Review")["source"] == "rules"
    monkeypatch.setattr(ai, "enabled", lambda: False)
    assert smart.suggest_stage("Paper in copyediting")["source"] == "none"
    fake_model({"stage": "production"})
    r = smart.suggest_stage("Paper in copyediting")
    assert (r["stage"], r["source"]) == ("production", "model")
    fake_model({"stage": "unknown"})
    assert smart.suggest_stage("Zzz qqq")["stage"] is None
    with pytest.raises(ValueError):
        smart.save_status_rule("x", "not-a-stage")


# ---------------------------------------------------------------- 自然语言提问
@pytest.mark.parametrize("q,intent,params", [
    ("哪些稿子审了超过三个月？", "stuck", {"days": 90}),
    ("papers stuck in review for more than 90 days", "stuck", {"days": 90, "stage": "review"}),
    ("How many papers by venue?", "count_by", {"dim": "venue"}),
    ("2025年投了哪些", "submitted_in", {"year": 2025}),
    ("2026年3月投的稿子", "submitted_in", {"year": 2026, "month": 3}),
    ("最近30天被拒的", "outcomes_recent", {"days": 30, "outcome": "rejected"}),
    ("返修截止日快到了的", "deadlines", {}),
    ("哪些在外审", "list_stage", {"stage": "review"}),
    ("find diffusion", "search", {"text": "diffusion"}),
    ("Show the decisions I received in the last 30 days", "outcomes_recent", {"days": 30, "outcome": "any"}),
    ("How many papers did I submit to each venue?", "count_by", {"dim": "venue"}),
    ("find my paper about graph neural networks", "search", {"text": "graph neural networks"}),
])
def test_rule_planning(q, intent, params):
    p = smart.plan_question(q, use_model=False)
    assert p["intent"] == intent and all(p["params"].get(k) == v for k, v in params.items()), p


def test_every_template_runs():
    for intent, params in [("list_stage", {"stage": "review"}), ("stuck", {"days": 1}), ("count_by", {"dim": "year", "year": 2026}),
                           ("count_by", {"dim": "outcome"}), ("submitted_in", {"year": 2026, "month": 1}),
                           ("outcomes_recent", {"days": 9999, "outcome": "any"}), ("review_time", {"text": "a_b%"}),
                           ("deadlines", {"days": 9999}), ("search", {"text": "100%_x"})]:
        sql, args, ex = smart.build_query(intent, params)
        assert sql.lstrip().upper().startswith("SELECT") and ex["zh"] and ex["en"]
        from app import db
        with db.get_db() as c:
            c.execute(sql, args).fetchall()
    with pytest.raises(ValueError):
        smart.build_query("drop_tables", {})


def test_model_only_classifies_cue_less_questions(fake_model):
    calls = fake_model({"intent": "outcomes_recent"})
    p = smart.plan_question("哪些稿子审了超过三个月？")
    assert p["intent"] == "stuck" and p["via"] == "rules" and not calls     # 规则有把握：不调模型
    p = smart.plan_question("Any news lately?")
    assert p["intent"] == "outcomes_recent" and p["via"] == "model" and len(calls) == 1
    fake_model({"intent": "list_stage"})                    # 模型选了 list_stage 但问题里没有阶段
    p = smart.plan_question("what is going on with my stuff")
    assert p["intent"] == "search" and p["notes"]
    fake_model(ai.AiError("本地模型没有响应（timeout）"))
    p = smart.plan_question("Any news lately?")
    assert p["intent"] == "search" and p["model"]["error"]


def test_letter_model_only_needs_supporting_quote(fake_model):
    ack = ("Dear Author, this message confirms that we have received your files. You can track the progress "
           "of your submission in the author centre.")
    fake_model({"decision": "desk_reject", "deadline": "", "manuscript_number": "",
                "quote": "this message confirms that we have received your files."})
    r = smart.analyze_letter(ack)
    assert r["decision"] == "other" and r["stage"] is None and any("依据" in n for n in r["notes"])
    hard = ("The reviewers feel substantial changes are needed. You may submit a revised version that "
            "addresses all of their comments.")
    fake_model({"decision": "major_revision", "deadline": "", "manuscript_number": "",
                "quote": "You may submit a revised version that addresses all of their comments."})
    r = smart.analyze_letter(hard)
    assert r["decision"] == "major_revision" and r["agreement"] == "model_only"


def test_status_examples_are_retrieved_from_the_dictionary():
    ex = smart.similar_statuses("Under Evaluation by Editor")
    assert ex[0] == ("under editor evaluation", "editor")
    assert {st for _k, st in ex} >= {"submitted", "review", "production"}     # 每个阶段都有锚点


# ---------------------------------------------------------------- 接口
def _new(client, title, number="PR-D-26-00001"):
    r = client.post("/api/submissions", json={
        "title": title, "manuscript_number": number, "submitted_at": "2026-06-01",
        "venue": {"name": "Pattern Recognition", "type": "journal", "tracker": "manual"}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_api_letter_preview_and_confirm(client):
    sid = _new(client, "LetterNet: Synthetic Paper")
    prev = client.post(f"/api/submissions/{sid}/letter", json={"text": LETTERS["major"]}).json()
    assert prev["decision"] == "major_revision" and prev["deadline"] == "2027-01-15"
    w = client.post(f"/api/submissions/{sid}/letter/confirm",
                    json={"text": LETTERS["major"], "decision": "major_revision", "deadline": "2027-01-15"}).json()
    assert w["ok"] and w["current_stage"] == "revise"
    d = client.get(f"/api/submissions/{sid}").json()
    assert d["revise_due"] == "2027-01-15"
    ev = [e for e in d["events"] if e["source"] == "letter"][-1]
    assert ev["payload"]["decision"] == "major_revision" and ev["payload"]["via"] == "rules"
    assert ev["observed_at"].startswith("2026-11-12")              # 按来信日期记入
    r = client.post(f"/api/submissions/{sid}/letter/confirm", json={"text": LETTERS["major"], "decision": "other"})
    assert r.status_code == 400


def test_api_letter_user_override_is_recorded(client):
    sid = _new(client, "OverrideNet: Synthetic Paper", "PR-D-26-00002")
    client.post(f"/api/submissions/{sid}/letter", json={"text": LETTERS["accept"]})
    client.post(f"/api/submissions/{sid}/letter/confirm", json={"text": LETTERS["accept"], "decision": "minor_revision"})
    ev = [e for e in client.get(f"/api/submissions/{sid}").json()["events"] if e["source"] == "letter"][-1]
    assert ev["stage"] == "revise" and ev["payload"]["via"] == "user"


def test_api_status_rules(client):
    r = client.post("/api/status-rules", json={"raw": "Waiting for referee reports", "stage": "review"})
    assert r.status_code == 200 and any(x["key"] == "waiting for referee reports" for x in r.json())
    assert stages.normalize("Waiting for referee reports") == "review"
    assert client.post("/api/status-rules/suggest", json={"raw": "Waiting for referee reports"}).json()["source"] == "user_rule"
    r = client.post("/api/status-rules/delete", json={"key": "waiting for referee reports"})
    assert all(x["key"] != "waiting for referee reports" for x in r.json())
    assert client.post("/api/status-rules", json={"raw": "x", "stage": "bogus"}).status_code == 400


def test_api_ask_and_ai_endpoints(client):
    _new(client, "AskNet: Synthetic Diffusion Paper", "PR-D-26-00003")
    r = client.post("/api/ask", json={"q": "find diffusion", "use_model": False}).json()
    assert r["intent"] == "search" and r["sql"].startswith("SELECT") and r["columns"][0] == "id"
    assert any("AskNet" in row[1] for row in r["rows"])
    assert client.post("/api/ask", json={"q": "", "use_model": False}).status_code == 400
    st = client.get("/api/stages").json()
    assert set(st["ai"]) == {"enabled", "supported"}
    s = client.get("/api/ai/status").json()
    assert s["installed"] is False and s["model"]["license"] == "Apache-2.0"
    assert client.post("/api/ai/enable", json={"enabled": True}).status_code == 400
    assert client.post("/api/ai/install", json={"source": "elsewhere"}).status_code == 400
    assert client.post("/api/ai/start").status_code == 503
