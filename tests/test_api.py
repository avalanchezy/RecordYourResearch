# -*- coding: utf-8 -*-
"""接口级测试（合成数据）：建档、时间线、回收站、撤销、变化区、账号同步导入、导出、备份、安全头。"""
import csv
import io
import os

from app import db, rankings as rk


def _new(client, title="DemoNet: Synthetic Paper", **venue):
    v = {"name": "Pattern Recognition", "type": "journal", "tracker": "manual"}
    v.update(venue)
    r = client.post("/api/submissions", json={"title": title, "venue": v})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_first_run_builds_public_rankings(client):
    assert rk.available()
    ds = rk.datasets()
    assert ds["ccf"] and ds["warning_lists"] and ds["cas"] and ds["xr"] and not ds["jcr"]
    assert rk.imported() == [] and not rk.outdated()
    r = client.post("/api/rankings/lookup", json={"name": "NeurIPS", "venue_type": "conference"}).json()
    assert r["ccf"]["rank"] == "A" and r["available"] is True
    st = client.get("/api/stages").json()
    assert st["rank_editions"]["cas"] == "2025" and st["rank_editions"]["xr"] == "2026"
    assert st["rank_outdated"] is False and st["rank_datasets"]["cas"]


def test_badges_show_source_and_edition(client):
    sid = _new(client)
    d = client.get(f"/api/submissions/{sid}").json()
    ccf = [b for b in d["badges"] if b["scope"] == "ccf"]
    assert ccf and "CCF 推荐目录" in ccf[0]["title"]
    cas = [b for b in d["badges"] if b["scope"] == "cas"]       # 随项目附带的中科院 2025 / 新锐 2026
    xr = [b for b in d["badges"] if b["scope"] == "xr"]
    assert cas and "中科院分区表升级版 2025（中科院文献情报中心）" in cas[0]["title"]
    assert xr and "新锐期刊分区表 2026（xr-scholar.com）" in xr[0]["title"]


def test_timeline_and_event_undo(client):
    sid = _new(client, "Timeline Paper")
    client.post(f"/api/submissions/{sid}/events", json={"stage": "review", "raw": "Under Review"})
    d = client.get(f"/api/submissions/{sid}").json()
    assert d["current_stage"] == "review"
    eid = max(e["id"] for e in d["events"])
    dele = client.delete(f"/api/submissions/{sid}/events/{eid}").json()
    assert client.get(f"/api/submissions/{sid}").json()["current_stage"] == "submitted"
    assert client.post(f"/api/submissions/{sid}/events/restore", json=dele["event"]).json()["ok"]
    assert client.get(f"/api/submissions/{sid}").json()["current_stage"] == "review"
    assert client.post(f"/api/submissions/{sid}/events/restore", json=dele["event"]).status_code in (409, 410)
    other = _new(client, "Other Paper")
    assert client.post(f"/api/submissions/{other}/events/restore", json={"id": eid}).status_code == 410


def test_future_event_time_rejected(client):
    sid = _new(client, "Future Paper")
    r = client.post(f"/api/submissions/{sid}/events", json={"stage": "review", "observed_at": "2099-01-01 10:00"})
    assert r.status_code == 400


def test_trash_restore_purge(client):
    sid = _new(client, "Trash Paper")
    n0 = client.get("/api/categories/counts").json()
    assert client.delete(f"/api/submissions/{sid}").json()["trashed"]
    n1 = client.get("/api/categories/counts").json()
    assert n1["trash"] == n0["trash"] + 1 and n1["all"] == n0["all"] - 1
    assert client.post(f"/api/submissions/{sid}/events", json={"stage": "review"}).status_code == 409
    assert client.post(f"/api/submissions/{sid}/restore").json()["ok"]
    assert client.delete(f"/api/submissions/{sid}?purge=1").status_code == 400
    client.delete(f"/api/submissions/{sid}")
    assert client.delete(f"/api/submissions/{sid}?purge=1").json()["purged"]
    assert client.get(f"/api/submissions/{sid}").status_code == 404


