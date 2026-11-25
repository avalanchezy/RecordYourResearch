# -*- coding: utf-8 -*-
"""智能功能（可选）：读编辑来信、归类认不出的状态词、用自然语言提问。

原则：规则优先、模型兜底、证据校验、用户确认、记录来源。
- 规则（正则、词典）能确定的就用规则，结果可复现；规则拿不准或有冲突时才看本地模型的判断。
- 模型输出先被 JSON schema 约束格式（app/ai.py），这里再逐项「对照原文」：
  日期、稿号、引文必须真的出现在来信里，问题里的数字必须真的出现在问题里；对不上就丢掉。
- 一切写入都要用户确认；事件里记下是规则、模型还是两者一致得出的结论，以及模型版本。
- 自然语言提问只会落到固定的参数化 SQL 模板上执行（只读），界面把实际执行的 SQL 亮出来。
没装模型时，来信识别和提问会退回纯规则，状态词归类可以手动指定。
"""
from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from datetime import date, datetime, timedelta

from app import ai, db
from app import stages as stages_mod
from app.stages import ACTIVE_STAGES, STAGES, status_key

# ================================================================ 日期工具
_MON = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON_RE = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE_RES = (
    (re.compile(r"(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"), "ymd"),
    (re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]"), "ymd"),
    (re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s-]+" + _MON_RE + r",?[\s-]+(20\d{2})(?!\d)", re.I), "dmy"),
    (re.compile(_MON_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})(?!\d)", re.I), "mdy"),
)


def _mk(y, m, d) -> str:
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return ""


def find_dates(text: str) -> list[tuple[int, int, str]]:
    """文中所有能确定的日期：[(起, 止, YYYY-MM-DD)]，按出现顺序。DD/MM 与 MM/DD 有歧义的写法不认。"""
    out = []
    for rx, kind in _DATE_RES:
        for m in rx.finditer(text):
            g = m.groups()
            if kind == "ymd":
                iso = _mk(g[0], g[1], g[2])
            elif kind == "dmy":
                iso = _mk(g[2], _MON[g[1][:3].lower()], g[0])
            else:
                iso = _mk(g[2], _MON[g[0][:3].lower()], g[1])
            if iso:
                out.append((m.start(), m.end(), iso))
    out.sort()
    dedup, last_end = [], -1
    for s, e, iso in out:                     # 同一处被两个写法匹配到时只留一个
        if s >= last_end:
            dedup.append((s, e, iso))
            last_end = e
    return dedup


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"[​-‏⁠﻿]", "", t)
    return t.replace("\r\n", "\n").replace("\r", "\n")


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


# ================================================================ ① 编辑来信
DECISIONS = {
    # key: (阶段, 中文, English)
    "accept":          ("accepted", "录用", "Accept"),
    "minor_revision":  ("revise", "小修", "Minor revision"),
    "major_revision":  ("revise", "大修", "Major revision"),
    "revise_resubmit": ("revise", "拒稿但鼓励修改后重投", "Reject and resubmit"),
    "reject":          ("rejected", "拒稿", "Reject"),
    "desk_reject":     ("rejected", "直接拒稿（未送外审）", "Desk reject"),
    "withdrawn":       ("withdrawn", "撤稿", "Withdrawn"),
    "other":           (None, "不是决定信（提醒、邀请、回执等）", "Not a decision letter"),
}

_NEG = r"(?<!not\s)(?<!never\s)(?<!cannot\s)(?<!can't\s)(?<!unable to\s)"
_DECISION_RULES = (
    ("desk_reject", re.compile(
        r"desk[\s-]*reject|without\s+(?:sending\s+it\s+(?:out\s+)?for\s+)?(?:external\s+|further\s+|peer\s+)?review"
        r"|not\s+(?:to\s+)?(?:be\s+)?sen[dt]\s+(?:it\s+)?(?:out\s+)?(?:for|to)\s+(?:external\s+|peer\s+)?review"
        r"|未送外审|不送外审|未经外审|直接退稿", re.I)),
    ("revise_resubmit", re.compile(
        r"reject(?:ed|ion)?\s+(?:and|but|with)\s+(?:\w+\s+){0,3}resubmi|revise\s+and\s+resubmit"
        r"|encourage\s+you\s+to\s+(?:revise\s+and\s+)?resubmit|修改后重投|鼓励.{0,6}重新投稿", re.I)),
    ("major_revision", re.compile(r"major\s+revisions?|大修|重大修改|较大修改", re.I)),
    ("minor_revision", re.compile(r"minor\s+revisions?|小修|少量修改|较小修改", re.I)),
    ("accept", re.compile(
        _NEG + r"\b(?:has|have)\s+(?:now\s+)?been\s+accepted|accepted\s+for\s+publication"
        r"|" + _NEG + r"\bis\s+(?:now\s+)?accepted\b|pleased\s+to\s+(?:inform|let)\s+you\s+.{0,80}?\baccept"
        r"|已被?录用|同意录用|接收发表|决定录用", re.I)),
    ("reject", re.compile(
        r"\b(?:unable|not\s+able|cannot|can\s*not)\s+(?:to\s+)?(?:accept|publish|consider\s+(?:it|your))"
        r"|\breject(?:ed|ion)?\b|regret\s+to\s+inform|not\s+(?:be\s+)?(?:suitable|accepted)\s+for\s+publication"
        r"|\bnot\s+(?:been\s+)?accepted\b|拒稿|不予录用|退稿|不能录用|未被录用|未予录用", re.I)),
    ("withdrawn", re.compile(r"\bwithdr[ae]wn?\b|已撤稿|撤回投稿", re.I)),
)
_CONDITIONAL_ACCEPT = re.compile(
    r"accept\w*\W+(?:with|subject\s+to|after|pending|upon)\b.{0,30}?(minor|major)\s+revision", re.I)


