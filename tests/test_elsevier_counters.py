# -*- coding: utf-8 -*-
"""爱思唯尔查稿页审稿人计数：粘连的「旧值→新值」、隐藏人数、单行粘贴等（只用合成文本）。"""
from app.elsevier_counters import parse_reviewer_counters as P

PASTE_WITH_CHANGES = """DemoNet: A Synthetic Paper for Parser Tests
更新时间：2026年11月24日
当前进度：
Under Review
评审完成：
02
接受评审邀请：
23
发出评审邀请*：
8
*此为系统记录的邀请数，仅供参考。稿件在同行评审中所需的完成评审数取决于期刊要求。"""


def page(c, a, i, extra=''):
    return f"更新时间：2026年11月24日\n当前进度：\nUnder Review\n评审完成：\n{c}\n接受评审邀请：\n{a}\n发出评审邀请*：\n{i}\n*此为系统记录的邀请数，仅供参考。{extra}"


def test_glued_old_new_values_are_split():
    r = P(PASTE_WITH_CHANGES)
    assert r['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}
    assert r['reviewers_prev'] == {'completed': 0, 'accepted': 2}
    assert not r['reviewers_ambiguous']
    assert '评审完成 0→2' in r['reviewers_note'] and '接受评审邀请 2→3' in r['reviewers_note']


def test_glued_values_with_history():
    r = P(PASTE_WITH_CHANGES, previous={'completed': 0, 'accepted': 2, 'invited': 5})
    assert r['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}
    assert not r['reviewers_ambiguous']


def test_separated_by_newline_space_arrow():
    for c in ['0\n2', '0 2', '0 → 2', '0->2', '0⟶2']:
        r = P(page(c, '2\n3', '8'))
        assert r['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}, c
        assert r['reviewers_prev'] == {'completed': 0, 'accepted': 2}, c


def test_no_transitions():
    r = P(page('0', '2', '5'))
    assert r['reviewers'] == {'completed': 0, 'accepted': 2, 'invited': 5}
    assert r['reviewers_prev'] == {}
    assert r['reviewers_note'] == ''


def test_hidden_count_and_mask_footnote_not_consumed():
    r = P(page('1', '3', '5+**', '\n**超过5人则不显示具体人数，人数发生变化时会更新“更新时间”。'))
    assert r['reviewers'] == {'completed': 1, 'accepted': 3, 'invited': 5}
    assert r['reviewers_at_least'] == ['invited']


def test_hidden_count_with_transition():
    r = P(page('1', '3', '45+**', '\n**超过5人则不显示具体人数'))
    assert r['reviewers']['invited'] == 5
    assert r['reviewers_prev']['invited'] == 4
    assert 'invited' in r['reviewers_at_least']


def test_genuinely_ambiguous_two_digits_flagged():
    r = P(page('0', '1', '12'))
    assert r['reviewers_ambiguous']
    assert '12' in r['reviewers_note'] or '1→2' in r['reviewers_note']


def test_history_resolves_ambiguity():
    r = P(page('0', '1', '12'), previous={'completed': 0, 'accepted': 1, 'invited': 11})
    assert r['reviewers']['invited'] == 12 and 'invited' not in r['reviewers_prev']
    assert not r['reviewers_ambiguous']
    r = P(page('0', '1', '12'), previous={'completed': 0, 'accepted': 1, 'invited': 1})
    assert r['reviewers']['invited'] == 2 and r['reviewers_prev']['invited'] == 1
    assert not r['reviewers_ambiguous']


def test_multi_digit_transition():
    r = P(page('2', '3', '1011'), previous={'invited': 10})
    assert r['reviewers']['invited'] == 11 and r['reviewers_prev']['invited'] == 10


def test_fullwidth_nbsp_zero_width():
    t = page('０２', '２​３', ' 8')
    r = P(t)
    assert r['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}


def test_same_line_values():
    t = "评审完成：0 → 2\n接受评审邀请：3\n发出评审邀请*：8"
    assert P(t)['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}


def test_english_labels():
    t = "Reviews completed: 2\nReview invitations accepted: 3\nReview invitations sent*: 8"
    assert P(t)['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}


def test_status_label_not_mistaken_for_counter():
    t = "当前进度：\n同行评审完成\n2\n评审完成：\n3\n接受评审邀请：\n3\n发出评审邀请*：\n6"
    assert P(t)['reviewers'] == {'completed': 3, 'accepted': 3, 'invited': 6}


def test_no_counters():
    assert P("当前进度：\nWith Editor\n更新时间：2026年11月24日") == {}


def test_partial():
    r = P("发出评审邀请*：\n4")
    assert r['reviewers'] == {'invited': 4}


def test_order_violation_noted():
    r = P(page('5', '3', '4'))
    assert '完成 ≤ 接受 ≤ 邀请' in r['reviewers_note']


def test_single_line_paste():
    t = "当前进度:Under Review评审完成:02接受评审邀请:23发出评审邀请*:8*此为系统记录的邀请数，仅供参考。"
    r = P(t)
    assert r['reviewers'] == {'completed': 2, 'accepted': 3, 'invited': 8}


def test_value_does_not_run_into_following_date_line():
    t = "发出评审邀请*：\n8\n\n2026-11-05\n提交日期"
    assert P(t)['reviewers'] == {'invited': 8}


def test_required_reviews_completed_status_not_a_label():
    t = "当前进度：\nRequired Reviews Completed\n评审完成：\n2\n接受评审邀请：\n2\n发出评审邀请*：\n4"
    assert P(t)['reviewers'] == {'completed': 2, 'accepted': 2, 'invited': 4}


def test_repasting_same_page_is_stable():
    first = P(PASTE_WITH_CHANGES, previous={'completed': 0, 'accepted': 2, 'invited': 5})
    again = P(PASTE_WITH_CHANGES, previous=first['reviewers'])
    assert again['reviewers'] == first['reviewers']
    t = page('0', '1', '12')
    a = P(t, previous={'completed': 0, 'accepted': 1, 'invited': 1})
    b = P(t, previous=a['reviewers'])
    assert a['reviewers'] == b['reviewers'] == {'completed': 0, 'accepted': 1, 'invited': 2}


def test_dates_after_event_labels_are_not_counts():
    """Research Square 等时间线：「Reviewers accepted」后面是事件日期，不是人数。"""
    for t in ["Reviews received\n04 Nov, 2026\nReviewers accepted\n08 Oct, 2026\nReviewers invited\n29 Sep, 2026",
              "Event\tDate\nReviews received\t11 Nov, 2026\nReviewers accepted\t15 Oct, 2026\nReviewers invited\t02 Oct, 2026",
              "Reviewers accepted 2026年10月15日\nReviewers invited 2026-10-02"]:
        assert P(t) == {}, t
