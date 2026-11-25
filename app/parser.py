# -*- coding: utf-8 -*-
"""PDF 元数据抽取。

两条路径：
① Editorial Manager 投稿封面页（结构化标签版式，Elsevier 渠道投稿 PDF 的常见形态）
   → 按标签行直接收割：稿件编号 / 文章类型 / 关键词 / 通讯作者 / 作者列表 / 摘要 / 期刊名；
   仅在出现 EM 专属证据时启用（「--Manuscript Draft--」行、「Powered by Editorial Manager」页脚，
   或 ≥3 个 EM 标签且含 Manuscript Number / Order of Authors）——普通论文首页的「Keywords:」不触发；
② 通用学术版式（arXiv / 会议模板）
   → 最大字号标题行 + 标题下方作者行 + Abstract/Keywords 标签定位（按栏收集，双栏不串行）；
   页眉 / 页脚水印识别会议、年份与双盲（ICLR / NeurIPS / ICML / CVF / ECCV / AAAI 等模板）。

抽取结果作为「暂存草稿」返回，关键字段由用户在界面确认后归档。
加密 / 无页面 / 无法读取的 PDF 抛 ValueError（中文提示）；文档句柄总会关闭，调用方（Windows）可随即删除上传文件。
"""
import gc
import os
import re
import unicodedata
from datetime import datetime

import pymupdf

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b")
ARXIV_RE = re.compile(r"\barxiv[:\s]*(\d{4}\.\d{4,5})(v\d+)?\b", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# arXiv 侧边水印「arXiv:2601.01234v1 [cs.CV] 2 Jan 2026」（通常旋转 90°；被抽成横排时同样排除）
ARXIV_STAMP_RE = re.compile(r"^arxiv:\s*(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v\d+)?\b", re.I)

EM_LABELS = ("Manuscript Number", "Article Type", "Keywords",
             "Corresponding Author", "First Author", "Order of Authors", "Abstract")
# EM 封面上其他标签（不收割，但作为字段边界——否则会被并进上一个字段，如作者列表）
EM_OTHER_LABELS = ("Full Title", "Short Title", "Section/Category", "Classifications",
                   "Corresponding Author Secondary Information",
                   "Corresponding Author's Institution",
                   "Corresponding Author's Secondary Institution",
                   "First Author Secondary Information",
                   "Order of Authors Secondary Information",
                   "Funding Information", "Suggested Reviewers", "Opposed Reviewers",
                   "Additional Information", "Author Comments", "Manuscript Region of Origin",
                   "Manuscript Classifications", "Question", "Response")
EM_LABEL_RE = re.compile(r"^(" + "|".join(re.escape(x) for x in EM_LABELS) + r")\s*:\s*(.*)$")
EM_ANY_LABEL_RE = re.compile(
    r"^(" + "|".join(re.escape(x) for x in EM_LABELS + EM_OTHER_LABELS) + r")\s*:\s*(.*)$")
# 跨行断开的标签尾巴（「Corresponding Author Secondary」+「Information:」）或任意「Xxx:」裸标签行
BARE_LABEL_RE = re.compile(r"^[A-Z][A-Za-z'’/ ()&-]{1,60}:\s*$")
DRAFT_MARK_RE = re.compile(r"^--\s*Maniscript Draft\s*--$|^--\s*Manuscript Draft\s*--$", re.I)
FOOTER_RE = re.compile(r"Powered by (Editorial Manager|ProduXion|Aries)", re.I)
NAME_SUFFIX_RE = re.compile(r",?\s*(Ph\.?D\.?|D\.?Phil\.?|Prof\.?|Dr\.?|Mr\.?|Ms\.?|"
                            r"M\.?D\.?|M\.?Sc\.?|Sr\.?|Jr\.?)\s*$", re.I)

# 投稿日期提示：第 2 页（通常是 cover letter）顶部单独成行的日期
_MONTH = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
# 可选前缀：「Date:」、星期或地点（「Wednesday, 15 January 2025」「Lyon, January 15, 2025」）
_DATE_LEAD = r"^(?:Date\s*:\s*)?(?:[A-Z][A-Za-z.'’ -]{1,40},\s*){0,2}"
# 「January 15, 2025」「Jan. 15, 2025」「Jan 15th, 2025」
DATE_ROW_RE = re.compile(_DATE_LEAD + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*|\s+)(\d{4})\.?$",
                         re.I)
# 「15 January 2025」「15th Jan. 2025」
DATE_ROW_DMY_RE = re.compile(_DATE_LEAD + r"(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r",?\s+(\d{4})\.?$",
                             re.I)
# 「2025-01-15」「2025年1月15日」
DATE_ROW_ISO_RE = re.compile(_DATE_LEAD + r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?$")
_MONTH_NUM = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}

# 已知会议缩写 → 渠道提示（OpenReview 承办的可自动追踪）；识别结果统一成这里的规范写法
KNOWN_CONFS = {
    "NeurIPS", "ICLR", "ICML", "COLM", "AAAI", "CVPR", "ICCV", "ECCV",
    "ICRA", "IROS", "CoRL", "RSS", "ACL", "EMNLP", "NAACL", "COLING",
    "SIGMOD", "VLDB", "KDD", "WWW", "SIGIR", "WSDM", "CIKM", "ICDE",
    "CVPRW", "WACV", "BMVC", "ACCV", "AISTATS", "UAI", "ECAI", "IJCAI",
    "TMLR", "IPMI", "MICCAI",
}
_CONF_CANON = {c.upper(): c for c in KNOWN_CONFS}
_CONF_CANON["NIPS"] = "NeurIPS"

# 会议页眉/页脚标记（按特异性从高到低尝试）
# CVF：「ICCV 2025 Submission #1234. CONFIDENTIAL REVIEW COPY. DO NOT DISTRIBUTE.」
CONF_MARK_RE = re.compile(r"\b([A-Z][A-Za-z]{1,9})\s*['’]?\s*(20\d{2})\s+[Ss]ubmission\b")
# ECCV：「ECCV-26 submission ID 1234」（两位年份）
CONF_SHORT_YEAR_RE = re.compile(r"\b([A-Z]{3,8})\s*-\s*(\d{2})\s+[Ss]ubmission\b")
# ICLR / COLM：「Under review as a conference paper at ICLR 2026」「Published as a conference paper at ICLR 2025」
CONF_PAPER_AT_RE = re.compile(
    r"(?:Under\s+review|Published|Accepted|Appearing)\s+as\s+an?\s+(?:[A-Za-z]+\s+){0,2}?paper\s+at\s+"
    r"(?:the\s+)?(?:(?:IEEE/CVF|IEEE|ACM)\s+)?([A-Z][A-Za-z]{1,9})(?:\s*['’]?\s*(20\d{2}))?", re.I)
# TMLR：「Under review as submission to TMLR」「Published in Transactions on Machine Learning Research (05/2025)」
CONF_TMLR_RE = re.compile(r"(?:Under\s+review\s+as\s+(?:a\s+)?submission\s+to|Published\s+in)\s+"
                          r"(?:TMLR|Transactions\s+on\s+Machine\s+Learning\s+Research)\b", re.I)
# NeurIPS / ICML 页脚：「…Conference on Neural Information Processing Systems (NeurIPS 2025)」「…(ICML)」
CONF_PAREN_RE = re.compile(
    r"\((" + "|".join(re.escape(x) for x in sorted(_CONF_CANON, key=len, reverse=True)) +
    r")\s*['’]?\s*(20\d{2}|\d{2})?\)", re.I)