def test_changes_inbox_counts_only_automatic_changes(client):
    sid = _new(client, "Inbox Paper")
    client.post("/api/changes/seen", json={"until": client.get("/api/changes").json()["now"] or ""})
    client.post(f"/api/submissions/{sid}/events", json={"stage": "review"})        # 手动：不算
    assert client.get("/api/changes").json()["total"] == 0
    with db.get_db() as c:
        db.add_event(c, sid, "reviews_done", "3 reviews released", "openreview", {"sync": True})
    ch = client.get("/api/changes").json()
    assert ch["total"] == 1 and ch["changes"][0]["prev_stage"] == "review" and ch["unread"] >= 1
    client.post("/api/changes/seen", json={"until": ch["now"]})
    assert client.get("/api/changes").json()["total"] == 0


def test_openreview_sync_and_import(client, fake_openreview, secure_store):
    r = client.post("/api/accounts/openreview", json={"email": "tester@example.invalid", "secret": "pw-123"})
    assert r.status_code == 200, r.text
    acc = client.get("/api/accounts").json()[0]
    assert acc["encrypted"] and "secret" not in acc
    with db.get_db() as c:
        stored = c.execute("SELECT secret FROM accounts").fetchone()["secret"]
    assert "pw-123" not in stored
    fake_openreview["subs"] = [{"forum": "FakeForum001", "title": "A Synthetic OpenReview Paper",
                                "venue_id": "ICLR.cc/2025/Conference", "venue_name": "ICLR 2025",
                                "venue_base_name": "ICLR", "year": "2025", "stage": "review",
                                "raw": "Under review"}]
    s = client.post("/api/sync/openreview").json()
    assert s["ok"] and fake_openreview["last_password"] == "pw-123"
    assert [d["forum"] for d in s["discovered"]] == ["FakeForum001"]
    pend = client.get("/api/changes").json()["pending"]
    assert [p["forum"] for p in pend] == ["FakeForum001"]
    imp = client.post("/api/sync/openreview/import", json=pend[0]["item"]).json()
    assert imp["id"] and not imp.get("existing")
    assert client.get("/api/changes").json()["pending"] == []
    d = client.get(f"/api/submissions/{imp['id']}").json()
    assert d["venue_openreview_id"] == "ICLR.cc/2025/Conference" and d["openreview_url"].endswith("FakeForum001")
    assert client.delete("/api/accounts/openreview").json()["ok"]
    assert client.get("/api/accounts").json() == []


def test_resubmit_links_original(client):
    sid = _new(client, "Rejected Paper")
    client.post(f"/api/submissions/{sid}/events", json={"stage": "rejected", "raw": "Reject"})
    r = client.post(f"/api/submissions/{sid}/resubmit",
                    json={"venue": {"name": "Neurocomputing", "type": "journal"}, "relation": "转投"}).json()
    nd = client.get(f"/api/submissions/{r['id']}").json()
    assert nd["title"] == "Rejected Paper"
    assert any(x["id"] == sid and x["direction"] == "in" for x in nd["related"])


def test_export_csv_is_formula_safe_and_filtered(client):
    sid = _new(client, "=HYPERLINK(1)")
    client.patch(f"/api/submissions/{sid}", json={"notes": "＝SUM(1,1)"})
    txt = client.get("/api/export.csv").content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(txt)))
    hdr = rows[0]
    row = [x for x in rows[1:] if x[0] == str(sid)][0]
    assert row[hdr.index("标题")] == "'=HYPERLINK(1)" and row[hdr.index("备注")] == "'＝SUM(1,1)"
    assert "CCF" in hdr and "结果日期" in hdr
    acc = list(csv.reader(io.StringIO(client.get("/api/export.csv?category=accepted").content.decode("utf-8-sig"))))
    assert all(x[hdr.index("当前状态")] in ("已录用", "制作中") for x in acc[1:])


def test_backup(client):
    b = client.post("/api/backup").json()
    assert b["ok"] and os.path.exists(os.path.join(db.BACKUP_DIR, b["file"]))
    lst = client.get("/api/backups").json()
    assert lst["items"][0]["file"] == b["file"] and lst["dir_display"].startswith("<数据目录>")