_NEGATION = re.compile(r"(?:\bnot|\bnever|cannot|can't|\bunable\s+to|\bno\s+longer|无法|不能|未被|不予|没有被?)\W*(?:\w+\W+){0,2}$",
                       re.I)


def _affirmed(rx, t: str) -> bool:
    """至少有一处命中前面没有否定词（cannot be accepted / 未被录用 不算录用）。"""
    return any(not _NEGATION.search(t[max(0, m.start() - 30):m.start()]) for m in rx.finditer(t))


def rule_decisions(text: str) -> list[str]:
    """规则能认出的全部决定类型（去掉被更具体的说法包含的那些）。"""
    t = _norm(text)
    hits = [k for k, rx in _DECISION_RULES if (_affirmed(rx, t) if k == "accept" else rx.search(t))]
    m = _CONDITIONAL_ACCEPT.search(t)
    if m:                                  # Accept with minor revisions = 小修，不是录用
        hits = [h for h in hits if h != "accept"]
        want = "minor_revision" if m.group(1).lower() == "minor" else "major_revision"
        if want not in hits:
            hits.append(want)
    if "desk_reject" in hits or "revise_resubmit" in hits:
        hits = [h for h in hits if h != "reject"]
    if "revise_resubmit" in hits:          # 「拒稿但可修改重投」里常提到 major revision
        hits = [h for h in hits if h != "major_revision"]
    if "withdrawn" in hits and len(hits) > 1:
        hits = [h for h in hits if h != "withdrawn"]   # 正文里顺带提到「撤稿」不算
    return hits


_DUE_KW = re.compile(r"\bby\b|\bbefore\b|no\s+later\s+than|\bdue\b|deadline|\bwithin\b|截止|之前|以前|前提交|内提交|返修期限",
                     re.I)
_REL_RE = re.compile(r"within\s+(\d{1,3})\s+(day|week|month)s?|(\d{1,3})\s*(天|周|个月)\s*(?:之?内)", re.I)
_DATE_LINE = re.compile(r"^\s*(?:date|sent|日期|发送时间|时间)\s*[:：]\s*(.+)$", re.I | re.M)
_MS_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,15}(?:[-_.][A-Z0-9]{1,12}){2,6}\b")


def letter_date(text: str) -> str:
    """来信日期：Date: 行，或开头 400 字内的第一个日期；不能晚于今天。"""
    t = _norm(text)
    m = _DATE_LINE.search(t[:1500])
    cands = find_dates(m.group(1)) if m else []
    if not cands:
        cands = [d for d in find_dates(t[:400])]
    today = date.today().isoformat()
    for _s, _e, iso in cands:
        if iso <= today:
            return iso
    return ""


def rule_deadlines(text: str, base: str = "") -> list[dict]:
    """返修截止日候选：截止类关键词后 80 字内（或日期后 12 字内跟着「前/之前」）的日期；
    「within 60 days / 60 天内」按来信日期（没有就今天）推算。"""
    t = _norm(text)
    out, seen = [], set()
    kws = [m.start() for m in _DUE_KW.finditer(t)]
    for s, e, iso in find_dates(t):
        near = any(0 <= s - k <= 80 for k in kws) or re.match(r"\s*(?:之?前|以前)", t[e:e + 12])
        if near and iso not in seen:
            seen.add(iso)
            out.append({"date": iso, "how": "stated", "text": t[max(0, s - 40):e + 10].strip()})
    try:
        b = datetime.strptime(base, "%Y-%m-%d").date() if base else date.today()
    except ValueError:
        b = date.today()
    for m in _REL_RE.finditer(t):
        n = int(m.group(1) or m.group(3))
        unit = (m.group(2) or m.group(4) or "").lower()
        days = n * (7 if unit in ("week", "周") else 30 if unit in ("month", "个月") else 1)
        if 0 < days <= 400:
            iso = (b + timedelta(days=days)).isoformat()
            if iso not in seen:
                seen.add(iso)
                out.append({"date": iso, "how": "relative", "text": m.group(0)})
    return out