CONF_CONTEXT_RE = re.compile(r"Conference|Proceedings|Symposium|Workshop|Submitted|Under\s+review|"
                             r"Accepted|Published|Appear", re.I)
# 会议全称（论文集页脚 / 版权行）；前文须有 Submitted to / Proceedings of / Copyright 等语境
CONF_FULLNAMES = tuple((re.compile(p, re.I), abbr) for p, abbr in (
    (r"Conference\s+on\s+Neural\s+Information\s+Processing\s+Systems", "NeurIPS"),
    (r"International\s+Conference\s+on\s+Learning\s+Representations", "ICLR"),
    (r"International\s+Conference\s+on\s+Machine\s+Learning(?!\s+(?:and|for|in|with)\b)", "ICML"),
    (r"Conference\s+on\s+Computer\s+Vision\s+and\s+Pattern\s+Recognition(?!\s+Workshops?\b)", "CVPR"),
    (r"International\s+Conference\s+on\s+Computer\s+Vision(?!\s+(?:and|Systems|Theory|Workshops?)\b)",
     "ICCV"),
    (r"European\s+Conference\s+on\s+Computer\s+Vision", "ECCV"),
    (r"Association\s+for\s+the\s+Advancement\s+of\s+Artificial\s+Intelligence|"
     r"AAAI\s+Conference\s+on\s+Artificial\s+Intelligence", "AAAI"),
    (r"International\s+Joint\s+Conference\s+on\s+Artificial\s+Intelligence", "IJCAI"),
    (r"Conference\s+on\s+Language\s+Modeling", "COLM"),
))
CONF_FULLNAME_CTX_RE = re.compile(r"Submitted\s+to|Under\s+review|Proceedings\s+of|Published|Accepted|"
                                  r"Copyright|©|Appear|Presented\s+at", re.I)
# arXiv 版脚注：「Submitted to ICCV 2025」
CONF_SUBMITTED_RE = re.compile(
    r"Submitted\s+to\s+(?:the\s+)?(?:\d+(?:st|nd|rd|th)\s+)?(?:(?:IEEE/CVF|IEEE|ACM)\s+)?"
    r"([A-Z][A-Za-z]{1,9})\s*['’]?\s*(20\d{2})\b")
# 前作 / 扩展版说明：「A preliminary version of this work appeared in … (CVPR 2023)」「extended version of our
# ICCV paper」——说的是旧版本所在的会议，不是本稿投向的会议
CONF_PRIOR_RE = re.compile(
    r"\b(?:preliminary|earlier|previous|prior|shorter|conference|initial|early)\s+version|"
    r"\bextended\s+(?:version|from)\b|\bextension\s+of\b|\bextends\s+(?:our|the|an?)\b", re.I)
# 本稿自身的投稿 / 发表标记（句首大写）；出现在前作说明之后时以它为准
CONF_OWN_RE = re.compile(r"Submitted\s+to|Under\s+review|Preliminary\s+work|Copyright|©|Do\s+not\s+distribute|"
                         r"DO\s+NOT\s+DISTRIBUTE|To\s+appear|Published\s+as|CONFIDENTIAL")
# 评审稿 / 双盲提示
DOUBLE_BLIND_RE = re.compile(r"CONFIDENTIAL\s+REVIEW\s+COPY|DO\s+NOT\s+DISTRIBUTE|Under\s+review|"
                             r"double[-\s]?blind|\banonymous\b|\banonymi[sz]ed\b|"
                             r"\bsubmission\s*(?:ID|#|No\.)", re.I)

# 双盲匿名作者行：「Anonymous CVPR submission」「Anonymous submission」「Anonymous Author(s)」
# 「Anonymous authors / Paper under double-blind review」「Anonymized Authors」
ANON_AUTHOR_RE = re.compile(
    r"^anonymous\b.*\bsubmission\b|^anonymous\s+author|^anonymi[sz]ed\b|^anonymous\s*$|"
    r"\bpaper\s+under\s+double[-\s]?blind\s+review\b|\bauthors?\s+anonymi[sz]ed\b", re.I)
# 作者行里写明会议：「Anonymous CVPR submission」「Anonymous ECCV 2026 Submission」
ANON_VENUE_RE = re.compile(r"^Anonymous\s+([A-Z][A-Za-z]{1,9})(?:[\s-]+(20\d{2}|\d{2}))?\s+[Ss]ubmission\b")
# AAAI 模板匿名稿的作者行就是一句「Anonymous submission」
ANON_SUBMISSION_RE = re.compile(r"^Anonymous\s+[Ss]ubmission\s*$")

# 通用版式：摘要 / 关键词 / 正文起点
ABSTRACT_HEAD_RE = re.compile(r"^(?:[Aa]bstract|ABSTRACT|A\s+B\s+S\s+T\s+R\s+A\s+C\s+T|摘\s*要)(?![a-z])")
ABSTRACT_LABEL_RE = re.compile(r"^(?:abstract|a\s+b\s+s\s+t\s+r\s+a\s+c\s+t|摘\s*要)\s*[:.—–\-·]*\s*", re.I)
KW_LABEL_RE = re.compile(
    r"^(?:(?:additional\s+)?key\s*words?(?:\s+and\s+phrases)?|index\s+terms)(?![a-z])\s*[:.—–\-·]*\s*|"
    r"^(?:关键词|关键字)\s*:?\s*", re.I)
SECTION_STOP_RE = re.compile(
    r"^(?:(?:\d{1,2}|[ivx]{1,4})\.?\s+)?introduction\b|"        # 1 Introduction / 1. Introduction / I. INTRODUCTION
    r"^(?:additional\s+)?key\s*words?(?![a-z])|^index\s+terms\b|^关键词|^关键字|"
    r"^ccs\s+concepts\b|^acm\s+reference\s+format\b|^general\s+terms\b|"
    r"^(?:preprint\s+submitted|submitted\s+to|under\s+review|published\s+as|proceedings\s+of)\b|"
    r"^(?:©|\(c\)\s|copyright\b)|^corresponding\s+author|^e-?mail\s+address|^[*∗†‡]", re.I)
_KW_SEP_IN_LABEL_RE = re.compile(r"[:：.—–\-·]")
# 小写的独立标题行（小型大写字母字体抽出来是小写）：整行只有标题词
_LOWER_HEADING_RE = re.compile(r"(?:\d{1,2}\.?\s+)?(?:introduction|key\s*words?|index\s+terms|ccs\s+concepts|"
                               r"general\s+terms)\s*[:.]?")


def _is_kw_label(text: str) -> bool:
    """关键词标签行。小写开头（摘要折行恰好以「keywords are …」起头）须带分隔符或整行只有标签才算。"""
    m = KW_LABEL_RE.match(text)
    if not m:
        return False
    return (not text[:1].islower() or bool(_KW_SEP_IN_LABEL_RE.search(m.group(0)))
            or bool(_LOWER_HEADING_RE.fullmatch(text.strip())))


def _is_section_stop(text: str) -> bool:
    """摘要 / 作者区的终止行（Introduction / Keywords / 版权脚注…）。

    小写开头的是正文折行（「…the\\nintroduction of …」「…\\nsubmitted to a server」），不算；
    整行只有标题词的小写行（小型大写字母标题）仍算。
    """
    if not SECTION_STOP_RE.match(text):
        return _is_kw_label(text)
    return not text[:1].islower() or bool(_LOWER_HEADING_RE.fullmatch(text.strip())) or _is_kw_label(text)


