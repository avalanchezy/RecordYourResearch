# -*- coding: utf-8 -*-
"""真实本地模型的端到端测试：按界面上的流程下载、校验、部署 llama.cpp + Qwen3.5-0.8B，再跑三个智能功能。

只在设置了 RYR_AI_REAL=1 时运行（CI 的 ai 任务；第一次要下载约 1 GB）。只用合成数据。
会打印模型自己的判断和用时，方便看小模型实际帮上了多少忙。
"""
import json
import os
import time

import pytest

from app import ai, db, smart

pytestmark = pytest.mark.skipif(os.environ.get("RYR_AI_REAL") != "1",
                                reason="real local model: set RYR_AI_REAL=1 (downloads about 1 GB)")


@pytest.fixture(scope="module", autouse=True)
def model():
    db.init_db()
    smart.load_status_rules()
    if not ai.installed()["both"]:
        ai.install(os.environ.get("RYR_AI_SOURCE", "huggingface"))
        t0 = time.time()
        while ai.status()["busy"]:
            assert time.time() - t0 < 1800, "download took more than 30 minutes"
            time.sleep(2)
    st = ai.status()
    assert st["installed"], st["progress"]
    with open(os.path.join(ai.AI_DIR, "installed.json"), encoding="utf-8") as f:
        print("\nINSTALLED:", json.dumps(json.load(f)), flush=True)
    ai.set_enabled(True)
    t0 = time.time()
    ai.ensure_running()
    print(f"model start: {time.time() - t0:.1f}s", flush=True)
    yield
    ai.stop()


LETTERS = [
    ("Dear Dr. Example,\nThank you for your submission PR-D-26-00001. Based on the reviewers' reports, your manuscript "
     "requires major revision. Please submit the revised version by 15 January 2027.\nBest regards,\nThe Editor",
     "major_revision", "2027-01-15"),
    ("Dear Author,\nWe are pleased to inform you that your paper has been accepted for publication in the journal. "
     "Congratulations!", "accept", ""),
    ("Dear Author,\nI regret to inform you that after review we are unable to accept your manuscript for publication. "
     "The reviewers' comments are attached below.", "reject", ""),
    ("Dear Author,\nAfter reading your manuscript, the editor has decided not to send it out for external review, "
     "as it falls outside the scope of the journal.", "desk_reject", ""),
    ("尊敬的作者：\n您的稿件经过同行评审，编辑部决定小修后录用。请在2027年1月10日前提交修改稿。\n编辑部", "minor_revision", "2027-01-10"),
    ("Dear Author,\nThank you for submitting your manuscript. This message confirms that we have received your "
     "files; you can track the progress of your submission in the author centre.", "other", ""),
]
# 规则认不出、需要模型的来信
HARD_LETTER = ("Dear Dr. Example,\nThe reviewers have now commented on your paper. They feel that substantial changes "
               "to the experiments and the presentation are needed. You may submit a revised version that addresses "
               "all of their comments.\nKind regards")


def test_letters_real():
    agree, lines = 0, []
    for text, want, due in LETTERS:
        r = smart.analyze_letter(text)
        m = r["model"] or {}
        lines.append(f"  want={want:16} final={r['decision']:16} model={str(m.get('decision')):16} "
                     f"due={r['deadline'] or '-':10} {m.get('ms')}ms {r['agreement']}")
        assert r["decision"] == want, (text, r)
        if want != "other" and DECISION_STAGE(want) == "revise":
            assert r["deadline"] == due
        agree += m.get("decision") == want
    r = smart.analyze_letter(HARD_LETTER)
    lines.append(f"  hard letter: final={r['decision']} model={(r['model'] or {}).get('decision')} {r['agreement']}")
    print("\n" + "\n".join(lines) + f"\nmodel agreed with the expected decision on {agree}/{len(LETTERS)}")
    assert agree >= len(LETTERS) - 2
    assert r["stage"] == "revise", r


def DECISION_STAGE(k):
    return smart.DECISIONS[k][0]


STATUSES = [("Out for refereeing", "review"), ("Waiting for referee reports", "review"),
            ("Paper in copyediting", "production"), ("Ready for publication", "production"),
            ("Under Evaluation by Editor", "editor")]


def test_status_words_real():
    ok, lines = 0, []
    for raw, want in STATUSES:
        r = smart.suggest_stage(raw)
        lines.append(f"  {raw!r:34} -> {r['stage']} ({r['source']}, {r.get('ms')}ms) want {want}")
        ok += r["stage"] == want
    print("\n" + "\n".join(lines) + f"\nstatus words right: {ok}/{len(STATUSES)}")
    assert ok >= 3


QUESTIONS = [("Which papers have been under review for more than 3 months?", "stuck"),
             ("哪些稿子审了超过三个月？", "stuck"),
             ("How many papers did I submit to each venue?", "count_by"),
             ("Show the decisions I received in the last 30 days", "outcomes_recent"),
             ("我的返修截止日都是什么时候？", "deadlines"),
             ("How long does Pattern Recognition take to make a decision?", "review_time"),
             ("find my paper about graph neural networks", "search")]


def test_questions_real():
    ok, lines = 0, []
    for q, want in QUESTIONS:
        r = smart.run_question(q)
        lines.append(f"  {q[:52]:52} -> {r['intent']:16} via {r['via']} {(r['model'] or {}).get('ms')}ms {r['params']}")
        ok += r["intent"] == want
        assert r["sql"].startswith("SELECT")
    print("\n" + "\n".join(lines) + f"\nintents right: {ok}/{len(QUESTIONS)}")
    assert ok >= len(QUESTIONS) - 2