def _in_text(needle: str, text: str) -> bool:
    n = _squash(needle)
    return bool(n) and n in _squash(text)


_LETTER_SYS = ("You read e-mails that journal or conference editors send to authors and identify the "
               "editorial decision. Reply only with JSON.")
_LETTER_PROMPT = """Decision options:
- accept: the paper is accepted for publication
- minor_revision: accepted after minor revisions / minor revision requested
- major_revision: major revision requested
- revise_resubmit: rejected in its current form but the authors may revise and resubmit
- reject: rejected after review
- desk_reject: rejected by the editor without external review
- withdrawn: the submission was withdrawn
- other: not a decision letter (reminder, acknowledgement, invitation, request for files, ...)

Also extract: the deadline for submitting the revision as YYYY-MM-DD if the letter states a date (else ""),
the manuscript number exactly as written (else ""), and copy the one sentence that states the decision.

Letter:
<<<
{text}
>>>"""
_LETTER_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONS)},
        "deadline": {"type": "string"},
        "manuscript_number": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["decision", "deadline", "manuscript_number", "quote"],
}


def _clip_letter(text: str, limit: int = 3500) -> str:
    """给模型看的部分：开头（决定一般写在前面）+ 带截止关键词的句子。"""
    t = _norm(text).strip()
    if len(t) <= limit:
        return t
    head = t[:limit - 600]
    extra = []
    for m in _DUE_KW.finditer(t[limit - 600:]):
        s = limit - 600 + m.start()
        extra.append(t[max(0, s - 120):s + 160])
        if sum(map(len, extra)) > 550:
            break
    return head + "\n...\n" + "\n...\n".join(extra)


_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 3600


def _cache_key(sid: int, text: str) -> str:
    return f"{sid}:" + hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()


def analyze_letter(text: str, sub: dict | None = None, use_model: bool = True) -> dict:
    """来信 → 预览。sub：当前稿件（用来比对稿号）。返回规则、模型、合并后的建议和各项证据。"""
    t = _norm(text)
    if len(t.strip()) < 20:
        raise ValueError("来信内容太短")
    sent = letter_date(t)
    rules = {"decisions": rule_decisions(t), "deadlines": rule_deadlines(t, sent),
             "manuscript_numbers": sorted(set(_MS_RE.findall(t)))[:5]}
    model = None
    notes = []
    if use_model and ai.enabled():
        try:
            raw = ai.chat_json(_LETTER_SYS, _LETTER_PROMPT.format(text=_clip_letter(t)), _LETTER_SCHEMA,
                               max_tokens=220)
            model = {"decision": raw.get("decision"), "ms": raw.get("_ms"), "model": ai.model_tag()}
            # 证据校验：引文、稿号、日期都要在原文里找得到
            q = (raw.get("quote") or "").strip()
            model["quote"] = q if _in_text(q, t) else ""
            if q and not model["quote"]:
                notes.append("模型给的引文在原文里找不到，已忽略")
            ms = (raw.get("manuscript_number") or "").strip()
            model["manuscript_number"] = ms if ms and _in_text(ms, t) else ""
            dl = (raw.get("deadline") or "").strip()
            ok_dates = {d["date"] for d in rules["deadlines"]} | {iso for _s, _e, iso in find_dates(t)}
            if dl and dl in ok_dates:
                model["deadline"] = dl
            else:
                model["deadline"] = ""
                if dl:
                    notes.append(f"模型给的截止日 {dl} 在原文里找不到，已忽略")
        except ai.AiError as e:
            model = {"error": str(e)}

    rd = rules["decisions"]
    md = (model or {}).get("decision") if model and not model.get("error") else None
    if len(rd) == 1 and md:
        decision, agreement = rd[0], ("agree" if md == rd[0] else "disagree")
    elif len(rd) == 1:
        decision, agreement = rd[0], "rules_only"
    elif md:
        decision, agreement = md, ("model_only" if not rd else ("model_pick" if md in rd else "model_only"))
    else:
        decision, agreement = (rd[0] if rd else "other"), ("ambiguous" if len(rd) > 1 else "none")

    dls = rules["deadlines"]
    deadline, deadline_how = "", ""
    if model and model.get("deadline"):
        deadline, deadline_how = model["deadline"], "model"
    elif dls:
        deadline, deadline_how = dls[0]["date"], dls[0]["how"]
    stage = DECISIONS[decision][0]
    if stage != "revise":
        deadline, deadline_how = "", ""

    msn = (model or {}).get("manuscript_number") or (rules["manuscript_numbers"][0]
                                                     if len(rules["manuscript_numbers"]) == 1 else "")
    warning = ""
    if sub and sub.get("manuscript_number") and msn:
        if _squash(msn) != _squash(sub["manuscript_number"]) and not _in_text(sub["manuscript_number"], t):
            warning = f"来信里的稿号 {msn} 与这篇稿件的 {sub['manuscript_number']} 不一致，确认是不是贴错了稿件"
    via = {"agree": "rules+model", "rules_only": "rules", "disagree": "rules", "model_only": "model",
           "model_pick": "rules+model", "ambiguous": "rules", "none": "rules"}[agreement]
    res = {
        "decision": decision, "decision_label": DECISIONS[decision][1], "decision_label_en": DECISIONS[decision][2],
        "stage": stage, "deadline": deadline, "deadline_how": deadline_how,
        "letter_date": sent, "manuscript_number": msn, "warning": warning,
        "quote": (model or {}).get("quote", ""), "agreement": agreement, "via": via,
        "rules": rules, "model": model, "notes": notes,
        "options": [{"key": k, "label": v[1], "label_en": v[2], "stage": v[0]} for k, v in DECISIONS.items()],
    }
    if sub:
        now = time.time()
        for k in [k for k, (ts, _v) in _CACHE.items() if now - ts > _CACHE_TTL]:
            _CACHE.pop(k, None)
        _CACHE[_cache_key(sub["id"], t)] = (now, res)
    return res


