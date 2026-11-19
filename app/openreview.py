# -*- coding: utf-8 -*-
"""OpenReview 官方 API 适配器。

- 2026-11 起 OpenReview 对匿名 API 读取一律返回 403 ChallengeRequiredError（公开稿件也一样），
  所以所有读操作都必须带登录 token：在「渠道查询」绑定账号后才能查询/同步。
  （按设计绝不尝试绕过 Challenge，也不改走其他未受限的接口。）
- API v2：api2.openreview.net（2023 年起的主流会议）；老 API：api.openreview.net（更早的会议），
  查不到时自动换另一套再试。
- 状态推断优先级：撤稿/Desk reject/决策回复 > content.venueid 状态后缀 > content.venue 文本 > invitations。
  v2 约定：在审 <venue>/Submission，拒稿 <venue>/Rejected_Submission，撤稿 <venue>/Withdrawn_Submission，
  Desk reject <venue>/Desk_Rejected_Submission，录用后 venueid 变为会议基 id（= note.domain）。
  TMLR：TMLR/Submitted → Assigning_AE/Assigned_AE → Under_Review → Decision_Pending → TMLR（录用）
  / TMLR/Rejected / TMLR/Desk_Rejected / TMLR/Withdrawn_Submission / TMLR/Retracted_Acceptance。
- 库里统一保存「会议基 id」（去掉状态后缀），所以同一篇稿件在审稿各阶段都能对上。
"""
import base64
import json as _json
import re
import threading
import time
from datetime import datetime

import httpx

from . import config
from .stages import normalize
from . import venueutil

API2 = "https://api2.openreview.net"
API1 = "https://api.openreview.net"

# 常用会议（与年份解耦：venue_id 由「会议模板 + 年份」组装；无模板 = 不在 OpenReview，走手动登记）
COMMON_CONFS = [
    {"abbr": "NeurIPS", "name": "NeurIPS", "template": "NeurIPS.cc/{year}/Conference", "tracker": "openreview"},
    {"abbr": "ICLR", "name": "ICLR", "template": "ICLR.cc/{year}/Conference", "tracker": "openreview"},
    {"abbr": "ICML", "name": "ICML", "template": "ICML.cc/{year}/Conference", "tracker": "openreview"},
    {"abbr": "CVPR", "name": "CVPR", "template": "thecvf.com/CVPR/{year}/Conference", "tracker": "openreview"},
    {"abbr": "ICCV", "name": "ICCV", "template": "thecvf.com/ICCV/{year}/Conference", "tracker": "openreview"},
    {"abbr": "ECCV", "name": "ECCV", "template": "thecvf.com/ECCV/{year}/Conference", "tracker": "openreview"},
    {"abbr": "COLM", "name": "COLM", "template": "colmweb.org/COLM/{year}/Conference", "tracker": "openreview"},
    {"abbr": "AAAI", "name": "AAAI", "template": "AAAI.org/{year}/Conference", "tracker": "openreview"},
    {"abbr": "IPMI", "name": "IPMI", "template": "ipmi-conf.org/IPMI/{year}/Conference", "tracker": "openreview"},
    {"abbr": "ACCV", "name": "ACCV", "template": "afcv.org/ACCV/{year}/Conference", "tracker": "openreview"},
    {"abbr": "BMVC", "name": "BMVC", "template": "bmva.org/BMVC/{year}/Conference", "tracker": "openreview"},
    {"abbr": "TMLR", "name": "TMLR", "template": "TMLR", "tracker": "openreview"},
    {"abbr": "ICRA", "name": "ICRA", "template": "", "tracker": "manual"},
    {"abbr": "IROS", "name": "IROS", "template": "", "tracker": "manual"},
]

# 兼容旧字段名（前端 stages 接口返回用）
COMMON_VENUES = COMMON_CONFS

NEED_ACCOUNT_MSG = ("需先在「渠道查询」绑定 OpenReview 账号：OpenReview 已不再允许匿名读取 API"
                    "（匿名请求一律返回 403 Challenge），绑定后自动带登录凭据查询")


def build_venue_id(abbr: str, year) -> str:
    """会议缩写 + 年份 → OpenReview venue id（无模板返回空，走手动登记）。"""
    key = (abbr or "").strip().upper()
    for conf in COMMON_CONFS:
        if conf["abbr"].upper() == key:
            tpl = conf["template"]
            if not tpl:
                return ""
            if "{year}" not in tpl:
                return tpl              # TMLR 等期刊型 venue 不带年份
            try:
                y = int(str(year).strip())
            except (TypeError, ValueError):
                return ""
            return tpl.format(year=y)
    return ""


