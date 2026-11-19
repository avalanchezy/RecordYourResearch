# -*- coding: utf-8 -*-
"""投稿系统页面粘贴解析：合成语料回归测试。

fixtures/syspaste_corpus.json 是按各系统公开文档合成的整页粘贴文本（ScholarOne、Editorial Manager、Atypon ReX、
Springer Nature / Nature MTS / Research Square、MDPI / T&F / Frontiers、中文期刊系统，共 6 个系统族），
每份样例带期望结果。内容全部虚构。这里：
- 跑全部样例，检查总体与各系统族的命中率；
- 每个系统族挑几份代表样例逐条核对；
- 检查结果与「今天」无关、与换行 / 不换行空格写法无关，以及几种常见的粘贴变化。

解析器会丢掉晚于今天的日期（兜底）；测试里把今天固定在很远的将来，截止日期之类必须靠规则排除。
"""
import json
import os
import time

import pytest

from app import syspaste as sp

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "syspaste_corpus.json")
with open(FIXTURE, encoding="utf-8") as _fh:
    FAMILIES = json.load(_fh)
SAMPLES = {s["name"]: s for f in FAMILIES for s in f["samples"]}
FIELDS = ("manuscript_id", "title", "stage", "status_date", "submitted_date")


@pytest.fixture(autouse=True)
def _far_future_today(monkeypatch):
    monkeypatch.setattr(sp, "_today", lambda: "2099-12-31")


def _find(items, exp):
    """期望的一条 → 解析结果里对应的一条：有编号按编号，没编号按标题。"""
    mid = (exp["manuscript_id"] or "").upper()
    if mid:
        return next((i for i in items if i["manuscript_id"].upper() == mid), None)
    tk = sp.title_key(exp["title"])[:30]
    return next((i for i in items if not i["manuscript_id"] and tk and sp.title_key(i["title"]).startswith(tk)), None)


def _title_ok(got, exp):
    g, e = sp.title_key(got), sp.title_key(exp)
    if not e:
        return not g
    return bool(g) and (g.startswith(e[:30]) or e.startswith(g[:30]))


def _score(samples):
    tot = dict.fromkeys(("ids", "found", "extra", "stage", "title", "dates", "date_n"), 0)
    for s in samples:
        items = sp.parse(s["text"])["items"]
        used = []
        for e in s["expected"]:
            tot["ids"] += 1
            g = _find(items, e)
            if g is None:
                continue
            used.append(id(g))
            tot["found"] += 1
            tot["stage"] += g["stage"] == e["stage"]
            tot["title"] += _title_ok(g["title"], e["title"])
            for k in ("status_date", "submitted_date"):
                if e[k]:
                    tot["date_n"] += 1
                    tot["dates"] += g[k] == e[k]
        tot["extra"] += sum(1 for i in items if id(i) not in used)
    return tot


def test_corpus_aggregate_thresholds():
    t = _score([s for f in FAMILIES for s in f["samples"]])
    n = t["ids"]
    assert n == 179
    # 当前：编号 179/179、多出 0、阶段 179、标题 179、日期 288/288；门槛留一点余量
    assert t["found"] / n >= 0.97, t
    assert t["extra"] <= 3, t
    assert t["stage"] / n >= 0.95, t
    assert t["title"] / n >= 0.95, t
    assert t["dates"] / t["date_n"] >= 0.95, t


@pytest.mark.parametrize("family", [f["family"] for f in FAMILIES], ids=lambda x: x.split()[0])
def test_corpus_each_family(family):
    f = next(f for f in FAMILIES if f["family"] == family)
    t = _score(f["samples"])
    assert t["found"] / t["ids"] >= 0.9, t
    assert t["stage"] / t["ids"] >= 0.9, t
    assert t["extra"] <= 1, t