def cached_analysis(sid: int, text: str) -> dict | None:
    hit = _CACHE.get(_cache_key(sid, text))
    return hit[1] if hit and time.time() - hit[0] <= _CACHE_TTL else None


# ================================================================ ③ 状态词规则
def load_status_rules():
    """把 status_rules 表载入 stages.USER_RULES（normalize 优先查它）。"""
    with db.get_db() as c:
        rows = c.execute("SELECT key, stage FROM status_rules").fetchall()
    stages_mod.USER_RULES.clear()
    stages_mod.USER_RULES.update({r["key"]: r["stage"] for r in rows if r["stage"] in STAGES})


def list_status_rules() -> list[dict]:
    with db.get_db() as c:
        return [dict(r) for r in c.execute(
            "SELECT key, raw, stage, source, created_at FROM status_rules ORDER BY created_at DESC")]


def save_status_rule(raw: str, stage: str, source: str = "user") -> dict:
    key = status_key(raw)
    if not key or len(key) > 200:
        raise ValueError("状态文字为空或太长")
    if stage not in STAGES:
        raise ValueError("未知阶段")
    if source not in ("user", "model"):
        source = "user"
    with db.get_db() as c:
        c.execute("INSERT INTO status_rules (key, raw, stage, source) VALUES (?,?,?,?)"
                  " ON CONFLICT(key) DO UPDATE SET stage=excluded.stage, source=excluded.source",
                  (key, raw.strip()[:200], stage, source))
    load_status_rules()
    return {"key": key, "stage": stage, "source": source}


def delete_status_rule(key: str):
    with db.get_db() as c:
        c.execute("DELETE FROM status_rules WHERE key=?", (key,))
    load_status_rules()


_STATUS_SYS = "You map status strings from journal and conference submission systems to lifecycle stages. Reply only with JSON."
_STATUS_PROMPT = """Stages of a paper submission, from the author's point of view:
- submitted: just submitted, technical check, returned to the author to fix files
- editor: with the editor, editor assignment, waiting for reviewers to be assigned
- review: under review, reviewers invited or assigned
- reviews_done: all reviews returned, waiting for the editor's recommendation
- decision: the decision is being made
- revise: revision requested or revision in progress
- accepted: accepted
- rejected: rejected
- withdrawn: withdrawn by the authors
- production: in production, proofs, published
- unknown: cannot tell

Status shown by the system: "{raw}"
Which stage is it?"""
_STATUS_SCHEMA = {"type": "object", "properties": {"stage": {"type": "string", "enum": list(STAGES) + ["unknown"]}},
                  "required": ["stage"]}