NUM_HEADING_RE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVX]{1,5}\.)\s+[A-Z][A-Za-z][A-Za-z\- ]{0,48}$")
# 单位 / 邮箱行（作者区到此为止）
AFFIL_RE = re.compile(
    r"\b(?:Universit(?:y|ies|ät|é|à|at|eit|ad|e)|Univ\.|Institut(?:e|o|ion)?|Department|Dept\.?|"
    r"School|Laborator(?:y|ies)|Lab|Labs|College|Academy|Faculty|Cent(?:er|re)|Hospital|"
    r"Inc\.?|Ltd\.?|Corporation|Corp\.|GmbH|Research|Affiliations?|Address)\b|"
    r"大学|学院|研究院|研究所|实验室|公司|医院", re.I)
IEEE_GRADE_RE = re.compile(r",?\s*\b(?:(?:Senior|Student|Life|Graduate\s+Student)\s+)?(?:Member|Fellow),?\s*IEEE\b",
                           re.I)
ORCID_RE = re.compile(r"[\[(]?\d{4}-\d{4}-\d{4}-\d{3}[\dX][\])]?")
NAME_SPLIT_RE = re.compile(r"\s*(?:,|;|·|•|、|\band\b|\bAND\b|&|\|)\s*")
FOOTNOTE_MARK_RE = re.compile(r"[*∗†‡§¶#✉⋆✝♠♣♥♦]+")
# 小号上标片段（单位角标 1,2 / a,b / * / †），抽作者时视作姓名分隔
_SUP_SPAN_RE = re.compile(r"^[\d\s,.*∗†‡§¶#✉⋆✝a-h]{1,12}$")


# ---------- 文本清洗 ----------
_INVISIBLE_RE = re.compile(r"[­᠎​-‏‪-‮⁠-⁤﻿]")
# C0 控制字符（保留 \t \n \r）：字体缺字形 / ToUnicode 损坏时 PDF 会抽出 \x00 之类
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_invisible(s: str) -> str:
    """去掉零宽字符 / 软连字符 / 方向控制符并去首尾空白（Word 导出的 PDF 常在标题末尾带 U+200B）。"""
    return _INVISIBLE_RE.sub("", str(s or "")).strip()


def _clean_line(s: str) -> str:
    """行级清洗：NFKC 兼容归一（ﬁ 连字、全角标点、不换行空格）+ 去不可见字符。

    PDF 里行内的 U+00AD 是画出来的连字符（部分字体把「-」映射成软连字符），词中间的还原成「-」，
    行尾的（断词）照旧去掉。
    """
    s = re.sub(r"(?<=\w)­(?=\w)", "-", unicodedata.normalize("NFKC", s or ""))
    s = _CONTROL_RE.sub("", s)
    return _INVISIBLE_RE.sub("", s).strip()


_CJK_RE = r"[　-〿㐀-鿿豈-﫿＀-￯]"


def _join_lines(parts) -> str:
    """多行拼接：中文行之间不加空格，其余用空格。"""
    out = ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        if out and not (re.search(_CJK_RE + "$", out) and re.match(_CJK_RE, p)):
            out += " "
        out += p
    return re.sub(r"\s+", " ", out).strip()


# ---------- 基础行结构 ----------
_TITLE_MARK_RE = re.compile(r"^[*∗†‡§¶⋆☆★✝]+$")


def _strip_title_mark(spans, size) -> str:
    """去掉行尾小号上标脚注符号后的行文本（标题「…Networks∗」「Title⋆」里 \\thanks 的标记）。

    只认 ∗ † ‡ § ¶ ⋆ ☆ 等符号，且前一字符是小写字母或句末标点（「A∗」这类名字里的星号保留）；
    数字角标不动（可能是 L² 之类的标题内容）。
    """
    spans = [s for s in spans if s.get("text")]
    if not spans:
        return ""
    base = next((s for s in spans if s["size"] >= size - 0.01), spans[0])
    base_y = base.get("origin", (0.0, 0.0))[1]
    k = len(spans)
    while k > 1:
        s = spans[k - 1]
        st = _CONTROL_RE.sub("", s["text"]).strip()
        if not st:
            k -= 1
            continue
        flags = s.get("flags", 0)
        small = s["size"] <= size * 0.8 or flags & 1
        raised = flags & 1 or s.get("origin", (0.0, base_y))[1] < base_y - 0.15 * size
        if small and raised and _TITLE_MARK_RE.match(st):
            k -= 1
            continue
        break
    kept = "".join(s["text"] for s in spans[:k])
    last = kept.rstrip()[-1:]
    if k == len(spans) or not last or not (last.islower() or last in "?!.)]"):
        kept = "".join(s["text"] for s in spans)
    return _clean_line(kept)


def _page_lines(page):
    """页面横排文本行：[{text,size,y,y1,x0,x1,bold,nosup,core}]，过滤空行。

    旋转 / 竖排文字（arXiv 侧边水印等，line dir ≠ (1,0)）与 arXiv 水印行不参与版面分析；
    nosup = 小号上标片段（单位角标）替换成逗号后的文本，只用于切分作者；
    core = 去掉行尾上标脚注符号后的文本，只用于拼标题。
    """
    d = page.get_text("dict")
    out = []
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            if ln.get("dir", (1.0, 0.0))[0] < 0.98:     # 非横排（旋转 90° 的水印 / 竖排表头）
                continue
            spans = ln["spans"]
            text = _clean_line("".join(s["text"] for s in spans))
            if not text or ARXIV_STAMP_RE.match(text):
                continue
            size = max(s["size"] for s in spans)
            nosup = []
            for s in spans:
                st = s["text"].strip()
                small = s["size"] <= size * 0.8 or s.get("flags", 0) & 1
                nosup.append("," if st and small and _SUP_SPAN_RE.match(st) else s["text"])
            x0, y0, x1, y1 = ln["bbox"]
            out.append({"text": text, "size": round(size, 1), "y": round(y0, 1), "y1": round(y1, 1),
                        "x0": round(x0, 1), "x1": round(x1, 1),
                        "bold": any(s.get("flags", 0) & 16 or "bold" in s.get("font", "").lower()
                                    for s in spans),
                        "nosup": _clean_line("".join(nosup)), "core": _strip_title_mark(spans, size)})
    out.sort(key=lambda x: (x["y"], x["size"]))
    return out


def _rows(lines):
    """同一视觉行的多个 span 合并为一行（EM 标签与值常同 y 并排）。"""
    rows = []
    for l in lines:
        if rows and abs(rows[-1]["y"] - l["y"]) < 2.5:
            if not rows[-1]["text"].endswith(" "):
                rows[-1]["text"] += " "
            rows[-1]["text"] += l["text"]
            rows[-1]["size"] = max(rows[-1]["size"], l["size"])
        else:
            rows.append({"text": l["text"], "size": l["size"], "y": l["y"]})
    return rows


def _is_noise(l) -> bool:
    """行号 / 页码（纯数字）——评审稿页边行号 000/001… 不参与版面分析。"""
    return bool(re.fullmatch(r"\d{1,4}(?:\s+\d{1,4})*", l["text"]))


def _in_col(l, col) -> bool:
    """行的大部分宽度落在栏 [x0,x1] 内（双栏时另一栏的行、跨栏宽行都不算）。"""
    ov = min(l["x1"], col[1]) - max(l["x0"], col[0])
    return ov >= 0.6 * max(1.0, l["x1"] - l["x0"])