# 代表样例：每个系统族至少一份，逐条核对编号、标题、阶段和两个日期（以及系统识别）
EXACT = {
    # MDPI / T&F / Frontiers
    "mdpi_susy_coauthored_manuscripts": "mdpi",                 # 曾被误认成 ScholarOne
    "tandf_portal_dashboard": "tandf",                          # 9 位编号、无标题占位、通知栏不算稿件
    "frontiers_my_submissions": "frontiers",                    # 7 位编号、Action required 的 Interactive review = 返修
    # Editorial Manager
    "em_revisions_sent_back_plus_declined_elsevier_ins": "editorial_manager",   # 文件夹决定阶段
    "em_sent_back_plus_incomplete_elsevier_eswa": "editorial_manager",         # 没编号的行、Date Submission Began 不是投稿日期
    "em_decision_folder_plos_pone_mmddyyyy": "editorial_manager",              # mm/dd/yyyy
    "em_history_popup_single_manuscript_elsevier_ins": "editorial_manager",    # 处理记录里最新一封信
    "em_main_menu_newui_springer_apin": "editorial_manager",                   # 只有菜单：没有稿件
    "em_author_details_coauthor_status_negative": "editorial_manager",         # 合作者确认页：没有稿件
    # Springer Nature
    "snapp_tracking_dashboard_five_submissions": "snapp",       # 没编号的卡片列表；Peer review 下的子状态
    "nature_mts_live_manuscripts_folder": "nature_mts",         # 「3rd Nov 26」日期、状态格里的阶段日期
    "researchsquare_public_timeline_bmc_major_revision": "researchsquare",
    # Atypon ReX
    "ieee_author_portal_my_submissions_tab_rows": "rex",        # Returned to Draft / Submission failed 横幅
    "wiley_authors_dashboard_cards": "rex",
    # ScholarOne
    "s1_newui_decisions_queue_ieee_style_with_versions": "scholarone",   # 多个版本只留最新（投稿日期取原稿）；Due on 不是日期
    "s1_newui_revised_draft_queue": "scholarone",               # 没有状态列：队列名即状态
    "s1_newui_submission_confirmation_oa_suffix": "scholarone",
    # 中文期刊
    "etiller_new_author_manuscript_list_jos_style": "cn",       # 状态格里的修改截止日期不算状态日期
    "cnki_legacy_2011_submitted_list_unlabeled_status": "cn",   # 状态列没有表头
    "cnki_review_history_popup": "cn",
    "founder_portal_manuscript_detail_timeline": "cn",
    "cnki_author_desktop_counts_only_negative": "cn",           # 只有分类计数：没有稿件
}
# 原文和期望写法不同、但都对的：横幅「Returned to Draft」压过卡片上的「Draft」
RAW_OVERRIDE = {("ieee_author_portal_my_submissions_tab_rows", "ACCESS-2026-99107"): "Returned to Draft"}


@pytest.mark.parametrize("name", sorted(EXACT))
def test_representative_samples_exact(name):
    s = SAMPLES[name]
    r = sp.parse(s["text"])
    assert r["system"] == EXACT[name]
    assert len(r["items"]) == len(s["expected"]), [i["manuscript_id"] or i["title"] for i in r["items"]]
    for e in s["expected"]:
        g = _find(r["items"], e)
        assert g is not None, e
        assert {k: g[k] for k in FIELDS} == {k: e[k] for k in FIELDS}
        raw = RAW_OVERRIDE.get((name, e["manuscript_id"].upper()), e["raw_status"])
        assert sp.same_status(g["raw_status"], raw), (g["raw_status"], raw)
        assert g["title_truncated"] == e["title"].endswith(("...", "…"))


def test_results_do_not_depend_on_today(monkeypatch):
    def run(today):
        monkeypatch.setattr(sp, "_today", lambda: today)
        return {n: sp.parse(s["text"])["items"] for n, s in SAMPLES.items()}
    # 语料里的日期都不晚于 2026-11-19；截止日期（Due on: 04-Jan-2027 …）不能因为「今天」变了就混进来
    assert run("2026-11-19") == run("2099-12-31")


def test_line_endings_and_nbsp_do_not_matter():
    for s in SAMPLES.values():
        base = sp.parse(s["text"])["items"]
        assert sp.parse(s["text"].replace("\n", "\r\n"))["items"] == base, s["name"]
        assert sp.parse(s["text"].replace(" ", " ", 40))["items"] == base, s["name"]


def test_list_page_followed_by_single_manuscript_page():
    """先粘列表、再接着粘一篇稿件的详情页：两边的稿件都要认出来。"""
    a, b = SAMPLES["sn_profile_your_research_submitted_tab_mixed_systems"], \
        SAMPLES["researchsquare_public_timeline_bmc_major_revision"]
    items = sp.parse(a["text"] + "\n\n" + b["text"])["items"]
    for e in a["expected"] + b["expected"]:
        g = _find(items, e)
        assert g is not None and g["stage"] == e["stage"], e


def test_lost_trailing_tabs_degrade_gracefully():
    """别的浏览器 / 编辑器可能吃掉行尾的 TAB（空的最后一格）：大部分稿件仍要认出来。"""
    t = _score([dict(s, text="\n".join(ln.rstrip("\t") for ln in s["text"].split("\n"))) for s in SAMPLES.values()])
    assert t["found"] / t["ids"] >= 0.95 and t["stage"] / t["ids"] >= 0.93, t


