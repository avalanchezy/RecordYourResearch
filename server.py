# -*- coding: utf-8 -*-
"""RecordYourResearch（论文投稿追踪）· 本地 Web 服务。

启动：python run.py  →  http://127.0.0.1:8765
数据：全部存 ./data/（SQLite + PDF 原件），无任何云端上传。
"""
import csv
import io
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import uuid
from datetime import datetime, date, timedelta
from urllib.parse import urlparse

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config
from app import db
from app import elsevier
from app import ocr
from app import ai, smart
from app.elsevier_counters import parse_reviewer_counters
from app import openreview
from app import rankings as rk
from app import syspaste
from app import vault
from app import venueutil as vu
from app.parser import parse_pdf, strip_invisible
from app.stages import (STAGES, ACTIVE_STAGES, TERMINAL_STAGES, CATEGORY_STAGES,
                        RAW_STATUS_MAP, normalize)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(db.DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 不提供 FastAPI 自带的 /docs、/redoc 调试页（它们会从 CDN 加载资源，违背「不连任何外部资源」的原则）
app = FastAPI(title="RecordYourResearch", version=config.VERSION,
              docs_url=None, redoc_url=None, openapi_url=None)
db.init_db()
smart.load_status_rules()        # 用户确认过的状态词规则 → stages.normalize
rk.ensure_db()          # 首次启动构建分级库（CCF + 预警名单 + 中科院 2025 + 新锐 2026）；随附数据更新后自动重建

VENUE_TYPES = ("journal", "conference")
TRACKERS = ("manual", "openreview", "elsevier")
_UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@app.exception_handler(OverflowError)
async def _overflow(request: Request, exc: OverflowError):
    return JSONResponse({"detail": "数值超出范围"}, status_code=400)


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
if os.environ.get("PAPER_TRACKER_TESTING") == "1":     # 仅自动化测试（TestClient 默认 Host）
    _LOCAL_HOSTS.add("testserver")


@app.middleware("http")
async def _local_only(request: Request, call_next):
    """只服务本机：拒绝非本机 Host（防 DNS rebinding），拒绝跨站发来的写请求（防 CSRF）。"""
    host_hdr = (request.headers.get("host") or "").strip().lower()
    host = host_hdr.rsplit(":", 1)[0] if not host_hdr.startswith("[") else host_hdr.split("]")[0] + "]"
    if host and host not in _LOCAL_HOSTS:
        return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            o = urlparse(origin)
            if origin == "null" or (o.netloc or "").lower() != host_hdr:
                return JSONResponse({"detail": "已拒绝跨站请求"}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"                       # 不允许被别的页面嵌入（防点击劫持）
    resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"   # 页面与脚本每次向本机校验：升级后不会拿旧的 app.js 配新页面
    return resp


def _cleanup_uploads(max_age_hours: int = 24):
    """清理解析后未建档的暂存 PDF（超过 24 小时）。"""
    now = time.time()
    try:
        for fn in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, fn)
            if os.path.isfile(p) and now - os.path.getmtime(p) > max_age_hours * 3600:
                os.remove(p)
    except OSError:
        pass


_cleanup_uploads()


# ---------- 模型 ----------
class VenueIn(BaseModel):
    name: str
    type: str = "journal"          # conference / journal
    acronym: str = ""
    publisher: str = ""
    tracker: str = "manual"        # openreview / elsevier / manual
    venue_id: str = ""              # OpenReview venue id（会议+年份组装）
    journal_acronym: str = ""       # 爱思唯尔期刊缩写
    year: str | int = ""             # 会议/年份解耦：NeurIPS + 2026（数字或字符串均可）
    ddl: str = ""                    # 会议/期刊相关截止日 YYYY-MM-DD
    submit_url: str = ""             # 投稿系统链接（EM / ScholarOne / CMT …）


class SubmissionIn(BaseModel):
    title: str
    authors: list[str] = []
    corresponding_author: str = ""
    corresponding_pinyin: str = ""   # 格式 FIRST LAST（爱思唯尔查询凭据）
    abstract: str = ""
    keywords: list[str] = []
    manuscript_number: str = ""
    submitted_at: str = ""           # YYYY-MM-DD
    revise_due: str = ""
    notes: str = ""
    initial_stage: str = "submitted"
    initial_raw: str = ""
    upload_id: str = ""              # 解析时暂存的 PDF
    file_name: str = ""              # 原始文件名
    venue: VenueIn | None = None
    venue_id: int | None = None
    openreview_forum: str = ""


class EventIn(BaseModel):
    stage: str
    raw: str = ""
    observed_at: str = ""            # YYYY-MM-DD HH:MM[:SS] / YYYY-MM-DDTHH:MM，空 = 现在
    payload: dict = {}
    revise_due: str | None = None    # stage=revise 时一并写入返修截止日


class QueryIn(BaseModel):
    submission_id: int


class RankLookupIn(BaseModel):
    name: str
    venue_type: str = ""        # journal / conference / 空=不限


class PatchIn(BaseModel):
    title: str | None = None
    authors: list[str] | None = None
    corresponding_author: str | None = None
    corresponding_pinyin: str | None = None
    abstract: str | None = None
    keywords: list[str] | None = None
    manuscript_number: str | None = None
    submitted_at: str | None = None
    revise_due: str | None = None
    notes: str | None = None
    openreview_forum: str | None = None
    venue_id: int | None = None


class VenuePatchIn(BaseModel):
    """修改会议/期刊信息（改名后徽章重新匹配）。"""
    name: str | None = None
    type: str | None = None
    year: str | int | None = None
    journal_acronym: str | None = None
    venue_id: str | None = None      # OpenReview venue id
    tracker: str | None = None
    ddl: str | None = None
    publisher: str | None = None
    acronym: str | None = None
    submit_url: str | None = None


class ResubmitIn(BaseModel):
    """被拒/撤稿后改投或扩写：复制本稿的元数据（可选连同 PDF）到新投稿，并自动建立关联。"""
    venue: VenueIn
    relation: str = "转投"           # 转投 / 扩写
    title: str = ""                  # 留空 = 沿用原标题（扩写常会改题）
    submitted_at: str = ""
    initial_stage: str = "submitted"
    manuscript_number: str = ""
    openreview_forum: str = ""
    copy_pdf: bool = True


# ---------- 校验 / 工具 ----------
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$", re.I)


def _check_url(v, label: str = "链接") -> str:
    v = str(v or "").strip()
    if v and not _URL_RE.match(v):
        raise HTTPException(400, f"{label}应以 http:// 或 https:// 开头")
    return v


_EM_NO_RE = re.compile(r"^([A-Z][A-Z0-9]{1,11})-D-\d{2}-\d{3,6}", re.I)
_MDPI_NO_RE = re.compile(r"^[a-z][a-z0-9]{1,24}-\d{6,7}$")


def _derive_submit_url(v: dict | None, manuscript_number: str = "") -> str:
    """没填投稿系统链接时按渠道/编号推断：OpenReview 会议页、Editorial Manager 期刊站点、MDPI SuSy。"""
    if not v:
        return ""
    if v.get("tracker") == "openreview" and v.get("venue_id"):
        return "https://openreview.net/group?id=" + v["venue_id"]
    m = _EM_NO_RE.match((manuscript_number or "").strip())
    if m:
        return f"https://www.editorialmanager.com/{m.group(1).lower()}/"
    if _MDPI_NO_RE.match((manuscript_number or "").strip()):
        return "https://susy.mdpi.com/"
    return ""
def _check_stage(stage: str):
    if stage not in STAGES:
        raise HTTPException(400, f"未知阶段：{stage}")


def _check_date(v, label: str) -> str:
    v = str(v or "").strip()
    if v and not vu.valid_date(v):
        raise HTTPException(400, f"{label}格式应为 YYYY-MM-DD")
    return v


def _clean_venue(name, year, vtype, tracker, venue_id, acronym="") -> dict:
    name, year = vu.split_year(strip_invisible(name or ""), year or "")
    if year and not re.fullmatch(r"(19|20)\d{2}", year):
        raise HTTPException(400, "年份应为四位数字，如 2027")
    tracker = tracker if tracker in TRACKERS else "manual"
    vtype = vtype if vtype in VENUE_TYPES else ("conference" if tracker == "openreview" else "journal")
    venue_id = openreview.base_venue_id(venue_id or "")
    if tracker == "openreview" and not venue_id:
        venue_id = openreview.build_venue_id(acronym or name, year)
    return {"name": name, "year": year, "type": vtype, "tracker": tracker, "venue_id": venue_id}


def _insert_venue(c, v: VenueIn) -> int:
    cv = _clean_venue(v.name, v.year, v.type, v.tracker, v.venue_id, v.acronym)
    if not cv["name"]:
        raise HTTPException(400, "会议/期刊名称不能为空")
    ddl = _check_date(v.ddl, "DDL")
    cur = c.execute(
        "INSERT INTO venues (name,type,acronym,publisher,tracker,venue_id,journal_acronym,year,ddl,"
        "submit_url) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cv["name"], cv["type"], v.acronym.strip(), v.publisher.strip(), cv["tracker"],
         cv["venue_id"], v.journal_acronym.strip().upper(), cv["year"], ddl,
         _check_url(v.submit_url, "投稿系统链接")))
    return cur.lastrowid


def _venue_of(conn, sid) -> dict | None:
    r = conn.execute(
        "SELECT v.* FROM submissions s JOIN venues v ON v.id=s.venue_id WHERE s.id=?", (sid,)
    ).fetchone()
    return dict(r) if r else None


def _require_sub(conn, sid):
    r = conn.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "投稿不存在")
    return r


def _require_live_sub(conn, sid):
    """写操作用：回收站里的稿件只读（先恢复再改）。"""
    r = _require_sub(conn, sid)
    if r["deleted_at"]:
        raise HTTPException(409, "这篇在回收站里，请先恢复再操作")
    return r


def _drop_orphan_venue(conn, venue_row_id):
    if venue_row_id and not conn.execute(
            "SELECT 1 FROM submissions WHERE venue_id=? LIMIT 1", (venue_row_id,)).fetchone():
        conn.execute("DELETE FROM venues WHERE id=?", (venue_row_id,))


def _stage_runs(events: list, current: str) -> tuple[str | None, str | None]:
    """events 按时间升序。返回 (进入当前阶段的时间, 进入终态的时间)。"""
    since = None
    for e in reversed(events):
        if e["stage"] == current:
            since = e["observed_at"]
        else:
            break
    end = None
    if current in TERMINAL_STAGES:
        for e in reversed(events):
            if e["stage"] in TERMINAL_STAGES:
                end = e["observed_at"]
            else:
                break
    return since, end


def _sub_row(conn, sid) -> dict:
    r = _require_sub(conn, sid)
    d = db.row_to_submission(r)
    v = _venue_of(conn, sid)
    d["venue_name"] = vu.display(v["name"], v.get("year")) if v else ""
    d["venue_base_name"] = v["name"] if v else ""
    d["venue_year"] = (v.get("year") or "") if v else ""
    d["venue_tracker"] = v["tracker"] if v else "manual"
    d["venue_type"] = v["type"] if v else ""
    d["venue_journal_acronym"] = (v.get("journal_acronym") or "") if v else ""
    d["venue_openreview_id"] = (v.get("venue_id") or "") if v else ""
    d["venue_ddl"] = (v.get("ddl") or "") if v else ""
    d["venue_publisher"] = (v.get("publisher") or "") if v else ""
    # 官方系统链接：已填的投稿系统地址优先，否则按渠道/编号推断；OpenReview 稿件页
    stored_url = (v.get("submit_url") or "") if v else ""
    d["venue_submit_url_stored"] = stored_url
    d["venue_submit_url"] = stored_url or _derive_submit_url(v, d.get("manuscript_number") or "")
    d["openreview_url"] = ("https://openreview.net/forum?id=" + d["openreview_forum"]) \
        if d.get("openreview_forum") else ""
    d["deleted"] = bool(d.get("deleted_at"))
    # 学术分级徽章（CCF / 中科院 / 新锐 / JCR / 预警）——懒加载并缓存到 venues
    badges = []
    if v:
        data = _venue_rank_data(conn, v["id"])
        if data:
            try:
                badges = rk.badge_summary(json.loads(data))
            except Exception:
                badges = []
    d["badges"] = badges
    # 在审天数（终态后停止计时）/ 当前阶段停留天数（从进入该阶段算起）
    events = conn.execute(
        "SELECT stage, observed_at FROM status_events WHERE submission_id=?"
        " ORDER BY observed_at, id", (sid,)).fetchall()
    since, end = _stage_runs(events, d["current_stage"])
    now = datetime.now()
    start = db.parse_ts(d.get("submitted_at")) or \
        (db.parse_ts(events[0]["observed_at"]) if events else None) or db.parse_ts(d["created_at"])
    end_dt = db.parse_ts(end) or now
    d["days_total"] = max(0, (end_dt.date() - start.date()).days) if start else None
    since_dt = db.parse_ts(since)
    d["stage_since"] = since or ""
    d["result_at"] = end or ""          # 出结果（进入录用/拒稿/撤稿）的时间
    d["days_in_stage"] = max(0, (now - since_dt).days) if since_dt else None
    d["archived"] = bool(d.get("archived"))
    d["last_synced_at"] = d.get("last_synced_at") or ""
    # 相关投稿（双向；direction=out 表示本稿 → 对方，如「转投为」）
    d["related"] = []
    rel_rows = conn.execute(
        "SELECT r.id AS link_id, r.submission_id AS a, r.related_id AS b, r.relation, r.note,"
        " s.title, s.current_stage, s.archived, v.name AS vname, v.year AS vyear"
        " FROM related_subs r"
        " JOIN submissions s ON s.id = CASE WHEN r.submission_id=? THEN r.related_id"
        "                                   ELSE r.submission_id END"
        " LEFT JOIN venues v ON v.id = s.venue_id"
        " WHERE (r.submission_id=? OR r.related_id=?) AND COALESCE(s.deleted_at,'')=''"
        " ORDER BY r.id", (sid, sid, sid)).fetchall()
    for rr in rel_rows:
        out = rr["a"] == sid
        d["related"].append({
            "id": rr["b"] if out else rr["a"], "link_id": rr["link_id"],
            "title": rr["title"], "stage": rr["current_stage"],
            "venue": vu.display(rr["vname"] or "", rr["vyear"] or ""),
            "relation": rr["relation"], "note": rr["note"],
            "direction": "out" if out else "in", "archived": bool(rr["archived"])})
    return d


def _log_poll(conn, channel, target, ok, latency_ms, error=""):
    conn.execute("INSERT INTO poll_logs (channel,target,ok,latency_ms,error) VALUES (?,?,?,?,?)",
                 (channel, target, 1 if ok else 0, latency_ms, error))


# ---------- 词典 / 渠道元信息 ----------
@app.get("/api/stages")
def stages():
    return {"stages": STAGES, "common_venues": openreview.COMMON_VENUES,
            "categories": CATEGORY_STAGES, "features": _features(),
            "rank_datasets": rk.datasets(), "rank_editions": rk.editions(), "rank_outdated": rk.outdated(),
            "version": config.VERSION, "demo": config.DEMO,
            "repo": config.REPO_URL, "secret_storage": vault.backend(), "ocr": ocr.status(),
            "ai": {"enabled": ai.enabled(), "supported": bool(ai.platform_key())}}


# ---------- 设置：实验功能开关（存在本机数据库 meta 表） ----------
def _features() -> dict:
    with db.get_db() as c:
        return {"elsevier_query": db.meta_get(c, "feature_elsevier_query", "") == "1"}


class SettingsIn(BaseModel):
    elsevier_query: bool | None = None


@app.get("/api/settings")
def get_settings():
    return _features()