_http = httpx.Client(timeout=25, headers={"Accept": "application/json",
                                          "User-Agent": config.USER_AGENT})


class _Polite:
    """对 OpenReview 礼貌一点：HTTP 429 按 Retry-After 等一会儿再试一次（最多等 60 秒）。"""

    def _send(self, method, url, **kw):
        r = getattr(_http, method)(url, **kw)
        if r.status_code == 429:
            try:
                wait = min(60.0, float(r.headers.get("Retry-After") or 5))
            except ValueError:
                wait = 5.0
            time.sleep(max(1.0, wait))
            r = getattr(_http, method)(url, **kw)
        return r

    def get(self, url, **kw):
        return self._send("get", url, **kw)

    def post(self, url, **kw):
        return self._send("post", url, **kw)


_client = _Polite()


class ORNotFound(RuntimeError):
    pass


class ORForbidden(RuntimeError):
    pass


class ORAuthExpired(RuntimeError):
    pass


def _norm_title(s: str) -> str:
    return venueutil.norm_title(s)


_FORUM_RE = re.compile(r"[?&](?:id|noteId|forum)=([A-Za-z0-9_\-]+)")


def normalize_forum(s: str) -> str:
    """稿件 ID 归一：用户常粘贴整条链接 https://openreview.net/forum?id=XXXX。"""
    s = str(s or "").strip()
    m = _FORUM_RE.search(s)
    if m:
        return m.group(1)
    return s if re.fullmatch(r"[A-Za-z0-9_\-]{4,40}", s) else s.split("/")[-1].strip()


# ---------- venue id：基 id 与状态后缀 ----------
# 后缀 → 状态类别（submitted/editor/active/decision/rejected/desk_rejected/withdrawn）
_STATUS_SUFFIX = {
    "submission": "active", "blind_submission": "active", "under_review": "active",
    "submitted": "submitted",
    "assigning_ae": "editor", "assigned_ae": "editor",
    "decision_pending": "decision",
    "rejected_submission": "rejected", "rejected": "rejected",
    "desk_rejected_submission": "desk_rejected", "desk_rejected": "desk_rejected",
    "desk_rejection": "desk_rejected",
    "withdrawn_submission": "withdrawn", "withdrawn": "withdrawn",
    "retracted_acceptance": "withdrawn",
}


def split_venueid(vid: str) -> tuple[str, str]:
    """'thecvf.com/ICCV/2025/Conference/Submission' → ('thecvf.com/ICCV/2025/Conference', 'active')。
    也接受 invitation 形式 '<venue>/-/Submission'。无状态后缀返回 (vid, '')。"""
    vid = str(vid or "").strip().strip("/")
    if not vid:
        return "", ""
    parts = vid.split("/")
    if len(parts) >= 3 and parts[-2] == "-":
        return "/".join(parts[:-2]), ""
    if len(parts) >= 2 and parts[-1].lower() in _STATUS_SUFFIX:
        return "/".join(parts[:-1]), _STATUS_SUFFIX[parts[-1].lower()]
    return vid, ""


def base_venue_id(vid: str) -> str:
    return split_venueid(vid)[0]


def venue_year(vid: str) -> str:
    m = re.search(r"(?:^|/)(20\d{2})(?:/|$)", vid or "")
    return m.group(1) if m else ""


def _api_order(venue_id: str) -> list[str]:
    """2022 年及以前的会议优先老 API，其余优先 v2（NeurIPS/ICML 2023 已是 v2）；查不到再试另一个。"""
    y = venue_year(venue_id)
    if y and int(y) <= 2022:
        return [API1, API2]
    return [API2, API1]


def api_for(venue_id: str) -> str:
    return _api_order(venue_id)[0]


