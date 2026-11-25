# -*- coding: utf-8 -*-
"""SQLite 存储层：单文件库 + 追加式事件流水。"""
import json
import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime

from . import config

BASE_DIR = config.BASE_DIR
DATA_DIR = config.DATA_DIR            # 可用 PAPER_TRACKER_DATA_DIR / run.py --data-dir 指定
PDF_DIR = os.path.join(DATA_DIR, "pdfs")
DB_PATH = os.path.join(DATA_DIR, "tracker.db")

os.makedirs(PDF_DIR, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  type TEXT DEFAULT 'journal',
  acronym TEXT,
  publisher TEXT,
  tracker TEXT DEFAULT 'manual',
  venue_id TEXT,
  journal_acronym TEXT,
  ddl TEXT,
  rank_data TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  authors TEXT DEFAULT '[]',
  corresponding_author TEXT DEFAULT '',
  corresponding_pinyin TEXT DEFAULT '',
  abstract TEXT DEFAULT '',
  keywords TEXT DEFAULT '[]',
  venue_id INTEGER REFERENCES venues(id),
  manuscript_number TEXT DEFAULT '',
  openreview_forum TEXT DEFAULT '',
  submitted_at TEXT DEFAULT '',
  current_stage TEXT DEFAULT 'submitted',
  current_raw TEXT DEFAULT '',
  revise_due TEXT DEFAULT '',
  notes TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS status_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER REFERENCES submissions(id) ON DELETE CASCADE,
  stage TEXT NOT NULL,
  raw_status TEXT DEFAULT '',
  source TEXT DEFAULT 'manual',
  observed_at TEXT DEFAULT (datetime('now','localtime')),
  payload TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER REFERENCES submissions(id) ON DELETE CASCADE,
  file_path TEXT,
  file_name TEXT,
  parse_result TEXT DEFAULT '',
  parsed_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS poll_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT, target TEXT, ok INTEGER, latency_ms INTEGER, error TEXT,
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT UNIQUE,             -- openreview / elsevier
  email TEXT NOT NULL,
  secret TEXT NOT NULL,             -- 密码或 API token（仅本机存，不外传）
  profile_id TEXT DEFAULT '',
  display_name TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS related_subs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  related_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
  relation TEXT DEFAULT '转投',      -- 转投 / 扩写 / 会议版·期刊版 / 同数据集等自定义
  note TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(submission_id, related_id)
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS pending_imports (
  forum TEXT PRIMARY KEY,            -- 账号同步发现、尚未建档的 OpenReview 稿件
  data TEXT NOT NULL,                -- 同步返回的 discovered 项（JSON）
  first_seen_at TEXT,
  last_seen_at TEXT,
  dismissed INTEGER DEFAULT 0        -- 用户点了「忽略」
);
CREATE TABLE IF NOT EXISTS status_rules (
  key TEXT PRIMARY KEY,              -- stages.status_key() 归一后的原始状态
  raw TEXT NOT NULL,                 -- 第一次见到时的原文
  stage TEXT NOT NULL,
  source TEXT DEFAULT 'user',        -- user = 手动指定；model = 采纳了本地模型的建议
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_events_sub ON status_events(submission_id, observed_at);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    """事务上下文：正常退出提交；块内抛异常（含 HTTPException）则整体回滚。"""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- 时间工具（observed_at 统一为 YYYY-MM-DD HH:MM:SS） ----------
_TS_RE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 写入时间由 SQLite 在语句执行时生成（毫秒）：拿到写锁之后才取时间，
# 所以「先提交的变化时间一定更早」，标记已读不会吞掉晚提交的变化
SQL_NOW_MS = "strftime('%Y-%m-%d %H:%M:%f','now','localtime')"


def now_us() -> str:
    """带微秒的写入时间（recorded_at / 上次查看时间）：同一秒内先后写入的变化也分得清先后。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def parse_ts(s) -> datetime | None:
    """宽松解析 'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM[:SS]' / 'YYYY-MM-DDTHH:MM'。"""
    if not s:
        return None
    m = _TS_RE.match(str(s))
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    except ValueError:
        return None


def norm_ts(s) -> str | None:
    """输入时间 → 规范串；只有日期时：当天 = 现在，往日 = 当日 12:00（绝不落到未来）。"""
    if not s:
        return None
    m = _TS_RE.match(str(s))
    if not m:
        return None
    dt = parse_ts(s)
    if dt is None:
        return None
    if m.group(4) is None:            # 只有日期
        return ts_from_date(dt.strftime("%Y-%m-%d"))
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def ts_from_date(d: str) -> str:
    """日期 → 事件时间：往日取当日 12:00；当天或未来日期（如官方页按北京时间已是「明天」）取现在。
    事件时间绝不落在未来，否则之后登记的状态都会被当成历史补录。"""
    try:
        day = datetime.strptime(str(d)[:10], "%Y-%m-%d")
    except ValueError:
        return now_str()
    if day.date() >= datetime.now().date():
        return now_str()
    return day.strftime("%Y-%m-%d 12:00:00")


def _premigration_backup():
    """有待执行的数据迁移时，先把现有库完整备份一份（data/backups/auto/premigrate-*.db），迁移出意外也能找回。"""
    if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "submissions" not in tables:
                return
            done = {r[0] for r in conn.execute("SELECT key FROM meta")} if "meta" in tables else set()
            pending = [k for k, _ in MIGRATIONS if k not in done]
            if not pending:
                return
            os.makedirs(BACKUP_DIR, exist_ok=True)
            path = os.path.join(BACKUP_DIR, f"premigrate-{pending[0]}-{datetime.now():%Y%m%d-%H%M%S}.db")
            dst = sqlite3.connect(path)
            try:
                conn.backup(dst)
                dst.execute("PRAGMA secure_delete=ON")
                _protect_secrets(dst)       # 备份里也不留明文密码
                dst.commit()
            finally:
                dst.close()
        finally:
            conn.close()
        _prune(BACKUP_DIR, "premigrate-", 3)
    except Exception as e:
        print(f"[warn] 迁移前备份失败（不影响启动）：{type(e).__name__}: {e}", file=sys.stderr)


def _protect_secrets(conn):
    """把库里仍是明文的账号密码改成 DPAPI 密文（幂等；加密失败的保持原样，下次启动再试）。"""
    from . import vault
    try:
        rows = conn.execute("SELECT id, secret, channel FROM accounts").fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        rid, sec, channel = row[0], row[1], row[2] or "openreview"
        if sec and not vault.is_protected(sec):
            enc = vault.protect(sec, key=vault.key_for(channel))
            if enc != sec:
                conn.execute("UPDATE accounts SET secret=? WHERE id=?", (enc, rid))


def _prune(folder: str, prefix: str, keep: int):
    try:
        files = sorted(f for f in os.listdir(folder) if f.startswith(prefix) and f.endswith(".db"))
    except OSError:
        return
    for old in files[:-keep] if keep > 0 else []:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass


def scrub_backup_accounts(channel: str) -> int:
    """解除绑定时把 data/backups 下所有备份库里该渠道的凭据也删掉（secure_delete 覆写）。返回处理的文件数。"""
    n = 0
    for dirpath, _dirs, files in os.walk(os.path.join(DATA_DIR, "backups")):
        for f in files:
            if not f.endswith(".db"):
                continue
            try:
                conn = sqlite3.connect(os.path.join(dirpath, f), timeout=5)
                try:
                    conn.execute("PRAGMA secure_delete=ON")
                    cur = conn.execute("DELETE FROM accounts WHERE channel=?", (channel,))
                    conn.commit()
                    n += 1 if cur.rowcount else 0
                finally:
                    conn.close()
            except sqlite3.Error:
                continue
    return n


def init_db():
    _premigration_backup()
    with get_db() as c:
        c.executescript(SCHEMA)
        # 兼容旧库：venues 补 rank_data / year；submissions 补 archived / last_synced_at
        cols = {r["name"] for r in c.execute("PRAGMA table_info(venues)")}
        if "rank_data" not in cols:
            c.execute("ALTER TABLE venues ADD COLUMN rank_data TEXT DEFAULT ''")
        if "year" not in cols:
            c.execute("ALTER TABLE venues ADD COLUMN year TEXT DEFAULT ''")
        if "submit_url" not in cols:          # 投稿系统链接（EM / ScholarOne / CMT …，一键打开）
            c.execute("ALTER TABLE venues ADD COLUMN submit_url TEXT DEFAULT ''")
        scols = {r["name"] for r in c.execute("PRAGMA table_info(submissions)")}
        if "archived" not in scols:
            c.execute("ALTER TABLE submissions ADD COLUMN archived INTEGER DEFAULT 0")
        if "last_synced_at" not in scols:
            c.execute("ALTER TABLE submissions ADD COLUMN last_synced_at TEXT DEFAULT ''")
        if "deleted_at" not in scols:         # 回收站：非空 = 已删除（30 天后自动清理）
            c.execute("ALTER TABLE submissions ADD COLUMN deleted_at TEXT DEFAULT ''")
        ecols = {r["name"] for r in c.execute("PRAGMA table_info(status_events)")}
        if "recorded_at" not in ecols:        # 写入时间（observed_at 是官方/发生时间）
            c.execute("ALTER TABLE status_events ADD COLUMN recorded_at TEXT DEFAULT ''")
        _run_migrations(c)
        _protect_secrets(c)                   # 每次启动补加密（DPAPI 上次失败的这次再试）
        _ensure_indexes(c)


# ---------- 一次性数据修复（幂等；meta 表记录已执行） ----------
def _migration_fix_20261118(c):
    """修复早期版本写入的脏数据：
    ① OpenReview venue id 去掉 /Submission 等状态后缀（统一为会议基 id）；
    ② 会议名与年份解耦（「ICCV 2025」→ name=ICCV, year=2025），清理「… Conference Submission」尾巴；
    ③ 清空分级缓存（按新匹配规则重算徽章）；
    ④ 作者列表里误抓的 EM 标签行（「Funding Information:」等）删除；
    ⑤ 删除无投稿引用的孤儿 venue；
    ⑥ 当前状态按时间线最新事件重算（不改 updated_at）。
    """
    from . import openreview, venueutil
    for v in c.execute("SELECT id, name, year, venue_id FROM venues").fetchall():
        vid = (v["venue_id"] or "").strip()
        new_vid = openreview.base_venue_id(vid) if vid else ""
        name, year = venueutil.split_year(v["name"] or "", v["year"] or "")
        if not name:
            name = v["name"] or ""
        c.execute("UPDATE venues SET venue_id=?, name=?, year=?, rank_data='' WHERE id=?",
                  (new_vid, name, year, v["id"]))
    for s in c.execute("SELECT id, title, authors FROM submissions").fetchall():
        title = re.sub(r"[​‌‍⁠﻿]", "", s["title"] or "").strip()
        if title and title != s["title"]:
            c.execute("UPDATE submissions SET title=? WHERE id=?", (title, s["id"]))
        try:
            authors = json.loads(s["authors"] or "[]")
        except Exception:
            continue
        if not isinstance(authors, list):
            continue
        clean = [a for a in authors if isinstance(a, str) and a.strip()
                 and not a.strip().endswith((":", "："))]
        if clean != authors:
            c.execute("UPDATE submissions SET authors=? WHERE id=?",
                      (json.dumps(clean, ensure_ascii=False), s["id"]))
    c.execute("DELETE FROM venues WHERE id NOT IN"
              " (SELECT venue_id FROM submissions WHERE venue_id IS NOT NULL)")
    # 旧版把「日期 + 10:00」当事件时间，当天上午会落在未来，导致之后登记的状态被当成历史补录
    now = now_str()
    c.execute("UPDATE status_events SET observed_at=? WHERE observed_at > ?", (now, now))
    # 旧版自动渠道每次同步都追加一条同状态事件：同一来源、阶段/原始状态/审稿人统计都没变的重复记录删掉
    # （只删自动来源 openreview / elsevier；手动登记的一律保留）
    for s in c.execute("SELECT id FROM submissions").fetchall():
        last_by_src = {}
        for e in c.execute("SELECT id, stage, raw_status, source, payload FROM status_events"
                           " WHERE submission_id=? ORDER BY observed_at, id", (s["id"],)).fetchall():
            if e["source"] not in ("openreview", "elsevier"):
                continue
            pl = _jload(e["payload"], {})
            sig = (e["stage"], e["raw_status"] or "",
                   json.dumps(pl.get("reviewers") if isinstance(pl, dict) else None, sort_keys=True))
            if last_by_src.get(e["source"]) == sig:
                c.execute("DELETE FROM status_events WHERE id=?", (e["id"],))
            else:
                last_by_src[e["source"]] = sig
    for s in c.execute("SELECT id FROM submissions").fetchall():
        refresh_current(c, s["id"], touch=False)
    # PDF 路径改存相对路径（pdfs/<文件名>）：项目目录移动/复制后不会失效，也不会误删别处的原件
    for d in c.execute("SELECT id, file_path FROM documents").fetchall():
        fp = d["file_path"] or ""
        if fp and os.path.isabs(fp):
            c.execute("UPDATE documents SET file_path=? WHERE id=?",
                      ("pdfs/" + os.path.basename(fp.replace("\\", "/")), d["id"]))
    # forum 字段里粘贴的整条链接 → 稿件 ID
    for s in c.execute("SELECT id, openreview_forum FROM submissions"
                       " WHERE openreview_forum LIKE '%/%' OR openreview_forum LIKE '%=%'").fetchall():
        c.execute("UPDATE submissions SET openreview_forum=? WHERE id=?",
                  (openreview.normalize_forum(s["openreview_forum"]), s["id"]))


def _ensure_indexes(c):
    # 同一 OpenReview 稿件只能建档一次（并发双击「建档」也不会产生重复）；已有重复数据时跳过
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_sub_forum ON submissions(openreview_forum)"
                  " WHERE openreview_forum IS NOT NULL AND openreview_forum <> ''")
    except sqlite3.IntegrityError:
        pass


def resolve_pdf(file_path: str) -> str | None:
    """documents.file_path → 本机绝对路径；只允许落在本实例的 data/pdfs 里。"""
    if not file_path:
        return None
    fp = file_path.replace("\\", "/")
    name = os.path.basename(fp)
    cand = os.path.join(PDF_DIR, name)
    real = os.path.realpath(cand)
    if os.path.dirname(real) != os.path.realpath(PDF_DIR):
        return None
    return real


def _migration_ux_20261119(c):
    """体验改版：① 旧事件的写入时间按发生时间补齐，并把「上次查看」设为现在（旧事件都算已读）；
    ② 已保存的 OpenReview 密码改为 DPAPI 加密存储。"""
    c.execute("UPDATE status_events SET recorded_at=observed_at"
              " WHERE recorded_at IS NULL OR recorded_at=''")
    meta_set(c, "last_seen_at", now_us())
    _protect_secrets(c)


MIGRATIONS = [
    ("fix-20261118", _migration_fix_20261118),
    ("ux-20261119", _migration_ux_20261119),
]


def meta_get(c, key: str, default: str = "") -> str:
    r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return (r["value"] if r else None) or default


def meta_set(c, key: str, value: str):
    c.execute("INSERT INTO meta (key, value) VALUES (?, ?)"
              " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ---------- 本地备份（sqlite 在线备份：服务运行中也能得到一致快照） ----------
BACKUP_DIR = os.path.join(DATA_DIR, "backups", "auto")


def backup_now(keep: int = 7) -> str:
    """把数据库备份到 data/backups/auto/tracker-时间.db，只保留最近 keep 份；返回备份文件路径。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f"tracker-{datetime.now():%Y%m%d-%H%M%S}.db")
    part = path + ".part"
    try:
        src = connect()
        try:
            dst = sqlite3.connect(part)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(part, path)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    _prune(BACKUP_DIR, "tracker-", keep)
    return path


def list_backups(prefix: str = "tracker-") -> list[dict]:
    if not os.path.isdir(BACKUP_DIR):
        return []
    out = []
    for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if f.startswith(prefix) and f.endswith(".db"):
            p = os.path.join(BACKUP_DIR, f)
            out.append({"file": f, "size": os.path.getsize(p),
                        "created_at": datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M:%S")})
    return out


def _run_migrations(c):
    """逐个执行未做过的迁移；某个迁移意外失败就整体回滚该迁移并跳过（服务照常启动，下次再试）。"""
    done = {r["key"] for r in c.execute("SELECT key FROM meta")}
    for key, fn in MIGRATIONS:
        if key in done:
            continue
        c.execute("SAVEPOINT mig")
        try:
            fn(c)
            c.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, now_str()))
            c.execute("RELEASE mig")
        except Exception as e:
            c.execute("ROLLBACK TO mig")
            c.execute("RELEASE mig")
            print(f"[warn] 数据迁移 {key} 失败，已回滚并跳过：{type(e).__name__}: {e}", file=sys.stderr)


# ---------- 序列化辅助 ----------
def _jload(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def row_to_venue(r):
    return dict(r) if r is not None else None


def row_to_submission(r):
    d = dict(r)
    d["authors"] = _jload(d.get("authors"), [])
    d["keywords"] = _jload(d.get("keywords"), [])
    return d


def row_to_event(r):
    d = dict(r)
    d["payload"] = _jload(d.get("payload"), {})
    return d


# ---------- 事件流水 ----------
def latest_event(conn, submission_id: int, source: str | None = None):
    sql = "SELECT * FROM status_events WHERE submission_id=?"
    args = [submission_id]
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY observed_at DESC, id DESC LIMIT 1"
    return conn.execute(sql, args).fetchone()


def refresh_current(conn, submission_id: int, touch: bool = True):
    """当前状态 = 时间线上最新（observed_at 最大）的事件；补录的历史事件不会覆盖当前状态。"""
    r = latest_event(conn, submission_id)
    if r:
        sql = "UPDATE submissions SET current_stage=?, current_raw=?"
        if touch:
            sql += ", updated_at=datetime('now','localtime')"
        conn.execute(sql + " WHERE id=?", (r["stage"], r["raw_status"] or "", submission_id))
    elif touch:
        conn.execute("UPDATE submissions SET updated_at=datetime('now','localtime') WHERE id=?",
                     (submission_id,))


def add_event(conn, submission_id: int, stage: str, raw: str = "",
              source: str = "manual", payload: dict | None = None,
              observed_at: str | None = None) -> int:
    """追加一条状态事件，并按时间线最新事件刷新 submissions.current_stage。"""
    observed_at = norm_ts(observed_at) or now_str()
    cur = conn.execute(
        "INSERT INTO status_events (submission_id, stage, raw_status, source, observed_at, payload,"
        " recorded_at) VALUES (?,?,?,?,?,?," + SQL_NOW_MS + ")",
        (submission_id, stage, raw or "", source, observed_at,
         json.dumps(payload, ensure_ascii=False) if payload else ""),
    )
    refresh_current(conn, submission_id)
    return cur.lastrowid


def add_event_if_changed(conn, submission_id: int, stage: str, raw: str = "",
                         source: str = "manual", payload: dict | None = None,
                         observed_at: str | None = None,
                         compare_keys: tuple = ()) -> int | None:
    """自动渠道用：与「同一来源的上一条事件」相比阶段/原始状态（及 compare_keys 指定的
    payload 字段）都没变就不再追加——每日同步不刷屏，也不会覆盖用户手动登记的阶段。
    返回新事件 id；无变化返回 None。"""
    last = latest_event(conn, submission_id, source=source)
    if last and last["stage"] == stage and (last["raw_status"] or "") == (raw or ""):
        old = _jload(last["payload"], {})
        old = old if isinstance(old, dict) else {}
        new = payload or {}
        if all(old.get(k) == new.get(k) for k in compare_keys):
            return None
    return add_event(conn, submission_id, stage, raw, source, payload, observed_at)