def suggest_stage(raw: str) -> dict:
    """规则认得就直接给规则结果；认不得再问模型（模型可以回答 unknown）。"""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("状态文字为空")
    key = status_key(raw)
    if key in stages_mod.USER_RULES:
        return {"raw": raw, "key": key, "stage": stages_mod.USER_RULES[key], "source": "user_rule"}
    st = stages_mod.normalize(raw)
    if st:
        return {"raw": raw, "key": key, "stage": st, "source": "rules"}
    if not ai.enabled():
        return {"raw": raw, "key": key, "stage": None, "source": "none",
                "hint": "本地模型没有启用，可以手动选一个阶段"}
    try:
        out = ai.chat_json(_STATUS_SYS, _STATUS_PROMPT.format(raw=raw[:200].replace('"', "'")), _STATUS_SCHEMA,
                           max_tokens=24)
    except ai.AiError as e:
        return {"raw": raw, "key": key, "stage": None, "source": "error", "hint": str(e)}
    st = out.get("stage")
    return {"raw": raw, "key": key, "stage": st if st in STAGES else None, "source": "model",
            "model": ai.model_tag(), "ms": out.get("_ms")}


# ================================================================ ② 自然语言提问 → 固定的参数化 SQL
LIVE = "COALESCE(s.archived,0)=0 AND COALESCE(s.deleted_at,'')=''"
VENUE = "COALESCE(NULLIF(v.acronym,''), v.name, '')"
COLS = f"s.id AS id, s.title AS title, {VENUE} AS venue, s.current_stage AS stage"
FROM = "FROM submissions s LEFT JOIN venues v ON v.id = s.venue_id"
OUTCOMES = {"accepted": ("accepted", "production"), "rejected": ("rejected",), "revise": ("revise",),
            "any": ("accepted", "rejected", "revise")}
INTENTS = ("list_stage", "stuck", "count_by", "submitted_in", "outcomes_recent", "review_time",
           "deadlines", "search")
DIMS = ("stage", "venue", "year", "outcome")
_MAX_ROWS = 200


def _ph(n: int) -> str:
    return ",".join("?" * n)