# ---------- 展示名（从会议 id 推导，稳定；content.venue 是状态标签，不拿来当会议名） ----------
def venue_name_from_id(base: str) -> tuple[str, str]:
    """会议基 id → (名称, 年份)：thecvf.com/ICCV/2025/Conference → ('ICCV','2025')；
    NeurIPS.cc/2025/Conference → ('NeurIPS','2025')；ICLR.cc/2025/Workshop/ICBINB → ('ICLR Workshop ICBINB','2025')；
    TMLR → ('TMLR','')。"""
    parts = [p for p in str(base or "").split("/") if p]
    if not parts:
        return "", ""
    year = next((p for p in parts if re.fullmatch(r"20\d{2}", p)), "")
    labels = []
    for i, p in enumerate(parts):
        if p == year or p == "Conference":
            continue
        if i == 0 and "." in p:
            # 组织域名：NeurIPS.cc/2025/... 取 NeurIPS；thecvf.com/ICCV/... 跳过域名用下一段
            if len(parts) > 1 and not re.fullmatch(r"20\d{2}", parts[1]):
                continue
            labels.append(p.split(".")[0])
            continue
        labels.append(p.replace("_", " "))
    return " ".join(labels).strip(), year


_PRESENTATION_TAIL_RE = re.compile(
    r"\s+(?:poster|oral|spotlight|highlight|notable[\w\s-]*|top[\s-]?\d+%?[\w\s-]*|"
    r"accept(?:ed)?(?:\s*\(.*\))?)$", re.I)


def venue_display(vid: str, venue_raw: str = "") -> tuple[str, str]:
    """返回 (名称不含年份, 年份)：优先由会议 id 推导；没有 id 时才用 content.venue 兜底
    （去掉 Submitted to / Under review for / Poster / Oral 等状态词）。"""
    base = base_venue_id(vid)
    id_name, id_year = venue_name_from_id(base)
    if id_name:
        return venueutil.split_year(id_name, id_year)
    raw = venueutil.clean_name(venue_raw)
    raw = re.sub(r"^(?:submitted\s+to|under\s+review\s+for|accepted\s+(?:to|by)|rejected\s+by)\s+",
                 "", raw, flags=re.I)
    raw = _PRESENTATION_TAIL_RE.sub("", raw).strip()
    return venueutil.split_year(raw, id_year)


# ---------- 回复（审稿意见 / 决策 / 撤稿）分类 ----------
def _invitations(note: dict) -> list[str]:
    invs = list(note.get("invitations") or [])
    if note.get("invitation"):
        invs.append(note["invitation"])
    return [i for i in invs if isinstance(i, str)]


def _inv_kinds(note: dict) -> set[str]:
    """回复类型 = invitation 最后一段：v2 '<venue>/Submission12/-/Official_Review'，
    v1 '<venue>/-/Paper12/Official_Review' 都取到 official_review；*_decision 归为 decision。"""
    kinds = set()
    for inv in _invitations(note):
        seg = inv.rstrip("/").rsplit("/", 1)[-1].lower()
        kinds.add("decision" if seg.endswith("decision") else seg)
    return kinds


def _content_val(content: dict, field: str):
    c = (content or {}).get(field)
    if isinstance(c, dict):
        return c.get("value", "")
    return c if c is not None else ""


def classify_replies(replies: list[dict]) -> dict:
    out = {"reviews": 0, "meta_reviews": 0, "decision": "", "withdrawn": False,
           "desk_rejected": False}
    for n in replies or []:
        kinds = _inv_kinds(n)
        content = n.get("content") or {}
        if any(k in ("official_review", "review") for k in kinds):
            out["reviews"] += 1
        if "meta_review" in kinds:
            out["meta_reviews"] += 1
        if "decision" in kinds:
            # 会议：content.decision；TMLR：content.recommendation
            d = str(_content_val(content, "decision") or _content_val(content, "recommendation")
                    or "").strip()
            if d:
                out["decision"] = d
        if any(k in ("withdrawal", "withdrawn_submission", "withdraw") for k in kinds):
            out["withdrawn"] = True
        if any(k in ("desk_rejection", "desk_reject", "desk_rejected_submission") for k in kinds):
            out["desk_rejected"] = True
        if "withdrawal_reversion" in kinds:
            out["withdrawal_reverted"] = True
        if "desk_rejection_reversion" in kinds:
            out["desk_rejection_reverted"] = True
    if out.pop("withdrawal_reverted", False):
        out["withdrawn"] = False
    if out.pop("desk_rejection_reverted", False):
        out["desk_rejected"] = False
    return out