def _visual_rows(lines):
    """按基线把行合并为视觉行，保留横向片段：片段间距很大（作者并排网格）时用逗号分隔。"""
    rows = []
    for l in sorted(lines, key=lambda l: (l["y"], l["x0"])):
        if rows and abs(rows[-1]["y"] - l["y"]) < max(2.5, 0.3 * l["size"]):
            rows[-1]["segs"].append(l)
        else:
            rows.append({"y": l["y"], "segs": [l]})
    for r in rows:
        segs = sorted(r["segs"], key=lambda s: s["x0"])
        text = nosup = ""
        for i, s in enumerate(segs):
            sep = ""
            if i:
                gap = s["x0"] - segs[i - 1]["x1"]
                sep = ", " if gap > 1.5 * max(s["size"], segs[i - 1]["size"]) else " "
            text += sep + s["text"]
            nosup += sep + s["nosup"]
        r.update(segs=segs, text=text, nosup=nosup, size=max(s["size"] for s in segs),
                 y1=max(s["y1"] for s in segs), x0=min(s["x0"] for s in segs),
                 x1=max(s["x1"] for s in segs))
    return rows


# ---------- EM 结构化封面 ----------
def _name_to_pinyin(name: str) -> str:
    """「Xiaoming Wang, Ph.D.」→「XIAOMING WANG」（爱思唯尔查询凭据，草稿值可改）。"""
    base = NAME_SUFFIX_RE.sub("", name.split(",")[0]).strip()
    tokens = base.split()
    if len(tokens) >= 2:
        return f"{tokens[0]} {tokens[-1]}".upper()
    return ""


def _split_keywords(text: str) -> list[str]:
    # 优先按分号切；其次按 LNCS 的「·」；都没有时按逗号切（部分期刊用逗号分隔关键词）
    if re.search(r"[;；]", text):
        sep = r"[;；]"
    elif re.search(r"[·•⋅∙]", text):
        sep = r"[·•⋅∙]"
    else:
        sep = r"[,，]"
    parts = re.split(sep, text)
    parts = [re.sub(r"\s+", " ", p).strip().rstrip(",，").strip() for p in parts]
    parts = [re.sub(r"(?<![A-Z])[.。]$", "", p).strip() for p in parts]   # IEEE「…, robustness.」
    return [p for p in parts if p and len(p) > 1]


def _em_evidence(rows) -> bool:
    """EM 专属证据：草稿标记行 / EM 页脚 / ≥3 个 EM 标签且含 Manuscript Number 或 Order of Authors。"""
    labels = set()
    for r in rows:
        t = r["text"].strip()
        if DRAFT_MARK_RE.match(t) or FOOTER_RE.search(t):
            return True
        m = EM_ANY_LABEL_RE.match(t)
        if m:
            labels.add(m.group(1))
    return len(labels) >= 3 and bool(labels & {"Manuscript Number", "Order of Authors"})


def _em_journal_rows(head):
    """EM 封面期刊名行：页首第一行（靠上、在标题块之上、非标签、字号不小于标题），可含冒号。

    head = 草稿标记（或首个标签）之上、字号 ≥11 的非标签行；最后一行必属标题块。
    """
    if len(head) < 2 or head[0]["y"] >= 60:
        return []
    first, title_size = head[0], head[-1]["size"]
    if first["size"] < title_size:
        return []
    out = [first]
    if first["size"] > title_size + 0.5:       # 期刊名过长折成两行：同字号、且明显大于标题字号
        for r in head[1:-1]:
            if abs(r["size"] - first["size"]) <= 0.3 and r["y"] - out[-1]["y"] <= r["size"] * 1.8:
                out.append(r)
            else:
                break
    return out


def _parse_em_rows(rows):
    """识别 Editorial Manager 投稿封面页并按标签收割字段；非该版式返回 None。"""
    labeled_idx = [i for i, r in enumerate(rows) if EM_LABEL_RE.match(r["text"])]
    if not labeled_idx or not _em_evidence(rows):
        return None
    info = {"journal_name": "", "title": "", "manuscript_number": "", "article_type": "",
            "keywords": [], "corresponding_author": "", "corresponding_affiliation": "",
            "corresponding_pinyin": "", "authors": [], "abstract": ""}
    draft_mark = next((i for i, r in enumerate(rows) if DRAFT_MARK_RE.match(r["text"])), None)

    # 期刊名 / 标题按结构识别：草稿标记（或首个标签）之上 = 期刊名行 + 标题块
    end = draft_mark if draft_mark is not None else labeled_idx[0]
    head = [r for r in rows[:end] if r["size"] >= 11 and not EM_ANY_LABEL_RE.match(r["text"])
            and not DRAFT_MARK_RE.match(r["text"])]
    journal_rows = _em_journal_rows(head)
    info["journal_name"] = " ".join(r["text"].strip() for r in journal_rows)
    title_rows = head[len(journal_rows):]
    info["title"] = re.sub(r"\s+", " ", " ".join(r["text"] for r in title_rows)).strip()

    # 各标签字段：值 = 标签同行值 + 后续行（直到下一个标签 / 页脚）
    for idx in labeled_idx:
        m = EM_LABEL_RE.match(rows[idx]["text"])
        label, val = m.group(1), m.group(2).strip()
        buf = []
        for j in range(idx + 1, len(rows)):
            t = rows[j]["text"].strip()
            if EM_ANY_LABEL_RE.match(t) or FOOTER_RE.search(t) or DRAFT_MARK_RE.match(t):
                break
            # 摘要可能含「Background:」等小标题，不按裸标签截断；其他字段遇到裸标签即止
            if label != "Abstract" and (BARE_LABEL_RE.match(t) or
                                        t.startswith(("Corresponding Author", "First Author",
                                                      "Order of Authors"))):
                break
            buf.append(t)
        if label == "Manuscript Number":
            info["manuscript_number"] = re.sub(r"\s+", "", val or (buf[0] if buf else ""))
        elif label == "Article Type":
            info["article_type"] = (val + " " + " ".join(buf)).strip()
        elif label == "Keywords":
            info["keywords"] = _split_keywords(val + " " + " ".join(buf))
        elif label == "Corresponding Author":
            info["corresponding_author"] = NAME_SUFFIX_RE.sub("", val).strip()
            info["corresponding_affiliation"] = " / ".join(
                b for b in buf if b and len(b) < 80)[:200]
        elif label == "First Author":
            if val and val not in info["authors"]:
                info["authors"].append(NAME_SUFFIX_RE.sub("", val).strip())
        elif label == "Order of Authors":
            names = [NAME_SUFFIX_RE.sub("", val).strip()] + \
                    [NAME_SUFFIX_RE.sub("", b).strip() for b in buf]
            info["authors"] = [n for n in names if n and 1 < len(n) < 60][:30]
        elif label == "Abstract":
            info["abstract"] = re.sub(r"\s+", " ", (val + " " + " ".join(buf)).strip())
    if info["corresponding_author"]:
        info["corresponding_pinyin"] = _name_to_pinyin(info["corresponding_author"])
    if not info["title"] and not info["abstract"]:
        return None
    return info