def build_query(intent: str, p: dict) -> tuple[str, list, dict]:
    """意图 + 参数 → (SQL, 绑定参数, 中英文解释)。SQL 都是写死的模板，只读。"""
    st, days, year, month = p.get("stage") or "", p.get("days") or 0, p.get("year") or 0, p.get("month") or 0
    if intent == "list_stage":
        if st not in STAGES:
            raise ValueError("没听出是哪个阶段")
        return (f"SELECT {COLS}, s.updated_at AS updated {FROM} WHERE {LIVE} AND s.current_stage = ?"
                " ORDER BY s.updated_at DESC", [st],
                {"zh": f"当前处于「{STAGES[st]['label']}」的稿件", "en": f"Papers currently {STAGES[st]['label_en'].lower()}"})
    if intent == "stuck":
        days = days or 60
        sts = [st] if st in STAGES else list(ACTIVE_STAGES)
        since = ("(SELECT MIN(e.observed_at) FROM status_events e WHERE e.submission_id = s.id"
                 " AND e.observed_at > COALESCE((SELECT MAX(e2.observed_at) FROM status_events e2"
                 " WHERE e2.submission_id = s.id AND e2.stage <> s.current_stage), ''))")
        sql = ("SELECT id, title, venue, stage, since,"
               " CAST(julianday('now','localtime') - julianday(since) AS INTEGER) AS days"
               f" FROM (SELECT {COLS}, {since} AS since {FROM} WHERE {LIVE}"
               f" AND s.current_stage IN ({_ph(len(sts))}))"
               " WHERE since IS NOT NULL AND julianday('now','localtime') - julianday(since) > ?"
               " ORDER BY since")
        where_zh = f"「{STAGES[st]['label']}」" if st in STAGES else "进行中的阶段"
        where_en = STAGES[st]["label_en"].lower() if st in STAGES else "an active stage"
        return (sql, sts + [days],
                {"zh": f"在{where_zh}停留超过 {days} 天的稿件", "en": f"Papers stuck in {where_en} for more than {days} days"})
    if intent == "count_by":
        dim = p.get("dim") if p.get("dim") in DIMS else "stage"
        yr = f" AND substr(COALESCE(NULLIF(s.submitted_at,''), s.created_at), 1, 4) = ?" if year else ""
        args = [str(year)] if year else []
        expr = {"stage": "s.current_stage", "venue": f"COALESCE(NULLIF({VENUE},''), '—')",
                "year": "substr(COALESCE(NULLIF(s.submitted_at,''), s.created_at), 1, 4)",
                "outcome": "CASE WHEN s.current_stage IN ('accepted','production') THEN 'accepted'"
                           " WHEN s.current_stage = 'rejected' THEN 'rejected'"
                           " WHEN s.current_stage = 'withdrawn' THEN 'withdrawn' ELSE 'in_progress' END"}[dim]
        name = {"stage": "stage", "venue": "venue", "year": "year", "outcome": "outcome"}[dim]
        zh = {"stage": "阶段", "venue": "期刊/会议", "year": "投稿年份", "outcome": "结果"}[dim]
        return (f"SELECT {expr} AS {name}, COUNT(*) AS n {FROM} WHERE {LIVE}{yr} GROUP BY 1 ORDER BY n DESC, 1",
                args, {"zh": f"按{zh}统计稿件数" + (f"（{year} 年投稿）" if year else ""),
                       "en": f"Number of papers by {name}" + (f" (submitted in {year})" if year else "")})
    if intent == "submitted_in":
        year = year or date.today().year
        args = [str(year)]
        mon = ""
        if 1 <= month <= 12:
            mon = " AND substr(s.submitted_at, 6, 2) = ?"
            args.append(f"{month:02d}")
        return (f"SELECT {COLS}, s.submitted_at AS submitted_at {FROM} WHERE {LIVE}"
                f" AND substr(s.submitted_at, 1, 4) = ?{mon} ORDER BY s.submitted_at", args,
                {"zh": f"{year} 年" + (f" {month} 月" if mon else "") + "投出的稿件",
                 "en": f"Papers submitted in {year}" + (f"-{month:02d}" if mon else "")})
    if intent == "outcomes_recent":
        days = days or 30
        oc = p.get("outcome") if p.get("outcome") in OUTCOMES else "any"
        sts = list(OUTCOMES[oc])
        ocz = {"accepted": "录用", "rejected": "拒稿", "revise": "返修", "any": "决定（录用/返修/拒稿）"}[oc]
        return (f"SELECT {COLS.replace('s.current_stage AS stage', 'e.stage AS stage')}, e.observed_at AS at"
                f" FROM status_events e JOIN submissions s ON s.id = e.submission_id"
                f" LEFT JOIN venues v ON v.id = s.venue_id WHERE {LIVE} AND e.stage IN ({_ph(len(sts))})"
                " AND e.observed_at >= datetime('now','localtime', ?) ORDER BY e.observed_at DESC",
                sts + [f"-{days} days"],
                {"zh": f"最近 {days} 天收到的{ocz}", "en": f"{oc.replace('any', 'Decisions').capitalize()} received in the last {days} days"})
    if intent == "review_time":
        txt = (p.get("text") or "").strip()
        filt, args = "", []
        if txt:
            filt = " AND (v.name LIKE ? ESCAPE '\\' OR v.acronym LIKE ? ESCAPE '\\')"
            like = "%" + re.sub(r"([%_\\])", r"\\\1", txt) + "%"
            args = [like, like]
        return (f"SELECT venue, COUNT(*) AS n, CAST(ROUND(AVG(days)) AS INTEGER) AS avg_days,"
                " CAST(MIN(days) AS INTEGER) AS min_days, CAST(MAX(days) AS INTEGER) AS max_days FROM ("
                f"SELECT COALESCE(NULLIF({VENUE},''), '—') AS venue, julianday((SELECT MIN(e.observed_at)"
                " FROM status_events e WHERE e.submission_id = s.id AND e.stage IN ('revise','accepted','rejected')))"
                f" - julianday(s.submitted_at) AS days {FROM} WHERE {LIVE} AND COALESCE(s.submitted_at,'') <> ''{filt})"
                " WHERE days IS NOT NULL AND days >= 0 GROUP BY venue ORDER BY n DESC, venue", args,
                {"zh": "从投稿到第一次决定平均用了多少天（按期刊/会议）" + (f"，只看「{txt}」" if txt else ""),
                 "en": "Days from submission to the first decision, per venue" + (f" (matching “{txt}”)" if txt else "")})
    if intent == "deadlines":
        days = days or 30
        return (f"SELECT {COLS}, s.revise_due AS revise_due, CAST(julianday(s.revise_due) -"
                f" julianday(date('now','localtime')) AS INTEGER) AS days {FROM} WHERE {LIVE}"
                " AND s.current_stage = 'revise' AND COALESCE(s.revise_due,'') <> ''"
                " AND s.revise_due <= date('now','localtime', ?) ORDER BY s.revise_due", [f"+{days} days"],
                {"zh": f"返修截止日在 {days} 天内（含已过期）的稿件",
                 "en": f"Revision deadlines within {days} days (overdue included)"})
    if intent == "search":
        txt = (p.get("text") or "").strip()
        if not txt:
            raise ValueError("没听出要找什么")
        like = "%" + re.sub(r"([%_\\])", r"\\\1", txt) + "%"
        return (f"SELECT {COLS}, s.manuscript_number AS manuscript_number {FROM} WHERE {LIVE} AND (s.title LIKE ?"
                " ESCAPE '\\' OR v.name LIKE ? ESCAPE '\\' OR v.acronym LIKE ? ESCAPE '\\'"
                " OR s.manuscript_number LIKE ? ESCAPE '\\') ORDER BY s.updated_at DESC", [like] * 4,
                {"zh": f"标题、期刊或稿号里含「{txt}」的稿件", "en": f"Papers whose title, venue or number contains “{txt}”"})
    raise ValueError("不支持的问题类型")