@app.post("/api/settings")
def set_settings(s: SettingsIn):
    with db.get_db() as c:
        if s.elsevier_query is not None:
            db.meta_set(c, "feature_elsevier_query", "1" if s.elsevier_query else "")
    return _features()


@app.get("/api/recent-events")
def recent_events():
    with db.get_db() as c:
        rows = c.execute(
            "SELECT e.*, s.title FROM status_events e JOIN submissions s"
            " ON s.id=e.submission_id WHERE COALESCE(s.archived,0)=0 AND COALESCE(s.deleted_at,'')=''"
            " ORDER BY e.observed_at DESC, e.id DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/api/poll-logs")
def poll_logs():
    with db.get_db() as c:
        rows = c.execute("SELECT * FROM poll_logs ORDER BY id DESC LIMIT 50").fetchall()
        return [dict(r) for r in rows]


# ---------- 学术分级（CCF2026 / 中科院2025 / 新锐2026 / JCR2025 / 预警） ----------
@app.post("/api/rankings/lookup")
def rankings_lookup(b: RankLookupIn):
    """建档向导：名称 → 全口径分级卡片（本地 rankings.db，离线可用）。"""
    r = rk.venue_rankings(b.name, b.venue_type)
    r["available"] = rk.available()
    r["datasets"] = rk.datasets()
    return r


@app.get("/api/rankings")
def rankings_search(q: str = "", kind: str = ""):
    """分级速查页：期刊全口径 + CCF 目录。"""
    if not q.strip():
        return {"ok": True, "journals": [], "ccf": []}
    jr = rk.lookup_journal(q, limit=10)
    ccf = rk.lookup_ccf(q, limit=10)
    return {"ok": True,
            "journals": jr.get("matches", []),
            "ccf": ccf.get("matches", []),
            "hint": jr.get("hint", "") or jr.get("error", "")}


@app.get("/api/rankings/suggest")
def rankings_suggest(q: str = ""):
    """输入联想：简称（ESWA/NeurIPS）→ 全称候选（含徽章摘要）。"""
    return rk.suggest(q)


def _venue_rank_data(conn, venue_id: int) -> str:
    """venues.rank_data：空则按刊名现查一次并缓存。"""
    if not venue_id:
        return ""
    r = conn.execute("SELECT name, type, rank_data FROM venues WHERE id=?",
                     (venue_id,)).fetchone()
    if not r:
        return ""
    if not rk.available():
        return ""            # 分级库还没建好：不缓存空卡片（否则建好后旧稿件也一直没有徽章）
    dbv = rk.db_version()
    if r["rank_data"]:
        # 匹配规则升级（RANK_VERSION 变化）或分级库重建 / 导入新数据后自动重算旧缓存
        try:
            cached = json.loads(r["rank_data"])
            if cached.get("v") == getattr(rk, "RANK_VERSION", None) and cached.get("dbv") == dbv:
                return r["rank_data"]
        except Exception:
            pass
    try:
        card = rk.card_for_venue(r["name"], r["type"] or "")
        card["dbv"] = dbv
        data = json.dumps(card, ensure_ascii=False)
        conn.execute("UPDATE venues SET rank_data=? WHERE id=?", (data, venue_id))
        return data
    except Exception:
        return ""


@app.get("/api/overview")
def overview():
    """总览（归档稿件不计入状态统计与提醒）。"""
    with db.get_db() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM submissions WHERE COALESCE(deleted_at,'')=''").fetchall()]
        subs = [_sub_row(c, i) for i in ids]
    live = [s for s in subs if not s["archived"]]
    counts = {}
    for s in live:
        counts[s["current_stage"]] = counts.get(s["current_stage"], 0) + 1
    alerts = []
    for s in live:
        sid, title = s["id"], s["title"]
        # 催稿提示：编辑处理超 4 周（从进入该阶段算）
        if s["current_stage"] == "editor" and (s.get("days_in_stage") or 0) > 28:
            alerts.append({"type": "urge_editor", "severity": "warn",
                           "submission_id": sid, "title": title,
                           "message": f"「编辑处理」已停留 {s['days_in_stage']} 天，可礼貌催稿"})
        # 返修截止日倒计时（仅返修阶段）
        if s["current_stage"] == "revise" and s["revise_due"]:
            try:
                due = datetime.strptime(s["revise_due"], "%Y-%m-%d").date()
            except ValueError:
                continue
            left = (due - date.today()).days
            if left < 0:
                alerts.append({"type": "revise_due", "severity": "danger",
                               "submission_id": sid, "title": title,
                               "message": f"返修截止日已过 {-left} 天！"})
            elif left <= 3:
                alerts.append({"type": "revise_due", "severity": "warn",
                               "submission_id": sid, "title": title,
                               "message": "今天就是返修截止日！" if left == 0 else
                               f"距返修截止日仅 {left} 天"})
    return {
        "counts": counts,
        "total": len(live),
        "archived": len(subs) - len(live),
        "all": len(subs),
        "active": sum(1 for s in live if s["current_stage"] in ACTIVE_STAGES),
        "accepted": sum(1 for s in live if s["current_stage"] in CATEGORY_STAGES["accepted"]),
        "rejected": sum(1 for s in live if s["current_stage"] == "rejected"),
        "alerts": sorted(alerts, key=lambda x: 0 if x["severity"] == "danger" else 1),
    }


# ---------- venues ----------
@app.get("/api/venues")
def list_venues():
    with db.get_db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM venues ORDER BY name").fetchall()]


@app.post("/api/venues")
def create_venue(v: VenueIn):
    with db.get_db() as c:
        return {"id": _insert_venue(c, v)}


@app.patch("/api/submissions/{sid}/venue")
def patch_venue(sid: int, p: VenuePatchIn):
    """修改投稿的会议/期刊信息；改名/改类型后清空分级缓存重新匹配。"""
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "没有可更新字段")
    with db.get_db() as c:
        sub = _require_live_sub(c, sid)
        v = _venue_of(c, sid)
        if not v:   # 尚未绑定会议/期刊：先建一条
            cur = c.execute("INSERT INTO venues (name, type, tracker) VALUES (?, 'journal', 'manual')",
                            ((fields.get("name") or "未命名").strip(),))
            c.execute("UPDATE submissions SET venue_id=? WHERE id=?", (cur.lastrowid, sub["id"]))
            v = _venue_of(c, sid)
        elif c.execute("SELECT COUNT(*) FROM submissions WHERE venue_id=?",
                       (v["id"],)).fetchone()[0] > 1:
            # 与其他投稿共用同一 venue 行：写时复制，只改本稿，不连带改别的稿件
            cols = [k for k in v.keys() if k not in ("id", "created_at")]
            cur = c.execute(f"INSERT INTO venues ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            [v[k] for k in cols])
            c.execute("UPDATE submissions SET venue_id=? WHERE id=?", (cur.lastrowid, sid))
            v = _venue_of(c, sid)
        vid_in = fields.get("venue_id")
        if vid_in is None and any(k in fields for k in ("name", "year", "acronym")):
            # 旧 id 是按「会议模板 + 年份」组装的 → 改了名称/年份就跟着重新组装（ICCV 2025 → ECCV 2026）
            old_built = openreview.build_venue_id(v.get("acronym") or v["name"], v.get("year") or "")
            if v.get("venue_id") and v.get("venue_id") == old_built:
                vid_in = ""
        cv = _clean_venue(fields.get("name", v["name"]), fields.get("year", v.get("year") or ""),
                          fields.get("type", v["type"]), fields.get("tracker", v["tracker"]),
                          vid_in if vid_in is not None else (v.get("venue_id") or ""),
                          fields.get("acronym", v.get("acronym") or ""))
        if not cv["name"]:
            raise HTTPException(400, "会议/期刊名称不能为空")
        upd = {"name": cv["name"], "year": cv["year"], "type": cv["type"],
               "tracker": cv["tracker"], "venue_id": cv["venue_id"]}
        if "ddl" in fields:
            upd["ddl"] = _check_date(fields["ddl"], "DDL")
        if "submit_url" in fields:
            upd["submit_url"] = _check_url(fields["submit_url"], "投稿系统链接")
        for k in ("journal_acronym", "publisher", "acronym"):
            if k in fields:
                upd[k] = fields[k].strip().upper() if k == "journal_acronym" else fields[k].strip()
        if upd["name"] != v["name"] or upd["type"] != v["type"]:
            upd["rank_data"] = ""      # 改名 → 徽章重查
        sets = ", ".join(f"{k}=?" for k in upd)
        c.execute(f"UPDATE venues SET {sets} WHERE id=?", (*upd.values(), v["id"]))
        c.execute("UPDATE submissions SET updated_at=datetime('now','localtime') WHERE id=?", (sid,))
    return {"ok": True}


# ---------- PDF 解析（暂存草稿，不建档） ----------
@app.post("/api/parse-pdf")
async def parse_pdf_api(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "请上传 PDF 文件")
    upload_id = uuid.uuid4().hex
    path = os.path.join(UPLOAD_DIR, upload_id + ".pdf")
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        draft = parse_pdf(path)
    except Exception as e:
        try:
            os.remove(path)
        except OSError:
            pass
        raise HTTPException(422, f"PDF 解析失败：{type(e).__name__}: {e}")
    draft["upload_id"] = upload_id
    draft["file_name"] = os.path.basename(file.filename or "")
    return draft


# ---------- 智能助手（可选的本地小模型，默认关闭；在「渠道查询」页下载部署） ----------
class AiInstallIn(BaseModel):
    source: str = "huggingface"


class AiEnableIn(BaseModel):
    enabled: bool


def _ai_call(fn, *a):
    try:
        return fn(*a)
    except ai.AiError as e:
        raise HTTPException(503 if isinstance(e, ai.AiUnavailable) else 502, str(e))


@app.get("/api/ai/status")
def ai_status():
    return ai.status()


@app.post("/api/ai/install")
def ai_install(p: AiInstallIn):
    if p.source not in ai.SOURCES:
        raise HTTPException(400, "未知的下载源")
    if not ai.platform_key():
        raise HTTPException(400, "当前系统没有现成的推理引擎包（支持 Windows x64、Linux x64、macOS）")
    return _ai_call(ai.install, p.source)


@app.post("/api/ai/cancel")
def ai_cancel():
    return ai.cancel()


@app.post("/api/ai/enable")
def ai_enable(p: AiEnableIn):
    if p.enabled and not ai.installed()["both"]:
        raise HTTPException(400, "还没下载完模型，先点「下载并部署」")
    return ai.set_enabled(p.enabled)


@app.post("/api/ai/start")
def ai_start():
    _ai_call(ai.ensure_running)
    return ai.status()


@app.post("/api/ai/stop")
def ai_stop():
    ai.stop()
    return ai.status()


@app.post("/api/ai/remove")
def ai_remove():
    return _ai_call(ai.remove)


# 读编辑来信：规则 + 本地模型（若启用）给出决定类型、返修截止日，校验后由用户确认写入
class LetterIn(BaseModel):
    text: str


class LetterConfirmIn(BaseModel):
    text: str
    decision: str
    deadline: str = ""
    observed_at: str = ""


@app.post("/api/submissions/{sid}/letter")
def letter_preview(sid: int, p: LetterIn):
    if len(p.text or "") > 100_000:
        raise HTTPException(413, "来信太长了（10 万字以内）")
    with db.get_db() as c:
        sub = dict(_require_live_sub(c, sid))
    try:
        return smart.analyze_letter(p.text, sub)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/submissions/{sid}/letter/confirm")
def letter_confirm(sid: int, p: LetterConfirmIn):
    d = smart.DECISIONS.get(p.decision)
    if not d or not d[0]:
        raise HTTPException(400, "请选择决定类型（不是决定信的不会写入）")
    stage, _zh, en = d
    with db.get_db() as c:
        sub = dict(_require_live_sub(c, sid))
    res = smart.cached_analysis(sid, p.text)
    if res is None:
        try:
            res = smart.analyze_letter(p.text, sub, use_model=False)
        except ValueError as e:
            raise HTTPException(400, str(e))
    deadline = _check_date(p.deadline, "返修截止日") if (p.deadline or "").strip() else ""
    if stage != "revise":
        deadline = ""
    observed = None
    ts = (p.observed_at or res.get("letter_date") or "").strip()
    if ts:
        observed = db.norm_ts(ts)
        if not observed:
            raise HTTPException(400, "来信日期格式应为 YYYY-MM-DD")
        if (db.parse_ts(observed) - datetime.now()).total_seconds() > 600:
            observed = None
    model = res.get("model") or {}
    payload = {"letter": True, "decision": p.decision,
               "via": res["via"] if p.decision == res["decision"] else "user",
               "agreement": res["agreement"]}
    if model.get("model"):
        payload["model"] = model["model"]
    if deadline:
        payload["deadline"] = deadline
        payload["deadline_via"] = (res.get("deadline_how") or "stated") if deadline == res.get("deadline") else "user"
    raw = f"Decision letter: {en}" + (f" (revision due {deadline})" if deadline else "")
    with db.get_db() as c:
        _require_live_sub(c, sid)
        eid = db.add_event(c, sid, stage, raw, "letter", payload, observed)
        if deadline:
            c.execute("UPDATE submissions SET revise_due=? WHERE id=?", (deadline, sid))
        cur = c.execute("SELECT current_stage FROM submissions WHERE id=?", (sid,)).fetchone()
    return {"ok": True, "id": eid, "current_stage": cur["current_stage"],
            "backdated": cur["current_stage"] != stage}


# 状态词规则：认不出的状态文字 → 模型建议 / 手动指定 → 存成规则，之后由规则直接识别
class StatusRuleIn(BaseModel):
    raw: str
    stage: str
    source: str = "user"


class StatusKeyIn(BaseModel):
    key: str


class SuggestIn(BaseModel):
    raw: str


@app.get("/api/status-rules")
def status_rules():
    return smart.list_status_rules()


@app.post("/api/status-rules")
def status_rule_save(p: StatusRuleIn):
    try:
        smart.save_status_rule(p.raw, p.stage, p.source)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return smart.list_status_rules()


@app.post("/api/status-rules/delete")
def status_rule_delete(p: StatusKeyIn):
    smart.delete_status_rule(p.key)
    return smart.list_status_rules()


@app.post("/api/status-rules/suggest")
def status_rule_suggest(p: SuggestIn):
    try:
        return smart.suggest_stage(p.raw)
    except ValueError as e:
        raise HTTPException(400, str(e))


# 用自然语言提问：问题 → 固定模板的参数化 SQL（只读），把执行的 SQL 一并返回
class AskIn(BaseModel):
    q: str
    use_model: bool = True


@app.post("/api/ask")
def ask(p: AskIn):
    try:
        return smart.run_question(p.q, p.use_model)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- 截图 OCR（可选组件；只把图片变成文字，解析和写入仍走原来的预览 → 确认） ----------
@app.post("/api/ocr")
def ocr_api(file: UploadFile = File(...)):
    if not ocr.available():
        raise HTTPException(501, f"OCR 组件没有安装。在项目目录运行：{ocr.INSTALL_HINT}，然后重启")
    data = file.file.read(ocr.MAX_IMAGE_BYTES + 1)
    try:
        res = ocr.image_to_text(data)
    except ocr.OcrError as e:
        raise HTTPException(413 if "太大" in str(e) else 422, str(e))
    if not res["text"].strip():
        raise HTTPException(422, "截图里没有认出文字：请截取投稿系统页面上有状态、编号的那一块")
    return res


# ---------- submissions ----------
_SORTS = {
    "updated": "s.updated_at DESC, s.id DESC",
    "venue": "COALESCE(v.name,'') COLLATE NOCASE ASC, COALESCE(v.year,'') ASC, s.updated_at DESC",
    "ddl": "CASE WHEN v.ddl IS NULL OR v.ddl='' THEN 1 ELSE 0 END ASC, v.ddl ASC, s.updated_at DESC",
    "revise": "CASE WHEN s.current_stage='revise' AND COALESCE(s.revise_due,'')<>'' THEN 0 ELSE 1 END"
              " ASC, s.revise_due ASC, s.updated_at DESC",
    "days": "CASE WHEN COALESCE(s.submitted_at,'')='' THEN 1 ELSE 0 END ASC,"
            " s.submitted_at DESC, s.updated_at DESC",
}


@app.get("/api/submissions")
def list_submissions(stage: str = "", q: str = "", category: str = "",
                     sort: str = "updated"):
    """分类列表：category = invest/accepted/rejected/withdrawn/archived/trash（空=全部含归档，不含回收站）。

    排序 sort = updated（更新时间，默认）/ venue（会议·期刊）/ ddl（会议截止）/ revise（返修截止）/ days（投稿日）。
    """
    with db.get_db() as c:
        return [_sub_row(c, i) for i in _filtered_ids(c, stage, q, category, sort)]


def _filtered_ids(c, stage: str = "", q: str = "", category: str = "", sort: str = "updated") -> list[int]:
    where, args = [], []
    if category == "trash":
        where.append("COALESCE(s.deleted_at,'')<>''")
    else:
        where.append("COALESCE(s.deleted_at,'')=''")
    if category == "archived":
        where.append("COALESCE(s.archived,0)=1")
    elif category in CATEGORY_STAGES:
        sts = CATEGORY_STAGES[category]
        where.append("s.current_stage IN (%s)" % ",".join("?" * len(sts)))
        args.extend(sts)
        where.append("COALESCE(s.archived,0)=0")
    if stage:
        where.append("s.current_stage=?")
        args.append(stage)
    q = q.strip()
    if q:
        like = f"%{q}%"
        where.append("(s.title LIKE ? OR s.manuscript_number LIKE ? OR v.name LIKE ?"
                     " OR TRIM(COALESCE(v.name,'') || ' ' || COALESCE(v.year,'')) LIKE ?"
                     " OR s.notes LIKE ? OR s.openreview_forum LIKE ? OR s.authors LIKE ?)")
        args += [like] * 7
    sql = "SELECT s.id FROM submissions s LEFT JOIN venues v ON v.id=s.venue_id"
    sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + (_SORTS.get(sort, _SORTS["updated"]) if category != "trash"
                           else "s.deleted_at DESC, s.id DESC")
    return [r["id"] for r in c.execute(sql, args).fetchall()]


@app.get("/api/categories/counts")
def category_counts():
    """分类计数（all = 全部含归档、不含回收站；trash = 回收站；用于标签栏徽标）。"""
    with db.get_db() as c:
        rows = c.execute("SELECT current_stage st, COALESCE(archived,0) ar,"
                         " COALESCE(deleted_at,'') del FROM submissions").fetchall()
    counts = {k: 0 for k in CATEGORY_STAGES}
    counts.update({"archived": 0, "all": 0, "trash": 0})
    for r in rows:
        if r["del"]:
            counts["trash"] += 1
            continue
        counts["all"] += 1
        if r["ar"]:
            counts["archived"] += 1
            continue
        for k, sts in CATEGORY_STAGES.items():
            if r["st"] in sts:
                counts[k] += 1
                break
    return counts


@app.post("/api/submissions")
def create_submission(s: SubmissionIn):
    if s.upload_id and not _UPLOAD_ID_RE.match(s.upload_id):
        raise HTTPException(400, "upload_id 无效")
    undo = []
    try:
        with db.get_db() as c:
            sid = _insert_submission(c, s)
            if s.upload_id:
                src = os.path.join(UPLOAD_DIR, s.upload_id + ".pdf")
                if os.path.exists(src):
                    _attach_pdf(c, sid, src, s.file_name, s.manuscript_number, move=True, undo=undo)
    except BaseException:
        _run_undo(undo)
        raise
    return {"id": sid}


def _run_undo(undo: list):
    for f in reversed(undo):
        try:
            f()
        except OSError:
            pass


def _forum_in_use(c, forum: str, exclude_id: int = 0):
    if not forum:
        return None
    return c.execute("SELECT id, deleted_at FROM submissions WHERE openreview_forum=? AND id<>?",
                     (forum, exclude_id)).fetchone()


def _forum_taken_msg(hit) -> str:
    where = "回收站里" if hit["deleted_at"] else "库中"
    return f"该 OpenReview 稿件已在{where}（#{hit['id']}），不能重复建档"


def _insert_submission(c, s: SubmissionIn) -> int:
    """校验并写入一篇投稿（含 venue 与初始事件），返回新 id。供建档向导与改投共用。"""
    title = strip_invisible(s.title)
    if not title:
        raise HTTPException(400, "标题不能为空")
    _check_stage(s.initial_stage)
    submitted = _check_date(s.submitted_at, "投稿日期")
    if submitted and submitted > date.today().isoformat():
        raise HTTPException(400, "投稿日期不能晚于今天")
    due = _check_date(s.revise_due, "返修截止日")
    forum = openreview.normalize_forum(s.openreview_forum)
    hit = _forum_in_use(c, forum)
    if hit:
        raise HTTPException(400, _forum_taken_msg(hit))
    vid = s.venue_id
    if vid is not None:
        if not c.execute("SELECT 1 FROM venues WHERE id=?", (vid,)).fetchone():
            raise HTTPException(400, "venue_id 不存在")
    elif s.venue is not None:
        vid = _insert_venue(c, s.venue)
    authors = [strip_invisible(a) for a in s.authors if strip_invisible(a)]
    keywords = [k.strip() for k in s.keywords if k.strip()]
    cur = c.execute(
        "INSERT INTO submissions (title,authors,corresponding_author,corresponding_pinyin,"
        "abstract,keywords,venue_id,manuscript_number,openreview_forum,submitted_at,"
        "current_stage,current_raw,revise_due,notes)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (title, json.dumps(authors, ensure_ascii=False), s.corresponding_author.strip(),
         s.corresponding_pinyin.strip().upper(), s.abstract.strip(),
         json.dumps(keywords, ensure_ascii=False),
         vid, s.manuscript_number.strip(), forum,
         submitted, s.initial_stage, s.initial_raw.strip(), due, s.notes.strip()))
    sid = cur.lastrowid
    # 初始事件（投稿日：往日记 12:00，当天记现在，不会落在未来）。
    # 建档时的阶段不是「已提交」且投稿日在以前：记两条——投稿日「已提交」+ 现在登记的阶段，
    # 否则「编辑处理」会从投稿日起算停留天数，一建档就弹催稿提醒
    raw0 = s.initial_raw.strip()
    if s.initial_stage != "submitted" and submitted and submitted < date.today().isoformat():
        db.add_event(c, sid, "submitted", "投稿", "manual", {"created": True},
                     observed_at=db.ts_from_date(submitted))
        db.add_event(c, sid, s.initial_stage, raw0 or "建档时登记的状态", "manual",
                     {"registered": True})
    else:
        db.add_event(c, sid, s.initial_stage, raw0 or "提交登记", "manual", {"created": True},
                     observed_at=db.ts_from_date(submitted) if submitted else None)
    return sid