# ---------- 通用版式兜底 ----------
def _find_title(lines, page_h=792.0):
    """首页最大字号横排文本行（连续同字号行合并）；排除页眉页脚、行号、arXiv 水印。

    返回 (标题, 标题各行)；作者窗口从标题最后一行下方开始。
    """
    if not lines:
        return "", []
    body = [l for l in lines if page_h * 0.05 < l["y"] < page_h * 0.91 and not _is_noise(l)]
    upper = [l for l in body if l["y"] < page_h * 0.6]
    cand = [l for l in (upper or body or lines) if len(re.findall(r"[^\W\d_]", l["text"])) >= 3]
    if not cand:
        return "", []
    max_size = max(l["size"] for l in cand)
    if max_size < 11:
        return "", []
    hits = sorted((l for l in cand if l["size"] >= max_size - 0.6), key=lambda l: (l["y"], l["x0"]))
    used, last_y = [], None
    for l in hits:
        if last_y is None or (l["y"] - last_y) <= l["size"] * 1.8:
            used.append(l)
            last_y = l["y"]
        else:
            break
    title = ""
    for l in used:
        t = (l.get("core") or l["text"]).strip()       # 行尾 \thanks 脚注符号已去掉
        title = title + t if (not title or title.endswith("-")) else title + " " + t
    return re.sub(r"\s+", " ", title).strip(), used


def _author_rows(lines, title_lines):
    """标题最后一行下方约 160pt 内的视觉行（不含标题字号行、行号）。"""
    if not title_lines:
        return []
    last = max(title_lines, key=lambda l: (l["y"], l["x0"]))
    ids = {id(l) for l in title_lines}
    lo, hi = last["y"] + 0.5 * last["size"], last["y1"] + 160
    win = [l for l in lines if id(l) not in ids and not _is_noise(l) and lo <= l["y"] < hi
           and l["size"] < last["size"] - 0.6]
    return _visual_rows(win)


def _looks_affiliation(text: str) -> bool:
    t = text.strip()
    return bool("@" in t or AFFIL_RE.search(t) or re.match(r"^[\d*∗†‡§¶✉]", t))


def _join_author_rows(rows, text_w=0.0) -> str:
    """多行作者拼接：上一行以分隔符结尾、或是排满整行的逗号串（姓名可能被折断）→ 空格续接；否则视作新条目。"""
    out = ""
    for i, r in enumerate(rows):
        t = r["nosup"].strip()
        if not i:
            out = t
            continue
        p = rows[i - 1]
        pt = p["nosup"].strip()
        wrapped = bool(re.search(r"(?:[,;&]|\band)$", pt)) or bool(
            len(p["segs"]) == 1 and text_w and (p["x1"] - p["x0"]) >= 0.85 * text_w
            and re.search(r"[,;&]|\band\b", pt))
        out += (" " if wrapped else ", ") + t
    return out


def _split_names(text: str) -> list[str]:
    """作者串 → 姓名列表：去 IEEE 会员称号 / ORCID / 上标角标 / 脚注符号，保留姓名内空格。"""
    t = IEEE_GRADE_RE.sub(",", text)
    t = ORCID_RE.sub(",", t)
    t = re.sub(r"(?<=[^\W\d_])\d+(?:\s*,\s*\d+)*", ",", t)     # 同字号上标数字（Smith1,2 → Smith,）
    t = re.sub(r"(?<=\s)\d{1,2}(?:\s*,\s*\d{1,2})*(?=[\s,]|$)", ",", t)   # 独立角标（Alice Smith 1 Bob Lee 2）
    names = []
    for p in NAME_SPLIT_RE.split(t):
        p = FOOTNOTE_MARK_RE.sub("", p)
        p = re.sub(r"[\s\d]+$", "", p)
        p = re.sub(r"\s+", " ", p).strip(" ,;:-")
        if not p or not re.search(r"[^\W\d_]", p):
            continue
        if not (1 < len(p) < 60) or len(p.split()) > 6:
            continue
        if "@" in p or AFFIL_RE.search(p) or ANON_AUTHOR_RE.search(p):
            continue
        if p.lower() in ("et al", "et al.", "ieee", "member", "fellow"):
            continue
        if p not in names:
            names.append(p)
    return names[:30]


def _find_authors(rows, title_lines=None, text_w=0.0):
    """标题下方作者行（通用版式兜底）→ (作者列表, 是否双盲匿名)。

    先在原始行上做匿名判定（Anonymous … submission / Anonymous Author(s) / Paper under double-blind
    review / Anonymized）；再收集作者行：遇到 Abstract 标题即止并保留已收集的行，遇到单位 / 邮箱 /
    上标数字开头 / 脚注行即止。
    """
    if not rows:
        return [], False
    for r in rows[:6]:
        if _is_abstract_head(r["text"]):
            break
        for t in [r["text"]] + [s["text"] for s in r["segs"]]:
            t = re.sub(r"^\d{1,4}\s+|\s+\d{1,4}$", "", t.strip())
            if ANON_AUTHOR_RE.search(t):
                return [], True
    last = max(title_lines, key=lambda l: l["y"]) if title_lines else None
    picked, first_size, prev = [], None, None
    for r in rows:
        t = re.sub(r"^\d{1,4}\s+(?=[^\W\d_])", "", r["text"].strip())
        if _is_abstract_head(t) or _is_section_stop(t):
            break                                       # 摘要 / 正文开始：保留已收集的作者行
        if not re.search(r"[^\W\d_]", r["nosup"]):
            continue                                    # 纯角标 / 符号行
        if _looks_affiliation(t):
            break
        if prev is None:
            if last is not None and r["y"] - last["y1"] > 100:
                break                                   # 离标题太远，不像作者行
        elif r["size"] < first_size - 1.0 or \
                r["y"] - prev["y1"] > max(2.2 * (prev["y1"] - prev["y"]), 18):
            break                                       # 字号明显变小（单位）/ 大段空白（作者块结束）
        if len(t.split()) > 24:
            break                                       # 正文段落
        picked.append(r)
        prev = r
        if first_size is None:
            first_size = r["size"]
        if len(picked) >= 4:
            break
    if not picked:
        return [], False
    joined = _join_author_rows(picked, text_w)
    if len(joined) > 400:
        joined = picked[0]["nosup"]
    return _split_names(joined), False


def _is_abstract_head(text: str) -> bool:
    """Abstract / 摘要 标题行（行内或单独成行）。小写开头的「abstract …」是正文折行，须带分隔符才算。"""
    if not ABSTRACT_HEAD_RE.match(text):
        return False
    return not text[:1].islower() or bool(re.match(r"^abstract\s*(?:[:.—–\-]|$)", text))


def _is_bare_abstract_head(text: str) -> bool:
    """只有标签本身的「Abstract」/「摘要」行（另一段摘要的标题），正文以 Abstract 一词开头的不算。"""
    return _is_abstract_head(text) and not ABSTRACT_LABEL_RE.sub("", text, count=1).strip()


