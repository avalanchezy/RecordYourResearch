# -*- coding: utf-8 -*-
"""学术分级匹配引擎：CCF 推荐目录 / 中科院预警名单（随项目发布）+ 可选导入的中科院分区 / 新锐分区 / JCR。

数据全部本地（<数据目录>/rankings/rankings.db，首次启动自动从 data-sources/ 构建），离线可用。
- 徽章与建档卡片（venue_rankings / card_for_venue）只用严格匹配：简称或全称精确命中
  （含 CCF「原名」、& ↔ and 两种拼写、ACM/IEEE 前缀、少量公认别名），其次全称相似度 ≥ 0.92
  且唯一；绝不拿子串模糊结果当徽章（宁缺毋滥）。
- 分级速查（lookup_journal / lookup_ccf）与输入联想（suggest）才用模糊匹配：精确 → 前缀 → 按词子串
  （mm 不命中 progra-mm-ing，短简称不会按词前缀误命中无关刊名）。
"""
import difflib
import os
import re
import sqlite3
from contextlib import closing

from . import config

DB_PATH = config.RANKINGS_DB

# 卡片口径版本：匹配规则 / 卡片结构变更时 +1；venues.rank_data 里的 "v" 与之不同即应重算
RANK_VERSION = 2

PUNCT = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def norm(s: str) -> str:
    return PUNCT.sub("", (s or "").lower()).strip()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- 名称变体（严格匹配与候选共用） ----------
_PREFIXES = ("proceedingsofthe", "ieeecvf", "ieeeacm", "ieee", "acm", "the")
_PAREN_RE = re.compile(r"\s*[（(][^（）()]*[)）]")
_PAREN_ABBR_RE = re.compile(r"[（(]([A-Za-z][A-Za-z0-9 &+\-]{1,15})[)）]")
_ACRO_YEAR_RE = re.compile(r"^([A-Za-z][A-Za-z&+]{1,14})[\s'’\-]+\d{2}$")   # AAAI-26 / ECCV'24
_FORMER_RE = re.compile(r"[（(]\s*原\s*([^（）()]*)[)）]")
_ISSN_RE = re.compile(r"^\d{4}-?\d{3}[\dX]$")
_NA = {"", "n/a", "na", "-", "—", "none", "null"}
_ORD_WORD = (r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|"
             r"thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|"
             r"thirtieth|fortieth|fiftieth|sixtieth)")