def _attach_pdf(c, sid: int, src: str, file_name: str, manuscript_number: str, move: bool,
                undo: list | None = None):
    """把 PDF 放进 data/pdfs 并登记到 documents（move=False 时复制，用于改投沿用原稿 PDF）。
    undo：事务失败时调用方执行这些回滚（复制的删掉、移动的挪回原处），data/pdfs 不留孤儿文件。"""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (manuscript_number or "").strip())[:40]
    fn = f"{sid}_{safe}_{int(time.time())}.pdf"
    dst = os.path.join(db.PDF_DIR, fn)
    try:
        (shutil.move if move else shutil.copy2)(src, dst)
    except BaseException:
        if os.path.exists(dst) and os.path.exists(src):     # 复制/跨盘移动到一半
            try:
                os.remove(dst)
            except OSError:
                pass
        raise
    if undo is not None:
        undo.append((lambda: shutil.move(dst, src)) if move else (lambda: os.remove(dst)))
    title = c.execute("SELECT title FROM submissions WHERE id=?", (sid,)).fetchone()["title"]
    c.execute("INSERT INTO documents (submission_id,file_path,file_name,parse_result)"
              " VALUES (?,?,?,?)",
              (sid, "pdfs/" + fn, (os.path.basename(file_name or "") or title)[:120],
               json.dumps({"title": title}, ensure_ascii=False)))


def _registration_event(c, sid: int):
    """建档/导入时写的那条初始事件（payload 带 created / sync_import）。"""
    for e in c.execute("SELECT id, observed_at, payload FROM status_events WHERE submission_id=?"
                       " ORDER BY observed_at, id", (sid,)).fetchall():
        pl = e["payload"] or ""
        if '"created"' in pl or '"sync_import"' in pl:
            return e
    return None


def _move_initial_event(c, sid: int, new_date: str):
    """投稿日期改了 → 建档时的初始事件跟着挪（只在不会越过其他事件时才挪）。"""
    reg = _registration_event(c, sid) if new_date else None
    if not reg:
        return
    ts = db.ts_from_date(new_date)
    later = c.execute("SELECT MIN(observed_at) m FROM status_events WHERE submission_id=? AND id<>?",
                      (sid, reg["id"])).fetchone()["m"]
    if later and ts >= later:
        return
    c.execute("UPDATE status_events SET observed_at=? WHERE id=?", (ts, reg["id"]))
    db.refresh_current(c, sid, touch=False)


@app.get("/api/submissions/search")
def search_submissions(q: str = "", exclude: int = 0):
    """关联选择器：按标题/编号/会议搜库内其他稿件（注意注册在 /{sid} 之前）。"""
    q = q.strip()
    if not q:
        return []
    like = f"%{q}%"
    with db.get_db() as c:
        rows = c.execute(
            "SELECT s.id, s.title, s.current_stage, s.archived, v.name vname, v.year vyear"
            " FROM submissions s LEFT JOIN venues v ON v.id=s.venue_id"
            " WHERE (s.title LIKE ? OR s.manuscript_number LIKE ? OR v.name LIKE ?"
            " OR TRIM(COALESCE(v.name,'') || ' ' || COALESCE(v.year,'')) LIKE ?) AND s.id != ?"
            " AND COALESCE(s.deleted_at,'')=''"
            " ORDER BY s.updated_at DESC LIMIT 12", (like, like, like, like, exclude)).fetchall()
        linked = set()
        if exclude:
            for r in c.execute("SELECT submission_id a, related_id b FROM related_subs"
                               " WHERE submission_id=? OR related_id=?", (exclude, exclude)):
                linked.add(r["b"] if r["a"] == exclude else r["a"])
    return [{"id": r["id"], "title": r["title"], "stage": r["current_stage"],
             "venue": vu.display(r["vname"] or "", r["vyear"] or ""),
             "linked": r["id"] in linked, "archived": bool(r["archived"])} for r in rows]


@app.get("/api/submissions/{sid}")
def get_submission(sid: int):
    with db.get_db() as c:
        sub = _sub_row(c, sid)
        events = [db.row_to_event(r) for r in c.execute(
            "SELECT * FROM status_events WHERE submission_id=? ORDER BY observed_at, id",
            (sid,)).fetchall()]
        docs = [dict(r) for r in c.execute(
            "SELECT id,file_name,parsed_at FROM documents WHERE submission_id=?",
            (sid,)).fetchall()]
        return {**sub, "events": events, "documents": docs}


@app.patch("/api/submissions/{sid}")
def patch_submission(sid: int, p: PatchIn):
    fields = {k: v for k, v in p.model_dump().items() if v is not None}
    if "title" in fields:
        fields["title"] = strip_invisible(fields["title"])
        if not fields["title"]:
            raise HTTPException(400, "标题不能为空")
    for k, label in (("submitted_at", "投稿日期"), ("revise_due", "返修截止日")):
        if k in fields:
            fields[k] = _check_date(fields[k], label)
    if fields.get("submitted_at") and fields["submitted_at"] > date.today().isoformat():
        raise HTTPException(400, "投稿日期不能晚于今天")
    for k in ("authors", "keywords"):
        if k in fields:
            fields[k] = json.dumps([x.strip() for x in fields[k] if x and x.strip()],
                                   ensure_ascii=False)
    if "corresponding_pinyin" in fields:
        fields["corresponding_pinyin"] = fields["corresponding_pinyin"].strip().upper()
    if "openreview_forum" in fields:
        fields["openreview_forum"] = openreview.normalize_forum(fields["openreview_forum"])
    with db.get_db() as c:
        old = _require_live_sub(c, sid)
        if "venue_id" in fields and not c.execute(
                "SELECT 1 FROM venues WHERE id=?", (fields["venue_id"],)).fetchone():
            raise HTTPException(400, "venue_id 不存在")
        hit = _forum_in_use(c, fields.get("openreview_forum") or "", sid)
        if hit:
            raise HTTPException(400, "该 OpenReview 稿件 ID 已绑定在另一篇投稿上（#%d%s）"
                                % (hit["id"], "，在回收站里" if hit["deleted_at"] else ""))
        for k, v in fields.items():
            c.execute(f"UPDATE submissions SET {k}=? WHERE id=?",
                      (v.strip() if isinstance(v, str) else v, sid))
        if fields.get("submitted_at") and fields["submitted_at"] != (old["submitted_at"] or ""):
            _move_initial_event(c, sid, fields["submitted_at"])
        c.execute("UPDATE submissions SET updated_at=datetime('now','localtime') WHERE id=?", (sid,))
    return {"ok": True}


TRASH_DAYS = 30


@app.delete("/api/submissions/{sid}")
def delete_submission(sid: int, purge: int = 0):
    """删除 = 移到回收站（可恢复，30 天后自动清理）；purge=1 且已在回收站 = 彻底删除（含 PDF）。"""
    with db.get_db() as c:
        sub = _require_sub(c, sid)
        if not purge:
            if not sub["deleted_at"]:
                c.execute("UPDATE submissions SET deleted_at=? WHERE id=?", (db.now_str(), sid))
            return {"ok": True, "trashed": True}
        if not sub["deleted_at"]:
            raise HTTPException(400, "请先移到回收站，再在回收站里彻底删除")
    _purge_submission(sid)
    return {"ok": True, "purged": True}


def _purge_submission(sid: int):
    with db.get_db() as c:
        sub = c.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
        if not sub:
            return
        files = [d["file_path"] for d in c.execute(
            "SELECT file_path FROM documents WHERE submission_id=?", (sid,)).fetchall()]
        if sub["openreview_forum"]:
            v = _venue_of(c, sid)
            data = json.dumps({"forum": sub["openreview_forum"], "title": sub["title"],
                               "venue_name": vu.display(v["name"], v.get("year")) if v else "",
                               "stage": sub["current_stage"], "raw": sub["current_raw"] or ""},
                              ensure_ascii=False)
            c.execute("INSERT INTO pending_imports (forum, data, first_seen_at, last_seen_at, dismissed)"
                      " VALUES (?,?," + db.SQL_NOW_MS + "," + db.SQL_NOW_MS + ",1)"
                      " ON CONFLICT(forum) DO UPDATE SET dismissed=1", (sub["openreview_forum"], data))
        c.execute("DELETE FROM submissions WHERE id=?", (sid,))
        _drop_orphan_venue(c, sub["venue_id"])
    # 事务提交后再删文件（删库失败不会误删原件；只删本实例 data/pdfs 里的文件）
    for fp in files:
        real = db.resolve_pdf(fp)
        try:
            if real and os.path.exists(real):
                os.remove(real)
        except OSError:
            pass