def show_sql(sql: str, args: list) -> str:
    """把绑定参数代进 SQL，只用于展示。"""
    it = iter(args)

    def lit(_m):
        v = next(it)
        return str(v) if isinstance(v, int) else "'" + str(v).replace("'", "''") + "'"
    return re.sub(r"\?", lit, sql)


# ---- 规则：从问题里抽数字、年份、阶段、意图（没有模型时用；有模型时用来核对模型给的参数）
_STAGE_WORDS = [(st, [STAGES[st]["label"], STAGES[st]["label_en"].lower()]) for st in STAGES] + [
    ("review", ["审稿中", "在审", "外审", "under review", "in review"]),
    ("revise", ["大修", "小修", "修改", "revision", "revise"]),
    ("accepted", ["接收", "录用", "accepted", "accept"]),
    ("rejected", ["被拒", "拒稿", "rejected", "reject"]),
    ("editor", ["编辑手里", "with editor", "editor"]),
]


def rule_params(q: str) -> dict:
    t = _norm(q).lower()
    out: dict = {}
    for m in re.finditer(r"(\d{1,4})\s*(天|days?|周|weeks?|个月|months?|年|years?)(?![a-z])", t):
        n, u = int(m.group(1)), m.group(2)
        if u in ("年", "year", "years"):
            if n >= 1900:                                  # 「2026 年」是年份，不是时长
                continue
            out["days"] = n * 365
        else:
            out["days"] = n * (7 if u[0] in "周w" else 30 if u in ("个月", "month", "months") else 1)
        break
    if "days" in out:
        pass
    elif re.search(r"半年|half a year", t):
        out["days"] = 182
    elif re.search(r"三个月|three months", t):
        out["days"] = 90
    y = re.search(r"(?<!\d)(20\d{2})(?!\d)", t)
    if y:
        out["year"] = int(y.group(1))
    elif re.search(r"今年|this year", t):
        out["year"] = date.today().year
    elif re.search(r"去年|last year", t):
        out["year"] = date.today().year - 1
    mo = re.search(r"(?<!\d)(1[0-2]|[1-9])\s*月(?!份?\s*内)", t)
    if mo:
        out["month"] = int(mo.group(1))
    for st, words in _STAGE_WORDS:
        if any(w in t for w in words):
            out["stage"] = st
            break
    if re.search(r"录用|接收|accept", t):
        out["outcome"] = "accepted"
    elif re.search(r"拒|reject", t):
        out["outcome"] = "rejected"
    elif re.search(r"返修|修改|revis", t):
        out["outcome"] = "revise"
    if re.search(r"按期刊|按会议|by venue|per venue|每个期刊|哪个期刊", t):
        out["dim"] = "venue"
    elif re.search(r"按年|每年|by year|per year", t):
        out["dim"] = "year"
    elif re.search(r"结果|outcome|录用率|acceptance", t):
        out["dim"] = "outcome"
    return out


def rule_intent(q: str, p: dict) -> str:
    t = _norm(q).lower()
    if re.search(r"截止|deadline|\bdue\b|到期", t):
        return "deadlines"
    if re.search(r"多久|多长时间|平均|周期|how long|average|turnaround", t):
        return "review_time"
    if re.search(r"超过|以上|一直|卡|longer than|more than|over \d|stuck|still", t) and p.get("days"):
        return "stuck"
    if re.search(r"多少|几篇|统计|分布|how many|count|number of", t):
        return "count_by"
    if re.search(r"最近|近\d|recent|last \d|past \d", t) and p.get("outcome"):
        return "outcomes_recent"
    if re.search(r"投了|投的|投出|submitted in|submitted during", t) and p.get("year"):
        return "submitted_in"
    if p.get("stage"):
        return "list_stage"
    return "search"