def _find_abstract(lines, title_lines=None):
    """按栏抽取摘要 → (摘要, 摘要标题行或 None)。

    定位 Abstract 标题行，只收与其同栏的后续行（阅读顺序），双栏版式不会串进另一栏；
    遇到 Keywords / Index Terms / 1 Introduction / I. INTRODUCTION 等、字号跳变或大段空白即止。
    """
    glines = sorted((l for l in lines if not _is_noise(l)), key=lambda l: (l["y"], l["x0"]))
    ids = {id(l) for l in (title_lines or [])}
    start_y = -1.0
    if title_lines:
        last = max(title_lines, key=lambda l: l["y"])
        start_y = last["y"] + 0.5 * last["size"]
    head = next((l for l in glines if l["y"] >= start_y and id(l) not in ids
                 and _is_abstract_head(l["text"])), None)
    if head is None:
        return "", None
    rest = ABSTRACT_LABEL_RE.sub("", head["text"], count=1).strip()
    same = []
    if not rest:
        # 标题与正文被抽成同一基线上的两段（「Abstract—」+「We present …」）
        same = [l for l in glines if l is not head and abs(l["y"] - head["y"]) < 2.5
                and 0 <= l["x0"] - head["x1"] < 2 * head["size"]]
    same_ids = {id(l) for l in same}
    below = [l for l in glines if l["y"] > head["y"] + 0.3 * head["size"] and id(l) not in same_ids]
    if same:
        rest = " ".join(l["text"] for l in same)
        head = dict(head, x1=max(l["x1"] for l in same))
    if rest:                                            # 行内标题：标题行本身就是首行
        col, buf, prev, base = (head["x0"], head["x1"]), [rest], head, head["size"]
    else:
        near = [l for l in below if l["y"] <= head["y1"] + 3 * head["size"]]
        over = [l for l in near if min(l["x1"], head["x1"]) > max(l["x0"], head["x0"])][:3]
        if not over and near:
            # 首行没伸到居中标题下方（摘要很短）：取紧邻首行里水平距离标题最近的一段
            same = [l for l in near if abs(l["y"] - near[0]["y"]) < 2.5]
            over = [min(same, key=lambda l: max(l["x0"] - head["x1"], head["x0"] - l["x1"], 0))]
        if not over:
            return "", head
        ref = max(over, key=lambda l: l["x1"] - l["x0"])
        col, buf, prev, base = (ref["x0"], ref["x1"]), [], None, None
    for l in below:
        if not _in_col(l, col):
            continue
        t = l["text"]
        if _is_section_stop(t) or _is_bare_abstract_head(t):
            break
        if NUM_HEADING_RE.match(t) and (l["bold"] or (base and l["size"] >= base + 0.5)):
            break                                       # 下一节标题
        if base is not None and (l["size"] >= base + 1.5 or l["size"] <= base - 1.8):
            break                                       # 字号跳变：下一节标题 / 页脚注
        anchor = prev or head
        if l["y"] - anchor["y1"] > max((2.5 if prev else 3.0) * (anchor["y1"] - anchor["y"]), 20):
            break                                       # 大段空白：摘要结束
        buf.append(t)
        prev = l
        if base is None:
            base = l["size"]
        if len(buf) > 80:
            break
    return _join_lines(buf), head


_KW_SEP_RE = re.compile(r"[;；,，·•⋅∙]")


def _find_keywords(lines, after_y=-1.0):
    """Keywords / Index Terms / 关键词 标签行 + 同栏紧邻的续行（多行关键词）。

    「Keywords:」单独成行且其下各行都没有分隔符时（Elsevier 正式版 ARTICLE INFO 栏），一行一个关键词。
    """
    glines = sorted((l for l in lines if not _is_noise(l)), key=lambda l: (l["y"], l["x0"]))
    head = next((l for l in glines if l["y"] >= after_y and _is_kw_label(l["text"])), None)
    if head is None:
        return []
    rest = KW_LABEL_RE.sub("", head["text"], count=1).strip()
    if not rest:
        # 标签与取值被抽成同一基线上的两段（粗体「Keywords:」+ 常规字体取值）
        same = [l for l in glines if l is not head and abs(l["y"] - head["y"]) < 2.5
                and 0 <= l["x0"] - head["x1"] < 3 * head["size"]]
        if same:
            rest = " ".join(l["text"] for l in same)
            head = dict(head, x1=max(l["x1"] for l in same))
    one_per_line = not rest
    parts, col, prev = ([rest] if rest else []), (head["x0"], head["x1"]), head
    for l in glines:
        if l["y"] <= prev["y"] + 0.3 * prev["size"]:
            continue
        t = l["text"]
        if not parts:                                   # 「KEYWORDS」单独成行：取紧随其后的同栏行
            if abs(l["x0"] - head["x0"]) > 24 or l["y"] - head["y1"] > 2 * head["size"]:
                continue
            if _is_section_stop(t) or _is_bare_abstract_head(t) or NUM_HEADING_RE.match(t):
                break
            col = (l["x0"], l["x1"])
        elif not _in_col(l, col):
            continue
        elif (l["y"] - prev["y1"] > 1.2 * (prev["y1"] - prev["y"]) or abs(l["size"] - prev["size"]) > 1
              or _is_section_stop(t) or _is_bare_abstract_head(t) or NUM_HEADING_RE.match(t)
              or re.match(r"^[A-Z][A-Za-z .()/-]{0,40}:\s", t) or EMAIL_RE.search(t)
              or re.search(r"[.。]$", parts[-1])
              or len(parts) >= (8 if one_per_line and not _KW_SEP_RE.search(" ".join(parts)) else 4)):
            break
        parts.append(t)
        prev = l
    if one_per_line and len(parts) > 1 and not _KW_SEP_RE.search(" ".join(parts)):
        kws = [re.sub(r"(?<![A-Z])[.。]$", "", re.sub(r"\s+", " ", p)).strip() for p in parts]
        return [k for k in kws if len(k) > 1]
    return _split_keywords(_join_lines(parts))


# ---------- 会议识别 ----------
def _conf_year(y):
    if not y:
        return None
    y = int(y)
    return 2000 + y if y < 100 else y


_JOURNAL_ACRONYMS = {"IJCV", "JMLR", "PAMI", "MEDIA", "MIA", "TACL", "JAIR", "AIJ", "CSUR"}


def _acronym_ok(name: str) -> bool:
    """像会议缩写：≥2 个大写字母；出版方 / 期刊缩写（IEEE、TPAMI、TMI、IJCV…）不算。"""
    up = name.upper()
    if up in _CONF_CANON:
        return True
    return (sum(c.isupper() for c in name) >= 2
            and up not in ("IEEE", "ACM", "CVF", "PMLR", "LNCS", "ID", "PDF", "DOI")
            and up not in _JOURNAL_ACRONYMS and not re.fullmatch(r"T[A-Z]{2,6}", name))


def _canon_conf(name: str) -> str:
    """会议缩写规范化：NEURIPS / Neurips → NeurIPS（以 KNOWN_CONFS 写法为准）。"""
    n = re.sub(r"\s+", "", name or "")
    canon = _CONF_CANON.get(n.upper())
    if canon:
        return canon
    return n.upper() if len(n) <= 6 else n


def _body_size(lines, page_h):
    """正文主字号：页面中段各行按字符数加权的众数（0.5pt 粒度）；脚注 / 页脚通常比它小。"""
    cnt = {}
    for l in lines:
        if page_h * 0.15 <= l["y"] <= page_h * 0.82 and not _is_noise(l):
            k = round(l["size"] * 2) / 2
            cnt[k] = cnt.get(k, 0) + len(l["text"])
    return max(cnt, key=cnt.get) if cnt else None