def _purge_old_trash(days: int = TRASH_DAYS) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as c:
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM submissions WHERE COALESCE(deleted_at,'')<>'' AND deleted_at < ?",
            (cutoff,)).fetchall()]
    for i in ids:
        _purge_submission(i)
    return len(ids)


@app.post("/api/submissions/{sid}/restore")
def restore_submission(sid: int):
    """从回收站恢复。"""
    with db.get_db() as c:
        _require_sub(c, sid)
        c.execute("UPDATE submissions SET deleted_at='' WHERE id=?", (sid,))
    return {"ok": True}


# ---------- 状态事件（手动登记 / 删除误记） ----------
@app.post("/api/submissions/{sid}/events")
def add_event_api(sid: int, e: EventIn):
    """手动登记状态变化；可补录历史时间（当前状态始终取时间线上最新的事件）。"""
    _check_stage(e.stage)
    observed = None
    if (e.observed_at or "").strip():
        observed = db.norm_ts(e.observed_at)
        if not observed:
            raise HTTPException(400, "发生时间格式应为 YYYY-MM-DD HH:MM")
        if (db.parse_ts(observed) - datetime.now()).total_seconds() > 600:
            raise HTTPException(400, "发生时间不能晚于现在")
    due = _check_date(e.revise_due, "返修截止日") if e.revise_due is not None else None
    with db.get_db() as c:
        _require_live_sub(c, sid)
        eid = db.add_event(c, sid, e.stage, (e.raw or "").strip(), "manual",
                           e.payload or None, observed)
        if due is not None and e.stage == "revise":
            c.execute("UPDATE submissions SET revise_due=? WHERE id=?", (due, sid))
        cur = c.execute("SELECT current_stage FROM submissions WHERE id=?", (sid,)).fetchone()
    return {"ok": True, "id": eid, "current_stage": cur["current_stage"],
            "backdated": cur["current_stage"] != e.stage}


@app.delete("/api/submissions/{sid}/events/{eid}")
def delete_event(sid: int, eid: int):
    """删除一条误记/重复的状态事件，并按剩余时间线重算当前状态。"""
    with db.get_db() as c:
        _require_live_sub(c, sid)
        row = c.execute("SELECT * FROM status_events WHERE id=? AND submission_id=?",
                        (eid, sid)).fetchone()
        if not row:
            raise HTTPException(404, "该状态记录不存在")
        n = c.execute("SELECT COUNT(*) FROM status_events WHERE submission_id=?",
                      (sid,)).fetchone()[0]
        if n <= 1:
            raise HTTPException(400, "至少保留一条状态记录（可直接登记新状态）")
        c.execute("DELETE FROM status_events WHERE id=?", (eid,))
        db.refresh_current(c, sid)
    _tomb_put(eid, sid, dict(row))
    return {"ok": True, "event": {"id": eid}}      # 前端「撤销」只送回 id


# 刚删除的事件留在内存里 1 小时，供「撤销」原样放回（服务重启后不能撤销）
_EVENT_TOMBS: dict = {}
_tomb_lock = threading.Lock()
TOMB_TTL = 3600


def _tomb_put(eid: int, sid: int, row: dict):
    now = time.time()
    with _tomb_lock:
        for k in [k for k, v in _EVENT_TOMBS.items() if now - v[2] > TOMB_TTL]:
            _EVENT_TOMBS.pop(k, None)
        _EVENT_TOMBS[eid] = (sid, row, now)


class EventRestoreIn(BaseModel):
    id: int


@app.post("/api/submissions/{sid}/events/restore")
def restore_event(sid: int, e: EventRestoreIn):
    """撤销删除：按服务器留存的原记录（同 id、同来源、同时间）放回时间线；只认本次运行 1 小时内删掉的。"""
    with _tomb_lock:
        t = _EVENT_TOMBS.get(e.id)
    if not t or t[0] != sid or time.time() - t[2] > TOMB_TTL:
        raise HTTPException(410, "无法撤销：只能撤销刚刚删除的记录")
    row = t[1]
    with db.get_db() as c:
        _require_live_sub(c, sid)
        if c.execute("SELECT 1 FROM status_events WHERE id=?", (e.id,)).fetchone():
            raise HTTPException(409, "这条记录已经在时间线上了")
        c.execute("INSERT INTO status_events (id, submission_id, stage, raw_status, source,"
                  " observed_at, payload, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                  (e.id, sid, row["stage"], row["raw_status"] or "", row["source"] or "manual",
                   row["observed_at"], row["payload"] or "", row["recorded_at"] or db.now_us()))
        db.refresh_current(c, sid)
    with _tomb_lock:
        _EVENT_TOMBS.pop(e.id, None)
    return {"ok": True}


# ---------- 归档 / 相关投稿 ----------
class ArchiveIn(BaseModel):
    archived: bool


@app.post("/api/submissions/{sid}/archive")
def toggle_archive(sid: int, a: ArchiveIn):
    """归档/取消归档：归档后不进任何状态分类（列表「归档」页可见）。"""
    with db.get_db() as c:
        _require_live_sub(c, sid)
        c.execute("UPDATE submissions SET archived=? WHERE id=?",
                  (1 if a.archived else 0, sid))
    return {"ok": True, "archived": a.archived}


class RelatedIn(BaseModel):
    related_id: int
    relation: str = "转投"            # 转投 / 扩写 / 会议版·期刊版 / 同数据集 / 同方法
    note: str = ""


@app.post("/api/submissions/{sid}/related")
def add_related(sid: int, r: RelatedIn):
    """建立两篇投稿的关联（双向可见）。方向：本稿 → 对方（对方是本稿的转投/扩写稿）。
    A-B 与 B-A 视为同一条；重复建立时按最新一次的方向与关系覆盖。"""
    if r.related_id == sid:
        raise HTTPException(400, "不能与自身建立关联")
    relation = (r.relation or "").strip()[:20] or "转投"
    note = (r.note or "").strip()[:200]
    with db.get_db() as c:
        _require_live_sub(c, sid)
        _require_live_sub(c, r.related_id)   # 目标不存在 → 404；在回收站 → 409
        dup = c.execute(
            "SELECT id, note FROM related_subs WHERE (submission_id=? AND related_id=?)"
            " OR (submission_id=? AND related_id=?)",
            (sid, r.related_id, r.related_id, sid)).fetchone()
        if dup:
            c.execute("UPDATE related_subs SET submission_id=?, related_id=?, relation=?, note=?"
                      " WHERE id=?", (sid, r.related_id, relation, note or dup["note"] or "",
                                     dup["id"]))
            return {"ok": True, "updated": True}
        c.execute(
            "INSERT INTO related_subs (submission_id, related_id, relation, note)"
            " VALUES (?,?,?,?)", (sid, r.related_id, relation, note))
    return {"ok": True}


@app.delete("/api/submissions/{sid}/related/{rid}")
def remove_related(sid: int, rid: int):
    with db.get_db() as c:
        row = c.execute("SELECT submission_id, related_id, relation, note FROM related_subs"
                        " WHERE (submission_id=? AND related_id=?) OR (submission_id=? AND related_id=?)",
                        (sid, rid, rid, sid)).fetchone()
        if not row:
            raise HTTPException(404, "关联不存在")
        c.execute("DELETE FROM related_subs WHERE (submission_id=? AND related_id=?)"
                  " OR (submission_id=? AND related_id=?)", (sid, rid, rid, sid))
    return {"ok": True, "link": dict(row)}     # 「撤销」= 按原方向/关系/备注重新建立


# ---------- 改投 / 扩写（被拒后一键另投，自动关联） ----------
@app.post("/api/submissions/{sid}/resubmit")
def resubmit(sid: int, r: ResubmitIn):
    relation = (r.relation or "").strip()[:20] or "转投"
    undo = []
    try:
        return _resubmit(sid, r, relation, undo)
    except BaseException:
        _run_undo(undo)
        raise


def _resubmit(sid: int, r: "ResubmitIn", relation: str, undo: list) -> dict:
    with db.get_db() as c:
        src = _require_live_sub(c, sid)
        sd = db.row_to_submission(src)
        body = SubmissionIn(
            title=(r.title or "").strip() or sd["title"], authors=sd["authors"],
            corresponding_author=sd.get("corresponding_author") or "",
            corresponding_pinyin=sd.get("corresponding_pinyin") or "",
            abstract=sd.get("abstract") or "", keywords=sd["keywords"],
            manuscript_number=r.manuscript_number, submitted_at=r.submitted_at,
            initial_stage=r.initial_stage, venue=r.venue, openreview_forum=r.openreview_forum,
            notes="")
        new_id = _insert_submission(c, body)
        if r.copy_pdf:
            doc = c.execute("SELECT file_path, file_name FROM documents WHERE submission_id=?"
                            " ORDER BY id DESC", (sid,)).fetchone()
            real = db.resolve_pdf(doc["file_path"]) if doc else None
            if real and os.path.exists(real):
                _attach_pdf(c, new_id, real, doc["file_name"], r.manuscript_number, move=False,
                            undo=undo)
        c.execute("INSERT INTO related_subs (submission_id, related_id, relation, note) VALUES (?,?,?,?)",
                  (sid, new_id, relation, "转投/扩写时自动关联"))
    return {"id": new_id}


@app.get("/api/rankings/similar")
def rankings_similar(name: str = "", type: str = ""):
    """改投建议：与原投会议/期刊同 CCF 领域、同级或低一级的候选。"""
    return rk.similar_venues(name, type)


@app.get("/api/submissions/{sid}/pdf")
def get_pdf(sid: int):
    with db.get_db() as c:
        doc = c.execute("SELECT file_path, file_name FROM documents WHERE submission_id=?"
                        " ORDER BY id DESC", (sid,)).fetchone()
    real = db.resolve_pdf(doc["file_path"]) if doc else None
    if not real or not os.path.exists(real):
        raise HTTPException(404, "该投稿未挂接 PDF 原件（或原件文件已不在 data/pdfs 中）")
    return FileResponse(real, media_type="application/pdf", content_disposition_type="inline",
                        filename=os.path.basename(real))


# ---------- 渠道查询 ----------
VAULT_ERR_MSG = ("本机无法解密已保存的 OpenReview 密码（数据库可能是从别的电脑或别的 Windows 用户复制来的），"
                 "需要解除绑定后重新绑定账号")