def test_security_headers_and_cross_site_rejected(client):
    r = client.get("/api/stages")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert client.post("/api/backup", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/stages", headers={"Host": "evil.example"}).status_code == 403


def test_elsevier_query_is_opt_in_and_rate_limited(client):
    sid = _new(client, "Elsevier Paper", tracker="elsevier", journal_acronym="ESWA")
    client.patch(f"/api/submissions/{sid}", json={"manuscript_number": "ESWA-D-25-00000",
                                                   "corresponding_pinyin": "XIAOMING WANG"})
    assert client.post("/api/query/elsevier", json={"submission_id": sid}).status_code == 403
    assert client.post("/api/settings", json={"elsevier_query": True}).json()["elsevier_query"]
    r = client.post("/api/query/elsevier", json={"submission_id": sid}).json()
    assert r["ok"] is False              # 测试里外网被拦截
    with db.get_db() as c:              # 模拟一次成功的查询之后：24 小时内不再请求
        c.execute("INSERT INTO poll_logs (channel, target, ok, latency_ms, error) VALUES ('elsevier', ?, 1, 1, '')",
                  (str(sid),))
    assert client.post("/api/query/elsevier", json={"submission_id": sid}).status_code == 429
    client.post("/api/settings", json={"elsevier_query": False})


def test_official_paste_parser_records_status(client):
    sid = _new(client, "Paste Paper", tracker="elsevier", journal_acronym="ESWA")
    client.patch(f"/api/submissions/{sid}", json={"manuscript_number": "ESWA-D-25-00000"})
    text = ("Review Status\n同行评审进行中\n更新时间：2025年1月20日\n当前进度：\nUnder Review\n评审完成：\n1\n"
            "接受评审邀请：\n2\n发出评审邀请*：\n3\nManuscript Number\nESWA-D-25-00000\n期刊\n"
            "Expert Systems with Applications\n提交日期\n2025年1月6日")
    p = client.post(f"/api/submissions/{sid}/elsevier-paste", json={"text": text}).json()
    assert p["recognized"] and p["stage"] == "review" and not p["warning"]
    assert p["reviewers"] == {"invited": 3, "accepted": 2, "completed": 1}
    w = client.post(f"/api/submissions/{sid}/elsevier-paste/confirm", json={"text": text}).json()
    assert w["written"] and w["current_stage"] == "review"
    assert client.get("/api/changes").json()["total"] == 0 or all(
        c["submission_id"] != sid for c in client.get("/api/changes").json()["changes"])
    bad = client.post(f"/api/submissions/{sid}/elsevier-paste", json={"text": text.replace("ESWA-D-25-00000", "ABCD-D-25-11111")}).json()
    assert bad["warning"]


def test_failed_bind_stores_nothing(client, fake_openreview, secure_store, monkeypatch, server_mod):
    def bad_login(email, pw):
        raise RuntimeError("OpenReview 登录失败：用户名或密码错误")
    monkeypatch.setattr(server_mod.openreview, "get_token", bad_login)
    r = client.post("/api/accounts/openreview", json={"email": "tester@example.invalid", "secret": "wrong-pw"})
    assert r.status_code == 400
    assert client.get("/api/accounts").json() == []
    if secure_store is not None:          # macOS / Linux：钥匙串里也不能留下错误的密码
        assert secure_store.store == {}


def test_elsevier_rate_limit_counts_attempts_that_reached_the_service(client):
    sid = _new(client, "Rate Paper", tracker="elsevier", journal_acronym="ESWA")
    client.patch(f"/api/submissions/{sid}", json={"manuscript_number": "ESWA-D-25-00001",
                                                   "corresponding_pinyin": "XIAOMING WANG"})
    client.post("/api/settings", json={"elsevier_query": True})
    with db.get_db() as c:      # 本机断网（请求没发出去）不算一次
        c.execute("INSERT INTO poll_logs (channel, target, ok, latency_ms, error) VALUES ('elsevier', ?, 0, 1, ?)",
                  (str(sid), "网络错误：ConnectError"))
    assert client.post("/api/query/elsevier", json={"submission_id": sid}).status_code == 200
    with db.get_db() as c:      # 碰到验证码 / 风控：算一次，24 小时内不再请求
        c.execute("INSERT INTO poll_logs (channel, target, ok, latency_ms, error) VALUES ('elsevier', ?, 0, 1, ?)",
                  (str(sid), "官方风控拦截（HTTP 403）"))
    assert client.post("/api/query/elsevier", json={"submission_id": sid}).status_code == 429
    client.post("/api/settings", json={"elsevier_query": False})
