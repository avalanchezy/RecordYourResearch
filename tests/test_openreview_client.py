# -*- coding: utf-8 -*-
"""OpenReview 要求人机验证（ChallengeRequired）时：不换接口重试，整轮同步停下。只用合成数据，不联网。"""
import pytest

from app import openreview as o

CHALLENGE = "OpenReview 要求人机验证（ChallengeRequired），请稍后再试或重新绑定账号"


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def challenged(*a, **k):
        seen.append(a[0] if a else "")
        raise o.ORForbidden(CHALLENGE)

    monkeypatch.setattr(o, "get_submission_status", challenged)
    monkeypatch.setattr(o, "match_by_title", challenged)
    return seen


def test_challenge_is_not_retried_on_the_other_api(calls):
    r = o.poll("SynthForum01", "ICLR.cc/2026/Conference", "A Synthetic Title", token="fake-token")
    assert r["ok"] is False and "ChallengeRequired" in r["error"] and len(calls) == 1
    calls.clear()
    r = o.poll("", "ICLR.cc/2026/Conference", "A Synthetic Title", token="fake-token")     # 按标题匹配的路径
    assert r["ok"] is False and "ChallengeRequired" in r["error"] and len(calls) == 1


def test_plain_403_still_tries_the_other_api(monkeypatch):
    apis = []

    def forbidden(forum_id, api, token):
        apis.append(api)
        raise o.ORForbidden("HTTP 403")

    monkeypatch.setattr(o, "get_submission_status", forbidden)
    r = o.poll("SynthForum01", "ICLR.cc/2026/Conference", "A Synthetic Title", token="fake-token")
    assert r["ok"] is False and len(apis) == 2 and len(set(apis)) == 2


def test_sync_round_stops_after_a_challenge(client, fake_openreview, secure_store, calls):
    r = client.post("/api/accounts/openreview", json={"email": "tester@example.invalid", "secret": "pw-456"})
    assert r.status_code == 200, r.text
    for i in (1, 2):
        client.post("/api/submissions", json={
            "title": f"Synthetic Challenge Paper {i}", "authors": ["A. Tester"],
            "venue": {"name": "ICLR", "year": "2026", "type": "conference", "tracker": "openreview"},
            "openreview_forum": f"SynthChal0{i}"}).raise_for_status()
    r = client.post("/api/poll/openreview").json()
    assert r["polled"] == 1 and len(calls) == 1, r
    assert "ChallengeRequired" in r["results"][0]["error"]
    assert client.delete("/api/accounts/openreview").json()["ok"]