_ASK_SYS = "You turn a researcher's question about their own paper submissions into one query intent with parameters. Reply only with JSON."
_ASK_PROMPT = """Intents:
- list_stage: papers currently in a stage (set stage)
- stuck: papers that have stayed in their current stage for more than N days (set days; set stage if one is named)
- count_by: count papers grouped by stage, venue, year or outcome (set dim)
- submitted_in: papers submitted in a year, optionally a month (set year, month)
- outcomes_recent: decisions received in the last N days (set outcome accepted / rejected / revise / any, and days)
- review_time: average time from submission to the first decision, per venue (set text to a venue name if one is named)
- deadlines: revision deadlines in the next N days (set days)
- search: find papers whose title, venue or manuscript number contains some text (set text)
Stages: {stages}.
Leave unused fields as "" or 0. Convert months to days (1 month = 30 days). Today is {today}.

Question: {q}"""
_ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "stage": {"type": "string", "enum": list(STAGES) + [""]},
        "days": {"type": "integer"},
        "year": {"type": "integer"},
        "month": {"type": "integer"},
        "dim": {"type": "string", "enum": list(DIMS) + [""]},
        "outcome": {"type": "string", "enum": list(OUTCOMES) + [""]},
        "text": {"type": "string"},
    },
    "required": ["intent", "stage", "days", "year", "month", "dim", "outcome", "text"],
}


def _strip_question(q: str) -> str:
    """search 意图兜底：去掉问句里的常见虚词，剩下的当关键词。"""
    t = re.sub(r"[？?。.!！,，]", " ", q)
    t = re.sub(r"(?i)\b(find|show|search|list|my|papers?|submissions?|the|about|for|with|in|on)\b", " ", t)
    t = re.sub(r"找|查|搜|一下|我的|投稿|稿件|论文|有哪些|哪些|关于|的", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _venue_in(q: str) -> str:
    """问题里出现的、库里已有的期刊/会议名（全称或缩写，取最长的那个）。"""
    t = _squash(q)
    best = ""
    with db.get_db() as c:
        for r in c.execute("SELECT DISTINCT name, acronym FROM venues"):
            for cand in (r["name"] or "", r["acronym"] or ""):
                cs = _squash(cand)
                if len(cs) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(cs) + r"(?![a-z0-9])", t) \
                        and len(cand) > len(best):
                    best = cand
    return best


def plan_question(q: str, use_model: bool = True) -> dict:
    """问题 → {intent, params, via, model, notes}。模型给的数字必须在问题里出现过（或由月/周换算）。"""
    q = (q or "").strip()
    if not q:
        raise ValueError("问题是空的")
    if len(q) > 300:
        raise ValueError("问题太长了（300 字以内）")
    rp = rule_params(q)
    notes, model = [], None
    intent, params, via = None, dict(rp), "rules"
    if use_model and ai.enabled():
        try:
            stages_txt = ", ".join(f"{k} ({v['label_en']})" for k, v in STAGES.items())
            out = ai.chat_json(_ASK_SYS, _ASK_PROMPT.format(stages=stages_txt, today=date.today().isoformat(), q=q),
                               _ASK_SCHEMA, max_tokens=120)
            model = {k: out.get(k) for k in _ASK_SCHEMA["properties"]}
            model["ms"], model["model"] = out.get("_ms"), ai.model_tag()
            intent, via = out.get("intent"), "model"
            for k in ("stage", "dim", "outcome"):         # 类别型参数：模型比关键词更懂上下文
                if out.get(k):
                    params[k] = out[k]
            for k in ("days", "year", "month"):            # 数字要有出处
                v = out.get(k) or 0
                if v and rp.get(k) and v != rp[k]:
                    notes.append(f"模型给的 {k}={v} 与问题里的不一致，按问题里的 {rp[k]}")
                elif v and not rp.get(k):
                    nums = {int(x) for x in re.findall(r"\d+", q)}
                    if v in nums or (k == "days" and (v // 30 in nums or v // 7 in nums)):
                        params[k] = v
                    else:
                        notes.append(f"模型给的 {k}={v} 在问题里找不到依据，已忽略")
            txt = (out.get("text") or "").strip()
            if txt and _in_text(txt, q):
                params["text"] = txt
            elif txt:
                notes.append("模型给的关键词不在问题里，已忽略")
        except ai.AiError as e:
            model = {"error": str(e)}
    if not intent:
        intent = rule_intent(q, rp)
    if intent == "review_time" and not params.get("text"):
        params["text"] = _venue_in(q)                  # 问题里提到了库里已有的期刊/会议名
    if intent == "search" and not params.get("text"):
        params["text"] = _strip_question(q)
    return {"intent": intent, "params": params, "via": via, "model": model, "notes": notes}


def run_question(q: str, use_model: bool = True) -> dict:
    t0 = time.time()
    plan = plan_question(q, use_model)
    sql, args, explain = build_query(plan["intent"], plan["params"])
    with db.get_db() as c:
        cur = c.execute(sql, args)
        cols = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchmany(_MAX_ROWS + 1)]
    return {**plan, "explain": explain, "sql": show_sql(sql, args), "columns": cols,
            "rows": rows[:_MAX_ROWS], "truncated": len(rows) > _MAX_ROWS,
            "ms": round((time.time() - t0) * 1000)}