# 会议届次前缀：The Thirteenth … / Proceedings of the 42nd … / Thirty-Ninth Annual …
_ORDINAL_RE = re.compile(
    rf"^\s*(?:proceedings\s+of\s+)?(?:the\s+)?"
    rf"(?:(?:\d{{1,3}}(?:st|nd|rd|th)|(?:(?:twenty|thirty|forty|fifty)[\s-]?)?{_ORD_WORD})\s+)?"
    rf"(?:annual\s+)?", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def _split_former(s: str) -> list[str]:
    """「现名 （原旧名）」→ [现名, 旧名]（CCF 2026 目录里改过名的条目，如 WWW / TON / VR / （原IR））。"""
    s = s or ""
    former = [m.group(1).strip() for m in _FORMER_RE.finditer(s)]
    main = _FORMER_RE.sub(" ", s).strip()
    return [x for x in [main] + former if x]


def _fold_keys(name: str) -> list[str]:
    """「&」↔「and」两种拼写的归一键（Information Processing & Management ↔ … and …）。
    norm() 会丢掉 &，所以「去掉独立的 and 单词」即等价于 & 写法；只按整词替换，绝不动 Landscape 这类词内的 and。"""
    s = name or ""
    out = []
    if "&" in s:
        out.append(norm(s.replace("&", " and ")))
    if re.search(r"\band\b", s, re.I):
        out.append(norm(re.sub(r"\band\b", " ", s, flags=re.I)))
    k = norm(s)
    return [x for x in out if x and x != k]


def _strip_prefixes(k: str) -> list[str]:
    """逐层去掉机构 / 会议录前缀（proceedingsofthe → ieeecvf → …），返回每一层的结果。"""
    out = []
    while True:
        for p in _PREFIXES:
            if k.startswith(p) and len(k) > len(p) + 2:
                k = k[len(p):]
                out.append(k)
                break
        else:
            return out


def _key_variants(name: str) -> list[str]:
    """严格匹配用的归一键，按优先级：原名 → 去括号注释 → 括号里的简称 → 「简称-年份」→
    去机构前缀 / 届次（The Thirteenth …）→ & ↔ and。全部是精确比对用的键，不做子串匹配。"""
    name = name or ""
    k = norm(name)
    out = [k] if k else []
    bare = _PAREN_RE.sub(" ", name).strip()
    if bare != name:
        out.append(norm(bare))
    # 括号里的简称：International Conference on Learning Representations (ICLR)
    m = _PAREN_ABBR_RE.search(name)
    if m:
        out.append(norm(m.group(1)))
    m = _ACRO_YEAR_RE.match(name.strip())
    if m:
        out.append(norm(m.group(1)))
    out += _strip_prefixes(k)
    plain = _ORDINAL_RE.sub("", bare, count=1)
    if plain != bare:
        out.append(norm(plain))
        out += _strip_prefixes(norm(plain))
    out += _fold_keys(bare)
    seen, uniq = set(), []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _tokens(s: str) -> list[str]:
    """名称 → 归一后的词序列（丢掉 and，使「&」与「and」两种写法一致）。"""
    return [t for t in (norm(m.group(0)) for m in _TOKEN_RE.finditer(s or "")) if t and t != "and"]


# 卫星活动标记词：输入里带、而 CCF 条目自己的全称 / 简称里没有 → 不是该 venue 本身。
# CVPR (Workshops)、…Pattern Recognition (CVPR) Workshops、Findings of EMNLP、WWW Companion
# 绝不能拿主会的 CCF 级别（括号简称 / 去括号 / 相似度兜底都可能把它们命中到主会）
_SATELLITE_WORDS = frozenset((
    "workshop", "workshops", "findings", "companion", "adjunct", "demo", "demos", "demonstration",
    "demonstrations", "tutorial", "tutorials", "doctoral", "student", "students", "abstract", "abstracts",
    "challenge", "challenges", "competition", "competitions", "satellite", "研讨会"))


def _satellite_words(name: str) -> frozenset:
    return frozenset(t for t in _tokens(name) if t in _SATELLITE_WORDS)


def _satellite_conflict(words, row) -> bool:
    """输入的卫星活动标记词（Workshop / Findings …）不在该 CCF 条目的全称或简称里 → 冲突。"""
    if not words:
        return False
    return bool(words - set(row["_name_t"]) - set(row["_abbr_t"]))


def _tok_prefix(qt: list[str], nt: list[str]) -> bool:
    """输入词序列在名称某个词起点处命中：前面的词整词相同、最后一个词是前缀
    （Pattern Rec → Pattern Recognition；短简称不会按词前缀误命中无关刊名）。"""
    n = len(qt)
    if not n:
        return False
    for i in range(len(nt) - n + 1):
        if nt[i:i + n - 1] == qt[:-1] and nt[i + n - 1].startswith(qt[-1]):
            return True
    return False


# ---------- 徽章文字 ----------
def _jcr_label(jcr, sep: str = " · ") -> str:
    """JCR 徽章：「JCR Q1 · IF 9.1」；分区为 N/A 时只写「IF 0.5」；IF 也缺失则不出徽章（返回空串）。"""
    if not isinstance(jcr, dict):
        return ""
    q = str(jcr.get("quartile") or "").strip().upper()
    f = str(jcr.get("if") or "").strip()
    has_q = bool(re.fullmatch(r"Q[1-4]", q))
    has_if = f.lower() not in _NA
    if has_q and has_if:
        return f"JCR {q}{sep}IF {f}"
    if has_q:
        return f"JCR {q}"
    if has_if:
        return f"IF {f}"
    return ""


def _xr_warning_label(w) -> str:
    """新锐分区预警标记 → 徽章文字。原值 Under Review 是「预警观察中」，绝不能裸写成像稿件状态的「⚠ Under Review」。"""
    w = str(w or "").strip()
    if not w:
        return ""
    if re.sub(r"[\s_-]+", " ", w).lower() in ("under review", "under observation", "watch list"):
        return "⚠ 新锐预警·观察中"
    return f"⚠ 新锐预警：{w}"


def _brief_badges(card: dict) -> str:
    """联想下拉里的徽章摘要（一行文字）。"""
    badges = []
    if card.get("cas"):
        badges.append(f"中科院{card['cas']['part']}区" + ("Top" if card["cas"]["top"] else ""))
    if card.get("xr"):
        badges.append(f"新锐{card['xr']['part']}区" + ("Top" if card["xr"]["top"] else ""))
    jl = _jcr_label(card.get("jcr"), sep=" ")
    if jl:
        badges.append(jl)
    return " ".join(badges)


# ---------- 期刊分级（中科院 / 新锐 / JCR / 预警） ----------
def _row_card(r) -> dict:
    def flag(v):
        return bool(v and str(v).strip() and str(v).strip() != "—")
    return {
        "journal": r["journal"],
        "issn": r["issn"], "eissn": r["eissn"],
        "cas": {"field": r["cas_field"], "part": r["cas_part"], "top": flag(r["cas_top"]),
                "wos": r["cas_wos"]} if r["cas_part"] else None,
        "xr": {"field": r["xr_field"], "part": r["xr_part"], "top": flag(r["xr_top"]),
               "warning": r["xr_warning"]} if r["xr_part"] else None,
        "jcr": {"if": r["jcr_if"], "quartile": r["jcr_quartile"],
                "category": r["jcr_cat"]} if r["jcr_quartile"] else None,
        "warn2025": r["warn2025"], "warn2024": r["warn2024"],
    }


def _issns(r) -> set:
    out = set()
    for col in ("issn", "eissn"):
        v = str(r[col] or "").strip().upper()
        if _ISSN_RE.match(v):
            out.add(v if "-" in v else v[:4] + "-" + v[4:])
    return out


# 同一 ISSN 被各分区表按不同刊名拆成多行时（Journal of the Franklin Institute / …-ENGINEERING AND
# APPLIED MATHEMATICS），只把缺失的整段补齐；ISSN 不同的同名/近名期刊（JCS&T ≠ JCST）绝不合并
_SECTIONS = (
    ("cas_part", ("cas_field", "cas_part", "cas_top", "cas_wos")),
    ("xr_part", ("xr_field", "xr_part", "xr_top", "xr_warning")),
    ("jcr_quartile", ("jcr_if", "jcr_quartile", "jcr_cat")),
    ("warn2025", ("warn2025",)),
    ("warn2024", ("warn2024",)),
)


_ISSN_CACHE = {"stamp": None, "index": {}}


def _issn_index(c) -> dict:
    """ISSN → [norm_key]（进程内缓存，rankings.db 变更后自动重建；eissn 列在库里没有索引）。"""
    stamp = _db_stamp()
    if stamp is not None and _ISSN_CACHE["stamp"] == stamp and _ISSN_CACHE["index"]:
        return _ISSN_CACHE["index"]
    index = {}
    for r in c.execute("SELECT norm_key, issn, eissn FROM journals"):
        for x in _issns(r):
            index.setdefault(x, []).append(r["norm_key"])
    _ISSN_CACHE.update(stamp=stamp, index=index)
    return index


def _merge_issn_siblings(c, r) -> dict:
    row = dict(r)
    issns = _issns(row)
    if not issns:
        return row
    index = _issn_index(c)
    keys = sorted({k for x in issns for k in index.get(x, ()) if k != row["norm_key"]})
    sibs = [s for s in (_journal_row(c, k) for k in keys) if s is not None]
    for s in sibs:
        for flag_col, cols in _SECTIONS:
            if not row.get(flag_col) and s[flag_col]:
                for col in cols:
                    row[col] = s[col]
        if not row.get("journal") and s["journal"]:
            row["journal"] = s["journal"]
    return row


def _journal_row(c, key: str):
    if not key:
        return None
    return c.execute("SELECT * FROM journals WHERE norm_key=?", (key,)).fetchone()


def lookup_journal(name: str, limit: int = 8) -> dict:
    """期刊名查询（分级速查页 / 候选参考）：精确（含 CCF 期刊简称 → 全称）→ 前缀 → 按词子串。"""
    if not name or not name.strip():
        return {"ok": False, "error": "请输入期刊名称", "matches": []}
    k = norm(name)
    if not k:
        return {"ok": False, "error": "期刊名称无效", "matches": []}
    if not os.path.exists(DB_PATH):
        return {"ok": False, "error": "分级数据库未初始化（重启本系统会自动构建，或运行 python tools/build_rankings.py）", "matches": []}
    with closing(_conn()) as c:
        found, seen = [], set()

        def take(row):
            if row is not None and row["norm_key"] not in seen and len(found) < limit:
                seen.add(row["norm_key"])
                card = _row_card(row)
                if not card["journal"]:          # 仅在预警名单里出现的期刊没有刊名
                    card["journal"] = name.strip()
                found.append(card)

        # CCF 期刊简称 → 全称（PR → Pattern Recognition，排在同名短刊之前）
        ccf_row, how = _strict_ccf_match(c, name, "journal")
        if ccf_row is not None and ccf_row["type"] == "journal" and how in ("abbr", "alias"):
            take(_strict_journal(c, name, ccf_row, how))
        exact = _strict_journal(c, name)
        take(exact)
        if exact is None:
            for kk in [k] + _fold_keys(name):
                for r in c.execute(
                        "SELECT * FROM journals WHERE norm_key LIKE ? AND journal<>''"
                        " ORDER BY length(norm_key) LIMIT ?", (kk + "%", limit)).fetchall():
                    take(r)
            # 子串只认「按词」命中（Recognition → PATTERN RECOGNITION），不认 ICME → ACADEMIC MEDICINE
            qt = _tokens(name)
            for kk in [k] + _fold_keys(name):
                if len(found) >= limit:
                    break
                for r in c.execute(
                        "SELECT * FROM journals WHERE norm_key LIKE ? AND journal<>''"
                        " ORDER BY length(norm_key) LIMIT 500", ("%" + kk + "%",)).fetchall():
                    if _tok_prefix(qt, _tokens(r["journal"])):
                        take(r)
        if not found:
            return {"ok": True, "matches": [], "hint": "本地库未收录，可手动填写"}
        return {"ok": True, "matches": found}


# ---------- CCF 目录 ----------
def _ccf_dict(r):
    return {"field": r["field"], "type": r["type"], "rank": r["rank"],
            "abbr": r["abbr"], "name": r["name"], "publisher": r["publisher"]}


_CCF_CACHE = {"stamp": None, "index": {}, "rows": []}


def _db_stamp():
    try:
        st = os.stat(DB_PATH)
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def available() -> bool:
    return os.path.exists(DB_PATH)


def db_version() -> str:
    """rankings.db 的版本戳（重建或导入新数据后会变）：缓存的徽章据此失效重算。"""
    st = _db_stamp()
    return f"{st[0]}:{st[1]}" if st else ""


_META_CACHE = {"stamp": None, "meta": {}}


def meta() -> dict:
    """数据集版本等元信息（tools/build_rankings.py 写入）。"""
    stamp = _db_stamp()
    if stamp is None:
        return {}
    if _META_CACHE["stamp"] != stamp:
        try:
            with closing(_conn()) as c:
                _META_CACHE["meta"] = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")}
        except sqlite3.Error:
            _META_CACHE["meta"] = {}
        _META_CACHE["stamp"] = stamp
    return _META_CACHE["meta"]


def datasets() -> dict:
    ds = set((meta().get("datasets") or "").split(","))
    return {k: (k in ds) for k in ("ccf", "warning_lists", "cas", "xr", "jcr")} if available() else \
        {k: False for k in ("ccf", "warning_lists", "cas", "xr", "jcr")}


def _ed(key: str, default: str) -> str:
    return meta().get(f"{key}_edition") or default


def ensure_db(log=print) -> bool:
    """首次启动：分级库不存在就用随项目发布的 CCF 目录 + 预警名单构建一个（不含任何受许可数据）。"""
    if available():
        return True
    try:
        from . import rankings_build
        rankings_build.build(log=log)
        return True
    except Exception as e:
        log(f"[warn] 分级库自动构建失败（不影响其他功能）：{type(e).__name__}: {e}")
        return False


def _ccf_index(c):
    """CCF 精确匹配索引（进程内缓存，rankings.db 变更后自动重建）。

    返回 (index, rows)：index = 归一键 → [(行, 来源)]，来源 abbr=简称（含「原简称」与去 ACM/IEEE
    前缀后的简称：ACM MM → MM）、name=全称（含「原名」）、fold=全称的 & ↔ and 拼写。
    """
    stamp = _db_stamp()
    if stamp is not None and _CCF_CACHE["stamp"] == stamp and _CCF_CACHE["rows"]:
        return _CCF_CACHE["index"], _CCF_CACHE["rows"]
    index, rows = {}, []

    def put(key, row, kind):
        if not key:
            return
        lst = index.setdefault(key, [])
        if all(x is not row for x, _ in lst):
            lst.append((row, kind))

    for r in c.execute("SELECT * FROM ccf"):
        row = dict(r)
        # 模糊候选用的预归一 / 预分词（以下划线开头，_ccf_dict 不会带出）
        row["_abbr_n"], row["_name_n"] = norm(row.get("abbr")), norm(row.get("name"))
        row["_abbr_t"], row["_name_t"] = _tokens(row.get("abbr")), _tokens(row.get("name"))
        names = _split_former(row.get("name"))
        row["_names_n"] = [x for x in (norm(n) for n in names) if x]     # 现名、原名（相似度兜底用）
        rows.append(row)
        for a in _split_former(row.get("abbr")):
            put(norm(a), row, "abbr")
            m = re.match(r"^(?:ACM|IEEE)\s+(\S.*)$", a)
            if m:
                put(norm(m.group(1)), row, "abbr")
        for n in names:
            put(norm(n), row, "name")
        for n in names:
            for f in _fold_keys(n):
                put(f, row, "fold")
    _CCF_CACHE.update(stamp=stamp, index=index, rows=rows)
    return index, rows


# 公认别名（只做精确映射，不是模糊）：会议俗称 + 投稿系统里的期刊编号前缀（如 ESWA-D-25-…、NEUCOM-D-25-…）
# 键 = norm(别名)，值 = (CCF 索引键, 限定类型)
_CCF_ALIASES = {
    "kdd": ("sigkdd", "conference"),
    "nips": ("neurips", "conference"),
    "neuralinformationprocessingsystems": ("neurips", "conference"),
    "advancesinneuralinformationprocessingsystems": ("neurips", "conference"),
    "conferenceoncomputervisionandpatternrecognition": ("cvpr", "conference"),
    "computervisionandpatternrecognition": ("cvpr", "conference"),
    "medicalimagecomputingandcomputerassistedintervention": ("miccai", "conference"),
    "acmmultimedia": ("acmmm", "conference"),
    "thewebconf": ("www", "conference"),
    "webconf": ("www", "conference"),
    "pami": ("tpami", "journal"),
    "ins": ("informationsciences", "journal"),
    "apin": ("appliedintelligence", "journal"),
    "knosys": ("knowledgebasedsystems", "journal"),
    "neucom": ("neurocomputing", "journal"),
    "eswa": ("expertsystemswithapplications", "journal"),
    "neunet": ("neuralnetworks", "journal"),
    "patcog": ("patternrecognition", "journal"),
    "artint": ("artificialintelligence", "journal"),
    "engappai": ("engineeringapplicationsofartificialintelligence", "journal"),
    "media": ("medicalimageanalysis", "journal"),
    "imavis": ("ivc", "journal"),
    "tcyb": ("ieeetransactionsoncybernetics", "journal"),
    # 「IEEE + 两字母简称」：去前缀后只剩两个字母，_strip_prefixes 不会剥（防 IEEE MM → ACM MM），逐个列出
    "ieeesp": ("sp", "conference"),          # IEEE S&P / IEEE SP（Oakland）
    "ieeetc": ("tc", "journal"),             # IEEE TC = IEEE Transactions on Computers
}


def _journal_for_ccf(c, ccf_row):
    """CCF 期刊条目在期刊分级库里对应的行（按现名 / 原名，含 & ↔ and 拼写）。"""
    for n in _split_former(ccf_row["name"]):
        for kk in [norm(n)] + _fold_keys(n):
            r = _journal_row(c, kk)
            if r is not None:
                return r
    return None


def _journal_conflict(c, base, ccf_row) -> bool:
    """非精确命中（& ↔ and 拼写 / 相似度）的 CCF 候选是否与输入冲突。

    输入名本身就是库里的一本期刊（base）时：候选是会议，或候选对应库里另一本 ISSN 不同的期刊
    （JCS&T ≠ JCST）→ 冲突，不能把这个 CCF 级别安到它头上。"""
    if base is None:
        return False
    if ccf_row["type"] != "journal":
        return True
    other = _journal_for_ccf(c, ccf_row)
    if other is None or other["norm_key"] == base["norm_key"]:
        return False
    return not (_issns(base) & _issns(other))


def _ccf_sort_key(venue_type: str):
    return lambda x: (0 if venue_type and x[0]["type"] == venue_type else 1,
                      0 if x[1] == "abbr" else 1, x[0]["seq"] or 0)


def _ccf_exact(c, name: str, venue_type: str = "", base=None, satellite=frozenset()):
    """首个命中的归一键下的全部精确候选 [(行, 方式)]，已按 类型偏好 → 简称优先 → 目录顺序 排好。
    方式：abbr / name / fold（& ↔ and 拼写命中，需过期刊冲突检查）。
    satellite = 输入里的卫星活动标记词（徽章用），与之冲突的条目跳过。"""
    keys = _key_variants(name)
    if not keys:
        return [], keys
    index, _ = _ccf_index(c)
    input_folds = set(_fold_keys(_PAREN_RE.sub(" ", name or "")))
    for kk in keys:
        hits = index.get(kk) or []
        cands = []
        for row, kind in hits:
            how = "fold" if (kind == "fold" or kk in input_folds) else kind
            if how == "fold" and _journal_conflict(c, base, row):
                continue
            if _satellite_conflict(satellite, row):
                continue
            cands.append((row, how))
        if cands:
            cands.sort(key=_ccf_sort_key(venue_type))
            return cands, keys
    return [], keys


def _input_journal(c, name: str):
    """输入名本身（原样或去括号注释）在期刊分级库里的精确行，用于冲突判定。"""
    bare = _PAREN_RE.sub(" ", name or "")
    for kk in dict.fromkeys([norm(name), norm(bare)]):
        r = _journal_row(c, kk)
        if r is not None:
            return r
    return None


def _strict_ccf_match(c, name: str, venue_type: str = ""):
    """CCF 严格匹配，返回 (行, 方式)；方式 abbr / name / fold / alias / similar，未命中 (None, "")。
    venue_type 只作偏好（同简称有期刊与会议两条时取对应类型），不作过滤。
    输入带 Workshop / Findings / Companion 等卫星活动标记词、而条目自身名称里没有时一律不认（宁缺毋滥）。"""
    base = _input_journal(c, name)
    sat = _satellite_words(name)
    cands, keys = _ccf_exact(c, name, venue_type, base, sat)
    if cands:
        return cands[0]
    if not keys:
        return None, ""
    index, rows = _ccf_index(c)
    # 公认别名：KDD → SIGKDD、ACM Multimedia → ACM MM、NEUCOM → Neurocomputing …
    for kk in keys:
        alias = _CCF_ALIASES.get(kk)
        if alias:
            target, typ = alias
            hits = [(row, "alias") for row, _ in index.get(target, [])
                    if (not typ or row["type"] == typ) and not _satellite_conflict(sat, row)]
            if hits:
                hits.sort(key=_ccf_sort_key(venue_type))
                return hits[0]
    # 全称写法略有出入（单复数 / 标点 / the）：相似度 ≥ 0.92，且明显优于次优、不与输入期刊冲突
    # （预筛阈值放宽到 0.88，保证与最优只差 0.03 以内的竞争者也被算进来，唯一性判断才可靠）
    probes = [kk for kk in keys if len(kk) >= 12]
    if not probes:
        return None, ""
    scored = {}
    for kk in probes:
        sm = difflib.SequenceMatcher(None)
        sm.set_seq2(kk)               # seq2 的索引只建一次，逐个换 seq1（候选全称）
        for row in rows:
            if _satellite_conflict(sat, row):
                continue              # …Conference Workshops 与主会全称相似度可达 0.92+，不能当主会
            for cand in row["_names_n"]:
                if 2.0 * min(len(kk), len(cand)) / (len(kk) + len(cand)) < 0.88:
                    continue          # 长度差太大，相似度上限都不够
                sm.set_seq1(cand)
                if sm.quick_ratio() < 0.88:
                    continue
                s = sm.ratio()
                if s > scored.get(id(row), (0.0, None))[0]:
                    scored[id(row)] = (s, row)
    ranked = sorted(scored.values(), key=lambda x: -x[0])
    if not ranked or ranked[0][0] < 0.92:
        return None, ""
    best_s, best = ranked[0]
    best_name = best["_names_n"][0] if best["_names_n"] else ""
    # 同名多行（同一 venue 列在两个领域，如 IPL）不算竞争者
    second = next((s for s, r in ranked[1:] if (r["_names_n"] or [""])[0] != best_name), 0.0)
    if best_s - second < 0.03 or _journal_conflict(c, base, best):
        return None, ""
    return best, "similar"


def _strict_ccf(c, name: str, venue_type: str = ""):
    return _strict_ccf_match(c, name, venue_type)[0]


def _fuzzy_rank(ks: list[str], qt: list[str], row) -> int:
    """模糊候选排序：0 简称前缀 / 简称按词前缀 → 1 全称前缀 → 2 全称按词命中 → 3 仅词中子串（丢弃）。
    ks = 输入的归一键（含 & ↔ and 拼写），qt = 输入词序列；row 是 _ccf_index 缓存的行（带预分词）。"""
    na, nn = row["_abbr_n"], row["_name_n"]
    if any(na.startswith(k) for k in ks) or _tok_prefix(qt, row["_abbr_t"]):
        return 0
    if any(nn.startswith(k) for k in ks):
        return 1
    if _tok_prefix(qt, row["_name_t"]):
        return 2
    return 3


def _ccf_fuzzy(c, name: str, venue_type: str = "") -> list:
    """CCF 模糊候选（只用于速查与联想）：按词匹配，简称前缀 → 全称前缀 → 词前缀排序；
    progra-mm-ing 这类词中子串不算（MM ≠ PPoPP）。目录只有几百条，直接在缓存里筛。"""
    k = norm(name)
    if not k:
        return []
    ks = [k] + _fold_keys(name)
    qt = _tokens(name)
    _, rows = _ccf_index(c)
    ranked = []
    for r in rows:
        rk_ = _fuzzy_rank(ks, qt, r)
        if rk_ < 3:
            ranked.append((rk_, 0 if venue_type and r["type"] == venue_type else 1,
                           len(r["name"] or ""), r["seq"] or 0, r))
    ranked.sort(key=lambda x: x[:4])
    return [x[4] for x in ranked]


def lookup_ccf(name: str, venue_type: str = "", limit: int = 8) -> dict:
    """CCF 2026 目录查询（分级速查页 / 候选参考）：简称（NeurIPS/PR）与全称精确命中优先，
    否则按「简称前缀 → 全称前缀 → 词前缀」给模糊候选；venue_type 只作排序偏好，不作过滤。"""
    if not name or not name.strip():
        return {"ok": False, "error": "请输入名称", "matches": []}
    k = norm(name)
    if not k:
        return {"ok": False, "error": "名称无效", "matches": []}
    if not os.path.exists(DB_PATH):
        return {"ok": False, "error": "分级数据库未初始化（重启本系统会自动构建，或运行 python tools/build_rankings.py）", "matches": []}
    with closing(_conn()) as c:
        cands, _ = _ccf_exact(c, name, venue_type, _input_journal(c, name))
        rows = [r for r, _ in cands]
        if not rows:
            r = _strict_ccf_match(c, name, venue_type)[0]      # 别名 / 相似度兜底
            if r is not None:
                rows = [r]
        if not rows:
            rows = _ccf_fuzzy(c, name, venue_type)
        return {"ok": True, "matches": [_ccf_dict(r) for r in rows[:limit]]}


# ---------- 联想补全（简称 → 全称） ----------
def suggest(prefix: str, limit: int = 8) -> dict:
    """输入联想：支持期刊简称（ESWA/PR）、全称前缀（Pattern…）与会议简称（NeurIPS）。

    CCF 只按「词」匹配（简称 / 全称 / 全称中某个词的前缀），不拿词中子串（MM ≠ Programming）；
    返回合并候选（含全称与徽章摘要），选中后前端用全称建档并查分级。
    """
    if not prefix or not prefix.strip():
        return {"ok": True, "items": []}
    k = norm(prefix)
    if len(k) < 2 or not os.path.exists(DB_PATH):
        return {"ok": True, "items": []}
    items, seen = [], set()

    def add(journal_name, source, extra=None):
        nk = norm(journal_name)
        keys = [nk] + _fold_keys(journal_name)       # & 与 and 两种写法只出一条
        if not nk or any(x in seen for x in keys):
            return
        seen.update(keys)
        items.append({"name": journal_name, "source": source, **(extra or {})})

    def add_ccf(r):
        add(_split_former(r["name"])[0] if r["name"] else r["name"], f"CCF-{r['rank']} · {r['field']}",
            {"ccf": {"rank": r["rank"], "field": r["field"], "abbr": r["abbr"], "type": r["type"]}})

    with closing(_conn()) as c:
        # ① CCF：精确简称 / 别名优先（ESWA → Expert Systems with Applications；KDD → SIGKDD）
        ccf_row, how = _strict_ccf_match(c, prefix)
        if ccf_row is not None and how in ("abbr", "name", "alias", "fold"):
            add_ccf(ccf_row)
        # ② CCF：简称前缀 → 全称前缀 → 全称按词命中
        for r in _ccf_fuzzy(c, prefix):
            if len(items) >= limit:
                break
            add_ccf(r)
        # ③ 期刊分级库：全称前缀（含 & ↔ and 拼写，带徽章摘要）
        for kk in [k] + _fold_keys(prefix):
            if len(items) >= limit:
                break
            rows = c.execute(
                "SELECT * FROM journals WHERE norm_key LIKE ? AND journal<>''"
                " ORDER BY length(norm_key) LIMIT ?", (kk + "%", limit)).fetchall()
            for r in rows:
                add(r["journal"], "期刊 · " + (_brief_badges(_row_card(r)) or "已收录"))
    return {"ok": True, "items": items[:limit]}


# ---------- 严格匹配（徽章 / 建档向导用：宁缺毋滥，绝不拿子串模糊结果当徽章） ----------
def _strict_journal(c, name: str, ccf_row=None, how: str = ""):
    """期刊分级库严格匹配（精确键 + ISSN 拆行补齐）。

    CCF 期刊按简称/别名命中时只认 CCF 全称（AI → Artificial Intelligence，绝不落到同名的 MDPI《AI》；
    HEALTH 不落到另一本 Health）；按全称命中时先输入名、再 CCF 全称。
    """
    keys = _key_variants(name)
    if ccf_row is not None and ccf_row["type"] == "journal" and ccf_row["name"]:
        full = []
        for n in _split_former(ccf_row["name"]):     # 「现名（原名）」两种写法都能命中
            full += _key_variants(n)
        if not how:
            how = "abbr" if (ccf_row["norm_abbr"] or "") in keys else "name"
        keys = full if how in ("abbr", "alias") else keys + full
    seen = set()
    for kk in keys:
        if kk in seen:
            continue
        seen.add(kk)
        r = _journal_row(c, kk)
        if r is not None:
            return _merge_issn_siblings(c, r)
    return None


# ---------- 组合查询（建档向导用） ----------
def _strict_pair(name: str, venue_type: str = ""):
    from .venueutil import rank_query_name
    q = rank_query_name(name)
    if not q or not norm(q) or not os.path.exists(DB_PATH):
        return q, None, None
    with closing(_conn()) as c:
        ccf_row, how = _strict_ccf_match(c, q, venue_type)
        jr_row = None
        if ccf_row is not None and ccf_row["type"] == "journal":
            jr_row = _strict_journal(c, q, ccf_row, how)
        elif venue_type != "conference":
            jr_row = _strict_journal(c, q)
            if jr_row is not None and ccf_row is not None:
                # CCF 命中的是会议、库里又有同名期刊（SPIN / Computational Visual Media）：
                # 一张卡片绝不混搭两种 venue，按登记类型取一边（未标类型时以 CCF 会议为准）
                if venue_type == "journal":
                    ccf_row = None
                else:
                    jr_row = None
    return q, ccf_row, jr_row


def venue_rankings(name: str, venue_type: str = "", with_candidates: bool = True) -> dict:
    """给定会议/期刊名，返回 CCF + 期刊分级合并卡片（严格匹配；另附模糊候选供参考）。"""
    q, ccf_row, jr_row = _strict_pair(name, venue_type)
    if not q or not with_candidates:
        return {"ok": True, "name": q,
                "ccf": _ccf_dict(ccf_row) if ccf_row is not None else None,
                "journal": _row_card(jr_row) if jr_row is not None else None,
                "ccf_all": [], "journal_all": [], "hint": ""}
    ccf_all = lookup_ccf(q, venue_type)
    jr_all = lookup_journal(q, limit=4) if venue_type != "conference" else {"matches": []}
    return {
        "ok": True,
        "name": q,
        "ccf": _ccf_dict(ccf_row) if ccf_row is not None else None,
        "journal": _row_card(jr_row) if jr_row is not None else None,
        "ccf_all": ccf_all.get("matches", []),
        "journal_all": jr_all.get("matches", []),
        "hint": jr_all.get("hint", ""),
    }


def card_for_venue(name: str, venue_type: str = "") -> dict:
    """建档时生成存进 venues.rank_data 的紧凑卡片（只用严格匹配结果；"v" = RANK_VERSION）。"""
    r = venue_rankings(name, venue_type, with_candidates=False)
    out = {"v": RANK_VERSION, "queried_name": r.get("name") or (name or "").strip()}
    if r.get("ccf"):
        c = r["ccf"]
        out["ccf"] = {"rank": c["rank"], "field": c["field"],
                      "abbr": c["abbr"], "type": c["type"]}
    if r.get("journal"):
        j = r["journal"]
        out["journal"] = {
            "journal": j["journal"] or out["queried_name"],     # 仅在预警名单里出现的期刊没有刊名
            "cas": j["cas"], "xr": j["xr"], "jcr": j["jcr"],
            "warn2025": j["warn2025"] or "", "warn2024": j["warn2024"] or "",
        }
    return out


def badge_summary(rank_data: dict) -> list[dict]:
    """rank_data → 徽章列表（前端渲染用）。"""
    out = []
    if not rank_data or not isinstance(rank_data, dict):
        return out
    ccf = rank_data.get("ccf")
    if isinstance(ccf, dict) and ccf.get("rank"):
        out.append({"scope": "ccf", "label": f"CCF-{ccf['rank']}",
                    "title": " · ".join(x for x in (f"CCF 推荐目录 {_ed('ccf', '2026')}", ccf.get("field") or "",
                                                     ccf.get("abbr") or "") if x)})
    j = rank_data.get("journal") or {}
    if not isinstance(j, dict):
        j = {}
    cas = j.get("cas")
    if isinstance(cas, dict) and cas.get("part"):
        out.append({"scope": "cas", "label": f"中科院{cas['part']}区" + ("Top" if cas.get("top") else ""),
                    "title": f"中科院分区表 {_ed('cas', '')}（你导入的数据）· {cas.get('field') or ''} · {cas.get('wos') or ''}"})
    xr = j.get("xr")
    if isinstance(xr, dict) and xr.get("part"):
        out.append({"scope": "xr", "label": f"新锐{xr['part']}区" + ("Top" if xr.get("top") else ""),
                    "title": f"新锐期刊分区表 {_ed('xr', '')}（你导入的数据）· {xr.get('field') or ''}"})
        wl = _xr_warning_label(xr.get("warning"))
        if wl:
            out.append({"scope": "warn", "label": wl,
                        "title": f"新锐期刊分区表 {_ed('xr', '')} 预警标记：{xr.get('warning')}"})
    jl = _jcr_label(j.get("jcr"))
    if jl:
        out.append({"scope": "jcr", "label": jl,
                    "title": f"JCR {_ed('jcr', '')}（你导入的数据，Clarivate）· {(j.get('jcr') or {}).get('category') or ''}"})
    if j.get("warn2025"):
        out.append({"scope": "warn", "label": "⚠ 预警2025",
                    "title": f"中科院预警名单2025：{j['warn2025']}"})
    elif j.get("warn2024"):
        out.append({"scope": "warn", "label": "⚠ 预警2024",
                    "title": f"中科院预警名单2024：{j['warn2024']}"})
    return out


# ---------- 改投建议（被拒后推荐同领域、同级或低一级的会议/期刊） ----------
def similar_venues(name: str, venue_type: str = "", limit: int = 12) -> dict:
    """按本地 CCF 目录：与原投会议/期刊同领域、同级或低一级的候选，同类型（会议→会议）优先。"""
    from .venueutil import rank_query_name
    q = rank_query_name(name)
    if not q or not os.path.exists(DB_PATH):
        return {"ok": True, "base": None, "items": []}
    order = {"A": 0, "B": 1, "C": 2}
    with closing(_conn()) as c:
        base = _strict_ccf(c, q, venue_type)
        if base is None:
            return {"ok": True, "base": None, "items": []}
        _, rows = _ccf_index(c)
        br = order.get(base["rank"], 3)
        tp = venue_type or base["type"]
        items = [r for r in rows
                 if r["field"] == base["field"]
                 and not (r["abbr"] == base["abbr"] and r["name"] == base["name"])
                 and br <= order.get(r["rank"], 3) <= br + 1]
        items.sort(key=lambda r: (0 if r["type"] == tp else 1, order.get(r["rank"], 3), r["seq"] or 0))
        return {"ok": True, "base": _ccf_dict(base), "items": [_ccf_dict(r) for r in items[:limit]]}