def _probe_text(lines, page_w, page_h, title_lines=None) -> str:
    """页眉区（标题之上 / 顶部 10%）+ 页脚区（底部 18% / 末 6 行）文本。

    页脚区只取比正文小号的脚注 / 页脚行（以及页面最底 6% 的页脚行）：页面下部的正文
    （引言里写的「accepted at … (ICLR 2024)」）不是本稿的会议标记。
    行序与分栏序两种拼接都给出：双栏页的左栏脚注折行时（ICML / AAAI），会议全称仍连续。
    """
    srt = sorted((l for l in lines if not _is_noise(l)), key=lambda l: (l["y"], l["x0"]))
    if not srt:
        return ""
    top_y = min(l["y"] for l in title_lines) if title_lines else page_h * 0.15
    head_y = max(top_y, page_h * 0.1)
    body = _body_size(srt, page_h)
    zone, seen = [], set()
    for l in [l for l in srt if l["y"] < head_y or l["y"] > page_h * 0.82] + srt[-6:]:
        if id(l) in seen:
            continue
        seen.add(id(l))
        if l["y"] >= head_y and body is not None and l["size"] > body - 0.4 and l["y"] < page_h * 0.94:
            continue                                    # 页面下部的正文行
        zone.append(l)
    by_row = sorted(zone, key=lambda l: (l["y"], l["x0"]))
    by_col = sorted(zone, key=lambda l: (l["y"] > page_h / 2, l["x0"] >= page_w / 2, l["y"], l["x0"]))
    return " ".join(l["text"] for l in by_row) + "\n" + " ".join(l["text"] for l in by_col)


def _prior_work(probe: str, m) -> bool:
    """匹配之前（同一段探测文本内约 220 字符）是前作 / 扩展版说明，且其后没有本稿自身的投稿标记。"""
    lo = max(0, m.start() - 220)
    nl = probe.rfind("\n", lo, m.start())
    lo = nl + 1 if nl >= 0 else lo
    cues = list(CONF_PRIOR_RE.finditer(probe, lo, m.start()))
    return bool(cues) and not CONF_OWN_RE.search(probe, cues[-1].end(), m.end())


def _match_conference(probe: str):
    """按特异性从高到低匹配会议标记 → (缩写, 年份或 None, 起, 止)；未识别返回 None。

    前作 / 扩展版说明里提到的会议（「A preliminary version … (CVPR 2023)」）跳过。
    """
    for pat in (CONF_MARK_RE, CONF_SHORT_YEAR_RE, CONF_PAPER_AT_RE):
        for m in pat.finditer(probe):
            if _acronym_ok(m.group(1)) and not _prior_work(probe, m):
                return m.group(1), m.group(2), m.start(), m.end()
    for m in CONF_TMLR_RE.finditer(probe):
        if not _prior_work(probe, m):
            y = re.search(r"\b(20\d{2})\b", probe[m.end():m.end() + 24])
            return "TMLR", (y.group(1) if y else None), m.start(), m.end()
    for m in CONF_PAREN_RE.finditer(probe):
        if CONF_CONTEXT_RE.search(probe[max(0, m.start() - 150):m.start()]) and not _prior_work(probe, m):
            return m.group(1), m.group(2), m.start(), m.end()
    for pat, abbr in CONF_FULLNAMES:
        for m in pat.finditer(probe):
            if not CONF_FULLNAME_CTX_RE.search(probe[max(0, m.start() - 100):m.start()]) or _prior_work(probe, m):
                continue
            after = re.search(r"\b(20\d{2})\b", probe[m.end():m.end() + 100])
            before = list(re.finditer(r"\b(20\d{2})\b", probe[max(0, m.start() - 60):m.start()]))
            cands = []
            if after:
                cands.append((after.start(), after.group(1)))
            if before:
                cands.append((min(60, m.start()) - before[-1].end(), before[-1].group(1)))
            year = min(cands)[1] if cands else None
            return abbr, year, m.start(), m.end()
    for m in CONF_SUBMITTED_RE.finditer(probe):
        if _acronym_ok(m.group(1)) and not _prior_work(probe, m):
            return m.group(1), m.group(2), m.start(), m.end()
    return None


def _detect_conference(lines, page_w=612.0, page_h=792.0, title_lines=None, author_texts=()):
    """页眉 / 页脚水印识别会议与年份，返回 {name, year, double_blind} 或 None。

    支持：CVF「ICCV 2025 Submission #1234. CONFIDENTIAL REVIEW COPY」、ECCV「ECCV-26 submission ID 1234」、
    ICLR「Under review as a conference paper at ICLR 2026」/「Published as …」、NeurIPS 页脚
    「Submitted to 39th Conference on Neural Information Processing Systems (NeurIPS 2025)」、
    ICML「… International Conference on Machine Learning (ICML)」、「Submitted to ICCV 2025」；
    作者区「Anonymous CVPR submission」、AAAI 模板「Anonymous submission」。年份缺失时 year=None。
    """
    probe = _probe_text(lines, page_w, page_h, title_lines)
    hit = _match_conference(probe)
    if hit:
        name, year, s, e = hit
        ctx = probe[max(0, s - 160):e + 160]
        return {"name": _canon_conf(name), "year": _conf_year(year),
                "double_blind": bool(DOUBLE_BLIND_RE.search(ctx))}
    for t in author_texts:
        t = re.sub(r"^\d{1,4}\s+|\s+\d{1,4}$", "", (t or "").strip())
        m = ANON_VENUE_RE.match(t)
        if m and _acronym_ok(m.group(1)):
            return {"name": _canon_conf(m.group(1)), "year": _conf_year(m.group(2)), "double_blind": True}
        if ANON_SUBMISSION_RE.match(t):
            return {"name": "AAAI", "year": None, "double_blind": True}
    return None