def _decision_stage(text: str) -> str | None:
    t = (text or "").strip().lower()
    if not t:
        return None
    if re.search(r"\breject|\bnot\s+accept", t):
        return "rejected"
    if re.search(r"\baccept|\boral\b|\bposter\b|\bspotlight\b|\bhighlight\b", t):
        return "accepted"
    st = normalize(text)
    return st if st in ("accepted", "rejected") else None


def infer_stage(note: dict, replies: list[dict] | None = None) -> tuple[str, str]:
    """从投稿 note（+ 可见回复）推断归一阶段，返回 (stage, raw)。"""
    content = note.get("content") or {}
    venue = str(_content_val(content, "venue") or "").strip()
    venueid = str(_content_val(content, "venueid") or "").strip()
    base, status = split_venueid(venueid)
    domain = str(note.get("domain") or "").strip()
    info = classify_replies(replies or [])
    # ① venueid 的撤稿/拒稿后缀（OpenReview 处理撤稿、Desk reject 时立即更新，撤回后也会改回，最可靠）
    if status == "withdrawn":
        return "withdrawn", venue or "Withdrawn Submission"
    if status in ("rejected", "desk_rejected"):
        return "rejected", venue or ("Desk Rejected Submission" if status == "desk_rejected"
                                     else "Rejected Submission")
    # ② 决策回复（作者可见时常常早于 venueid 更新）
    if info["decision"]:
        st = _decision_stage(info["decision"])
        if st:
            return st, info["decision"]
    # ③ 没有 venueid 状态的老 API 稿件：看撤稿 / Desk reject 回复
    if not status:
        if info["withdrawn"]:
            return "withdrawn", venue or "Withdrawn Submission"
        if info["desk_rejected"]:
            return "rejected", venue or "Desk Rejected Submission"
    if status == "submitted":
        return "submitted", venue or "Submitted"
    if status == "editor":
        return "editor", venue or "Assigning Action Editor"
    if status == "decision":
        return "decision", venue or "Decision Pending"
    if status == "active":
        if info["meta_reviews"]:
            return "decision", f"{venue or 'Under Review'} · meta-review released"
        if info["reviews"]:
            return "reviews_done", f"{venue or 'Under Review'} · {info['reviews']} reviews released"
        return "review", venue or "Under Review"
    # ③ venueid 等于会议基 id（= note.domain）→ 已正式录用到该 venue
    if venueid and (not domain or base == domain) and \
            not re.search(r"submitted\s+to|under\s+review|submission", venue, re.I):
        return "accepted", venue or "Accepted"
    # ④ content.venue 文本 / invitations 兜底（老 API 无 venueid）
    st = normalize(venue) if venue else None
    if st in ("withdrawn", "rejected", "accepted"):
        return st, venue
    inv_str = " ".join(_invitations(note)).lower()
    if "withdrawn" in inv_str:
        return "withdrawn", "Withdrawn Submission"
    if "desk_reject" in inv_str or "desk-reject" in inv_str:
        return "rejected", "Desk Rejected Submission"
    if venue:
        return (st if st in ("submitted", "review") else "review"), venue
    return "submitted", "Submitted"


def note_date(note: dict) -> str:
    """投稿日期 = note 的创建时间（cdate，其次 tcdate；pdate 是录用/发表时间，不能当投稿日）。"""
    for k in ("cdate", "tcdate"):
        v = note.get(k)
        if isinstance(v, (int, float)) and v > 0:
            try:
                return datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                continue
    return ""


# ---------- HTTP ----------
def _error_text(r: httpx.Response) -> tuple[str, str]:
    try:
        j = r.json()
        return str(j.get("name") or ""), str(j.get("message") or "")[:200]
    except Exception:
        return "", (r.text or "")[:200]