def _get_account(channel: str) -> dict | None:
    """读取已绑定账号，secret 解密为明文（Windows DPAPI）。解不开时 secret 置空并带 vault_error。"""
    with db.get_db() as c:
        r = c.execute("SELECT * FROM accounts WHERE channel=?", (channel,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["secret"] = vault.reveal(d.get("secret") or "")
    except vault.VaultError:
        d["secret"] = ""
        d["vault_error"] = VAULT_ERR_MSG
    return d


def _or_poll(forum: str, venue_id: str, title: str, acc: dict | None = None) -> dict:
    """带登录凭据查询一篇 OpenReview 稿件（OpenReview 已不再支持匿名 API）；token 过期自动重登一次。"""
    acc = acc if acc is not None else _get_account("openreview")
    if not acc:
        return openreview.poll(forum, venue_id, title, token=None)
    if acc.get("vault_error"):
        return {"ok": False, "error": "OpenReview 登录失败：" + acc["vault_error"], "latency_ms": 0}
    for attempt in range(2):
        try:
            token = openreview.get_token(acc["email"], acc["secret"])
        except Exception as e:
            return {"ok": False, "error": f"OpenReview 登录失败：{e}", "latency_ms": 0}
        r = openreview.poll(forum, venue_id, title, token=token,
                            profile_id=acc.get("profile_id") or "")
        if r.get("auth_expired") and attempt == 0:
            openreview.drop_token(acc["email"])
            continue
        return r
    return {"ok": False, "error": "OpenReview 登录已过期，请重新绑定账号", "latency_ms": 0}


class ForumConflict(Exception):
    def __init__(self, other_id: int):
        super().__init__(f"该 OpenReview 稿件已绑定在 #{other_id}，未写入（可能是重复建档，请删除或合并其中一篇）")
        self.other_id = other_id


def _apply_or_result(c, sid: int, r: dict, payload: dict) -> bool:
    """把一次 OpenReview 结果写回：回填 forum/投稿日/会议 id，状态有变化才记事件。返回是否新增事件。
    该稿件（forum）已绑定在另一条记录上时抛 ForumConflict，什么都不写。"""
    row = c.execute(
        "SELECT s.openreview_forum, s.submitted_at, s.venue_id AS vrow, v.venue_id AS or_vid,"
        " v.tracker, v.year FROM submissions s LEFT JOIN venues v ON v.id=s.venue_id"
        " WHERE s.id=?", (sid,)).fetchone()
    if not row:
        return False
    forum = r.get("forum") or ""
    if forum and forum != (row["openreview_forum"] or ""):
        other = c.execute("SELECT id FROM submissions WHERE openreview_forum=? AND id<>?",
                          (forum, sid)).fetchone()
        if other:
            raise ForumConflict(other["id"])
        if not row["openreview_forum"]:
            c.execute("UPDATE submissions SET openreview_forum=? WHERE id=?", (forum, sid))
    if r.get("submitted_at") and not row["submitted_at"] and vu.valid_date(r["submitted_at"]):
        c.execute("UPDATE submissions SET submitted_at=? WHERE id=?", (r["submitted_at"], sid))
    if row["vrow"]:
        base = openreview.base_venue_id(r.get("venue_id") or "")
        vp = {}
        # 按标题匹配来的结果不覆盖已填的会议 id（只补空）；按 forum 查到的以 OpenReview 为准
        if base and base != (row["or_vid"] or "") and \
                (not row["or_vid"] or r.get("matched_by") != "title"):
            vp["venue_id"] = base
        if row["tracker"] != "openreview":
            vp["tracker"] = "openreview"
        y = r.get("year") or openreview.venue_year(base)
        if y and not row["year"]:
            vp["year"] = y
        for k, v in vp.items():
            c.execute(f"UPDATE venues SET {k}=? WHERE id=?", (v, row["vrow"]))
    changed = False
    if r.get("stage") in STAGES:
        pl = dict(payload)
        if r.get("decision"):
            pl["decision"] = r["decision"]
        if r.get("reviews"):
            pl["reviews"] = r["reviews"]
        changed = db.add_event_if_changed(c, sid, r["stage"], r.get("raw") or "OpenReview",
                                          "openreview", pl) is not None
    c.execute("UPDATE submissions SET last_synced_at=? WHERE id=?", (db.now_str(), sid))
    return changed


@app.post("/api/query/openreview")
def query_openreview(body: QueryIn):
    sid = body.submission_id
    with db.get_db() as c:
        _require_live_sub(c, sid)
        sub = _sub_row(c, sid)
        venue = _venue_of(c, sid)
    if not venue or venue.get("tracker") != "openreview":
        raise HTTPException(400, "该投稿未启用 OpenReview 通道（在「编辑信息」里把追踪渠道改为 OpenReview）")
    result = _or_poll(sub.get("openreview_forum") or "", venue.get("venue_id") or "", sub["title"])
    if result.get("ok"):
        try:
            with db.get_db() as c:
                result["changed"] = _apply_or_result(c, sid, result, {"poll": True, "manual_query": True})
        except ForumConflict as e:
            result = {"ok": False, "error": str(e), "latency_ms": result.get("latency_ms", 0)}
    with db.get_db() as c:
        _log_poll(c, "openreview", str(sid), result.get("ok", False),
                  result.get("latency_ms", 0), result.get("error", ""))
    return result


@app.post("/api/query/openreview/match")
def match_openreview(venue_id: str, title: str):
    r = _or_poll("", venue_id, title)
    return r if r.get("ok") else {}


@app.post("/api/query/elsevier")
def query_elsevier_api(body: QueryIn):
    """【实验功能，默认关闭】爱思唯尔中国稿件查询服务的一键查稿；每篇稿件 24 小时最多查一次。"""
    if not _features()["elsevier_query"]:
        raise HTTPException(403, "「爱思唯尔一键查稿」是默认关闭的实验功能：请先在「渠道查询」页开启，"
                                 "或用「粘贴官方结果」记录状态")
    submission_id = body.submission_id
    with db.get_db() as c:
        _require_live_sub(c, submission_id)
        last = c.execute("SELECT created_at FROM poll_logs WHERE channel='elsevier' AND target=?"
                         " AND (ok=1 OR COALESCE(error,'') NOT LIKE '网络错误%')"
                         " ORDER BY id DESC LIMIT 1", (str(submission_id),)).fetchone()
        lt = db.parse_ts(last["created_at"]) if last else None
        if lt and (datetime.now() - lt).total_seconds() < 24 * 3600:
            raise HTTPException(429, f"这篇稿件 {last['created_at'][:16]} 已查过，24 小时内不重复查询"
                                     "（对官方服务友好）；需要更新可用「粘贴官方结果」")
        sub = _sub_row(c, submission_id)
        venue = _venue_of(c, submission_id)
    if not venue or venue.get("tracker") != "elsevier":
        raise HTTPException(400, "该投稿未启用爱思唯尔通道")
    pinyin = (sub.get("corresponding_pinyin") or "").strip()
    parts = pinyin.replace(",", " ").split()
    if len(parts) < 2:
        raise HTTPException(400, "缺少通讯作者姓名拼音（FIRST LAST，爱思唯尔查询凭据）")
    first, last = parts[0], parts[-1]
    result = elsevier.query(sub.get("manuscript_number") or "", last, first,
                            venue.get("journal_acronym") or "")
    with db.get_db() as c:
        _log_poll(c, "elsevier", str(submission_id), result.get("ok", False),
                  result.get("latency_ms", 0), result.get("error", ""))
        if result.get("ok") and result.get("stage"):
            detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
            result["changed"] = db.add_event_if_changed(
                c, submission_id, result["stage"], result["raw"], "elsevier",
                {**detail, "manual_query": True}) is not None
            c.execute("UPDATE submissions SET last_synced_at=? WHERE id=?",
                      (db.now_str(), submission_id))
    return result


@app.get("/api/query/elsevier/journals")
def elsevier_journals():
    if not _features()["elsevier_query"]:
        raise HTTPException(403, "实验功能未开启")
    return elsevier.candidate_journals()


_poll_lock = threading.Lock()


def _poll_all(force: bool = True) -> dict:
    with db.get_db() as c:
        subs = c.execute(
            "SELECT s.id, s.title, s.openreview_forum, s.last_synced_at, v.venue_id"
            " FROM submissions s JOIN venues v ON v.id=s.venue_id"
            " WHERE v.tracker='openreview' AND COALESCE(s.archived,0)=0"
            " AND COALESCE(s.deleted_at,'')=''"
            " AND s.current_stage NOT IN ('rejected','withdrawn','production')"
            " ORDER BY s.id").fetchall()
    acc = _get_account("openreview")
    if not acc:
        return {"polled": 0, "ok": 0, "changed": 0, "results": [], "need_account": True,
                "error": openreview.NEED_ACCOUNT_MSG}
    if acc.get("vault_error"):
        return {"polled": 0, "ok": 0, "changed": 0, "results": [], "need_account": True,
                "error": acc["vault_error"]}
    results = []
    for i, s in enumerate(subs):
        if not force and s["last_synced_at"]:
            last = db.parse_ts(s["last_synced_at"])
            if last and (datetime.now() - last).total_seconds() < 6 * 3600:
                continue     # 6 小时内已查过（如刚同步过账号），跳过
        if results:
            time.sleep(1)    # 低频友好间隔
        try:
            r = _or_poll(s["openreview_forum"] or "", s["venue_id"] or "", s["title"], acc)
            if r.get("ok"):
                with db.get_db() as c:
                    r["changed"] = _apply_or_result(c, s["id"], r, {"poll": True})
        except Exception as e:     # 单篇出错（含 ForumConflict）只记日志，不影响后面的稿件
            r = {"ok": False, "error": str(e) if isinstance(e, ForumConflict)
                 else f"{type(e).__name__}: {e}", "latency_ms": 0}
        with db.get_db() as c:
            _log_poll(c, "openreview", str(s["id"]), r.get("ok", False),
                      r.get("latency_ms", 0), r.get("error", ""))
        results.append({"id": s["id"], "title": s["title"][:60], **r})
        err = str(r.get("error", ""))
        if err.startswith("OpenReview 登录失败") or "ChallengeRequired" in err or "HTTP 429" in err:
            break    # 登录不上 / 要人机验证 / 持续限流：整轮停下，不对后面的稿件继续请求
    return {"polled": len(results), "ok": sum(1 for r in results if r.get("ok")),
            "changed": sum(1 for r in results if r.get("changed")), "results": results}


@app.post("/api/poll/openreview")
def poll_all_openreview():
    """手动触发全量轮询（跳过归档与已拒/已撤/制作中的稿件；每日后台线程亦调用）。"""
    if not _poll_lock.acquire(blocking=False):
        return {"polled": 0, "ok": 0, "changed": 0, "results": [], "busy": True,
                "error": "轮询正在进行中，请稍候"}
    try:
        return _poll_all(force=True)
    finally:
        _poll_lock.release()


# ---------- OpenReview 账号导入：凭据仅存本机，自动同步我的全部投稿 ----------
class AccountIn(BaseModel):
    email: str
    secret: str        # 密码（登录换 token）


@app.post("/api/accounts/openreview")
def add_openreview_account(a: AccountIn):
    """绑定 OpenReview 账号：先登录验证 + 拉一次 profile 与投稿，验证通过才保存密码。

    密码只放在本机的安全存储里（Windows：DPAPI 加密进数据库；macOS / Linux：系统钥匙串），
    没有可用的安全存储就拒绝绑定；仅用于请求 OpenReview 官方 API。
    """
    email = a.email.strip()
    if not vault.available():
        raise HTTPException(500, "没有可用的安全存储（macOS 钥匙串 / Linux Secret Service），"
                                 "为安全起见不保存密码，账号未绑定")
    try:
        openreview.drop_token(email)
        token = openreview.get_token(email, a.secret)
        prof = openreview.get_my_profile(token)
        subs = openreview.fetch_my_submissions(token, prof["id"])
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"账号验证失败：{type(e).__name__}: {e}")
    try:
        stored = vault.protect(a.secret, force=True, strict=True, key=vault.key_for("openreview"))
    except vault.VaultError as e:
        raise HTTPException(500, f"{e}。为安全起见没有保存密码，账号未绑定")
    with db.get_db() as c:
        c.execute(
            "INSERT INTO accounts (channel,email,secret,profile_id,display_name)"
            " VALUES (?,?,?,?,?)"
            " ON CONFLICT(channel) DO UPDATE SET"
            " email=excluded.email, secret=excluded.secret,"
            " profile_id=excluded.profile_id, display_name=excluded.display_name,"
            " created_at=datetime('now','localtime')",
            ("openreview", email, stored, prof["id"],
             prof.get("names") or prof["id"]))
    return {"ok": True, "profile": prof, "my_submissions": len(subs)}


@app.get("/api/accounts")
def list_accounts():
    """脱敏列出已绑定账号（不回传凭据）；encrypted = 密码已加密保存，needs_rebind = 本机解不开需重绑。"""
    with db.get_db() as c:
        rows = c.execute("SELECT channel,email,secret,profile_id,display_name,created_at"
                         " FROM accounts").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        sec = d.pop("secret", "") or ""
        d["encrypted"] = vault.is_protected(sec)
        d["storage"] = vault.storage_of(sec)
        d["needs_rebind"] = False
        if d["encrypted"]:
            try:
                vault.reveal(sec)
            except vault.VaultError:
                d["needs_rebind"] = True
        out.append(d)
    return out


@app.delete("/api/accounts/{channel}")
def delete_account(channel: str):
    acc = _get_account(channel)
    if acc:
        openreview.drop_token(acc["email"])
    with db.get_db() as c:
        row = c.execute("SELECT secret FROM accounts WHERE channel=?", (channel,)).fetchone()
        c.execute("DELETE FROM accounts WHERE channel=?", (channel,))
    if row:
        vault.forget(row["secret"])                    # 钥匙串里的密码也删掉
    scrubbed = db.scrub_backup_accounts(channel)       # 备份里的凭据也删掉
    return {"ok": True, "scrubbed_backups": scrubbed}


_sync_lock = threading.Lock()


@app.post("/api/sync/openreview")
def sync_openreview():
    """账号同步：拉取我名下全部 OpenReview 投稿。

    同一篇的判定（避免把转投/扩写误判为重复）：
    ① OpenReview forum id 相同 → 同一篇；
    ② 会议基 id 相同 且 标题归一相同 → 同一篇（手动建档的稿件由此升级为自动追踪）；
    ③ 会议展示名（如 NeurIPS 2025）相同 且 标题相同、本地稿还没绑定 forum → 同一篇；
    ④ 标题相同/相近但会议不同（转投/扩写）→ 独立新投稿，进 discovered 并标注疑似关联。
    状态只在与上次 OpenReview 结果不同时才记事件（每日同步不刷屏，也不覆盖手动登记）。
    """
    if not _sync_lock.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "同步正在进行中，请稍候再试"}
    try:
        return _sync_openreview()
    finally:
        _sync_lock.release()


def _sync_openreview() -> dict:
    acc = _get_account("openreview")
    if not acc:
        raise HTTPException(400, "尚未绑定 OpenReview 账号")
    if acc.get("vault_error"):
        with db.get_db() as c:
            _log_poll(c, "openreview-sync", "account", False, 0, acc["vault_error"])
        return {"ok": False, "error": acc["vault_error"], "need_rebind": True, "latency_ms": 0}
    t0 = time.time()
    try:
        try:
            token = openreview.get_token(acc["email"], acc["secret"])
            subs = openreview.fetch_my_submissions(token, acc["profile_id"])
        except openreview.ORAuthExpired:
            openreview.drop_token(acc["email"])          # token 过期：重登一次再拉
            token = openreview.get_token(acc["email"], acc["secret"])
            subs = openreview.fetch_my_submissions(token, acc["profile_id"])
    except Exception as e:
        openreview.drop_token(acc["email"])
        msg = f"同步失败：{e}"
        ms = int((time.time() - t0) * 1000)
        with db.get_db() as c:
            _log_poll(c, "openreview-sync", "account", False, ms, msg)
        return {"ok": False, "error": msg, "latency_ms": ms}

    updated, unchanged, discovered, trashed = [], [], [], []
    with db.get_db() as c:
        rows = c.execute(
            "SELECT s.id, s.title, s.openreview_forum, s.archived, COALESCE(s.deleted_at,'') AS deleted_at,"
            " v.venue_id AS or_vid, v.name AS vname, v.year AS vyear FROM submissions s"
            " LEFT JOIN venues v ON v.id = s.venue_id ORDER BY s.id").fetchall()
        # 回收站里的稿件也参与匹配（不会被当成「新发现」重复建档），但不更新它们；在库稿件优先
        existing = [r for r in rows if not r["deleted_at"]]
        by_forum, by_vid, by_vname = {}, {}, {}
        for r in existing + [r for r in rows if r["deleted_at"]]:
            if r["openreview_forum"]:
                by_forum.setdefault(r["openreview_forum"], r)
            nt = vu.norm_title(r["title"])
            if not nt:
                continue
            if r["or_vid"]:
                by_vid.setdefault((openreview.base_venue_id(r["or_vid"]), nt), r)
            vn = vu.norm_title(vu.display(r["vname"] or "", r["vyear"] or ""))
            if vn:
                by_vname.setdefault((vn, nt), r)
        used = set()
        for s in subs:
            nt = vu.norm_title(s["title"])
            r = by_forum.get(s["forum"])
            if r is None and nt:
                cand = by_vid.get((s["venue_id"], nt)) or \
                    by_vname.get((vu.norm_title(s["venue_name"]), nt))
                # 已绑定其他 forum 的本地稿 = 另一篇，不能合并
                if cand is not None and (cand["openreview_forum"] or "") in ("", s["forum"]):
                    r = cand
            if r is not None and r["id"] not in used:
                used.add(r["id"])
                if r["deleted_at"]:
                    trashed.append({"id": r["id"], "title": s["title"][:80]})
                    continue
                try:
                    changed = _apply_or_result(c, r["id"], s, {"sync": True, "venue": s["venue_id"]})
                except ForumConflict as e:      # 理论上不会发生（forum 优先匹配），出现也只跳过这一篇
                    _log_poll(c, "openreview-sync", str(r["id"]), False, 0, str(e))
                    continue
                item = {"id": r["id"], "title": s["title"][:80], "stage": s["stage"],
                        "raw": s["raw"], "archived": bool(r["archived"])}
                (updated if changed else unchanged).append(item)
            else:
                sims = [x for x in existing
                        if vu.title_similarity(x["title"], s["title"]) >= 0.85]
                discovered.append({
                    **s,
                    "_related": bool(sims),
                    "_related_ids": [x["id"] for x in sims][:4],
                    "_related_titles": [f"#{x['id']} {x['title'][:50]}" for x in sims][:4],
                    "_same_title": any(vu.norm_title(x["title"]) == nt for x in sims),
                })
        _save_pending_imports(c, discovered)
        ms = int((time.time() - t0) * 1000)
        _log_poll(c, "openreview-sync", "account", True, ms,
                  f"拉到 {len(subs)} 篇 · 状态变化 {len(updated)} · 新发现 {len(discovered)}")
    return {"ok": True, "updated": updated, "unchanged": unchanged, "discovered": discovered,
            "trashed": trashed, "total": len(subs), "latency_ms": int((time.time() - t0) * 1000)}


PENDING_KEEP_DAYS = 30