def test_status_vocabulary_spot_checks():
    cases = {
        "Awaiting Admin Processing": "submitted", "Awaiting Reviewer Invitation Responses": "review",
        "Awaiting Editor-in-Chief Check": "editor", "Awaiting Admin Final Processing": "decision",
        "Reviewer report(s) received": "reviews_done", "Peer review: Reviewer report(s) received": "reviews_done",
        "Rights and open access payments": "accepted", "Accepted, Under Review for Production": "accepted",
        "Accepted: Exported": "production", "Draft (Transferred)": "", "Draft": "", "Replaced": "", "Revised version review": "review",
        "Author proofreading – resubmitted": "production", "Transfer Pending": "rejected",
        "在线出版[录用]": "production", "初审[退改]": "submitted", "终审[退稿]": "rejected", "(录用)": "accepted",
        "修改后审": "revise", "已修回": "editor", "初审已审回": "reviews_done",
    }
    # 还没投出去的：阶段留空（显示出来，但不导入）；投过又退回来的仍算已提交
    for raw in ("Incomplete", "Needs Approval", "Needs Approval by Author", "Building PDF", "Submission Incomplete",
                "Submission Created", "Submission in Progress", "Draft Invited"):
        cases[raw] = ""
    cases.update({"Sent Back to Author": "submitted", "Returned to Draft": "submitted"})
    for raw, stage in cases.items():
        assert (sp._status_core(raw, "cell") or ("",))[0] == stage, raw
    # 不是状态：按钮、表头、计数、通知
    for raw in ("view decision letter", "Decline to Revise", "Accepted (1)", "Submitted on 3 May 2026",
                "Revision required on submission 261077352", "Reject All", "Submissions Being Processed"):
        assert sp._status_of(raw) is None, raw


def test_short_titles_with_status_words_stay_titles():
    """标题里带 Accepted / Under Review 之类的词：一行普通文字只有整串是词典里的状态才算状态，
    所以标题不会被当成状态，期刊名也不会挪成标题。"""
    text = ("Your research\nSubmitted\nPublished\n"
            "Accepted Answers in Forums\nApplied Intelligence\nSubmitted on 12 October 2026\nUnder Review\n"
            "Updated 9 November 2026\nGo to submission\n"
            "Under Review Bias\nDiscover Computing\nSubmitted on 2 March 2026\nRejected\nUpdated 19 May 2026\n")
    got = [(i["title"], i["stage"], i["journal"]) for i in sp.parse(text)["items"]]
    assert got == [("Accepted Answers in Forums", "review", "Applied Intelligence"),
                   ("Under Review Bias", "rejected", "Discover Computing")]
    for title in ("Accepted Answers in Forums", "Under Review Bias", "Rejected Samples Matter", "Decision Trees Revisited"):
        assert sp._status_of(title) is None, title
    assert sp._status_of("Status: Under Review Bias") is not None          # 带标签的仍走规则兜底


def test_rejected_title_line_is_left_empty_not_shifted():
    """文章类型下面那一行不像标题（这里是个很短的问句）：标题留空，不把期刊名挪成标题、也不把文章类型当期刊。"""
    text = ("IEEE Author Portal\n\nIEEE Access\nResearch Article\nWhy?\nSubmission Status\nUnder Review\n"
            "Manuscript ID\nAccess-2026-90001\nSubmitted On\n2 October 2026 by Alice Example\n")
    it = sp.parse(text)["items"][0]
    assert (it["manuscript_id"], it["title"], it["journal"], it["stage"]) == ("Access-2026-90001", "", "IEEE Access",
                                                                              "review")


def test_em_draft_folders_force_empty_stage():
    """EM「Incomplete Submissions」里还没投出去的新稿：阶段留空；返修稿还没交（Being Revised）：原稿在返修中。"""
    head = "Action\tManuscript Number\tTitle\tInitial Date Submitted\tStatus Date\tCurrent Status\n"
    draft = ("Incomplete Submissions\nPage 1 of 1 (1 total submissions)\n" + head +
             "Edit Submission\t\tA Made-Up Draft About Toy Gadgets\t\tMar 03, 2026\tIncomplete\n")
    assert all(not i["stage"] for i in sp.parse(draft)["items"])
    text = ("Incomplete Submissions Being Revised\nPage 1 of 1 (1 total submissions)\n" + head +
            "Edit Revision\tABC-D-26-00007R1\tA Made-Up Study of Toy Widgets\tJan 02, 2026\tMar 03, 2026\tRevise\n")
    it = sp.parse(text)["items"][0]
    assert (it["manuscript_id"], it["stage"]) == ("ABC-D-26-00007R1", "revise")