def _get_notes(api: str, params: dict, token: str | None) -> list[dict]:
    if not token:
        raise ORForbidden(NEED_ACCOUNT_MSG)
    r = _client.get(f"{api}/notes", params=params, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 401:
        raise ORAuthExpired("OpenReview 登录已过期")
    if r.status_code == 403:
        name, msg = _error_text(r)
        if name == "ChallengeRequiredError":
            raise ORForbidden("OpenReview 要求人机验证（ChallengeRequired），请稍后再试或重新绑定账号")
        raise ORForbidden(f"OpenReview 拒绝访问（HTTP 403 {name}）：{msg}")
    if r.status_code == 404:
        raise ORNotFound("OpenReview 未找到该记录")
    if r.status_code >= 400:
        name, msg = _error_text(r)
        raise RuntimeError(f"OpenReview 返回 HTTP {r.status_code}（{name or msg or 'error'}）")
    return r.json().get("notes", [])


def _brief(note: dict, replies: list[dict] | None = None) -> dict:
    content = note.get("content") or {}
    stage, raw = infer_stage(note, replies)
    vid = str(note.get("domain") or "") or str(_content_val(content, "venueid") or "")
    info = classify_replies(replies or [])
    return {
        "forum": note.get("forum") or note.get("id"),
        "title": str(_content_val(content, "title") or "").strip(),
        "authors": _authors_to_names(_content_val(content, "authors")),
        "stage": stage,
        "raw": raw,
        "decision": info["decision"],
        "reviews": info["reviews"],
        "venue_id": base_venue_id(vid),
        "submitted_at": note_date(note),
        "pdate": note.get("pdate"),
    }


def get_submission_status(forum_id: str, api: str, token: str) -> dict:
    """反查单稿当前状态 + 决策/审稿意见摘要（带 token；作者本人可见已发布的审稿意见与决策）。"""
    notes = _get_notes(api, {"id": forum_id, "details": "directReplies"}, token)
    root = next((n for n in notes if n.get("id") == forum_id), None)
    if root is not None:
        replies = (root.get("details") or {}).get("directReplies") or []
        note_count = 1 + len(replies)
    else:
        notes = _get_notes(api, {"forum": forum_id, "limit": 1000}, token)
        if not notes:
            raise ORNotFound("OpenReview 未找到该稿件记录（稿件 ID 有误，或当前账号无权查看）")
        root = next((n for n in notes if n.get("id") == forum_id), None) or \
            next((n for n in notes if not n.get("replyto")), notes[0])
        replies = [n for n in notes if n is not root]
        note_count = len(notes)
    out = _brief(root, replies)
    out["forum"] = forum_id
    out["note_count"] = note_count
    return out


def _exact_title_hit(candidates: list[dict], title: str, title_of, base: str = "",
                     base_of=None) -> dict | None:
    """只认「归一后标题完全相同」且唯一的候选——模糊匹配会把别人的稿件永久绑上来。"""
    target = _norm_title(title)
    if not target:
        return None
    hits = [n for n in candidates if _norm_title(title_of(n)) == target]
    if base and base_of:
        # 已知会议时只认同一会议：同名稿件可能是转投前被拒的那一篇，绝不能跨会议绑定
        hits = [n for n in hits if base_of(n) == base]
    return hits[0] if len(hits) == 1 else None


def match_by_title(venue_id: str, title: str, api: str = API2,
                   token: str | None = None) -> dict | None:
    """按标题精确查询（content.title 过滤 + 会议校验）。"""
    if not (title or "").strip():
        return None
    base = base_venue_id(venue_id)
    notes = _get_notes(api, {"content.title": title.strip(), "limit": 50}, token)
    notes = [n for n in notes if _is_venue_submission(n)]     # 排除 DBLP/ORCID/档案记录
    hit = _exact_title_hit(notes, title, lambda x: _content_val(x.get("content") or {}, "title"),
                           base, lambda x: base_venue_id(x.get("domain") or
                                                         _content_val(x.get("content") or {}, "venueid")))
    return _brief(hit) if hit else None


def poll(forum_id: str, venue_id: str, title: str, token: str | None = None,
         profile_id: str = "") -> dict:
    """单稿查询：优先按绑定的 forum id 反查；否则在「我的投稿」里按标题精确匹配，再按标题检索。"""
    t0 = time.time()
    ms = lambda: int((time.time() - t0) * 1000)   # noqa: E731
    if not token:
        return {"ok": False, "need_account": True, "error": NEED_ACCOUNT_MSG, "latency_ms": 0}
    forum_id = normalize_forum(forum_id)
    try:
        apis = _api_order(venue_id)
        status = None
        if forum_id:
            err = None
            for api in apis:
                try:
                    status = get_submission_status(forum_id, api, token)
                    break
                except ORNotFound as e:
                    err = e
                except ORForbidden as e:
                    if "ChallengeRequired" in str(e):
                        raise           # 要人机验证：不换接口重试，交给上层停下这一轮
                    err = e
            if status is None:
                raise err or ORNotFound("OpenReview 未找到该稿件记录")
        else:
            m = None
            base = base_venue_id(venue_id)
            if profile_id:
                mine = fetch_my_submissions(token, profile_id)
                m = _exact_title_hit(mine, title, lambda x: x["title"], base, lambda x: x["venue_id"])
            if not m:
                for api in apis:
                    try:
                        m = match_by_title(venue_id, title, api, token)
                    except ORNotFound:
                        m = None
                    except ORForbidden as e:
                        if "ChallengeRequired" in str(e):
                            raise
                        m = None
                    if m:
                        break
            if not m:
                return {"ok": False, "error": "未匹配到投稿：标题需与 OpenReview 上完全一致（且在同一会议）；"
                        "也可在「编辑信息」里填写 OpenReview 稿件 ID（forum）", "latency_ms": ms()}
            status = {k: m.get(k) for k in ("forum", "stage", "raw", "decision", "reviews",
                                             "venue_id", "submitted_at")}
            status["matched_by"] = "title"
        status["ok"] = True
        status["latency_ms"] = ms()
        return status
    except ORAuthExpired as e:
        return {"ok": False, "auth_expired": True, "error": str(e), "latency_ms": ms()}
    except (ORNotFound, ORForbidden) as e:
        return {"ok": False, "error": str(e), "latency_ms": ms()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "latency_ms": ms()}


# ==================== 账号导入：自动同步我的全部投稿 ====================
# 官方认证（docs.openreview.net + 实测核实）：
# POST /login {id, password, expiresIn} → token；JWT payload 的 user 字段内含完整 profile
# （preferredId / fullname / emails），无需再请求 /profile（该端点 404）。
# 开启多因素认证（MFA）的账号返回 {mfaPending: true, ...}，需要验证码，无法无人值守登录。
# 投稿列表：GET /notes?content.authorids=<preferredId>（会混入 DBLP/ORCID 导入的论文记录，需过滤）。

def _jwt_payload(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(part))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _jwt_user(token: str) -> dict:
    """解码 JWT payload，取 user 对象（含 profile）。"""
    user = _jwt_payload(token).get("user") or {}
    return user if isinstance(user, dict) else {}


def login(email: str, password: str, api: str = API2) -> str:
    """登录换 token（有效期申请 7 天）；失败抛 RuntimeError（含官方错误信息）。"""
    r = _client.post(f"{api}/login", json={"id": email, "password": password,
                                           "expiresIn": 7 * 24 * 3600})
    if r.status_code != 200:
        name, msg = _error_text(r)
        raise RuntimeError(f"登录失败（HTTP {r.status_code}）：{msg or name}")
    try:
        j = r.json()
    except Exception:
        raise RuntimeError("登录响应无法解析")
    if j.get("mfaPending"):
        raise RuntimeError("该账号开启了多因素认证（MFA），需要验证码才能登录，本工具暂不支持；"
                           "这类稿件请手动登记状态（请不要为了本工具关闭 MFA）")
    token = j.get("token")
    if not token:
        raise RuntimeError("登录成功但未返回 token")
    return token


# token 缓存：避免每次查询/同步都重新登录（按 JWT exp 过期，未知则 1 小时）
_tok_lock = threading.Lock()
_tok_cache: dict[str, tuple[str, float]] = {}


def get_token(email: str, password: str) -> str:
    with _tok_lock:
        tok, exp = _tok_cache.get(email, ("", 0.0))
        if tok and time.time() < exp - 300:
            return tok
    tok = login(email, password)
    exp = float(_jwt_payload(tok).get("exp") or 0) or (time.time() + 3600)
    with _tok_lock:
        _tok_cache[email] = (tok, exp)
    return tok


def drop_token(email: str):
    with _tok_lock:
        _tok_cache.pop(email, None)


def get_my_profile(token: str, api: str = API2) -> dict:
    """从登录 token 解出自己的 profile（不再请求 /profile，实测 404）。"""
    prof = _jwt_user(token).get("profile") or {}
    if not prof.get("id"):
        raise RuntimeError("登录 token 中未找到 profile 信息")
    return {
        "id": prof.get("preferredId") or prof.get("id", ""),
        "email": prof.get("preferredEmail") or prof.get("email", ""),
        "names": prof.get("fullname") or "",
    }


def _venue_from_inv(inv: str) -> str:
    """从 invitation 推导 venue id：colmweb.org/COLM/2026/Conference/-/Submission
    → colmweb.org/COLM/2026/Conference；TMLR/-/Submission → TMLR。"""
    return base_venue_id(inv) if inv else ""


def _authors_to_names(val) -> list[str]:
    """authors 字段压平：['Xiaoming Wang'] 或 [{username, fullname, email, country}] → 姓名列表。"""
    if isinstance(val, str):
        return [val.strip()] if val.strip() else []
    if not isinstance(val, list):
        return []
    out = []
    for a in val:
        if isinstance(a, dict):
            name = a.get("fullname") or a.get("name") or a.get("username") or ""
            name = str(name).strip()
        elif isinstance(a, str):
            name = a.strip()
        else:
            continue
        if name and name not in out:
            out.append(name)
    return out


def _keywords(val) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [x.strip() for x in re.split(r"[,;，；]", val) if x.strip()]
    return []


_SUBMISSION_INV_RE = re.compile(r"/-/[A-Za-z_]*Submission$")
_NOT_A_VENUE_RE = re.compile(r"^(dblp\.org|DBLP\.org|OpenReview\.net/Archive|ORCID|orcid\.org)", re.I)


def _is_venue_submission(n: dict) -> bool:
    """只保留真正投到某个 venue 的稿件；排除 DBLP/ORCID 导入记录、档案上传等「个人主页论文」。"""
    if n.get("replyto") or n.get("ddate"):
        return False
    content = n.get("content") or {}
    domain = str(n.get("domain") or "")
    vid = str(_content_val(content, "venueid") or "")
    if _NOT_A_VENUE_RE.match(domain) or _NOT_A_VENUE_RE.match(vid):
        return False
    return any(_SUBMISSION_INV_RE.search(i) for i in _invitations(n))


def fetch_my_submissions(token: str, profile_id: str, api: str = API2) -> list[dict]:
    """拉取该账号名下的全部投稿（顶层投稿 note），附带可见的直接回复用于判断审稿意见/决策。

    返回 [{forum, title, authors, keywords, abstract, stage, raw, decision, reviews,
           venue_id(基 id), venue_name(展示名), venue_base_name, year, submitted_at, pdate, inv}]
    """
    headers = {"Authorization": f"Bearer {token}"}
    notes, offset, with_details = [], 0, True
    while True:
        params = {"content.authorids": profile_id, "limit": 1000, "offset": offset}
        if with_details:
            params["details"] = "directReplies"
        r = _client.get(f"{api}/notes", params=params, headers=headers)
        if r.status_code == 400 and with_details:
            with_details = False        # 个别部署不支持 details：退回只拉投稿本体
            continue
        if r.status_code == 401:
            raise ORAuthExpired("OpenReview 登录已过期")
        if r.status_code != 200:
            name, msg = _error_text(r)
            raise RuntimeError(f"拉取投稿列表失败（HTTP {r.status_code} {name}）{msg}")
        batch = r.json().get("notes", [])
        notes.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    out = []
    for n in notes:
        if not _is_venue_submission(n):
            continue
        content = n.get("content") or {}
        replies = ((n.get("details") or {}).get("directReplies")) or []
        stage, raw = infer_stage(n, replies)
        info = classify_replies(replies)
        inv = next((i for i in _invitations(n) if _SUBMISSION_INV_RE.search(i)), "")
        vid_raw = str(n.get("domain") or "") or str(_content_val(content, "venueid") or "") \
            or _venue_from_inv(inv)
        base = base_venue_id(vid_raw)
        venue_raw = str(_content_val(content, "venue") or "")
        vname, year = venue_display(base, venue_raw)
        out.append({
            "forum": n.get("forum") or n.get("id"),
            "title": str(_content_val(content, "title") or "").strip(),
            "authors": _authors_to_names(_content_val(content, "authors")),
            "keywords": _keywords(_content_val(content, "keywords")),
            "abstract": str(_content_val(content, "abstract") or "")[:500],
            "stage": stage,
            "raw": raw or venue_raw,
            "decision": info["decision"],
            "reviews": info["reviews"],
            "venue_id": base,
            "venue_name": venueutil.display(vname, year),
            "venue_base_name": vname,
            "year": year or venue_year(base),
            "submitted_at": note_date(n),
            "pdate": n.get("pdate"),
            "inv": inv,
        })
    return out