# ---------- 主入口 ----------
def _date_from_row(text: str) -> str:
    """cover letter 日期行 → YYYY-MM-DD（January 15, 2025 / 15 January 2025 / Jan. 15, 2025 / 2025-01-15）。"""
    t = re.sub(r"\s+", " ", (text or "").strip())
    m = DATE_ROW_RE.match(t)
    if m:
        mon, day, year = m.group(1), m.group(2), m.group(3)
    else:
        m = DATE_ROW_DMY_RE.match(t)
        if m:
            day, mon, year = m.group(1), m.group(2), m.group(3)
        else:
            m = DATE_ROW_ISO_RE.match(t)
            if not m:
                return ""
            year, mon, day = m.group(1), m.group(2), m.group(3)
    month = int(mon) if mon.isdigit() else _MONTH_NUM.get(mon[:3].lower())
    try:
        return datetime(int(year), month, int(day)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def _open_pdf(path: str):
    """打开并校验 PDF；加密 / 无页面 / 损坏 / 非 PDF 抛 ValueError，失败时不留文件句柄。"""
    doc, err = None, ""
    try:
        doc = pymupdf.open(path)
    except Exception as e:      # 损坏或根本不是 PDF
        msg = str(e)
        for p in {str(path), repr(str(path))[1:-1], os.path.abspath(str(path))}:
            msg = msg.replace(p, os.path.basename(str(path)))   # 提示信息会回给前端：不带服务器本地路径
        err = f"{type(e).__name__}: {msg}"
    if doc is None:
        # 打开失败的回溯仍引用半构造的文档对象（Windows 上文件被锁）：离开 except 后再回收、再抛
        gc.collect()
        raise ValueError(f"PDF 文件损坏或无法读取，无法解析（{err}）")
    try:
        if not doc.is_pdf:
            raise ValueError("不是有效的 PDF 文件，无法解析")
        if doc.needs_pass or doc.page_count == 0:
            raise ValueError("PDF 已加密或无页面，无法解析")
    except Exception:
        doc.close()
        raise
    return doc


def parse_pdf(path: str) -> dict:
    """解析投稿 PDF，返回元数据草稿（关键字段由用户在界面确认后归档）。

    加密 / 无页面 / 无法读取的 PDF 抛 ValueError（中文提示）；无论成功失败都会关闭文档。
    """
    doc = _open_pdf(path)
    try:
        return _parse_doc(doc)
    finally:
        doc.close()


_OCR_ZOOM = 2.0          # 渲染扫描页：2 倍 ≈ 144 dpi，正文字高 20–30 像素，识别足够


def _ocr_lines(page) -> tuple[list, dict | None]:
    """扫描版首页走 OCR（装了可选组件才会）。返回 (行, OCR 信息)；没装或失败返回 ([], 说明)。"""
    from app import ocr
    if not ocr.available():
        return [], {"used": False, "reason": "not_installed", "install": ocr.INSTALL_HINT}
    try:
        png = page.get_pixmap(matrix=pymupdf.Matrix(_OCR_ZOOM, _OCR_ZOOM)).tobytes("png")
        lines = ocr.page_lines(png, _OCR_ZOOM)
    except Exception as e:
        return [], {"used": False, "reason": f"{type(e).__name__}: {e}"}
    scores = [l.pop("score") for l in lines]
    for l in lines:
        l["text"] = l["nosup"] = l["core"] = _clean_line(l["text"])
    lines = [l for l in lines if l["text"] and not ARXIV_STAMP_RE.match(l["text"])]
    return lines, {"used": True, "engine": ocr.status()["engine"], "lines": len(lines),
                   "min_score": round(min(scores), 3) if scores else None}


def _parse_doc(doc) -> dict:
    pages = doc.page_count
    try:
        page = doc[0]
        page_w, page_h = page.rect.width, page.rect.height
        lines = _page_lines(page)
        full_text = page.get_text()
    except Exception as e:
        raise ValueError(f"PDF 首页无法读取，无法解析（{type(e).__name__}: {e}）") from None
    ocr_info = None
    if not lines:                                   # 扫描版 / 图片 PDF：没有文字层
        lines, ocr_info = _ocr_lines(page)
        if lines:
            full_text = "\n".join(l["text"] for l in lines)
    rows = _rows(lines)

    draft = {"layout": "generic", "title": "", "authors": [], "abstract": "", "keywords": [],
             "confidence": {}, "journal_name": "", "manuscript_number": "", "article_type": "",
             "corresponding_author": "", "corresponding_pinyin": "", "doi": "", "arxiv": "",
             "conf_name": "", "conf_year": None, "double_blind": False, "conf_known": False,
             "anonymous_authors": False, "submitted_hint": "", "submitted_hint_source": "",
             "pages": pages, "ocr": ocr_info}
    em = _parse_em_rows(rows)
    if em:
        draft.update(
            layout="editorial_manager", title=em["title"], authors=em["authors"],
            abstract=em["abstract"], keywords=em["keywords"], journal_name=em["journal_name"],
            manuscript_number=em["manuscript_number"], article_type=em["article_type"],
            corresponding_author=em["corresponding_author"],
            corresponding_pinyin=em["corresponding_pinyin"],
            corresponding_affiliation=em["corresponding_affiliation"])
        draft["confidence"] = {
            "title": "auto" if em["title"] else "missing",
            "authors": "auto" if em["authors"] else "missing",
            "abstract": "auto" if em["abstract"] else "missing",
            "keywords": "auto" if em["keywords"] else "missing",
            "number": "auto" if em["manuscript_number"] else "missing",
            "pinyin": "auto" if em["corresponding_pinyin"] else "missing",
        }
    else:
        title, title_lines = _find_title(lines, page_h)
        arows = _author_rows(lines, title_lines)
        text_w = max((l["x1"] - l["x0"] for l in lines), default=0.0)
        authors, anonymous = _find_authors(arows, title_lines, text_w)
        abstract, abs_head = _find_abstract(lines, title_lines)
        below_title = max(l["y"] for l in title_lines) + 1 if title_lines else -1.0
        keywords = _find_keywords(lines, abs_head["y"] if abs_head else below_title)
        if not keywords and abs_head:
            keywords = _find_keywords(lines, below_title)
        draft.update(title=title, authors=authors, abstract=abstract, keywords=keywords)
        draft["confidence"] = {
            "title": "auto" if title else "missing",
            "authors": "auto" if authors else "missing",
            "abstract": "auto" if abstract else "missing",
            "keywords": "auto" if keywords else "missing",
        }
        # ---------- 会议识别（页眉 / 页脚水印、作者区匿名行）----------
        conf = _detect_conference(lines, page_w, page_h, title_lines, [r["text"] for r in arows[:6]])
        if conf:
            draft["conf_name"] = conf["name"]
            draft["conf_year"] = conf["year"]
            draft["double_blind"] = conf["double_blind"]
            draft["conf_known"] = conf["name"] in KNOWN_CONFS
        # 双盲匿名作者：Anonymous XXX submission / Anonymous Author(s) 等不作为作者
        if anonymous:
            draft["authors"] = []
            draft["anonymous_authors"] = True
            draft["double_blind"] = True
            draft["confidence"]["authors"] = "anon"

    # DOI / arXiv：前两页全文正则（含旋转的 arXiv 侧边水印）
    if pages > 1:
        try:
            full_text += "\n" + doc[1].get_text()
        except Exception:
            pass
    doi_m = DOI_RE.search(full_text)
    arxiv_m = ARXIV_RE.search(full_text)
    draft["doi"] = doi_m.group(1).rstrip(".;,") if doi_m else ""
    draft["arxiv"] = (arxiv_m.group(1) + (arxiv_m.group(2) or "")) if arxiv_m else ""

    # 兜底：作者列表里混入的匿名占位
    if draft["authors"]:
        filtered = [a for a in draft["authors"] if not ANON_AUTHOR_RE.search(a)]
        if len(filtered) != len(draft["authors"]):
            draft["authors"] = filtered
            draft["anonymous_authors"] = True
            draft["confidence"]["authors"] = "anon" if not filtered else "auto"
    # 双盲会议：匿名作者属正常（CVPR/ICLR 等评审期去标识）
    if draft["conf_name"] and not draft["authors"] and draft["double_blind"]:
        draft["anonymous_authors"] = True
        draft["confidence"]["authors"] = "anon"

    # 投稿日期提示：第 2 页（通常是 cover letter）顶部的日期行；来源标注为 cover_letter，
    # 只是信件落款日，官方提交日期以投稿系统为准
    if pages > 1:
        try:
            lines2 = _page_lines(doc[1])
        except Exception:
            lines2 = []
        # 整行（同基线合并）与单个文本行都试：日期可能与「Dear Editor」等同一基线左右并排
        for text in [r["text"] for r in _rows(lines2)[:10]] + [l["text"] for l in lines2[:12]]:
            hint = _date_from_row(text)
            if hint:
                draft["submitted_hint"] = hint
                draft["submitted_hint_source"] = "cover_letter"
                break

    # 去掉零宽字符（行级已清洗，这里兜底）
    for k in ("title", "abstract", "journal_name", "corresponding_author"):
        if isinstance(draft.get(k), str):
            draft[k] = strip_invisible(draft[k])
    for k in ("authors", "keywords"):
        if isinstance(draft.get(k), list):
            draft[k] = [strip_invisible(x) for x in draft[k] if strip_invisible(x)]
    return draft