def test_title_key_keeps_every_script():
    keys = [sp.title_key(t) for t in ("Синтетическое исследование", "Συνθετική μελέτη", "합성 연구", "合成研究")]
    assert all(keys) and len(set(keys)) == 4
    assert sp.title_key("ＡＢＣ　１２３…") == sp.title_key("abc 123") == "abc123"
    assert sp.title_key("Ŝtudo: A, B!") == "ŝtudoab"


def test_slash_dates_are_read_once_per_paste():
    page = ("Manuscript Number: JX-2026-0001\nTitle: A Made-Up Study of Toy Widgets in Imaginary Places\n"
            "Status: Under Review\nSubmitted: 03/04/2026\nLast updated: {}\n")
    dates = lambda text: [(i["submitted_date"], i["status_date"]) for i in sp.parse(text)["items"]]
    assert dates(page.format("25/04/2026")) == [("2026-04-03", "2026-04-25")]    # 有首段 > 12 的：整页日/月
    assert dates(page.format("04/25/2026")) == [("2026-03-04", "2026-04-25")]    # 有次段 > 12 的：整页月/日
    assert dates(page.format("05/06/2026")) == [("", "")]                        # 看不出来：不认
    em = ("Editorial Manager\nSubmissions Being Processed\nAction\tManuscript Number\tTitle\tInitial Date Submitted\t"
          "Status Date\tCurrent Status\nAction Links\tABC-D-26-00001\tA Made-Up Study of Toy Widgets\t03/04/2026\t"
          "05/06/2026\tUnder Review\n")
    assert dates(em) == [("2026-03-04", "2026-05-06")]                           # EM 用美式日期


def test_headerless_scholarone_paste():
    """表头没复制上：「最近 5 封邮件」里的标题提到的编号不算稿件，行里最早的日期是投稿日期。"""
    s = SAMPLES["s1_legacy_ui_decisions_queue_tandf_style"]
    text = "\n".join(ln for ln in s["text"].split("\n") if not ln.startswith("Manuscript ID\tManuscript Title"))
    got = {i["manuscript_id"]: i for i in sp.parse(text)["items"]}
    assert set(got) == {e["manuscript_id"] for e in s["expected"]}
    for e in s["expected"]:
        g = got[e["manuscript_id"]]
        assert (g["stage"], g["submitted_date"], g["status_date"]) == (e["stage"], e["submitted_date"], e["status_date"])


def _adversarial(n):
    # 每行带上行号，各不相同：不让缓存把逐行的回溯掩盖掉
    lines = lambda make, width: "\n".join(make() + f" {i}" for i in range(n // width))
    return {
        "long space run": "Manuscript ID:" + " " * n + "x",
        "space run lines": lines(lambda: "Due" + " " * 4000 + "x", 4010),
        "unclosed brackets": "【" * n,
        "unclosed bracket lines": lines(lambda: "【" * 4000 + " x", 4010),
        "commas and spaces": lines(lambda: ("A" + " " * 60 + ",") * 60, 3730),
        "view submission spaces": lines(lambda: "view" + " " * 4000 + "submissionx", 4020),
        "quotes": lines(lambda: "Name (X): \"" + "\"" * 4000 + "x", 4020),
        "many ID lines": "\n".join(f"ABC-D-26-{i:05d}\tA Made-Up Title {i}\tUnder Review" for i in range(n // 40)),
        "many cards": "\n".join(f"Title number {i} about made-up things\nUnder Review\nManuscript ID: X{i:07d}\n"
                                f"Submitted On: {i % 28 + 1} October 2026\n" for i in range(n // 95)),
        "one long line": ("Status: Under Review Manuscript ID ABC-D-26-00001 12 Nov 2026 " * (n // 64)),
        "blank lines": "\n" * n,
    }


@pytest.mark.parametrize("name", sorted(_adversarial(10)))
def test_adversarial_input_parses_fast(name):
    """回溯很重的正则会卡住服务（还占着 GIL）：这些输入每个都要很快解析完（完整 2M 字符的版本见开发脚本）。"""
    text = _adversarial(400_000)[name]
    t0 = time.perf_counter()
    r = sp.parse(text)
    assert time.perf_counter() - t0 < 2.0, name
    assert len(r["items"]) <= 500