def _save_pending_imports(c, discovered: list):
    """同步发现、尚未建档的投稿记进 pending_imports（总览「待建档」）：已忽略的保持忽略；
    已在库（含回收站）的、或连续 30 天同步都没再出现的清掉。discovered 项补 _dismissed 标记。"""
    for d in discovered:
        f = str(d.get("forum") or "")
        if not f:
            continue
        c.execute("INSERT INTO pending_imports (forum, data, first_seen_at, last_seen_at, dismissed)"
                  " VALUES (?,?," + db.SQL_NOW_MS + "," + db.SQL_NOW_MS + ",0) ON CONFLICT(forum) DO UPDATE SET"
                  " data=excluded.data, last_seen_at=excluded.last_seen_at",
                  (f, json.dumps(d, ensure_ascii=False)))
    c.execute("DELETE FROM pending_imports WHERE forum IN (SELECT openreview_forum FROM submissions"
              " WHERE COALESCE(openreview_forum,'')<>'')")
    cutoff = (datetime.now() - timedelta(days=PENDING_KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("DELETE FROM pending_imports WHERE COALESCE(last_seen_at,'') < ?", (cutoff,))
    dismissed = {r["forum"] for r in c.execute("SELECT forum FROM pending_imports WHERE dismissed=1")}
    for d in discovered:
        d["_dismissed"] = str(d.get("forum") or "") in dismissed


@app.post("/api/sync/openreview/import")
def import_discovered(s: dict):
    """一键把账号同步发现的未登记投稿建档（前端传单条 discovered 项）。

    防真重复：forum id 已在库 / 同会议基 id + 同标题已存在 → 返回已有 id，不重复建档。
    （标题相同但会议不同 = 转投/扩写，允许独立建档；link_related_ids 给出时自动建立「转投」关联。）
    """
    forum = openreview.normalize_forum(str(s.get("forum") or ""))
    try:
        res = _import_discovered(s)
    except sqlite3.IntegrityError:
        # 并发重复点击：唯一索引挡住了第二次插入 → 返回已建好的那条
        with db.get_db() as c:
            hit = c.execute("SELECT id, COALESCE(deleted_at,'') AS del FROM submissions"
                            " WHERE openreview_forum=?", (forum,)).fetchone()
        if not hit:
            raise
        res = _existing_hit(hit["id"], hit["del"], "这篇已在库中")
    if forum:
        with db.get_db() as c:
            if c.execute("SELECT 1 FROM submissions WHERE openreview_forum=?", (forum,)).fetchone():
                c.execute("DELETE FROM pending_imports WHERE forum=?", (forum,))
    return res


def _existing_hit(sid: int, deleted_at: str, msg: str) -> dict:
    if deleted_at:
        return {"id": sid, "existing": True, "in_trash": True,
                "hint": f"这篇在回收站里（#{sid}），可到「回收站」恢复，未重复建档"}
    return {"id": sid, "existing": True, "hint": f"{msg}（#{sid}），未重复建档"}


def _plain(v) -> str:
    if isinstance(v, dict):
        v = v.get("value", "")
    return str(v or "")[:5000]


def _import_discovered(s: dict) -> dict:
    forum = openreview.normalize_forum(str(s.get("forum") or ""))
    vid = openreview.base_venue_id(str(s.get("venue_id") or ""))
    title = strip_invisible(str(s.get("title") or "")) or "未命名投稿"
    nt = vu.norm_title(title)
    with db.get_db() as c:
        if forum:
            hit = c.execute("SELECT id, COALESCE(deleted_at,'') AS del FROM submissions"
                            " WHERE openreview_forum=?", (forum,)).fetchone()
            if hit:
                return _existing_hit(hit["id"], hit["del"], "这篇已在库中")
        if vid and nt:
            for r in c.execute("SELECT s.id, s.title, v.venue_id, COALESCE(s.deleted_at,'') AS del,"
                               " COALESCE(s.openreview_forum,'') AS f"
                               " FROM submissions s JOIN venues v ON v.id=s.venue_id"
                               " WHERE COALESCE(v.venue_id,'')<>''"
                               " ORDER BY (COALESCE(s.deleted_at,'')<>''), s.id").fetchall():
                if openreview.base_venue_id(r["venue_id"]) != vid or vu.norm_title(r["title"]) != nt:
                    continue
                if r["f"] and forum and r["f"] != forum:
                    continue            # 已绑定另一篇 forum：同标题重投（如 TMLR 被拒后再投），是另一篇
                if forum and not r["f"] and not r["del"]:
                    c.execute("UPDATE submissions SET openreview_forum=? WHERE id=?", (forum, r["id"]))
                    return {"id": r["id"], "existing": True, "bound": True,
                            "hint": f"库里已有这篇（#{r['id']}），已与这条 OpenReview 稿件绑定，之后自动跟踪"}
                return _existing_hit(r["id"], r["del"], "该会议下已有这篇")
        name = str(s.get("venue_base_name") or s.get("venue_name") or "").strip()
        year = str(s.get("year") or "").strip()
        if not name and vid:
            name, y2 = openreview.venue_name_from_id(vid)
            year = year or y2
        name, year = vu.split_year(name, year)
        if year and not re.fullmatch(r"(19|20)\d{2}", year):
            year = ""
        name = name or "OpenReview"
        vtype = "conference" if (year or "/Conference" in vid) else "journal"
        authors = openreview._authors_to_names(s.get("authors") or [])
        keywords = openreview._keywords(s.get("keywords"))
        submitted = str(s.get("submitted_at") or "").strip()
        if not vu.valid_date(submitted):
            submitted = ""
        st = s.get("stage")
        stage = st if isinstance(st, str) and st in STAGES else "submitted"
        raw = _plain(s.get("raw")).strip()[:200] or "账号同步导入"
        cur = c.execute(
            "INSERT INTO venues (name,type,acronym,publisher,tracker,venue_id,journal_acronym,year,ddl)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (name, vtype, "", "", "openreview" if vid else "manual", vid, "", year, ""))
        vrow = cur.lastrowid
        cur = c.execute(
            "INSERT INTO submissions (title,authors,abstract,keywords,venue_id,openreview_forum,"
            "submitted_at,current_stage,current_raw,notes,last_synced_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (title, json.dumps(authors, ensure_ascii=False), _plain(s.get("abstract")),
             json.dumps(keywords, ensure_ascii=False), vrow, forum, submitted, stage, raw,
             "OpenReview 账号同步导入", db.now_str()))
        sid = cur.lastrowid
        payload = {"sync_import": True}
        if isinstance(s.get("decision"), str) and s["decision"]:
            payload["decision"] = s["decision"]
        sub_ts = db.ts_from_date(submitted) if submitted else None
        if stage in TERMINAL_STAGES and sub_ts:
            # 已出结果的稿件：投稿日记「已提交」，结果记在录用日（pdate）或现在——在审天数才对
            db.add_event(c, sid, "submitted", "OpenReview 投稿", "openreview",
                         {"sync_import": True}, observed_at=sub_ts)
            pd = s.get("pdate")
            res_ts = None
            if stage == "accepted" and isinstance(pd, (int, float)) and pd > 0:
                res_ts = min(datetime.fromtimestamp(pd / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                             db.now_str())
            payload.pop("sync_import", None)
            payload["sync"] = True
            payload["imported"] = True     # 建档时一并写入的结果，不算「新变化」
            db.add_event(c, sid, stage, raw, "openreview", payload,
                         observed_at=res_ts if res_ts and res_ts > sub_ts else None)
        else:
            db.add_event(c, sid, stage, raw, "openreview", payload, observed_at=sub_ts)
        linked = []
        ids = s.get("link_related_ids")
        for rid in (ids if isinstance(ids, list) else []):
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            other = c.execute("SELECT id, submitted_at FROM submissions WHERE id=?"
                              " AND COALESCE(deleted_at,'')=''", (rid,)).fetchone()
            if not other or rid == sid:
                continue
            # 方向：早投的 → 后投的（后者是前者的转投/扩写稿）；日期未知时 已有稿 → 新建稿
            a, b = rid, sid
            if other["submitted_at"] and submitted and other["submitted_at"] > submitted:
                a, b = sid, rid
            if not c.execute("SELECT 1 FROM related_subs WHERE (submission_id=? AND related_id=?)"
                             " OR (submission_id=? AND related_id=?)", (a, b, b, a)).fetchone():
                c.execute("INSERT INTO related_subs (submission_id, related_id, relation, note)"
                          " VALUES (?,?,?,?)", (a, b, "转投", "账号同步导入时按同名/近似标题自动关联"))
                linked.append(rid)
    return {"id": sid, "linked": linked}


# ---------- 爱思唯尔官方结果粘贴解析（人工过码后 → 自适应提取 → 自动入档） ----------
class PasteIn(BaseModel):
    text: str
    via: str = ""                   # "ocr"：文字由截图识别而来（可能经用户手改），记进事件的来源信息
    ocr_score: float | None = None  # OCR 各行置信度的最小值


def _via_payload(via: str, score: float | None) -> dict:
    """粘贴文本的来历：空 = 复制网页；ocr = 截图识别。其他值一律拒绝。"""
    if via not in ("", "ocr"):
        raise HTTPException(400, "via 只能是空或 ocr")
    if via != "ocr":
        return {}
    out = {"via": "ocr"}
    if isinstance(score, (int, float)) and 0 <= score <= 1:
        out["ocr_min_score"] = round(float(score), 3)
    return out


# 官方页中文状态通栏 → 归一阶段
_ZH_STAGE = {
    "同行评审进行中": "review", "正在评审": "review",
    "编辑初审": "editor", "编辑处理中": "editor",
    "初审阶段": "submitted", "已提交": "submitted",
    "所需审稿意见完成": "reviews_done", "同行评审完成": "reviews_done",
    "审稿完成": "decision", "决策中": "decision",
    "返修": "revise", "修改中": "revise",
    "已录用": "accepted", "已接受": "accepted",
    "已拒稿": "rejected", "已退稿": "rejected",
    "已撤稿": "withdrawn", "制作中": "production",
}

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_PAT = (r"(\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
             r"|\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})")


def _to_ymd(s: str) -> str:
    """中文 / ISO / 英文日期 → YYYY-MM-DD；认不出返回空。"""
    s = (s or "").strip()
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s) or \
        re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)
        if m:
            d, mo, y = int(m.group(1)), _MONTHS.get(m.group(2)[:3].lower()), int(m.group(3))
        else:
            m = re.match(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
            if not m:
                return ""
            mo, d, y = _MONTHS.get(m.group(1)[:3].lower()), int(m.group(2)), int(m.group(3))
    try:
        return date(y, mo, d).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _label_re(labels: str, value: str) -> re.Pattern:
    """「标签[*][：:] 值」——值可以在同一行，也可以在下一行（微信端两行结构）。"""
    return re.compile(r"(?:" + labels + r")\*?[：:]?[ \t]*\n?[ \t]*" + value, re.I)


_PASTE_PATTERNS = {
    "progress": _label_re(r"当前进度|当前状态|Current\s+status|Manuscript\s+status|"
                          r"(?<![A-Za-z])Status(?!\s+date)", r"([A-Za-z][^\n]{0,80})"),
    "stage_zh": re.compile(r"(同行评审进行中|正在评审|编辑初审|编辑处理中|初审阶段|"
                           r"所需审稿意见完成|同行评审完成|审稿完成|决策中|返修|修改中|"
                           r"已提交|已录用|已接受|已拒稿|已退稿|已撤稿|制作中)"),
    "submitted": _label_re(r"提交日期|提交时间|投稿日期|Date\s+submitted|Submission\s+date|"
                           r"Submitted\s+on|Submitted", _DATE_PAT),
    "updated": _label_re(r"更新时间|最后更新|更新日期|Last\s+updated|Updated\s+on|Updated|"
                         r"Status\s+date", _DATE_PAT),
    "revise_due": _label_re(r"(?:返修|修改|修回)稿?(?:截止|期限)(?:日期|时间)?|"
                            r"Revision\s+due(?:\s+date)?|Revision\s+deadline", _DATE_PAT),
    "manuscript": _label_re(r"Manuscript\s*(?:Number|No\.?|ID)|稿件编号|稿号",
                            r"([A-Z0-9][A-Z0-9.\-]{5,25})"),
    # 期刊名：「期刊」独立成行后取下一行（微信端两行结构）；排除备注行「…取决于期刊要求。」误抓
    "journal": re.compile(r"(?:^|\n)\s*(?:期刊|Journal)[：:]?\s*\n\s*([^\n*，。;]{2,80})(?=\n|$)", re.I),
    "journal_inline": re.compile(r"(?:期刊|Journal)[：:]\s*([^\n*，。;]{2,80})", re.I),
}
# 单行粘贴（换行丢失）时，状态值后面紧跟的其他标签
_PROGRESS_CUT_RE = re.compile(
    r"\s{2,}|[一-鿿：:*]|\s+(?=(?:Manuscript\s*(?:Number|No|ID)|Date\s+submitted|"
    r"Submission\s+date|Last\s+updated|Reviewers?\s+(?:invited|accepted)|Reviews?\s+completed|"
    r"Journal\s*[:：])\b)", re.I)


def _prep_paste(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t　]*([：:])[ \t　]*", r"\1", t)     # 冒号两侧的空格
    t = re.sub(r"(\d)[ \t　]*([年月日])[ \t　]*", r"\1\2", t)   # 2026 年 11 月 18 日
    return t


def _status_key(line: str) -> str:
    k = re.sub(r"[‐-―−]", "-", line.strip().lower())
    k = re.sub(r"\s*-\s*", " - ", k)
    return re.sub(r"\s+", " ", k).strip(" .;:，。")


def _parse_official_paste(text: str, prev_reviewers: dict | None = None) -> dict:
    """自适应解析官方查询页粘贴文本（中文微信页 / 英文页 / 单行粘贴；含二维码提示语等噪声）。

    prev_reviewers：该稿上次记录的审稿人统计，用来拆分官方页「旧值→新值」粘连的数字。"""
    if not text or not text.strip():
        raise HTTPException(400, "粘贴内容为空")
    t = _prep_paste(text)
    out = {"stage": "", "raw": "", "stage_zh": "", "reviewers": {}, "observed_at": "",
           "submitted_at": "", "manuscript_number": "", "journal": "", "revise_due": ""}
    # ① 当前进度（英文状态行，如 Under Review）
    m = _PASTE_PATTERNS["progress"].search(t)
    if m:
        raw = _PROGRESS_CUT_RE.split(m.group(1), maxsplit=1)[0].strip()
        if raw:
            out["raw"] = raw
            out["stage"] = normalize(raw) or ""
    # ② 中文状态通栏（同行评审进行中 → review）
    m = _PASTE_PATTERNS["stage_zh"].search(t)
    if m:
        out["stage_zh"] = m.group(1)
        if not out["stage"]:
            out["stage"] = _ZH_STAGE.get(m.group(1), "")
        if not out["raw"]:
            out["raw"] = m.group(1)
    if not out["stage"]:
        # ③ 兜底：只认「整行就是一个已知状态」的行（不做子串匹配，避免标题里的 Accept 等误判）
        for line in t.splitlines():
            if _status_key(line) in RAW_STATUS_MAP:
                out["stage"], out["raw"] = RAW_STATUS_MAP[_status_key(line)], line.strip()
                break
    # ④ 审稿人统计：官方页对有变化的计数显示「旧值 →(图标) 新值」，复制后粘成 "02"、"23"，
    #    交给 elsevier_counters 拆分（参考上次记录 + 完成 ≤ 接受 ≤ 邀请），读法有歧义时给出提示
    out.update({"reviewers_prev": {}, "reviewers_at_least": [], "reviewers_ambiguous": False,
                "reviewers_note": ""})
    out.update(parse_reviewer_counters(text, prev_reviewers))
    # ⑤ 日期
    for key, pat in (("observed_at", "updated"), ("submitted_at", "submitted"),
                     ("revise_due", "revise_due")):
        m = _PASTE_PATTERNS[pat].search(t)
        if m:
            out[key] = _to_ymd(m.group(1))
    # ⑥ 编号与期刊
    m = _PASTE_PATTERNS["manuscript"].search(t)
    if m:
        out["manuscript_number"] = m.group(1).strip(" .-").upper()
    m = _PASTE_PATTERNS["journal"].search(t) or _PASTE_PATTERNS["journal_inline"].search(t)
    if m:
        cand = m.group(1).strip()
        # 排除备注噪声词（「期刊要求。」等误抓）
        if not re.search(r"(要求|参考|邀请|取决于)", cand):
            out["journal"] = cand
    out["recognized"] = bool(out["stage"] or out["stage_zh"] or out["reviewers"])
    return out


def _last_reviewers(c, sid: int) -> dict | None:
    """该稿时间线上最近一次记录的审稿人统计。"""
    for r in c.execute("SELECT payload FROM status_events WHERE submission_id=?"
                       " ORDER BY observed_at DESC, id DESC", (sid,)).fetchall():
        pl = db._jload(r["payload"], {})
        if isinstance(pl, dict) and isinstance(pl.get("reviewers"), dict) and pl["reviewers"]:
            return pl["reviewers"]
    return None


def _ms_base(s: str) -> str:
    """稿件编号去掉修改稿后缀：ESWA-D-25-00000R1 → ESWA-D-25-00000。"""
    return re.sub(r"R\d+$", "", (s or "").strip().upper())


def _paste_mismatch(sub_number: str, parsed: dict) -> str:
    a, b = _ms_base(sub_number), _ms_base(parsed.get("manuscript_number"))
    if a and b and a != b:
        return (f"粘贴内容的稿件编号 {parsed.get('manuscript_number')} 与本稿编号 {sub_number} 不一致"
                "——请确认粘贴的是这篇稿件的查询结果")
    return ""


@app.post("/api/submissions/{sid}/elsevier-paste")
def elsevier_paste(sid: int, p: PasteIn):
    """粘贴官方查询页文本 → 自适应解析 → 预览（不写入，前端确认后再调 confirm）。"""
    with db.get_db() as c:
        sub = _require_live_sub(c, sid)
        prev = _last_reviewers(c, sid)
    parsed = _parse_official_paste(p.text, prev)
    parsed["warning"] = _paste_mismatch(sub["manuscript_number"], parsed)
    stored = sub["submitted_at"] or ""
    parsed["submitted_note"] = "" if not parsed["submitted_at"] else (
        "（与已登记一致）" if parsed["submitted_at"] == stored else
        f"（已登记 {stored}，将以官方日期为准）" if stored else "（将回填）")
    return parsed


@app.post("/api/submissions/{sid}/elsevier-paste/confirm")
def elsevier_paste_confirm(sid: int, p: PasteIn):
    """确认写入：状态事件 + 审稿人统计 + 日期回填（与上次官方结果完全相同则不重复记事件）。"""
    with db.get_db() as c:
        sub = _require_live_sub(c, sid)
        parsed = _parse_official_paste(p.text, _last_reviewers(c, sid))
        mismatch = _paste_mismatch(sub["manuscript_number"], parsed)
        if mismatch:
            raise HTTPException(400, mismatch)
        if not parsed["recognized"]:
            raise HTTPException(400, "没有识别到任何状态信息（可能粘贴的是验证码页或其他页面），未写入")
        stage = parsed["stage"] or "review"     # 只有审稿人统计、没有状态行 → 在审
        raw = parsed["raw"] or (parsed["stage_zh"] or "官方查询结果")
        # observed_at 用官方更新时间（当天 = 现在，往日 = 当日 12:00；无则现在）
        observed = db.ts_from_date(parsed["observed_at"]) if parsed["observed_at"] else None
        payload = {"official_paste": True, **_via_payload(p.via, p.ocr_score)}
        if parsed["reviewers"]:
            payload["reviewers"] = parsed["reviewers"]
            if parsed["reviewers_prev"]:
                payload["reviewers_prev"] = parsed["reviewers_prev"]       # 官方显示的旧值
            if parsed["reviewers_at_least"]:
                payload["reviewers_at_least"] = parsed["reviewers_at_least"]  # 「5+」只知下限
        if parsed["observed_at"]:
            payload["official_date"] = parsed["observed_at"]
        # ① 先以官方提交日期为准（顺带挪动建档时的初始事件），再记本次状态——时间线顺序才对
        official_sub = parsed["submitted_at"]
        if official_sub and official_sub <= date.today().isoformat() and \
                official_sub != (sub["submitted_at"] or ""):
            c.execute("UPDATE submissions SET submitted_at=? WHERE id=?", (official_sub, sid))
            _move_initial_event(c, sid, official_sub)
        eid = db.add_event_if_changed(c, sid, stage, raw, "elsevier", payload,
                                      observed_at=observed, compare_keys=("reviewers",))
        # ② 官方状态时间早于建档登记事件（投稿日留成了「今天」等）→ 登记事件挪到它之前
        if eid is not None:
            ev_at = c.execute("SELECT observed_at FROM status_events WHERE id=?", (eid,)).fetchone()[0]
            reg = _registration_event(c, sid)
            if reg and (reg["observed_at"] or "") > ev_at:
                before = (db.parse_ts(ev_at) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
                c.execute("UPDATE status_events SET observed_at=? WHERE id=?", (before, reg["id"]))
                db.refresh_current(c, sid)
        # ③ 编号为空则回填；返修截止日随返修状态写入
        if parsed["manuscript_number"] and not sub["manuscript_number"]:
            c.execute("UPDATE submissions SET manuscript_number=? WHERE id=?",
                      (parsed["manuscript_number"], sid))
        if stage == "revise" and parsed["revise_due"]:
            c.execute("UPDATE submissions SET revise_due=? WHERE id=?", (parsed["revise_due"], sid))
        c.execute("UPDATE submissions SET last_synced_at=? WHERE id=?", (db.now_str(), sid))
        cur = c.execute("SELECT current_stage FROM submissions WHERE id=?", (sid,)).fetchone()[0]
    if eid is None:
        return {"ok": True, "stage": stage, "raw": raw, "written": False, "current_stage": cur,
                "hint": "与上一次官方结果相同（状态与审稿人统计均未变化），未重复记录"}
    return {"ok": True, "stage": stage, "raw": raw, "written": True, "current_stage": cur,
            "backdated": cur != stage}


# ---------- 投稿系统页面导入（ScholarOne / Editorial Manager / IEEE Author Portal / Snapp / MDPI / 中文期刊…）----------
# 用户自己登录官方投稿系统，把作者中心整页复制粘贴进来。本程序不登录、不访问这些网站，也不保存它们的密码。
class SysPasteIn(BaseModel):
    text: str


class SysPastePick(BaseModel):
    index: int
    action: str                 # update / create
    venue_name: str = ""        # create：期刊名（页面上没有时由用户补）


class SysPasteConfirmIn(BaseModel):
    text: str
    picks: list[SysPastePick] = []
    via: str = ""
    ocr_score: float | None = None


def _sys_parse(text: str) -> dict:
    if not text or not text.strip():
        raise HTTPException(400, "粘贴内容为空")
    if len(text) > 2_000_000:
        raise HTTPException(400, "粘贴内容太长（超过 200 万字符）")
    return syspaste.parse(text)


_TERMINAL = ("rejected", "withdrawn")


def _cjk_len(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")


def _sys_brief(r) -> dict:
    return {"id": r["id"], "title": r["title"], "venue": r["venue_name"] or "",
            "current_stage": r["current_stage"], "trashed": bool(r["deleted_at"])}


def _sys_match(c, items: list[dict], page_journal: str = "") -> list[dict]:
    """把解析出的每篇稿件对到库里。

    - 有编号：按编号对（完全相同的编号优先，其次忽略 R1 / .R1 等修改稿后缀）。
    - 没有编号：才按标题对，而且只对期刊投稿；页面上知道期刊名时还要对得上；
      已拒 / 已撤的旧稿不接新的在审状态（转投后同名的是另一篇）。
    - 对上不止一篇 → 「待确认」（ambiguous），对上的在回收站里 → trashed：都不能导入，也不会新建。
    """
    rows = c.execute("SELECT s.id, s.title, s.manuscript_number, s.current_stage, s.current_raw,"
                     " COALESCE(s.deleted_at,'') AS deleted_at, v.name AS venue_name, v.type AS venue_type"
                     " FROM submissions s LEFT JOIN venues v ON v.id=s.venue_id").fetchall()
    by_ms, by_exact, by_title = {}, {}, {}
    for r in rows:
        num = (r["manuscript_number"] or "").strip().upper()
        if num:
            by_exact.setdefault(num, []).append(r)
            by_ms.setdefault(syspaste.ms_base(num), []).append(r)
        tk = syspaste.title_key(r["title"])
        if len(tk) >= 6:
            by_title.setdefault(tk, []).append(r)
    out = []
    for it in items:
        row = dict(it, match=None, candidates=[])
        mid = (it.get("manuscript_id") or "").strip().upper()
        ms = syspaste.ms_base(mid)
        cands, how = [], ""
        if ms:
            exact = by_exact.get(mid, [])
            cands, how = (exact if len(exact) == 1 else by_ms.get(ms, [])), "number"
        elif it.get("title"):
            tk = syspaste.title_key(it["title"])
            if len(tk) >= 6:
                cands = list(by_title.get(tk, []))
                if not cands and it.get("title_truncated") and (_cjk_len(tk) >= 6 or len(tk) >= 20):
                    cands = [r for k, rs in by_title.items() if k.startswith(tk) for r in rs]
            jn = vu.norm_title(it.get("journal") or page_journal or "")
            cands = [r for r in cands if (r["venue_type"] or "journal") == "journal"
                     and (not jn or not r["venue_name"] or vu.norm_title(r["venue_name"]) == jn)]
            if it.get("stage") and it["stage"] not in _TERMINAL:
                cands = [r for r in cands if r["current_stage"] not in _TERMINAL]
            how = "title"
        live = [r for r in cands if not r["deleted_at"]]
        if len(live) == 1:
            hit = live[0]
            same = hit["current_stage"] == it.get("stage") and \
                syspaste.same_status(hit["current_raw"], it.get("raw_status"))
            row["match"] = dict(_sys_brief(hit), by=how)
            row["action"] = "same" if same else ("update" if it.get("stage") else "skip")
        elif len(live) > 1:
            row["action"], row["candidates"] = "ambiguous", [_sys_brief(r) for r in live[:5]]
        elif cands:
            row["action"], row["candidates"] = "trashed", [_sys_brief(r) for r in cands[:5]]
        elif not it.get("stage"):
            row["action"] = "skip" if it.get("raw_status") else "unknown"      # 草稿 / 被取代 / 认不出的状态
        else:
            row["action"] = "create" if it.get("title") else "no_title"
        out.append(row)
    # 同一次粘贴里两篇不同的稿件对到库里同一篇：都改成「待确认」，不把两条时间线揉到一起
    claims = {}
    for row in out:
        if row["match"]:
            claims.setdefault(row["match"]["id"], []).append(row)
    for rs in claims.values():
        keys = {syspaste.ms_base(r.get("manuscript_id")) or "t:" + syspaste.title_key(r.get("title")) for r in rs}
        if len(keys) > 1:
            for r in rs:
                r["action"], r["candidates"], r["match"] = "ambiguous", [r["match"]], None
    return out


@app.post("/api/import/system-paste")
def system_paste_preview(p: SysPasteIn):
    """粘贴投稿系统页面 → 认出每篇稿件 → 预览（不写入）。"""
    parsed = _sys_parse(p.text)
    with db.get_db() as c:
        parsed["items"] = _sys_match(c, parsed["items"], parsed.get("journal") or "")
    return parsed


def _sys_event(c, sid: int, it: dict, parsed: dict, via: dict | None = None) -> int | None:
    stage = it.get("stage")
    if not stage:
        return None
    raw = (it.get("raw_status") or parsed.get("system_label") or "")[:200]
    observed = db.ts_from_date(it["status_date"]) if it.get("status_date") else db.now_str()
    # 同一稿件、同阶段、同原文、同一天的粘贴记录只记一次：同一页粘贴几次都不会刷屏
    if c.execute("SELECT 1 FROM status_events WHERE submission_id=? AND source='paste' AND stage=?"
                 " AND COALESCE(raw_status,'')=? AND substr(observed_at,1,10)=?",
                 (sid, stage, raw, observed[:10])).fetchone():
        return None
    payload = {"system_paste": True, "system": parsed.get("system") or "",
               "system_label": parsed.get("system_label") or "", **(via or {})}
    if it.get("status_date"):
        payload["official_date"] = it["status_date"]
    if it.get("manuscript_id"):
        payload["manuscript_id"] = it["manuscript_id"]
    eid = db.add_event(c, sid, stage, raw, "paste", payload, observed)
    # 官方时间早于建档登记事件（建档时没填投稿日期等）→ 登记事件挪到它前面，官方状态才会成为当前状态
    reg = _registration_event(c, sid)
    if reg and reg["id"] != eid and (reg["observed_at"] or "") > observed:
        before = (db.parse_ts(observed) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE status_events SET observed_at=? WHERE id=?", (before, reg["id"]))
        db.refresh_current(c, sid)
    return eid


@app.post("/api/import/system-paste/confirm")
def system_paste_confirm(p: SysPasteConfirmIn):
    """确认导入：选中的已有稿件记一条官方状态（同一天同状态不重复），对不上的新建投稿。"""
    parsed = _sys_parse(p.text)
    via = _via_payload(p.via, p.ocr_score)
    res = {"updated": 0, "created": 0, "unchanged": 0, "backdated": 0, "ids": []}
    today = date.today().isoformat()
    with db.get_db() as c:
        items = _sys_match(c, parsed["items"], parsed.get("journal") or "")
        seen, made = set(), set()
        for pk in p.picks:
            if not (0 <= pk.index < len(items)) or pk.index in seen:
                raise HTTPException(400, "选择的条目不存在或重复，请重新解析")
            seen.add(pk.index)
            it = items[pk.index]
            if pk.action == "update" and it["match"] and it["action"] in ("update", "same"):
                sid = it["match"]["id"]
                sub = _require_live_sub(c, sid)
                eid = _sys_event(c, sid, it, parsed, via)
                if it.get("manuscript_id") and not sub["manuscript_number"]:
                    c.execute("UPDATE submissions SET manuscript_number=? WHERE id=?", (it["manuscript_id"], sid))
                sd = it.get("submitted_date") or ""
                if sd and sd <= today and not sub["submitted_at"]:
                    c.execute("UPDATE submissions SET submitted_at=? WHERE id=?", (sd, sid))
                    _move_initial_event(c, sid, sd)
                c.execute("UPDATE submissions SET last_synced_at=? WHERE id=?", (db.now_str(), sid))
                res["updated" if eid else "unchanged"] += 1
                if eid and c.execute("SELECT current_stage FROM submissions WHERE id=?", (sid,)).fetchone()[0] != it["stage"]:
                    res["backdated"] += 1          # 时间线上有更晚的记录：记进了历史，当前状态不变
                res["ids"].append(sid)
            elif pk.action == "create" and it["action"] == "create":
                key = syspaste.ms_base(it.get("manuscript_id")) or "t:" + syspaste.title_key(it.get("title"))
                if key in made:                    # 同一页里重复出现的同一篇只建一次
                    res["unchanged"] += 1
                    continue
                made.add(key)
                venue = strip_invisible(pk.venue_name or it.get("journal") or parsed.get("journal") or "")
                if not venue:
                    raise HTTPException(400, f"《{it['title'][:40]}》缺少期刊名，请补上后再导入")
                sd = it.get("submitted_date") or ""
                sid = _insert_submission(c, SubmissionIn(
                    title=it["title"], manuscript_number=it.get("manuscript_id") or "",
                    submitted_at=sd if sd and sd <= today else "",
                    notes=f"从 {parsed.get('system_label') or '投稿系统'} 页面导入",
                    venue=VenueIn(name=venue, type="journal", tracker="manual")))
                _sys_event(c, sid, it, parsed, via)
                res["created"] += 1
                res["ids"].append(sid)
            else:
                raise HTTPException(400, f"第 {pk.index + 1} 条不能「{pk.action}」，请重新解析后再选")
    return res


# ---------- 导出台账 ----------
_TRACKER_LABEL = {"manual": "手动", "openreview": "OpenReview", "elsevier": "爱思唯尔"}
_TYPE_LABEL = {"journal": "期刊", "conference": "会议"}
# 英文界面导出（?lang=en）：表头、阶段、类型、渠道、关系、徽章翻成英文；用户数据（标题、备注……）原样
_CSV_HEADER = ["ID", "标题", "作者", "会议/期刊", "类型", "年份", "CCF", "中科院分区", "新锐分区",
               "JCR", "预警", "追踪通道", "稿件编号", "当前状态", "原始状态", "投稿日期", "结果日期",
               "在审天数", "当前阶段停留天数", "返修截止", "会议截止", "相关投稿", "归档", "备注",
               "最近更新"]
_CSV_HEADER_EN = ["ID", "Title", "Authors", "Venue", "Type", "Year", "CCF", "CAS tier", "XR tier",
                  "JCR", "Warning", "Channel", "Manuscript number", "Status", "Raw status", "Submitted", "Decided",
                  "Days in review", "Days in current stage", "Revision due", "Venue deadline", "Related", "Archived",
                  "Notes", "Last updated"]
_TRACKER_LABEL_EN = {"manual": "Manual", "openreview": "OpenReview", "elsevier": "Elsevier"}
_TYPE_LABEL_EN = {"journal": "Journal", "conference": "Conference"}
_RELATION_EN = {"转投": "Resubmission", "扩写": "Extension", "会议版·期刊版": "Conference/journal version",
                "同数据集": "Same dataset", "同方法": "Same method"}


def _badge_en(label: str) -> str:
    """中科院1区Top → CAS Q1 Top；新锐2区 → XR Q2；⚠ 预警2025 → Warning 2025；其余原样。"""
    m = re.match(r"^(中科院|新锐)(\d)区(Top)?$", label)
    if m:
        return f"{'CAS' if m.group(1) == '中科院' else 'XR'} Q{m.group(2)}" + (" Top" if m.group(3) else "")
    m = re.match(r"^预警(\d{4})$", label)
    if m:
        return f"Warning {m.group(1)}"
    if label.startswith("新锐预警"):
        return "XR warning" + label[len("新锐预警"):].replace("·观察中", " · watch list").replace("：", ": ")
    return label


def _csv_cell(v):
    """防 CSV 公式注入：以 = + - @（含全角 ＝＋－＠、前导空格后）开头或以制表/换行开头的文本前加单引号。"""
    if isinstance(v, str) and v:
        s = unicodedata.normalize("NFKC", v).lstrip()
        if v[0] in "\t\r\n" or s[:1] in ("=", "+", "-", "@"):
            return "'" + v
    return v


@app.get("/api/export.csv")
def export_csv(stage: str = "", q: str = "", category: str = "", sort: str = "updated", lang: str = ""):
    """导出台账（Excel 直接打开）。带 category / stage / q / sort 时只导出当前列表里看到的那些；lang=en 用英文表头与标签。"""
    en = lang == "en"
    with db.get_db() as c:
        rows = [_sub_row(c, i) for i in _filtered_ids(c, stage, q, category, sort)]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER_EN if en else _CSV_HEADER)
    type_label, tracker_label = (_TYPE_LABEL_EN, _TRACKER_LABEL_EN) if en else (_TYPE_LABEL, _TRACKER_LABEL)
    for r in rows:
        rel = "; ".join(f"{_RELATION_EN.get(x['relation'], x['relation']) if en else x['relation']} #{x['id']}"
                        for x in r.get("related") or [])
        by = {}
        for b in r.get("badges") or []:
            lab = str(b.get("label") or "")
            by.setdefault(b.get("scope"), []).append(_badge_en(lab.replace("⚠", "").strip()) if en else lab)
        badge = lambda k: " / ".join(by.get(k, []))
        writer.writerow([_csv_cell(x) for x in (
            r["id"], r["title"], "; ".join(r["authors"]), r.get("venue_name", ""),
            type_label.get(r.get("venue_type") or "", r.get("venue_type") or ""), r.get("venue_year", ""),
            badge("ccf"), badge("cas"), badge("xr"), badge("jcr"),
            " / ".join(x.replace("⚠", "").strip() for x in by.get("warn", [])),
            tracker_label.get(r.get("venue_tracker") or "", r.get("venue_tracker") or ""),
            r["manuscript_number"],
            STAGES.get(r["current_stage"], {}).get("label_en" if en else "label", r["current_stage"]),
            r["current_raw"], r["submitted_at"], (r.get("result_at") or "")[:10],
            "" if r.get("days_total") is None else r["days_total"],
            "" if r.get("days_in_stage") is None else r["days_in_stage"],
            r["revise_due"], r.get("venue_ddl", ""), rel, ("yes" if en else "是") if r.get("archived") else "",
            r["notes"], r["updated_at"],
        )])
    data = "\ufeff" + buf.getvalue()  # UTF-8 BOM，Excel 直接打开不乱码
    tag = category if category in CATEGORY_STAGES or category in ("archived", "trash") else ""
    fname = f"submissions{'-' + tag if tag else ''}-{date.today():%Y%m%d}.csv"
    return StreamingResponse(iter([data]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


# ---------- 变化区：自上次查看以来的自动状态变化 + 账号同步发现的待建档投稿 ----------
def _is_auto_change(e) -> bool:
    """渠道自动检测到的状态变化才算「新变化」；手动登记、官方页粘贴、建档导入时写入的不算。"""
    if e["source"] not in ("openreview", "elsevier"):
        return False
    try:
        pl = json.loads(e["payload"]) if e["payload"] else {}
    except Exception:
        pl = {}
    if not isinstance(pl, dict):
        return True
    return not (pl.get("sync_import") or pl.get("imported") or pl.get("official_paste")
                or pl.get("manual_query"))


@app.get("/api/changes")
def list_changes(limit: int = 30):
    """铃铛与总览「自上次查看以来」：unread = 新变化条数 + 新发现的待建档篇数。"""
    limit = max(1, min(limit, 200))
    with db.get_db() as c:
        seen = db.meta_get(c, "last_seen_at", "")
        rows = c.execute(
            "SELECT e.id, e.submission_id, e.stage, e.raw_status, e.source, e.observed_at,"
            " e.recorded_at, e.payload, s.title, s.current_stage, v.name AS vname, v.year AS vyear"
            " FROM status_events e JOIN submissions s ON s.id=e.submission_id"
            " LEFT JOIN venues v ON v.id=s.venue_id"
            " WHERE COALESCE(s.deleted_at,'')='' AND COALESCE(s.archived,0)=0"
            " AND COALESCE(e.recorded_at,'') > ?"
            " ORDER BY e.recorded_at DESC, e.id DESC", (seen,)).fetchall()
        items = []
        for e in rows:
            if not _is_auto_change(e):
                continue
            prev = c.execute(
                "SELECT stage FROM status_events WHERE submission_id=? AND"
                " (observed_at < ? OR (observed_at = ? AND id < ?))"
                " ORDER BY observed_at DESC, id DESC LIMIT 1",
                (e["submission_id"], e["observed_at"], e["observed_at"], e["id"])).fetchone()
            items.append({
                "id": e["id"], "submission_id": e["submission_id"], "title": e["title"],
                "venue": vu.display(e["vname"] or "", e["vyear"] or ""),
                "stage": e["stage"], "prev_stage": prev["stage"] if prev else "",
                "raw_status": e["raw_status"], "source": e["source"],
                "observed_at": e["observed_at"], "recorded_at": e["recorded_at"] or "",
                "current_stage": e["current_stage"]})
        pending = []
        for p in c.execute(
                "SELECT forum, data, first_seen_at FROM pending_imports WHERE dismissed=0"
                " AND forum NOT IN (SELECT openreview_forum FROM submissions"
                "                   WHERE COALESCE(openreview_forum,'')<>'')"
                " ORDER BY first_seen_at DESC").fetchall():
            try:
                d = json.loads(p["data"])
            except Exception:
                continue
            if isinstance(d, dict):
                pending.append({"forum": p["forum"], "item": d,
                                "first_seen_at": p["first_seen_at"] or "",
                                "new": (p["first_seen_at"] or "") > seen})
        n_dismissed = c.execute("SELECT COUNT(*) FROM pending_imports WHERE dismissed=1").fetchone()[0]
        last_sync = c.execute("SELECT ok, error, created_at FROM poll_logs"
                              " WHERE channel='openreview-sync' ORDER BY id DESC LIMIT 1").fetchone()
        acc = c.execute("SELECT secret FROM accounts WHERE channel='openreview'").fetchone()
    account = {"bound": bool(acc), "needs_rebind": False,
               "encrypted": bool(acc) and vault.is_protected(acc["secret"] or "")}
    if account["encrypted"]:
        try:
            vault.reveal(acc["secret"])
        except vault.VaultError:
            account["needs_rebind"] = True
    # 「全部标为已读」的游标 = 本次返回的最新一条（而不是墙上时间）：之后才提交的变化不会被一并标掉
    cursor = max([seen] + [i["recorded_at"] for i in items[:1]]
                 + [p["first_seen_at"] for p in pending if p["new"]])
    return {"now": cursor, "seen_at": seen, "total": len(items), "changes": items[:limit],
            "unread": len(items) + sum(1 for p in pending if p["new"]),
            "pending": pending, "pending_dismissed": n_dismissed,
            "last_sync": dict(last_sync) if last_sync else None, "account": account}


class SeenIn(BaseModel):
    until: str = ""        # 只把这个时间（GET /api/changes 返回的 now）之前的标为已读
    reset: bool = False    # 撤销「全部标为已读」：把上次查看时间设回 until


_SEEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{1,6})?$")


@app.post("/api/changes/seen")
def mark_changes_seen(b: SeenIn):
    limit_ts = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    until = (b.until or "").strip()
    if b.reset and until == "":
        pass                                # 撤销到「从未查看」：全部重新显示为未读
    elif not _SEEN_RE.match(until) or until > limit_ts:
        if b.reset:
            raise HTTPException(400, "时间格式不对")
        until = db.now_us()
    with db.get_db() as c:
        prev = db.meta_get(c, "last_seen_at", "")
        if b.reset or until > prev:
            db.meta_set(c, "last_seen_at", until)
        cur = db.meta_get(c, "last_seen_at", "")
    return {"ok": True, "seen_at": cur, "previous": prev}


class DismissIn(BaseModel):
    forum: str
    dismissed: bool = True


@app.post("/api/pending-imports/dismiss")
def dismiss_pending(b: DismissIn):
    """忽略 / 取消忽略一条待建档（忽略后同步也不会再提示）。"""
    with db.get_db() as c:
        cur = c.execute("UPDATE pending_imports SET dismissed=? WHERE forum=?",
                        (1 if b.dismissed else 0, b.forum.strip()))
        if cur.rowcount == 0:
            raise HTTPException(404, "没有这条待建档记录（可能已经建档，或已被同步清理）")
    return {"ok": True}


class ForumsIn(BaseModel):
    forums: list[str] | None = None


@app.post("/api/pending-imports/dismiss-all")
def dismiss_all_pending():
    """把当前所有待建档一次忽略（返回被忽略的 forum，供「撤销」）。"""
    with db.get_db() as c:
        forums = [r["forum"] for r in c.execute(
            "SELECT forum FROM pending_imports WHERE dismissed=0 AND forum NOT IN"
            " (SELECT openreview_forum FROM submissions WHERE COALESCE(openreview_forum,'')<>'')")]
        for f in forums:
            c.execute("UPDATE pending_imports SET dismissed=1 WHERE forum=?", (f,))
    return {"ok": True, "forums": forums}


@app.post("/api/pending-imports/undismiss-all")
def undismiss_all_pending(b: ForumsIn | None = None):
    """恢复显示已忽略的待建档；给了 forums 只恢复这些（「全部忽略」的撤销）。"""
    with db.get_db() as c:
        if b and b.forums is not None:
            n = 0
            for f in b.forums[:500]:
                n += c.execute("UPDATE pending_imports SET dismissed=0 WHERE forum=?", (str(f),)).rowcount
        else:
            n = c.execute("UPDATE pending_imports SET dismissed=0 WHERE dismissed=1").rowcount
    return {"ok": True, "restored": n}


# ---------- 本地备份 ----------
BACKUP_KEEP = 14
_backup_lock = threading.Lock()


def _do_backup() -> str:
    with _backup_lock:
        return db.backup_now(keep=BACKUP_KEEP)


def _display_path(p: str) -> str:
    """界面上显示的路径：数据目录下的用相对路径，其余把用户主目录换成 ~（截图不泄露用户名）。"""
    try:
        rel = os.path.relpath(p, db.DATA_DIR)
        if not rel.startswith(".."):
            return os.path.join("<数据目录>", rel)
    except ValueError:
        pass
    home = os.path.expanduser("~")
    return ("~" + p[len(home):]) if p.startswith(home) else p


@app.get("/api/backups")
def get_backups():
    with db.get_db() as c:
        last = db.meta_get(c, "daily_backup_at", "")
        err = db.meta_get(c, "daily_backup_error", "")
    return {"dir": db.BACKUP_DIR, "dir_display": _display_path(db.BACKUP_DIR),
            "items": db.list_backups(), "last_auto": last, "keep": BACKUP_KEEP,
            "upgrade_items": db.list_backups("premigrate-"), "last_error": err}


@app.post("/api/backup")
def make_backup():
    """立即备份数据库（不含 PDF 原件）到 data/backups/auto，只保留最近 14 份。"""
    try:
        path = _do_backup()
    except Exception as e:
        raise HTTPException(500, f"备份失败：{type(e).__name__}: {e}")
    return {"ok": True, "file": os.path.basename(path), "dir": db.BACKUP_DIR,
            "items": db.list_backups()}


# ---------- 静态前端 ----------
app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "web"), html=True), name="web")


# ---------- 每日轮询线程 ----------
def _due(key: str, hours: float) -> bool:
    """按墙上时间判断任务是否到期（电脑睡眠/重启后也会补跑；时钟被调回过去也算到期）。"""
    with db.get_db() as c:
        last = db.parse_ts(db.meta_get(c, key, ""))
    now = datetime.now()
    return last is None or last > now + timedelta(hours=1) or \
        (now - last).total_seconds() >= hours * 3600


def _mark_done(key: str, value: str | None = None):
    with db.get_db() as c:
        db.meta_set(c, key, db.now_str() if value is None else value)


def _log_daily_error(channel: str, e: Exception):
    try:
        with db.get_db() as c:
            _log_poll(c, channel, "daily", False, 0, f"{type(e).__name__}: {e}")
    except Exception:
        pass


def _daily_worker():
    time.sleep(90)  # 启动 90 秒后再检查，避免拖慢启动
    while True:
        try:
            _daily_tick()
        except Exception as e:           # 兜底：任何意外都不让后台线程退出
            _log_daily_error("daily", e)
        time.sleep(1800)


def _daily_tick():
    # 1) 备份：成功才记「上次自动备份」；失败的 2 小时后再试
    try:
        if _due("daily_backup_at", 24) and _due("daily_backup_try_at", 2):
            _mark_done("daily_backup_try_at")
            try:
                _do_backup()
            except Exception as e:
                _mark_done("daily_backup_error", f"{db.now_str()} {type(e).__name__}: {e}"[:300])
                raise
            _mark_done("daily_backup_at")
            _mark_done("daily_backup_error", "")
    except Exception as e:
        _log_daily_error("backup", e)
    # 2) 回收站里超过 30 天的彻底删除
    try:
        _purge_old_trash()
    except Exception as e:
        _log_daily_error("trash", e)
    # 3) 账号同步 + 轮询：先记时间（失败也等下一轮，不会每半小时重试登录）
    try:
        if not _due("daily_sync_at", 23):
            return
        _mark_done("daily_sync_at")
    except Exception as e:
        _log_daily_error("openreview-sync", e)
        return
    try:
        if _get_account("openreview"):
            sync_openreview()   # 绑定了账号时：先同步我的全部投稿（一次请求）
    except Exception as e:
        _log_daily_error("openreview-sync", e)
    try:
        if _get_account("openreview") and _poll_lock.acquire(blocking=False):
            try:
                _poll_all(force=False)   # 账号同步刚覆盖过的稿件会跳过
            finally:
                _poll_lock.release()
    except Exception as e:
        _log_daily_error("openreview", e)
    try:
        _cleanup_uploads()
    except Exception as e:
        _log_daily_error("uploads", e)


if os.environ.get("PAPER_TRACKER_NO_DAILY") != "1":
    threading.Thread(target=_daily_worker, daemon=True, name="daily-poll").start()
