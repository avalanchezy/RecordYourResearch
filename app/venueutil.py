# -*- coding: utf-8 -*-
"""会议/期刊名称工具：年份解耦、OpenReview 噪声后缀清理、展示名、标题归一。

约定（全系统统一）：
- venues.name 存「不含末尾年份」的名称（NeurIPS、ICML、ICLR 2025 Workshop ICBINB）；
- venues.year 单独存年份；展示名 = name + 年份（name 中已含该年份则不重复拼接）；
- 分级徽章匹配用 rank_query_name()：去掉年份与噪声词后再查。
"""
import difflib
import re

# 会议年份只认 2010–2099：「Periodontology 2000」这类刊名里的数字不当年份拆走
YEAR_RE = re.compile(r"(?<!\d)(20[1-9]\d)(?!\d)")
_TRAIL_YEAR_RE = re.compile(r"\s+(20[1-9]\d)$")
# OpenReview 原始 venue 串常带的尾巴：Conference Submission / Withdrawn Submission / Submission …
_NOISE_TAIL_RE = re.compile(
    r"\s*(?:(?:Conference|Journal)\s+)?"
    r"(?:(?:Desk\s+)?Rejected\s+|Withdrawn\s+|Blind\s+)?Submissions?\s*$", re.I)
_YEAR_CONF_TAIL_RE = re.compile(r"((?<!\d)20[1-9]\d)\s+Conference$", re.I)


def clean_name(name: str) -> str:
    """去掉 OpenReview 噪声尾巴：「ICML 2025 Conference Submission」→「ICML 2025」。"""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    prev = None
    while prev != name:
        prev = name
        name = _NOISE_TAIL_RE.sub("", name).strip()
        name = _YEAR_CONF_TAIL_RE.sub(r"\1", name).strip()   # COLM 2026 Conference → COLM 2026
    return name


def split_year(name: str, year="") -> tuple[str, str]:
    """名称与年份解耦：('NeurIPS 2025','') → ('NeurIPS','2025')；名称末尾的年份优先。

    年份在名称中间（ICLR 2025 Workshop …）时保留原名，只在 year 为空时补齐年份。
    """
    name = clean_name(name)
    year = str(year or "").strip()
    m = _TRAIL_YEAR_RE.search(name)
    if m and m.start() > 0:
        return name[:m.start()].strip(), m.group(1)
    if not year:
        m2 = YEAR_RE.search(name)
        if m2:
            year = m2.group(1)
    return name, year


def display(name: str, year="") -> str:
    """展示名：name + 年份；name 中已出现该年份则不重复。"""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    year = str(year or "").strip()
    if year and not re.search(r"(?<!\d)" + re.escape(year) + r"(?!\d)", name):
        return f"{name} {year}".strip()
    return name


def rank_query_name(name: str) -> str:
    """分级匹配用名称：去年份、去 OpenReview 噪声。"""
    n = clean_name(name)
    n = YEAR_RE.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


# ---------- 标题归一 / 相似度（同一篇判定、转投扩写提示） ----------
_TITLE_KEEP = re.compile(r"[^a-z0-9一-鿿]+")


def norm_title(s: str) -> str:
    return _TITLE_KEEP.sub("", str(s or "").lower())


def _title_head(s: str) -> str:
    """冒号前的方法名（DemoNet: …）→ 归一；不足 4 字符视为无。"""
    s = str(s or "")
    if ":" not in s:
        return ""
    head = norm_title(s.split(":", 1)[0])
    return head if len(head) >= 4 else ""


def title_similarity(a: str, b: str) -> float:
    """0~1：完全相同 1.0；同方法名（冒号前）0.9；否则按字符序列相似度。"""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ha, hb = _title_head(a), _title_head(b)
    if ha and ha == hb:
        return 0.9
    return difflib.SequenceMatcher(None, na, nb).ratio()


def is_similar_title(a: str, b: str, threshold: float = 0.85) -> bool:
    return title_similarity(a, b) >= threshold


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(s: str) -> bool:
    """YYYY-MM-DD 且是真实日期。"""
    from datetime import datetime
    s = str(s or "").strip()
    if not DATE_RE.match(s):
        return False
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False
