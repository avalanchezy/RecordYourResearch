# -*- coding: utf-8 -*-
"""投稿系统页面粘贴解析。

用户自己在浏览器里登录投稿系统，把作者中心（或单篇稿件的状态页）整页复制（Ctrl+A / Ctrl+C）粘贴进来，
这里只解析这段文本：认出每篇稿件的编号、标题、官方状态和日期。不联网、不登录任何网站。

按页面特征识别：ScholarOne Manuscripts（IEEE、Taylor & Francis、Wiley、SAGE、IOP、自动化学报…）、
Editorial Manager（Springer、Elsevier、PLOS…）、IEEE Author Portal / Wiley Research Exchange（Atypon ReX）、
Springer Nature Snapp / 个人主页 Your research、Nature 投稿系统（eJournalPress）、Research Square、MDPI SuSy、
Frontiers、Taylor & Francis Submission Portal，以及勤云 / 知网腾云 / 方正 / 自建等中文期刊系统。
认不出系统时也按通用规则尽量解析；宁可留空，也不猜错。

Chrome 复制时，表格同一行的单元格用 TAB 分隔、行与行用换行分隔，单元格里的换行原样保留。按版式分三种解析：
1. 表格（EM 文件夹、ScholarOne 队列、MDPI / 中文期刊列表、Snapp 列表…）：认出表头行，按列名定每列的含义
   （编号 / 标题 / 状态 / 投稿日期 / 状态日期…），再把多行单元格拼回整行（凑够「列数 - 1」个 TAB 即一行）；
2. 卡片（ReX My Submissions、Wiley Authors、Frontiers、T&F、Springer Nature Your research）：以状态字段为锚，
   向上下扩到相邻的「标签 / 值」行，标题取文章类型行之后（或期刊行之前）的那一行；
3. 单篇页面（ScholarOne 投稿确认、EM History、MTS / MDPI / 勤云 / 方正 / 知网详情、Research Square…）：
   读带标签的字段，没有状态标签时取处理记录（时间线）里最新的一条。
三种都没认出稿件时，退回「先找编号、再看状态 / 标题在编号哪一侧」的旧办法。
"""
import re
import unicodedata
from contextvars import ContextVar
from datetime import date
from functools import lru_cache

from .stages import RAW_STATUS_MAP, STAGES, normalize, status_key

# ---------------- 系统识别 ----------------
# (标记文本（小写）, 权重)：网址、厂商页脚、系统独有的文件夹 / 按钮名权重高，各家都可能出现的词权重低
SYSTEMS = {
    "scholarone": ("ScholarOne Manuscripts", (
        ("scholarone", 3), ("manuscriptcentral", 3), ("publishingportal.ieee.org", 3),
        ("manuscripts with decisions", 3), ("revised manuscripts in draft", 3),
        ("unsubmitted and manuscripts in draft", 3), ("manuscripts i have co-authored", 3),
        ("manuscripts awaiting revision", 3), ("manuscripts awaiting approval to transfer", 3),
        ("5 most recent e-mails", 3), ("status\tid\ttitle\tcreated\tsubmitted", 3),
        ("action\tstatus\tid\ttitle\tsubmitted\tdecisioned", 3), ("continue\tid\ttitle\tcreated\tdelete", 3),
        ("a revision has been started", 2), ("author dashboard", 1), ("author center", 1),
        ("view decision letter", 1), ("create a revision", 1), ("contact journal", 1), ("due on:", 1),
        ("adm:", 1), ("submission confirmation", 1), ("instructions & forms", 1), ("get help now", 1),
        ("clarivate", 1), ("silverchair", 1), ("english language editing service", 1))),
    "editorial_manager": ("Editorial Manager", (
        ("editorial manager", 3), ("editorialmanager", 3), ("aries systems", 3), ("author main menu", 3),
        ("submissions being processed", 3), ("revisions being processed", 3), ("submissions needing revision", 3),
        ("submissions with a decision", 3), ("submissions sent back to author", 3),
        ("submissions waiting for author's approval", 3), ("incomplete submissions being revised", 3),
        ("revisions waiting for author's approval", 3), ("revisions sent back to author", 3),
        ("declined revisions", 3), ("submission transfers waiting for author's approval", 3),
        ("submissions with production completed", 3), ("completed production task assignments", 3),
        ("current task assignments", 3), ("total submissions)", 3), ("initial date submitted", 3),
        ("date submission began", 3), ("date revision due", 3), ("history for manuscript number:", 3),
        ("author details for manuscript number:", 3), ("correspondence date\tletter", 3),
        ("action links", 1), ("reinstate submission", 1), ("similarity check/ithenticate", 1))),
    "rex": ("IEEE Author Portal / Wiley Research Exchange", (
        ("ieee author portal", 3), ("research exchange", 3), ("atypon rex", 3), ("atyponrex.com", 3),
        ("view accepted submission overview", 3), ("all submission statuses", 3), ("open submission statuses", 3),
        ("wiley authors", 3), ("authors.wiley.com", 3), ("article missing?", 3), ("filter by journal", 3),
        ("editorial reference code", 3), ("submission started", 3), ("submission status", 1),
        ("submission overview", 1), ("resume submission", 1), ("revise submission", 1), ("editorial contact", 1),
        ("my dashboard", 1), ("my submissions", 1), ("accepted (final files)", 2))),
    "snapp": ("Springer Nature Snapp", (
        ("submission.nature.com", 3), ("my-profile.springernature.com", 3), ("link.springernature.com/home", 3),
        ("snapp", 3), ("reviewer report(s) received", 3), ("rights and open access payments", 3),
        ("springer nature", 1), ("your research", 2), ("editorial assignment", 1), ("technical check", 1))),
    "nature_mts": ("Nature Portfolio MTS (eJournalPress)", (
        ("manuscript tracking system", 3), ("live manuscripts", 3), ("send manuscript correspondence", 3),
        ("modify my springer nature account", 3), ("nature.com/cgi-bin/main.plex", 3), ("current revision #", 2))),
    "researchsquare": ("Research Square", (
        ("research square", 3), ("10.21203/rs.3.rs-", 3), ("researchsquare.com/article/rs-", 3),
        ("peer review timeline", 3), ("under review at nature portfolio", 2))),
    "mdpi": ("MDPI SuSy", (
        ("susy.mdpi.com", 3), ("mdpi (basel, switzerland)", 3), ("display submitted manuscripts", 3),
        ("display co-authored manuscripts", 3), ("submissions menu", 3), ("english correction done", 2),
        ("pending editor decision", 2), ("revised version review", 2), ("mdpi", 1), ("susy", 1),
        ("pending review", 1), ("pending major revisions", 1), ("pending minor revisions", 1))),
    "frontiers": ("Frontiers", (
        ("my frontiers", 3), ("enter review forum", 3), ("enter production forum", 3), ("frontiers media s.a.", 3),
        ("frontiersin.org", 3), ("10.3389/", 3), ("review finalized", 2), ("interactive review", 2),
        ("independent review", 2), ("initial validation", 2), ("frontiers", 1))),
    "tandf": ("Taylor & Francis Submission Portal", (
        ("rp.tandfonline.com", 3), ("informa uk limited", 3), ("taylor & francis group", 3),
        ("no title supplied yet", 3), ("revision required on submission", 3), ("submission portal", 1),
        ("taylor & francis", 1), ("taylor and francis", 1), ("tandfonline", 1), ("submission incomplete", 1))),
    "cn": ("中文期刊投稿系统", (
        ("北京勤云科技", 3), ("勤云科技", 3), ("beijing e-tiller", 3), ("e-tiller.com", 3), ("user.aipub.cn", 3),
        ("aipub归智期刊联盟", 3), ("/ch/author/", 3), ("中国知网提供技术支持", 3), ("cb@cnki.net", 3),
        ("cb.cnki.net", 3), ("cbpt.cnki.net", 3), ("掌上腾云", 3), ("----作者工作中心", 3), ("我的稿件管理", 3),
        ("查看审理历史", 3), ("导航式投稿", 3), ("北京北大方正电子", 3), ("founderss.cn", 3), ("ids.fzyun.cn", 3),
        ("cjc.ict.ac.cn", 3), ("注册稿件编号", 3), ("登 录 / 查 询", 3), ("作者中心", 1), ("稿件编号", 1),
        ("稿件状态", 1), ("审稿状态", 1), ("投稿日期", 1), ("收稿日期", 1), ("退修", 1), ("外审", 1),
        ("稿件处理", 1), ("采编", 1))),
}

# ---------------- 稿件编号 ----------------
_ID_RES = (
    # Editorial Manager：NEUCOM-D-25-01234、PONE-D-25-12345R1（演示站点前缀可含小写和下划线）
    ("editorial_manager", re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_]{1,24}-D-\d{2}-\d{4,6}(?:R\d{1,2})?"
                                     r"(?![A-Za-z0-9])")),
    # ScholarOne / ReX 后台：XMI-2026-0412.R1、TPAMI-2025-01-0123、TNNLS-2025-P-12345、T-IFS-99999-2014.R1、
    # EXM-202611-0815-OA、BDSP-BK-2015-0007（必须带 19xx / 20xx 年份段）
    ("scholarone", re.compile(r"(?<![A-Za-z0-9.\-])[A-Z][A-Za-z0-9]{0,11}(?:-[A-Z][A-Za-z0-9]{0,11}){0,2}-"
                              r"(?:(?:19|20)\d{2}-(?:\d{2}-\d{3,5}|[A-Z]{1,3}-\d{3,6}|\d{3,6})|(?:19|20)\d{4}-\d{3,5}"
                              r"|\d{4,6}-(?:19|20)\d{2})(?:-[A-Z]{2,4})?(?:\.R\d{1,2})?(?![\w-]|\.\w)")),
    # MDPI：sensors-3456789（小写刊名代码，不能转大写）
    ("mdpi", re.compile(r"(?<![\w/.-])[a-z][a-z0-9]{1,24}-\d{6,8}(?![\w-])")),
    # 两位年份：Nature MTS NCOMMS-26-01357A、Wiley Review SYNMA-26-00488、旧式 AAS-24-0123
    ("short", re.compile(r"(?<![A-Za-z0-9&-])[A-Z][A-Za-z0-9&]{1,14}-\d{2}-\d{4,6}[A-Z]?(?![\w-])")),
    # Research Square：10.21203/rs.3.rs-8123456/v1 → rs-8123456
    ("researchsquare", re.compile(r"(?:10\.21203/rs\.3\.)?(?<![\w-])(rs-\d{5,8})(?:/v\d{1,2})?(?![\w-])")),
)
# 带标签的编号的值：含数字、不含空格（Frontiers 纯数字、中文稿号、Snapp UUID…）
_ID_VALUE_RE = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff][\w.\-/()（）#\u4e00-\u9fff]{2,44}$")
_ID_STOP = {"view", "details", "status", "title", "submitted", "none", "draft", "unassigned", "n/a", "na", "-"}
_TF_ID_RE = re.compile(r"^(?:1[89]|2\d)\d{7}$")          # T&F Submission Portal：9 位，前两位是创建年份
_LABELED_ID_RE = re.compile(
    r"(?:Manuscript\s*(?:ID|Number|No\.?|#)|Submission\s*(?:ID|Number|No\.?)|Article\s*ID|Paper\s*ID|"
    r"Editorial\s+Reference\s+Code|稿件编号|稿件号|稿号|投稿编号|文章编号)\s*(?:[:：#]\s*)?([A-Za-z0-9\u4e00-\u9fff]"
    r"[A-Za-z0-9\u4e00-\u9fff._\-/]{2,44})", re.I)
_ID_LABEL_HINT_RE = re.compile(r"(?i)manuscript|submission|article|paper|editorial|稿|编号")
# 状态句里引用另一份稿件编号的行（「a revision has been submitted (XXX.R1)」「Replaced by …」），不单独成篇
_REF_LINE_RE = re.compile(r"revision\s+has\s+been\s+submitted|has\s+been\s+replaced|replaced\s+(?:by|with|this)|"
                          r"transferred\s+to|resubmitted\s+as|see\s+(?:the\s+)?(?:new|active)\s+submission|"
                          r"active\s+submission|associated\s+manuscripts|已转投|已替换|"
                          # 通知 / 邮件标题里提到的稿件（T&F「Revision required on submission 2610…」、
                          # ScholarOne「Decision on Manuscript ID …」）
                          r"\b(?:required|decision|accepted|rejected|received)\s+on\s+(?:submission|manuscript)\b|"
                          r"\bmanuscript\s+id\s+[A-Za-z0-9][\w.\-]*\s+(?:has\s+been\s+)?(?:accepted|rejected|declined|"
                          r"withdrawn|revised|transferred|submitted)\b|"
                          # EM 合作者确认弹窗（Author Details）只列作者确认情况，没有稿件状态
                          r"author\s+details\s+for\s+manuscript", re.I)


def ms_base(s) -> str:
    """稿件编号去掉修改稿后缀，用来把 R1 / .R1 对回原稿：NEUCOM-D-25-01234R1 → NEUCOM-D-25-01234。"""
    s = (s or "").strip().upper()
    return re.sub(r"(?:[._\-]?R\d{1,2}|[._\-]REV\d{1,2})$", "", s) if s else ""


def _rev_no(s) -> int:
    m = re.search(r"(?:[._\-]?R|[._\-]REV)(\d{1,2})$", (s or "").strip().upper())
    return int(m.group(1)) if m else 0


def title_key(s) -> str:
    """标题比较用的键：NFKC + casefold，只留字母和数字（任何文字：拉丁、西里尔、希腊、谚文、汉字…），省略号等标点都去掉。"""
    s = unicodedata.normalize("NFKC", s or "").casefold()
    return "".join(ch for ch in s if ch.isalnum())


def same_status(a, b) -> bool:
    return status_key(a or "") == status_key(b or "")


@lru_cache(maxsize=8192)
def _ids_in(s: str, kinds=None) -> tuple[tuple[int, str], ...]:
    """文本里的稿件编号 ((位置, 编号), …)，重叠时取更长的那个。"""
    if not s or "-" not in s:            # 各种编号格式都带连字符
        return ()
    hits = []
    for kind, rx in _ID_RES:
        if kinds is not None and kind not in kinds:
            continue
        for m in rx.finditer(s or ""):
            v = m.group(1) if kind == "researchsquare" else m.group(0)
            hits.append((m.start(), -(m.end() - m.start()), v))
    hits.sort()
    out, end = [], -1
    for pos, neg, v in hits:
        if pos >= end:
            out.append((pos, v))
            end = pos - neg
    return tuple(out)


def _id_like(v: str) -> str:
    """带标签 / 编号列里的值 → 编号；不像编号返回 ""。"""
    v = (v or "").strip()
    if len(v) > 60:
        return ""
    v = v.strip(" .,;:：")
    m = re.match(r"^Draft\s*\((.+)\)$", v, re.I)          # ScholarOne「Draft (XMI-2026-0871.R1)」
    if m:
        v = m.group(1).strip()
    if not v or v.lower() in _ID_STOP or not _ID_VALUE_RE.match(v) or not re.search(r"\d", v):
        return ""
    if _dates_in(v) and len(re.sub(r"[\d\s\-/.:年月日]", "", v)) == 0:
        return ""
    if re.fullmatch(r"\d{1,3}", v):
        return ""
    return v


# ---------------- 状态 ----------------
# 各系统特有、但全局词典里没有（或口径不同）的状态词，键为 status_key 写法。来源见语料库 statuses 的官方说明。
EXTRA_STATUS = {
    # ScholarOne
    "awaiting admin processing": "submitted", "awaiting admin checklist": "submitted",
    "awaiting adm checklist": "submitted", "awaiting jm checklist": "submitted",
    "awaiting editorial assistant processing": "submitted", "manuscript submitted": "submitted",
    "submission confirmation": "submitted",
    "awaiting eic assignment": "editor", "awaiting editor-in-chief check": "editor",
    "awaiting second opinion": "editor", "awaiting referee selection": "editor",
    "awaiting eic decision": "decision", "awaiting final decision": "decision", "awaiting decision approval": "decision",
    "awaiting admin final processing": "decision", "awaiting ed final processing": "decision",
    "awaiting author response": "revise", "revision in draft": "revise", "revised manuscript in draft": "revise",
    "revised manuscripts in draft": "revise", "manuscripts awaiting revision": "revise",
    "a revision has been started": "revise", "a revision has been submitted": "editor",
    "awaiting reviewer invitation responses": "review", "reviewers contacted": "review", "in peer review": "review",
    "awaiting reviewer reports": "review", "awaiting referee scores": "review", "awaiting ce/reviewer scores": "review",
    "awaiting technical editor scores": "review", "waiting for reviewers' scores": "review",
    # 转投 = 本刊拒稿后转到别的期刊（不是作者撤稿）
    "manuscript transferred": "rejected", "submission transferred": "rejected",
    "manuscripts awaiting approval to transfer": "rejected", "transfer pending": "rejected",
    "等待管理员处理": "submitted", "等待审稿人评分": "review",
    # Editorial Manager
    # 还没投出去（作者还在填 / 还没确认 PDF、受邀还没投）：显示出来，但不记事件、不建档
    "building pdf": None, "incomplete": None, "incomplete submission": None, "needs approval": None,
    "needs approval by author": None, "author invited": None,
    "received by journal": "submitted",
    "author submits new manuscript confirmation": "submitted", "author submits revised manuscript confirmation": "editor",
    # Atypon ReX（IEEE Author Portal / Wiley）
    "accepted (final files)": "accepted", "accepted, pending agreement": "accepted",
    "accepted, updates requested": "accepted", "accepted, under review for production": "accepted",
    "accepted: awaiting checks": "accepted", "accepted: exported": "production",
    "in revision": "revise", "in screening": "submitted", "editor assignment in progress": "editor",
    "decision in progress": "decision",
    # 草稿（还没投出去）、被新记录取代的旧记录、提交失败：显示出来，但不记事件、不建档
    "draft": None, "draft invited": None, "draft (transferred)": None,
    "returned to draft": "submitted", "replaced": None, "submission failed": None,
    # Springer Nature Snapp / Research Square / Nature MTS
    "editorial assignment": "editor", "peer review": "review", "reviewer(s) invited": "review",
    "reviewers invited": "review", "reviewer(s) accepted": "review", "reviewers accepted": "review",
    "reviewer report(s) received": "reviews_done", "reviewer reports received": "reviews_done",
    "reviews received": "reviews_done", "multiple rounds of peer review": "review",
    "final decision": "decision", "decision sent to author": "decision", "decision made": "decision",
    "rights and open access payments": "accepted", "publication": "production",
    "manuscript received": "submitted", "manuscript under consideration": "editor", "under consideration": "editor",
    "revision under consideration": "editor", "manuscript accepted": "accepted",
    "under review at nature portfolio": "review",
    # MDPI
    "pending review": "submitted", "pending precheck decision": "editor", "pending editor decision": "decision",
    "pending decision": "decision", "pending major revisions": "revise", "pending minor revisions": "revise",
    "pending revisions": "revise", "pending major or minor revisions": "revise", "revised version review": "review",
    "pending english editing": "accepted", "english correction done": "production", "author proofreading": "production",
    "author proofreading - resubmitted": "production", "pending conversion": "production",
    "xml2pdf done": "production", "conversion skipped": "production", "pending publication": "production",
    # Taylor & Francis Submission Portal
    "submission created": None, "submission in progress": None, "submission incomplete": None,
    "declined to revise": "withdrawn", "revision declined": "withdrawn",
    # Frontiers（Interactive review 有 Action required 时另算返修，见 _ACTION_UPGRADE）
    "initial validation": "submitted", "in preparation": None, "review": "review",
    "interactive review": "review", "independent review": "review", "review finalized": "reviews_done",
    "under evaluation": "editor", "final validation": "accepted",
    # 中文期刊（勤云 / 知网 / 方正 / 自建）
    "待处理": "submitted", "新稿件": "submitted", "新到稿件": "submitted", "最新投稿": "submitted",
    "投稿成功": "submitted", "形式审查": "submitted", "形式审查中": "submitted", "收稿": "submitted",
    "学术不端检测": "submitted", "已发回执": "submitted",
    "初审": "editor", "初审中": "editor", "编辑初审": "editor", "编辑部初审": "editor", "待分配": "editor",
    "预审": "editor", "审理费通知": "editor", "编辑部收到审理费": "editor", "待交审稿费": "editor",
    "外审": "review", "外审中": "review", "专家审稿": "review", "专家审稿中": "review", "专家评审": "review",
    "送审": "review", "送审中": "review", "送外审": "review", "送专家外审": "review", "同行评议": "review",
    "审稿中": "review", "复审": "review", "重审": "review", "评审": "review", "修改稿审理中": "review",
    "初审部分审回": "review",
    "审回": "reviews_done", "初审已审回": "reviews_done", "审稿意见已返回": "reviews_done",
    "终审": "decision", "终审中": "decision", "主编终审": "decision", "再次终审": "decision", "待定": "decision",
    "终审已审回": "decision",
    "退修": "revise", "退修中": "revise", "退改": "revise", "退改重投": "revise", "修改": "revise",
    "修改中": "revise", "大修": "revise", "小修": "revise", "修后再审": "revise", "修改后再审": "revise",
    "修改后审": "revise", "修改后审中": "revise", "修后发表": "revise", "修改后录用": "revise", "评审后退改": "revise",
    "已修回": "editor", "修回": "editor", "修改稿待审": "editor",
    "录用": "accepted", "拟录用": "accepted", "已录用": "accepted", "录用待刊": "accepted", "录用待排版": "accepted",
    "采用": "accepted", "已采用": "accepted", "接收": "accepted", "交纳版面费": "accepted", "版面费通知": "accepted",
    "退稿": "rejected", "已退稿": "rejected", "不录用": "rejected", "退稿重投": "rejected", "退稿或改投它刊": "rejected",
    "经审理后退稿": "rejected",
    "撤稿": "withdrawn", "已撤稿": "withdrawn", "弃稿": "withdrawn",
    "排版": "production", "正式排版": "production", "校对": "production", "作者校对": "production",
    "核清样": "production", "编辑加工": "production", "组稿": "production", "安排刊期": "production",
    "刊期安排": "production", "安排期刊": "production", "安排发表": "production", "网络首发": "production",
    "在线出版": "production", "印刷出版": "production", "电子版上网": "production", "已发表": "production",
    "已刊出": "production", "已出版": "production",
}
EXTRA_STATUS = {status_key(k): v for k, v in EXTRA_STATUS.items()}      # 键统一成 status_key 写法（破折号 → 「 - 」）
# 精确词典之外的短语规则（整串匹配为主，避免把标题当成状态）
_LOCAL_RULES = tuple((re.compile(p), st) for p, st in (
    (r"\bfinal\s+processing$", "decision"),
    (r"^awaiting\s+(?:admin|adm|editorial\s+assistant|ea|jm|eo)\b.*\b(?:processing|checklist)$", "submitted"),
    (r"^(?:awaiting|waiting\s+for)\s+(?:\S+\s+){0,2}(?:scores?|reports?)$", "review"),
    (r"^(?:awaiting\s+)?reviewer\s+invitation\s+responses?$|^reviewers?\s+contacted$|^in\s+peer\s+review$", "review"),
    (r"^awaiting\s+(?:referee|reviewer)\s+selection$|^awaiting\s+editor\s*-\s*in\s*-\s*chief\s+check$"
     r"|^awaiting\s+second\s+opinion$", "editor"),
    (r"^reviewer\s+reports?(?:\s?\(s\))?\s+received$|^reviews?\s+received$", "reviews_done"),
    (r"^(?:manuscript\s+|revision\s+|revised\s+manuscript\s+)?under\s+consideration$", "editor"),
    (r"^decision\s+(?:made|sent(?:\s+to\s+authors?)?)$", "decision"),
    (r"^author\s+submits\s+revised\b", "editor"),
    (r"^author\s+submits\s+new\b", "submitted"),
    (r"^author\s+proofreading\b|^english\s+(?:correction|editing)\s+done$|^pending\s+conversion$|^xml2pdf\b",
     "production"),
    (r"^draft\s*[(\-]", "submitted"),
))
# 中文短状态的兜底（只用于状态列 / 状态标签里的值，不用于任意行）
_CJK_RULES = tuple((re.compile(p), st) for p, st in (
    (r"不录用|退稿|拒稿", "rejected"),
    (r"撤稿|撤回|弃稿", "withdrawn"),
    (r"修改后录用|修后录用|修后发表", "revise"),
    (r"已修回|修回|修改稿已收到|修改稿待审", "editor"),
    (r"修改稿.*审", "review"),
    (r"退修|退改|修改|大修|小修|修后", "revise"),
    (r"录用|采用|接收|版面费", "accepted"),
    (r"排版|校对|校样|清样|刊期|出版|发表|首发|编辑加工", "production"),
    (r"审回|意见已返回", "reviews_done"),
    (r"终审", "decision"),
    (r"审理费|审稿费", "editor"),
    (r"外审|送审|审稿|评审|评议|复审|重审", "review"),
    (r"初审|编辑|分配|预审", "editor"),
    (r"新到|收稿|投稿成功|形式审查|待处理|已提交", "submitted"),
))
# 知网「阶段[结果]」：结果为录用时，阶段是出版环节的算制作中
_CN_PROD_STEP_RE = re.compile(r"出版|刊期|编辑加工|排版|校对|清样|发表|首发")
# ReX 卡片标题下面的横幅：期刊把稿件退回草稿 / 稿件被删，压过卡片上写的状态
_BANNERS = {"returned to draft", "submission failed"}
# 同一卡片 / 行里还写着 Action required 时更进一步的状态（Frontiers：Interactive review 要作者回复 = 返修）
_ACTION_UPGRADE = {"interactive review": "revise"}
# 只是阶段分组名、下面一行才是具体子状态（Snapp「Peer review」+「Reviewer(s) accepted」）
_GROUP_STATUS = {"peer review"}

_STATUS_PREFIX_RE = re.compile(
    r"^(?:[•·◦▪●○■\-–—*]+\s*)?(?:(?:ADM|EIC|EiC|EO|AE|SE|ME|Editor|Admin|Managing Editor)\s*:\s*[^•·]*?[•·]\s*)?"
    r"(?:(?:Current\s+)?Status|Submission\s+Status|Manuscript\s+Status|Decision|Stage|当前状态|稿件状态|审稿状态|状态)"
    r"\s*[:：]\s*|^(?:[•·◦▪●○■\-–—*]+\s*)", re.I)
_PAREN_TAIL_RE = re.compile(r"\s*[(（][^()（）]*[)）]\s*$")
_PAREN_DATE_RE = re.compile(r"\s*[(（]([^()（）]*\d{4}[^()（）]*)[)）]\s*$")
_STATUS_LABEL_RE = re.compile(r"^(?:(?:Current\s+)?Status|Submission\s+Status|Manuscript\s+Status|当前状态|稿件状态|审稿状态|状态)"
                              r"\s*[:：]", re.I)
_STATUS_WORDS = re.compile(r"\b(?:review|reviewer|revis|accept|reject|withdr|decision|editor|submitted|technical|"
                           r"proof|production|published|pending|awaiting|draft|transfer|consideration|remov|retract)\w*", re.I)
_ROLE_LINE_RE = re.compile(r"^[A-Z][A-Za-z]{0,4}\s*:\s+\S")      # ScholarOne 角色行：ADM: Surname, Given / AE: …
_COUNT_TAIL_RE = re.compile(r"[(（]\s*\d+\s*[)）]$|\s\d+\s*篇$")      # 「Accepted (1)」「待修改稿件(2)」是分类计数
_EVENT_PREFIX_RE = re.compile(r"^(?:manuscript\s+moved\s+to|moved\s+to|status\s+changed\s+to|editor\s+decision\s*-)\s*")
_EVENT_MOVED_RE = re.compile(r"^(?:manuscript\s+moved\s+to|moved\s+to|status\s+changed\s+to)\s+", re.I)

# 表头 / 字段名 / 网页按钮：长得像状态，但不是状态
_NOT_STATUS_RE = re.compile(
    r"^(?:initial\s+date\s+submitted|date\s+submitted|submission\s+date|status\s+date|current\s+status|"
    r"submitted\s+(?:on|date)\b.*|last\s+(?:updated|modified|update)\b.*|updated\s+\d.*|date\s+(?:received|accepted|of\s+decision)|"
    r"decision\s+date|created|accept\s+all(?:\s+cookies)?|reject\s+all(?:\s+cookies)?|"
    r"(?:manage|cookie)\s+(?:settings|preferences|cookies)|accept\s+cookies|reject\s+cookies)$", re.I)


@lru_cache(maxsize=8192)
def _sk(s) -> str:
    return status_key(s)


_MISS = object()


@lru_cache(maxsize=8192)
def _lookup(v: str, mode: str = "line"):
    """状态文本 → 阶段（"" = 已知但不是投稿阶段）；认不出返回 _MISS。mode：cell（状态列 / 标签值）、line（任意一行）、event（处理记录）。"""
    k = _sk(v)
    if not k:
        return _MISS
    if mode == "event":
        k = _EVENT_PREFIX_RE.sub("", k).strip()
    cands = [k]
    pk = _sk(_PAREN_TAIL_RE.sub("", k))
    if pk and pk != k:
        cands.append(pk)
    m = re.match(r"^[(（](.+)[)）]$", k)                  # 知网旧版「(录用)」
    if m:
        cands.append(_sk(m.group(1)))
    m = re.match(r"^peer\s+review\s*[:：]\s*(.+)$", k)    # Springer Nature「Peer review: Reviewer report(s) received」
    if m:
        cands.append(_sk(m.group(1)))
    for c in cands:
        if c in EXTRA_STATUS:
            return EXTRA_STATUS[c] or ""
        if c in RAW_STATUS_MAP:
            return RAW_STATUS_MAP[c]
    if mode == "line":                  # 任意一行：只认整串（见 _status_core）
        return _MISS
    m = re.match(r"^(.+?)\s*[\[【](.+?)[\]】]$", k)        # 知网「在线出版[录用]」「终审[退稿]」「初审[退改]」
    if m:
        stage_part, result = m.group(1), m.group(2)
        r = _lookup(result, "cell")
        if r is not _MISS and r:
            if r == "accepted" and _CN_PROD_STEP_RE.search(stage_part):
                return "production"
            if r == "revise" and stage_part.startswith("初审"):
                return "submitted"            # 初审就退回补充材料，还没送审
            return r
        r = _lookup(stage_part, "cell")
        if r is not _MISS:
            return r
    for c in cands:
        for rx, st in _LOCAL_RULES:
            if rx.search(c):
                return st
    if mode == "cell":
        for c in cands:
            if re.search(r"[一-鿿]", c) and len(c) <= 10 and not re.search(r"[请。，,；;]", c):
                for rx, st in _CJK_RULES:
                    if rx.search(c):
                        return st
    return _MISS


@lru_cache(maxsize=8192)
def _status_core(cell: str, mode: str = "line", labeled_only: bool = False):
    """单元格 / 行 → (阶段, 原文)；不是状态返回 None。"""
    raw = (cell or "").strip()
    if not raw or len(raw) > 140 or "\t" in raw:
        return None
    labeled = bool(_STATUS_LABEL_RE.match(raw))
    if labeled_only and not labeled:
        return None
    if mode != "cell" and not labeled and (raw.count(" ") > 9 or not _maybe_status(raw)):   # 快速排除：长句、没有状态词
        return None
    if _NOT_STATUS_RE.match(raw) or _REF_LINE_RE.search(raw):
        return None
    v = _STATUS_PREFIX_RE.sub("", raw).strip()
    v = re.sub(r"^(?:ADM|EIC|EiC|EO|AE|SE|ME)\s*:\s*", "", v)
    if not v or _is_noise(v) or _COUNT_TAIL_RE.search(v) or _folder_of(v):
        return None
    shown = _PAREN_DATE_RE.sub("", v).strip() or v        # 「Major Revision (12-Nov-2025)」：日期另记为状态日期
    if mode == "event":                                     # Frontiers「Manuscript moved to Review finalized」
        shown = _EVENT_MOVED_RE.sub("", shown).strip() or shown
    st = _lookup(shown, "cell" if (labeled or mode == "cell") else mode)
    if st is not _MISS:
        return st, shown
    # 规则兜底（normalize）只用于已知是状态的地方：带「Status:」标签的、状态列 / 状态格里的。
    # 随便一行文字只认词典里的整串，免得「Accepted Methods for …」这种短标题被当成状态
    if not (labeled or mode == "cell") or len(v) > 80:
        return None
    if labeled or len(re.findall(r"[A-Za-z]+", v)) <= 10:
        st = normalize(_PAREN_TAIL_RE.sub("", shown) or shown)
        if st:
            return st, shown
    return None


_STOP_WORDS = {"a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with", "by", "at", "after", "under", "s"}
_VOCAB_WORDS = frozenset(w for k in list(EXTRA_STATUS) + list(RAW_STATUS_MAP) for w in re.findall(r"[a-z]+", k)
                         if w not in _STOP_WORDS)


def _maybe_status(s: str) -> bool:
    """有没有可能是状态：含状态词典里的词、常见状态词根，或是中文。"""
    if re.search(r"[一-鿿]", s) or _STATUS_WORDS.search(s):
        return True
    return any(w in _VOCAB_WORDS for w in re.findall(r"[a-z]+", s.lower()))


def _status_of(cell: str, labeled_only: bool = False):
    """单元格 / 行 → (阶段, 原文)；不是状态返回 None。标题里出现 Accept 之类的词不会被当成状态。"""
    return _status_core(cell, "line", labeled_only)


def _stage_rank(st: str) -> int:
    return STAGES.get(st or "", {}).get("order", 0)


# ---------------- 文件夹 / 队列 ----------------
# 名称 → (方式, 阶段)：force = 文件夹决定阶段；fallback = 状态认不出时才用；status = 没有状态列时队列名就是状态
_FOLDERS = {
    # Editorial Manager
    # 「draft」= 还没投出去（作者没填完 / 没确认 PDF）：阶段留空。退回作者的（Sent Back）是投过又退回来的，算已提交
    "submissions sent back to author": ("force", "submitted"), "incomplete submissions": ("draft", ""),
    "submissions waiting for author's approval": ("draft", ""),
    "submissions being processed": ("fallback", "submitted"),
    "submissions needing revision": ("force", "revise"), "revisions sent back to author": ("force", "revise"),
    # 返修稿还没交上去：原稿已经投过，这篇就是「返修」中（与 ScholarOne 的 Revised Manuscripts in Draft 一致）
    "incomplete submissions being revised": ("force", "revise"),
    "revisions waiting for author's approval": ("force", "revise"),
    "revisions being processed": ("fallback", "editor"), "declined revisions": ("force", "withdrawn"),
    "submissions with a decision": ("fallback", "decision"),
    "submission transfers waiting for author's approval": ("force", "rejected"),
    "submissions in production": ("force", "production"), "current task assignments": ("force", "production"),
    "completed production task assignments": ("force", "production"),
    "submissions with production completed": ("force", "production"),
    # ScholarOne 队列（只是提示，不覆盖状态格）
    "revised manuscripts in draft": ("status", "revise"), "resubmitted manuscripts in draft": ("draft", ""),
    "unsubmitted and manuscripts in draft": ("draft", ""), "unsubmitted manuscripts": ("draft", ""),
    "manuscripts awaiting revision": ("fallback", "revise"), "manuscripts with decisions": ("fallback", "decision"),
    "submitted manuscripts": ("fallback", ""), "manuscripts i have co-authored": ("fallback", ""),
    "withdrawn manuscripts": ("fallback", "withdrawn"), "manuscripts awaiting approval to transfer": ("fallback", "rejected"),
    "invited manuscripts": ("fallback", ""), "manuscripts in appeal": ("fallback", ""),
    # 知网 / 中文期刊文件夹
    "待修改稿件": ("fallback", "revise"), "责编退改稿件": ("fallback", "revise"), "评审后退改稿件": ("fallback", "revise"),
    "副主编退改稿件": ("fallback", "revise"), "主编退改稿件": ("fallback", "revise"), "退修稿件": ("fallback", "revise"),
    "需要修改稿件": ("fallback", "revise"), "录用稿件": ("fallback", "accepted"), "已经录用的稿件": ("fallback", "accepted"),
    "退稿稿件": ("fallback", "rejected"), "经审理后退稿": ("fallback", "rejected"),
    "未入库退稿的稿件": ("fallback", "rejected"), "已投稿件": ("fallback", ""), "编辑部正在处理的稿件": ("fallback", "editor"),
    "看校样稿件": ("fallback", "production"), "草稿": ("skip", ""),
}


@lru_cache(maxsize=8192)
def _folder_key(s: str) -> str:
    s = (s or "").strip()
    if len(s) > 200:                                     # 文件夹名都很短
        return ""
    s = _WS_RE.sub(" ", s.lstrip("←< ")).lower().replace("’", "'")
    s = s.split(" for author ")[0]                       # 旧版 EM「Submissions Being Processed for Author X」
    return re.sub(r"^\d+ ", "", s).strip()               # ScholarOne 左侧菜单「3 Submitted Manuscripts」


def _folder_of(s: str):
    return _FOLDERS.get(_folder_key(s))


def _folder_before(lines, idx, stop=0):
    """表头之前最近的文件夹 / 队列标题（不带计数的整行）。"""
    for j in range(idx - 1, max(stop, idx - 16) - 1, -1):
        ln = lines[j].strip()
        if not ln or "\t" in ln or _COUNT_TAIL_RE.search(ln):
            continue
        f = _folder_of(ln)
        if f:
            return f, _folder_key(ln), re.sub(r"^[←<\s]+", "", ln).split(" for Author ")[0].strip()
        m = re.search(r">>\s*([^>]+)$", ln)                  # 知网面包屑「稿件中心 >> 我的稿件管理 >> 已投稿件」
        if m and _folder_of(m.group(1)):
            return _folder_of(m.group(1)), _folder_key(m.group(1)), m.group(1).strip()
    return None


# ---------------- 日期 ----------------
_DIGIT_RE = re.compile(r"\d")
_MONTHS = {}
for _i, _names in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                             ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                             ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
                             ("dec", "december"))):
    for _n in _names:
        _MONTHS[_n] = _i + 1
_DATE_RES = (
    # 19-Nov-2025 / 19 November 2025 / 12 Nov, 2026 / 3rd Nov 2026
    (re.compile(r"(?<![\w.])(\d{1,2})(?:st|nd|rd|th)?[-\s/.]+([A-Za-z]{3,9})\.?,?[-\s/.,]+(\d{4})(?!\d)"), "dmy"),
    # Nature MTS「3rd Nov 26」：只认带序数后缀的两位年份
    (re.compile(r"(?<![\w.])(\d{1,2})(?:st|nd|rd|th)\s+([A-Za-z]{3,9})\.?\s+(\d{2})(?![\d:/.\-])"), "dmy2"),
    # Nov 19, 2025 / Nov 14 2026 9:02PM
    (re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)"), "mdy"),
    # 2025-11-19 / 2026/07/14 / 2026-3-9（同一种分隔符；知网稿号 2026.11.02.0005 不算日期）
    (re.compile(r"(?<![\d.\-/])(\d{4})([-/.])(\d{1,2})\2(\d{1,2})(?!\d|[-/.]\d)"), "ymd"),
    (re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"), "ymd_cn"),
    # 11/19/2025（EM / PLOS 是 mm/dd/yyyy；首段大于 12 时按 dd/mm）
    (re.compile(r"(?<![\d/.\-])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])"), "us"),
)
# 截止日期：修改期限 / 任务期限，不是状态日期
_DUE_LEAD_RE = re.compile(r"(?:\bdue\b(?:\s+(?:on|date|by))?|\bdeadline\b|\bexpires?\b|截止(?:日期|时间)?|前提交|计划完成(?:时间)?)"
                          r"\s*[:：]?\s*[(（]?\s*$", re.I)
_DUE_LINE_RE = re.compile(r"[(（]?(?:due\b|deadline|revision\s+due|date\s+revision\s+due|修改截止|截止)", re.I)   # 用在 strip 过的行上
# a/b/yyyy 怎么读：整段粘贴统一定（见 _slash_mode）。mdy = 月/日，dmy = 日/月，"" = 两段都不大于 12 时不认
_SLASH = ContextVar("syspaste_slash", default="")


def _mode_cached(fn):
    """lru_cache，键里带上当前这段粘贴的 a/b/yyyy 读法：换一段粘贴不会拿到按别的读法算出的旧结果。"""
    cached = lru_cache(maxsize=8192)(lambda mode, *a: fn(*a))

    def wrapper(*a):
        return cached(_SLASH.get(), *a)
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


def _is_due_line(ln: str) -> bool:
    return bool(_DUE_LINE_RE.match((ln or "").strip()))


def _slash_mode(t: str, system: str) -> str:
    """有 a > 12 的就都是日/月，有 b > 12 的就都是月/日；都看不出来时只有 EM（美式日期）按月/日读，其余不认。"""
    dmy = mdy = False
    for m in _DATE_RES[-1][0].finditer(t):
        a, b = int(m.group(1)), int(m.group(2))
        dmy |= a > 12 >= b
        mdy |= b > 12 >= a
    if dmy != mdy:
        return "dmy" if dmy else "mdy"
    if dmy:                                 # 同一页两种写法都有：只认不含糊的
        return ""
    return "mdy" if system == "editorial_manager" else ""


def _dates_in(s: str) -> tuple[tuple[int, str], ...]:
    """文本 → ((位置, YYYY-MM-DD), …)，按出现顺序。"""
    return _dates_in_mode(s, _SLASH.get())


@lru_cache(maxsize=8192)
def _dates_in_mode(s: str, slash: str) -> tuple[tuple[int, str], ...]:
    if not s or not _DIGIT_RE.search(s):
        return ()
    out = []
    for rx, kind in _DATE_RES:
        for m in rx.finditer(s or ""):
            try:
                if kind in ("dmy", "dmy2", "mdy"):
                    w = (m.group(2) if kind != "mdy" else m.group(1)).lower().rstrip(".")
                    mo = _MONTHS.get(w)
                    if not mo:
                        continue
                    d = int(m.group(1) if kind != "mdy" else m.group(2))
                    y = int(m.group(3))
                    if kind == "dmy2":
                        y += 2000
                elif kind == "ymd":
                    y, mo, d = int(m.group(1)), int(m.group(3)), int(m.group(4))
                elif kind == "ymd_cn":
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                else:
                    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    if a > 12 or (b <= 12 and slash == "dmy"):
                        mo, d = b, a
                    elif b > 12 or slash == "mdy":
                        mo, d = a, b
                    else:
                        continue            # 03/04/2026：这一页看不出是几月几号，宁可不认
                if not (1990 <= y <= 2100):
                    continue
                out.append((m.start(), m.end(), date(y, mo, d).isoformat()))
            except ValueError:
                continue
    out.sort()
    dedup, end = [], -1
    for pos, e, d in out:
        if pos >= end:
            dedup.append((pos, d))
            end = e
    return tuple(dedup)


def _plain_dates(s: str) -> list[str]:
    """一段文本里不是截止日期的日期。"""
    out = []
    for ln in (s or "").split("\n"):
        if _is_due_line(ln):
            continue
        for pos, d in _dates_in(ln):
            if not _DUE_LEAD_RE.search(ln[max(0, pos - 30):pos]):
                out.append(d)
    return out


def _first_date(s: str) -> str:
    ds = _plain_dates(s)
    return ds[0] if ds else ""


_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?", re.I)


@_mode_cached
def _date_only(s: str) -> str:
    """整行只是一个日期（可带时间、「Completed」前缀）→ 日期；否则 ""。"""
    s = (s or "").strip()
    if not s or len(s) > 40 or "\t" in s:
        return ""
    ds = _dates_in(s)
    if len(ds) != 1:
        return ""
    rest = s
    for rx, _k in _DATE_RES:
        rest = rx.sub(" ", rest)
    rest = _TIME_RE.sub(" ", rest)
    rest = re.sub(r"^\s*(?:completed|on)\b", " ", rest, flags=re.I)
    return ds[0][1] if not re.sub(r"[\s,.;:()（）|-]+", "", rest) else ""


def _today() -> str:
    return date.today().isoformat()


# ---------------- 噪声 / 标题 ----------------
_NOISE_EXACT = {
    "action", "actions", "action links", "view submission", "view submission (pdf)", "[view submission]",
    "view decision letter", "view decision", "view letter", "view proof", "view reviews", "view review", "view details",
    "view article", "view attachments", "view revision", "view reference checking results", "view assignment letter",
    "view instructions", "view questionnaire responses", "view accepted submission overview", "view submitted files",
    "view review forum", "view report", "review decision letter", "decision letter", "decision letters",
    "correspondence", "send e-mail", "send email", "contact journal", "contact support", "contact us",
    "contact the editorial office", "submission overview", "details", "display", "withdraw submission",
    "resume", "resume submission", "revise submission", "continue", "continue submission", "create a revision",
    "create a resubmission", "edit submission", "edit revision", "approve submission", "approve revision",
    "remove submission", "reinstate submission", "decline to revise", "decline to transfer", "agree to transfer",
    "file inventory", "fees and payments", "delete", "print", "help", "home", "main menu", "author main menu",
    "author dashboard", "my submissions", "my dashboard", "log out", "logout", "log in", "skip to main content",
    "skip to content", "go to submission", "submit revision", "start resubmission", "manage submission",
    "update submission", "manage article", "request an extension", "request extension", "show versions",
    "resend to journal", "upload revised version", "submit task", "assignment files", "check status",
    "transfer copyright", "enter review forum", "enter production forum", "no action required",
    "start a new submission", "start new submission", "new submission", "submit a manuscript", "submit new manuscript",
    "pay apc", "download pdf", "cite", "share", "read the published article", "view transfer options",
    "previous", "next", "results per page", "top", "search", "all submission statuses", "open submission statuses",
    "all publications", "all author roles", "all journals", "no title supplied yet", "(no title entered)",
    "initial submission", "查看", "查看投稿", "联系期刊", "上传修改稿", "下载录用通知", "立即处理", "关闭本页", "返回",
    "[返回]", "退出", "首页", "帮助", "操作", "详情", "查看审理历史", "查看费用情况", "退出登录", "查询", "重置", "前往",
    "页", "全选/取消全选", "跳转", "我的稿件", "个人信息", "修改密码", "在线投稿", "作者首页", "我要投稿", "消息通知",
    "个人中心", "稿件中心", "返回工作桌面", "new submissions", "revisions",
}
# 表头 / 字段名：不是标题
_HEADER_WORDS = {
    "title", "manuscript number", "manuscript id", "initial date submitted", "status date", "current status",
    "status", "id", "created", "submitted", "journal", "article type", "corresponding author", "author", "authors",
    "co-author", "co-authors", "abstract", "keywords", "files", "history", "overview", "comments", "标题", "题目",
    "稿件编号", "状态", "投稿日期", "修改", "投稿",
}
# 在 _nkey 之后用（小写、空白已折成单个空格）：开头固定的整行模式 + 行内任意位置的片段，都不带会回溯的「.*X.*」
_NOISE_RE = re.compile(
    r"(?:page ?:? ?\d+ of \d+|showing \d+|displaying |©|copyright|privacy|"
    r"terms (?:of|and|&)|powered by|site time |view (?:the|active|accepted) |back to |similarity check|search by |"
    r"sort by|共 ?[（(]? ?\d|第 ?\d+ ?页|每页|技术支持|版权所有|本系统|友情链接|欢迎您|您好)|"
    r"(?:\d+|revision \d+|version \d+|previous(?: \d+)?(?: next)?)$")
_NOISE_IN_RE = re.compile(r"\(\d+ total submissions\)|results? per page|all rights reserved|\bcookies?\b|>>|【[^【】]*】")


_WS_RE = re.compile(r"\s+")
_ARROW_RE = re.compile(r"[↓↑▲▼]")


@lru_cache(maxsize=8192)
def _nkey(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").strip().lower()).strip(" :：")


@lru_cache(maxsize=8192)
def _is_noise(s: str) -> bool:
    if len(s or "") > 300:                  # 按钮、页脚、分页都是短行
        return False
    k = _nkey(s)
    return not k or k in _NOISE_EXACT or bool(_NOISE_RE.match(k)) or bool(_NOISE_IN_RE.search(k))


_ARTICLE_TYPES = {
    "research article", "article", "original research", "original article", "regular paper", "regular article",
    "letter", "letters", "communication", "short communication", "rapid communication", "review", "review article",
    "mini review", "systematic review", "methods", "method", "brief research report", "brief report",
    "conference paper", "special issue paper", "technical note", "perspective", "editorial", "commentary",
    "case report", "data descriptor", "full paper", "short paper", "survey", "research paper",
    "hypothesis and theory", "opinion", "研究论文", "综述", "研究简报", "论著", "短文",
}
_FUNC_WORDS = re.compile(r"\b(?:of|for|in|on|the|a|an|and|with|to|via|from|under|using|by|at|into|over|toward|towards|"
                         r"is|are|as|or|its|through|based)\b")
_TITLE_TAIL_RE = re.compile(r"\[\s?view\s{1,3}submission\s?\]$|view\s{1,3}submission$", re.I)


def _clean_title(s: str) -> str:
    """去掉 ScholarOne 旧版标题后面的「[View Submission]」和首尾引号。"""
    s = (s or "").strip()
    tail = s[-40:]                                      # 只在结尾几十个字上找，不在长串上跑正则
    m = _TITLE_TAIL_RE.search(tail)
    if m:
        s = s[:len(s) - len(tail) + m.start()].rstrip()
    return s.strip("\"“”'").strip()


def _is_article_type(s: str) -> bool:
    return _nkey(s) in _ARTICLE_TYPES


def _is_note(s: str) -> bool:
    """卡片里的说明句（以句号结尾的整句），不是标题、不是期刊名。"""
    return (s or "").strip().endswith((".", "。")) and _is_sentence(s)


def _is_sentence(s: str) -> bool:
    s = (s or "").strip()
    words = len(s.split())
    cjk = len(re.findall(r"[一-鿿]", s))
    if s.endswith((".", "。", "!", "！")) and (words >= 4 or cjk >= 6):
        return True
    return s.endswith(("?", "？")) and words <= 5


def _is_author_line(s: str) -> bool:
    """「Alice Example *, Bruno Placeholder」「Bob Example，Carol Example」这样的作者名单。"""
    s = (s or "").strip()
    if len(s) > 300:
        return False
    parts = [p.strip(" \t*†‡0123456789,") for p in re.split(r"[,，、;；]", s)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return False
    for p in parts:
        if re.fullmatch(r"[一-鿿·]{2,5}", p):
            continue
        toks = p.split()
        if not 1 <= len(toks) <= 4 or _FUNC_WORDS.search(p.lower()):
            return False
        if not all(re.fullmatch(r"[A-Z][A-Za-z'’.\-]*\*?", t) for t in toks):
            return False
    return True


@_mode_cached
def _title_score(s: str) -> int:
    if len(s or "") > 420:
        return 0
    s = _clean_title(s)
    if not s or len(s) > 400 or "\t" in s or _is_noise(s) or _nkey(s) in _HEADER_WORDS:
        return 0
    cjk = len(re.findall(r"[一-鿿]", s))
    words = len(re.findall(r"[A-Za-z]{2,}", s))
    if (cjk < 6 and words < 3) or (len(s) < 12 and cjk < 6):
        return 0
    if _dates_in(s) and len(s) < 40:
        return 0
    if re.search(r"\b(?:\w+@\w+|https?://|www\.)|\b10\.\d{4,9}/", s) or " · " in s or "|" in s:
        return 0
    if _is_sentence(s) or _is_author_line(s) or _COUNT_TAIL_RE.search(s) or _folder_of(s) or _is_article_type(s):
        return 0
    if s.endswith((",", "，", ":", "：", ";", "；")):        # 称呼（Dear Dr X,）、字段名：不是标题
        return 0
    if s.isupper() and not cjk:
        return 0
    if _status_of(s):
        return 0
    return min(len(s), 200) + (40 if s[:1].isupper() or cjk else 0)


_LABEL_TITLE_RE = re.compile(r"^(?:(?:Manuscript\s+|Article\s+|Paper\s+)?Title|标题|题目|论文题目|稿件题目|稿件标题)\s*[:：]\s*(.+)$", re.I)
_LABEL_JOURNAL_RE = re.compile(r"^(?:Journal(?:\s+name)?|Publication|Submitted\s+to|期刊|刊名)\s*[:：\t]\s*(.+)$", re.I)


# ---------------- 预处理 ----------------
def _prep(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").replace("\u3000", " ")
    t = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", t)
    return re.sub(r"\n{3,}", "\n\n", t)          # 连续的空行只留两行（多余的空行不影响版式，只拖慢逐行处理）


def detect_system(t: str) -> str:
    low = t.lower()
    scores = {}
    for key, (_label, marks) in SYSTEMS.items():
        scores[key] = sum(w for m, w in marks if m in low)
    for kind, rx in _ID_RES:
        if kind in ("editorial_manager", "mdpi", "researchsquare") and rx.search(t):
            key = kind if kind != "researchsquare" else "researchsquare"
            scores[key] = scores.get(key, 0) + 3
    best = max(scores, key=lambda k: scores[k]) if scores else ""
    return best if best and scores[best] >= 2 else ""


# ---------------- 表格 ----------------
_COL_ROLES = {}
for _role, _names in (
        ("id", ("manuscript number", "manuscript id", "manuscript #", "manuscript no", "manuscript no.", "id",
                "submission id", "paper id", "article id", "稿件编号", "稿号", "编号", "投稿编号", "文章编号", "论文编号")),
        ("title", ("title", "manuscript title", "article title", "paper title", "submission title", "稿件标题", "标题",
                   "题目", "论文题目", "稿件题目", "文章标题", "中文标题")),
        ("status", ("status", "current status", "current stage", "manuscript status", "submission status", "稿件状态",
                    "当前状态", "当前阶段", "审理状态", "审稿状态", "状态")),
        ("submitted", ("initial date submitted", "date submitted", "submitted", "submission date", "submitted on",
                       "date received", "投稿日期", "投稿时间", "收稿日期", "提交日期", "提交时间", "注册时间", "注册日期")),
        ("status_date", ("status date", "last update", "last updated", "updated", "last modified", "decision date",
                         "date of decision", "处理日期", "最后处理日期", "最后处理时间", "状态更新时间", "更新时间", "更新日期",
                         "最后更新", "处理时间")),
        ("decisioned", ("decisioned", "date decisioned")),
        ("created", ("created", "date created", "date submission began", "last saved", "submission started", "创建日期",
                     "创建时间")),
        ("due", ("date revision due", "due date", "date task due", "revision due", "计划完成时间", "截止日期", "修改截止日期")),
        ("task", ("task",)),
        ("task_date", ("date task assigned",)),
        ("action", ("action", "actions", "操作", "continue", "delete", "立即处理", "options", "continue submission")),
        ("journal", ("journal", "publication", "期刊", "刊名", "journal name")),
        ("event", ("event", "letter", "处理过程", "历史阶段")),
        ("date", ("date", "correspondence date", "开始时间", "event date")),
        ("ignore", ("序号", "作者", "计划栏目", "学科", "投稿方向", "稿件类型", "authorship", "view decision",
                    "submitting author", "current revision #", "date until due", "doi", "article type", "assigned by",
                    "order", "author name", "email address", "orcid identifier", "academic degree(s)", "affiliation",
                    "institution", "confirmed?", "added in revision", "recipient", "revision", "revision #", "decision",
                    "处理人", "实际完成时间", "历史明细", "说明", "姓名", "单位", "通讯作者", "type", "manuscript type",
                    "authors", "author", "corresponding author", "处理状态", "处理结果", "action required"))):
    for _n in _names:
        _COL_ROLES[_n] = _role
_MEANINGFUL = {"id", "title", "status", "task", "event"}


@lru_cache(maxsize=8192)
def _col_label(c: str) -> str:
    return _WS_RE.sub(" ", _ARROW_RE.sub("", c or "")).strip().lower().strip(" :：*")


@lru_cache(maxsize=8192)
def _header_roles(line: str):
    """表头行 → 每列的含义（元组）；不是表头返回 None。"""
    if "\t" not in line or len(line) > 400:
        return None
    cells = [_col_label(c) for c in line.split("\t")]
    nonempty = [c for c in cells if c]
    if len(nonempty) < 2:
        return None
    known = [c for c in nonempty if c in _COL_ROLES]
    if len(known) < 2 or len(known) < 0.6 * len(nonempty):
        return None
    roles = [_COL_ROLES.get(c) if c else None for c in cells]
    rs = set(r for r in roles if r)
    timeline = bool({"date", "status_date"} & rs and {"event", "action"} & rs and not rs & {"id", "title"})
    if not (rs & _MEANINGFUL) and not timeline:
        return None
    if len(nonempty) == 2 and not timeline:        # 「Submission Status<TAB>Submitted」是一行「标签 / 值」，不是表头
        return None
    return tuple(roles)


class _Table:
    def __init__(self, hdr, roles):
        self.hdr, self.roles, self.rows, self.end = hdr, roles, [], hdr + 1
        self.ncols = len(roles)
        self.guess_status = None        # 没有状态表头时，按内容认出的状态列

    def col(self, role):
        return next((i for i, r in enumerate(self.roles) if r == role), None)


def _find_tables(lines):
    tables, n, i = [], len(lines), 0
    while i < n:
        roles = _header_roles(lines[i])
        if not roles:
            i += 1
            continue
        tb = _Table(i, roles)
        need = tb.ncols - 1
        buf, btabs, j, start, height = [], 0, i + 1, i + 1, 1
        while j < n:
            ln = lines[j]
            if _header_roles(ln):
                break
            t = ln.count("\t")
            if btabs + t > need:
                if buf and btabs >= need - 1 and need >= 3:     # 少了最后一个空格子（尾部 TAB 被吃掉）
                    tb.rows.append((start, j - 1, "\n".join(buf).split("\t") + [""]))
                    tb.end = j
                buf, btabs, start = [], 0, j
                if t > need:
                    j += 1
                    start = j
                    continue
            if not buf:
                start = j
            buf.append(ln)
            btabs += t
            if btabs == need:
                tb.rows.append((start, j, "\n".join(buf).split("\t")))
                height = max(height, len(buf))
                buf, btabs = [], 0
                tb.end = j + 1
            elif len(buf) > (40 if len(tb.rows) < 2 else 2 * height + 2):
                break           # 表格已经结束（窄表格后面的「标签<TAB>值」行不能被凑成表格行）
            j += 1
        if buf and btabs >= need - 1 and need >= 3:     # 最后一行少了尾部的空格子
            tb.rows.append((start, start + len(buf) - 1, "\n".join(buf).split("\t") + [""]))
            tb.end = start + len(buf)
        tables.append(tb)
        i = max(tb.end, i + 1)
    return tables


def _cell_lines(cell: str) -> list[str]:
    return [ln.strip() for ln in (cell or "").split("\n") if ln.strip()]


def _id_from_cell(cell: str) -> str:
    lns = _cell_lines(cell)
    for ln in reversed(lns):
        ids = _ids_in(ln)
        if ids and not _REF_LINE_RE.search(ln):
            return ids[0][1]
    for ln in reversed(lns):
        v = _id_like(ln)
        if v and _nkey(ln) not in _NOISE_EXACT:
            return v
    return ""


def _title_from_cell(cell: str):
    """标题格 → (标题, 第二行（期刊 / 作者）)。"""
    out = []
    for ln in _cell_lines(cell):
        ln = _clean_title(ln)
        if not ln or _is_noise(ln) or _nkey(ln) in _HEADER_WORDS or _is_author_line(ln):
            continue
        out.append(ln)
    return (out[0] if out else ""), (out[1] if len(out) > 1 else "")


def _status_from_cell(cell: str, mode: str = "cell"):
    """状态格（可能多行：角色行、Contact Journal、Due on、view decision letter…）→ (阶段, 原文, 状态日期)。"""
    found, others, sdate = [], [], ""
    for ln in _cell_lines(cell):
        ln = re.sub(r"^[•·◦▪●○■\-–—*]+\s*", "", ln).strip()
        if not ln or _is_noise(ln) or _ROLE_LINE_RE.match(ln) and not _STATUS_LABEL_RE.match(ln):
            continue
        if _DUE_LINE_RE.match(ln) or _REF_LINE_RE.search(ln):
            continue
        d = _date_only(ln)
        if d:
            sdate = sdate or d
            continue
        st = _status_core(ln, mode)
        if st:
            m = _PAREN_DATE_RE.search(ln)
            if m and not sdate:
                sdate = _first_date(m.group(1))
            found.append(st)
        else:
            others.append(ln)
    if len(found) > 1 and _sk(found[0][1]) in _GROUP_STATUS:
        found = found[1:]
    if found:
        if len(found) == 1:
            return found[0][0], found[0][1], sdate
        best = max(found, key=lambda x: _stage_rank(x[0]))
        return best[0], "; ".join(x[1] for x in found), sdate
    return "", (_WS_RE.sub(" ", others[0])[:200] if others else ""), sdate      # 认不出的状态原样留着（截短）


def _row_record(tb, cells, system, folder):
    cells = (cells + [""] * tb.ncols)[:tb.ncols]
    get = lambda role: cells[tb.col(role)] if tb.col(role) is not None else ""
    mid = _id_from_cell(get("id")) if tb.col("id") is not None else ""
    title, second = _title_from_cell(get("title")) if tb.col("title") is not None else ("", "")
    journal = _cell_lines(get("journal"))[0] if _cell_lines(get("journal")) else ""
    if not journal and second and system == "snapp":
        journal = second
    stage, raw, sdate = "", "", ""
    sc = tb.col("status") if tb.col("status") is not None else tb.col("task")
    if sc is None:
        sc = tb.guess_status
    if sc is not None:
        stage, raw, sdate = _status_from_cell(cells[sc])
    sub = _first_date(get("submitted"))
    sdate = sdate or _first_date(get("status_date")) or _first_date(get("task_date")) or _first_date(get("decisioned"))
    if folder:
        (mode, fst), _fkey, fname = folder
        if mode == "skip":
            return None
        if mode == "draft":                 # 还没投出去：阶段留空（显示出来，但不导入）
            raw, stage = raw or fname, ""
        elif mode == "status" and sc is None:
            raw, stage = raw or fname, fst
        elif mode == "force" and fst:
            stage = fst
        elif fst and not stage:
            stage = fst
    if not mid and not title:
        return None
    return {"mid": mid, "title": title, "raw": raw, "stage": stage, "sd": sdate, "sub": sub, "journal": journal}


def _rec_key(r):
    """去重用：同一稿件各版本算一篇，没编号的按标题。"""
    return ms_base(r["mid"]) if r["mid"] else "#" + title_key(r["title"])


def _table_records(tables, lines, system):
    recs, page_status, seen, looked = [], [], set(), 0
    for tb in tables:
        rs = set(r for r in tb.roles if r)
        if not tb.rows:
            continue
        if not ({"id", "title"} & rs):
            if "status" in rs:              # 知网审理历史「当前阶段」表：只有状态，归到所在的单篇页面
                cells = (tb.rows[0][2] + [""] * tb.ncols)[:tb.ncols]
                st, raw, sd = _status_from_cell(cells[tb.col("status")])
                dc = tb.col("date") if tb.col("date") is not None else tb.col("status_date")
                if raw:
                    page_status.append((tb.hdr, st, raw, sd or (_first_date(cells[dc]) if dc is not None else "")))
            continue
        if not ({"status", "task"} & rs):
            # 没有状态列：看有没有哪一列（含空表头列）的值大多是状态（知网旧版的状态列没有表头）
            for ci, r in enumerate(tb.roles):
                vals = [row[2][ci] for row in tb.rows[:200] if ci < len(row[2]) and row[2][ci].strip()] \
                    if r in (None, "ignore") else []
                if vals and sum(1 for v in vals if _status_from_cell(v)[0]) >= max(1, len(vals) * 0.6):
                    tb.guess_status = ci
                    break
            if tb.guess_status is None and "id" not in rs:
                continue            # 没有编号也没有状态（MDPI「Incomplete Submissions」草稿表）：不是稿件列表
        folder = _folder_before(lines, tb.hdr)
        for start, end, cells in tb.rows:
            if len(seen) >= _MAX_ITEMS or looked >= _MAX_LOOK:
                break
            looked += 1
            r = _row_record(tb, cells, system, folder)
            if r:
                r["span"] = (start, end)
                recs.append(r)
                seen.add(_rec_key(r))
    return recs, page_status


# ---------------- 标签 / 值（卡片与单篇页面） ----------------
_LABELS = {}
for _role, _names in (
        ("id", ("manuscript id", "manuscript #", "manuscript number", "manuscript no", "manuscript no.", "submission id",
                "editorial reference code", "稿件编号", "稿号", "投稿编号", "paper id", "article id", "submission")),
        ("title", ("title", "manuscript title", "article title", "paper title", "稿件标题", "标题", "题目", "论文题目",
                   "稿件题目", "中文标题", "中文题目")),
        ("title_en", ("英文标题", "english title", "英文题目")),
        ("status", ("status", "submission status", "current status", "manuscript status", "current stage", "稿件状态",
                    "当前状态", "审稿状态", "审理状态")),
        ("journal", ("journal", "submitted to", "期刊", "刊名", "journal name")),
        ("submitted", ("submitted on", "submitted", "date submitted", "submission date", "submission received",
                       "initial date submitted", "投稿日期", "投稿时间", "收稿日期", "提交日期", "提交时间", "date received")),
        ("status_date", ("last modified", "last update", "last updated", "updated", "updated on", "decision date",
                         "status date", "状态更新时间", "最后处理日期", "更新时间", "date of decision")),
        ("ignore_date", ("submission started", "revision due", "due", "due on", "due date", "date revision due",
                         "修改截止日期", "截止日期", "last saved", "created", "date created", "posted")),
        ("other", ("article type", "peer review type", "authors", "author", "corresponding author", "abstract", "keywords",
                   "affiliations", "doi", "preprint doi", "charges", "section", "type", "number of pages",
                   "manuscript type", "subject terms", "contributing authors", "assistant editor", "作者", "稿件类型",
                   "关键词", "投稿人", "manuscript files", "figures", "additional information", "current revision #",
                   "associated manuscripts", "decision", "publication", "author role", "通讯作者", "单位"))):
    for _n in _names:
        _LABELS[_n] = _role
_STATE_DATE_RE = re.compile(r"^(accepted|rejected|withdrawn|revision\s+requested|declined|published|returned|"
                            r"transferred|decision)\s+on$")
_DATE_ROLES = {"submitted", "status_date", "ignore_date", "state_date"}
_INLINE_DATE_LABEL_RE = re.compile(
    r"^(submitted\s+on|submitted|last\s+update|last\s+updated|updated(?:\s+on)?|last\s+modified|accepted\s+on|"
    r"rejected\s+on|withdrawn\s+on|revision\s+requested\s+on|posted|due(?:\s+on)?)\s+(?=\S*\d)(.+)$", re.I)
_FLAG_RE = re.compile(r"^action\s+required$", re.I)


def _label_role(lab: str):
    k = _col_label(lab)
    if k in _LABELS:
        return _LABELS[k], ""
    m = _STATE_DATE_RE.match(k)
    if m:
        return "state_date", m.group(1)
    return None, ""


@_mode_cached
def _label_of(line: str):
    """一行 → (角色, 值, 写法, 状态名, 标签)；不是「标签 / 值」行返回 None。写法：tab / colon / bare / inline。"""
    s = line.strip()
    if not s or len(s) > 300:
        return None
    if "\t" in s:
        cells = [c.strip() for c in s.split("\t")]
        role, state = _label_role(cells[0])
        if role:
            return role, next((c for c in cells[1:] if c), ""), "tab", state, _col_label(cells[0])
        return None
    m = re.match(r"^([^:：]{1,40}?)\s*[:：]\s*(.*)$", s)
    if m:
        role, state = _label_role(m.group(1))
        if role:
            return role, m.group(2).strip(), "colon", state, _col_label(m.group(1))
    role, state = _label_role(s)
    if role:
        return role, "", "bare", state, _col_label(s)
    m = _INLINE_DATE_LABEL_RE.match(s)
    if m and _dates_in(m.group(2)):
        role, state = _label_role(m.group(1))
        if role:
            return role, m.group(2).strip(), "inline", state, _col_label(m.group(1))
    return None


def _value_ok(role: str, v: str) -> bool:
    v = (v or "").strip()
    if not v or _is_noise(v) and role not in ("title", "id"):
        return False
    if role in _DATE_ROLES:
        return bool(_dates_in(v))
    if role == "id":
        return bool(_id_like(v) or _ids_in(v))
    if role == "status":
        if _status_core(v.split("\n")[0], "cell"):
            return True
        return not _label_of(v) and not _title_score(v) and len(v.split()) <= 6
    return True


class _F:
    """一个「标签 / 值」字段。"""
    __slots__ = ("i", "j", "role", "value", "state", "lab")

    def __init__(self, i, j, role, value, state="", lab=""):
        self.i, self.j, self.role, self.value, self.state, self.lab = i, j, role, value, state, lab

    def id_value(self) -> str:
        """编号字段的值；T&F 的「SUBMISSION」标签只认 9 位编号（时间线里同名的小节标题后面跟的是日期）。"""
        v = self.value.split("\n")[0].strip()
        if self.lab == "submission":
            return v if _TF_ID_RE.match(v) else ""
        ids = _ids_in(v)
        return ids[0][1] if ids else _id_like(v)


def _classify(lines, skip):
    """每行的类别：blank / label / value / noise / flag / text / table；以及字段列表。"""
    n = len(lines)
    kind = ["text"] * n
    fields = []
    i = 0
    while i < n:
        if i in skip:
            kind[i] = "table"
            i += 1
            continue
        s = lines[i].strip()
        if not s:
            kind[i] = "blank"
            i += 1
            continue
        lab = _label_of(s)
        if lab:
            role, val, form, state, ltext = lab
            j = i
            if form in ("bare", "colon") and not val:
                k = i + 1
                nxt = lines[k].strip() if k < n and k not in skip else ""
                if nxt and (not _label_of(nxt) or role == "status") \
                        and not (nxt.isupper() and len(nxt) <= 30 and not re.search(r"\d", nxt)) and _value_ok(role, nxt):
                    val, j = nxt, k
                elif form == "bare" and role not in ("other",):
                    kind[i] = "noise" if _is_noise(s) else "text"
                    i += 1
                    continue
            elif not _value_ok(role, val) and role not in ("other", "title"):
                if role in _DATE_ROLES or role == "id":
                    kind[i] = "text"
                    i += 1
                    continue
            # 折行：「Accepted On<TAB>2 November 2026」+「by Editorial Office」、「Current Stage<TAB>X」+「6th Nov 26」
            k = j + 1
            while k < n and k not in skip:
                nx = lines[k].strip()
                if nx and "\t" not in nx and (re.match(r"^by\s+\S", nx) or (role == "status" and _date_only(nx))):
                    val = val + "\n" + nx
                    k += 1
                else:
                    break
            kind[i] = "label"
            for x in range(i + 1, k):
                kind[x] = "value"
            fields.append(_F(i, k - 1, role, val, state, ltext))
            i = k
            continue
        if _FLAG_RE.match(s) or s.upper() == "ACTION REQUIRED":
            kind[i] = "flag"
        elif _is_noise(s):
            kind[i] = "noise"
        i += 1
    return kind, fields


@_mode_cached
def _bare_status(s: str):
    s = (s or "").strip()
    if not s or "\t" in s or len(s) > 80 or s.count(" ") > 9 or not _maybe_status(s):
        return None
    if _COUNT_TAIL_RE.search(s) or _folder_of(s) or _is_noise(s):
        return None
    lab = _label_of(s)
    if _is_sentence(s) or (lab and not (lab[2] == "bare" and lab[0] in _DATE_ROLES)):
        return None
    return _status_core(s, "line")


def _field_date(f: _F) -> str:
    return _first_date(f.value)


# ---------------- 卡片 ----------------
def _card_records(lines, kind, fields, bare):
    n = len(lines)
    fat = {}
    for f in fields:
        for x in range(f.i, f.j + 1):
            fat[x] = f
    zone_kinds = ("label", "value", "flag")
    # 锚：带标签的状态字段，以及单独一行的状态（Wiley Authors / Springer Nature 卡片）；后者要求紧挨着编号或日期字段
    anchors = [(f.i, f.j, f.value) for f in fields
               if f.role == "status" and f.value and not _is_noise(f.value.split("\n")[0])]
    anchors += [(i, i, lines[i].strip()) for i in range(n) if bare[i]]

    def zone(a0, a1):
        zs, ze = a0, a1
        while zs - 1 >= 0 and kind[zs - 1] in zone_kinds and (zs - 1) not in starts:
            zs -= 1
        while ze + 1 < n and kind[ze + 1] in zone_kinds and (ze + 1) not in starts:
            ze += 1
        return zs, ze

    starts = set(a[0] for a in anchors)
    zones = sorted((a0, a1, val) + zone(a0, a1) for a0, a1, val in anchors)
    # 相邻卡片中间没有空行 / 按钮时，两个区间会连在一起：按第一张卡片的首个标签（各卡片字段顺序相同）切开
    if zones:
        first = next((fat[z[3]] for z in zones if z[3] in fat and fat[z[3]].i == z[3]), None)
        first_lab = first.lab if first is not None else None
        for k in range(1, len(zones)):
            pa0, pa1, pval, pzs, pze = zones[k - 1]
            a0, a1, val, zs, ze = zones[k]
            if zs <= pze:
                split = next((x for x in range(a0, pa1, -1) if x in fat and fat[x].i == x and fat[x].lab == first_lab),
                             None) if first_lab else None
                split = split if split is not None else a0
                zones[k - 1] = (pa0, pa1, pval, pzs, min(pze, split - 1))
                zones[k] = (a0, a1, val, max(zs, split), ze)
    cards = []
    for a0, a1, val, zs, ze in zones:
        zf = [fat[x] for x in range(zs, ze + 1) if x in fat and fat[x].i == x]
        if not any(f.role == "id" and f.id_value() or (f.role in ("submitted", "status_date", "state_date")
                                                       and _field_date(f)) for f in zf):
            continue
        if any(f.role == "status" for f in zf if f.i != a0) and kind[a0] != "label":
            continue
        cards.append([a0, a1, val, zs, ze, zf])
        if len(cards) >= _MAX_LOOK:
            break
    recs, seen = [], set()
    for k, (a0, a1, val, zs, ze, zf) in enumerate(cards):
        lo = cards[k - 1][4] + 1 if k else max(0, zs - 10)
        hdr = [x for x in range(lo, zs) if kind[x] in ("text",) and lines[x].strip()]
        # 标题
        title, ti = "", None
        ats = [x for x in hdr if _is_article_type(lines[x])]
        dots = [x for x in hdr if " · " in lines[x]]
        tf = next((f for f in zf if f.role == "title"), None)
        if tf:
            title = _clean_title(tf.value.split("\n")[0])
            if _is_noise(title):
                title = ""
        else:
            # 标题的位置按版式定：文章类型那一行的下一行；「期刊 · 栏目」那一行的上一行；都没有时是卡片的第一行
            # （第一张卡片前面混着页头，取最像标题的一行）。那一行不像标题（太短、是个问句…）就留空，
            # 不挪到旁边的行去——否则期刊名会变成标题、文章类型变成期刊
            if ats:
                ti = next((x for x in hdr if x > ats[-1]), None)
            elif dots:
                ti = next((x for x in reversed(hdr) if x < dots[-1]), None)
            elif k == 0:
                scored = [(x, _title_score(lines[x])) for x in hdr]
                scored = [x for x in scored if x[1]]
                ti = max(scored, key=lambda x: x[1])[0] if scored else None
            else:
                ti = next((x for x in hdr if not _is_note(lines[x])), None)
            if ti is not None and _title_score(lines[ti]):
                title = _clean_title(lines[ti])
        # 期刊：卡片字段 > 标题下面的「期刊 · 栏目」> 文章类型上面那一行 > 标题下面那一行
        journal = ""
        jf = next((f for f in zf if f.role == "journal"), None)
        pos = ti if ti is not None else (ats[-1] if ats else None)
        dot = next((x for x in hdr if pos is not None and x > pos and " · " in lines[x]), None)
        if jf:
            journal = jf.value.split("\n")[0].strip()
        elif dot is not None:
            journal = lines[dot].split(" · ")[0].strip()
        elif ats:
            before = [x for x in hdr if x < ats[-1] and not _is_article_type(lines[x])
                      and _nkey(lines[x]) not in _HEADER_WORDS and not _is_note(lines[x])]
            journal = lines[before[-1]].strip() if before else ""
        elif ti is not None:
            after = [x for x in hdr if x > ti]
            if after and not bare[after[0]]:
                journal = lines[after[0]].strip()
        # 编号：卡片字段里的，其次标题和字段之间（「期刊 · Manuscript number X」）
        mid = next((f.id_value() for f in zf if f.role == "id" and f.id_value()), "")
        if not mid:
            for x in range(ti + 1 if ti is not None else zs, ze + 1):
                if _REF_LINE_RE.search(lines[x]):
                    continue
                ids = _ids_in(lines[x])
                if ids:
                    mid = ids[0][1]
                    break
        # 状态
        stage, raw, sdate = _status_from_cell(val.split("\n")[0] if "\n" in val and kind[a0] == "label" else val)
        if "\n" in val:
            sdate = sdate or _first_date("\n".join(val.split("\n")[1:]))
        for x in hdr:                   # 卡片头里的横幅（只认这几句）压过卡片状态
            if pos is not None and x > pos and bare[x] and _sk(lines[x]) in _BANNERS:
                stage, raw = bare[x]
        nxt = cards[k + 1][3] if k + 1 < len(cards) else min(n, ze + 8)
        own = range(zs, nxt)
        if any(kind[x] == "flag" for x in own) and _sk(raw) in _ACTION_UPGRADE:
            stage = _ACTION_UPGRADE[_sk(raw)]
        # 日期
        sub, sd_state, sd_label = "", "", ""
        for x in own:
            f = fat.get(x)
            if f is None or f.i != x:
                continue
            d = _field_date(f)
            if not d:
                continue
            if f.role == "submitted":
                sub = sub or d
            elif f.role == "state_date":
                st2 = _lookup(f.state, "cell")
                if st2 is not _MISS and st2 and st2 == stage and not sd_state:
                    sd_state = d
            elif f.role == "status_date":
                sd_label = sd_label or d
        sd = sd_state or sdate or sd_label
        if not sd and _sk(raw) in ("submitted", "submission confirmation"):
            sd = sub
        if not (mid or title):
            continue
        recs.append({"mid": mid, "title": title, "raw": raw, "stage": stage, "sd": sd, "sub": sub,
                     "journal": journal, "span": (min(zs, ti if ti is not None else zs), max(ze, nxt - 1))})
        seen.add(_rec_key(recs[-1]))
        if len(seen) >= _MAX_ITEMS:
            break
    return recs


# ---------------- 时间线（处理记录） ----------------
_SUBMIT_EVENT_RE = re.compile(r"^(?:manuscript\s+submitted|submitted|submission\s+received|manuscript\s+received|"
                              r"author\s+submits\s+new\b.*|新到稿件|投稿成功|投稿)$")


def _event_status(s: str):
    s = (s or "").strip()
    if not s or len(s) > 120 or _is_noise(s):
        return None
    return _status_core(s, "event")


def _timeline(lines, kind, tables):
    """[(日期, 阶段, 原文, 行号)]：处理记录表格 + 「日期行 / 事件行」交替的列表。"""
    ev = []
    for tb in tables:
        rs = set(r for r in tb.roles if r)
        if {"id", "title", "status", "task"} & rs:
            continue
        dc = next((tb.col(r) for r in ("date", "status_date", "submitted") if tb.col(r) is not None), None)
        ec = next((tb.col(r) for r in ("event", "action") if tb.col(r) is not None), None)
        if dc is None or ec is None:
            continue
        for start, end, cells in tb.rows:
            cells = (cells + [""] * tb.ncols)[:tb.ncols]
            d = _first_date(cells[dc])
            text = _cell_lines(cells[ec])[0] if _cell_lines(cells[ec]) else ""
            st = _event_status(text) if d else None
            if st and st[0]:                # 草稿类事件（Submission Created…）不算进度
                ev.append((d, st[0], st[1], start))
    n = len(lines)
    dl = [i for i in range(n) if kind[i] == "text" and _date_only(lines[i])
          and not re.match(r"^\s*posted\b", lines[i], re.I)]
    if dl:
        def ev_at(x):
            if 0 <= x < n and kind[x] in ("text",) and not _date_only(lines[x]):
                return _event_status(lines[x])
            return None
        ed = sum(1 for i in dl if ev_at(i - 1))
        de = sum(1 for i in dl if ev_at(i + 1))
        use_prev = ed > de or (ed == de and ev_at(dl[0] - 1) is not None)
        for i in dl:
            st = ev_at(i - 1 if use_prev else i + 1)
            if st and st[0]:
                ev.append((_date_only(lines[i]), st[0], st[1], i))
    return ev


def _latest(ev, stage=None):
    cand = [e for e in ev if stage is None or e[1] == stage]
    if not cand:
        return None
    best_d = max(e[0] for e in cand)
    tops = [e for e in cand if e[0] == best_d]
    # 同一天的几条：看列表是新的在前还是旧的在前
    asc = sum(1 for a, b in zip(ev, ev[1:]) if a[0] < b[0] and a[3] < b[3])
    desc = sum(1 for a, b in zip(ev, ev[1:]) if a[0] > b[0] and a[3] < b[3])
    return max(tops, key=lambda e: e[3]) if asc > desc else min(tops, key=lambda e: e[3])


# ---------------- 单篇页面 ----------------
_EM_TITLE_RE = re.compile(r"^[^\"“\t]{2,100}?:\s*[\"“](.+)[\"”]\s*$")


def _single_record(lines, kind, fields, tables, page_status, ev, bare, lo=0, hi=None, strict=False):
    """单篇稿件页面（或页面里没被列表 / 卡片占用的一段 [lo, hi)）→ 一条记录。strict：要有编号和明确的状态来源。"""
    hi = len(lines) if hi is None else hi
    n = hi
    fields = [f for f in fields if lo <= f.i < hi]
    tables = [tb for tb in tables if lo <= tb.hdr < hi]
    ev = [e for e in ev if lo <= e[3] < hi]
    page_status = next(((st, raw, sd) for h, st, raw, sd in page_status if lo <= h < hi), None)
    if strict and sum(1 for f in fields if f.role == "status") > 1:
        return None                 # 好几个状态字段：是没认出来的列表的一段，不是一篇稿件的详情页
    get = lambda role: next((f for f in fields if f.role == role and f.value.strip()), None)
    # 编号
    mid = next((f.id_value() for f in fields if f.role == "id" and f.id_value()), "")
    if not mid:
        for i in range(lo, hi):
            ids = _ids_in(lines[i]) if kind[i] not in ("table", "ref") else ()
            if ids and not _REF_LINE_RE.search(lines[i]):
                mid = ids[0][1]
                break
    # 状态
    stage, raw, sd, src = "", "", "", ""
    sf = get("status")
    if sf:
        stage, raw, sd = _status_from_cell(sf.value.split("\n")[0])
        sd = sd or _first_date("\n".join(sf.value.split("\n")[1:]))
        src = "label"
    if not raw and page_status:
        stage, raw, sd = page_status
        src = "table"
    if not raw and ev and (len(ev) >= 2 or not strict):
        e = _latest(ev)
        stage, raw, sd, src = e[1], e[2], e[0], "timeline"
    if not raw and strict:
        return None
    if not raw:
        # 单独一行的状态（ReX 概览页的状态框、ScholarOne「Submission Confirmation」）；
        # 连着三行以上都是状态的是进度条（Frontiers 六个阶段全列出来），不算
        cand, i = [], lo
        while i < n:
            if bare[i]:
                j = i
                while j + 1 < n and bare[j + 1]:
                    j += 1
                if j - i < 2:
                    cand += [(x, bare[x]) for x in range(i, j + 1)]
                i = j + 1
            else:
                i += 1
        ev_lines = set(e[3] for e in ev) | set(e[3] - 1 for e in ev) | set(e[3] + 1 for e in ev)
        cand = [x for x in cand if x[0] not in ev_lines]
        if cand:
            stage, raw = cand[0][1]
            src = "bare"
    if not raw:
        return None
    # 标题
    title = ""
    tf = get("title")
    if tf:
        title = _clean_title(tf.value.split("\n")[0])
        if _is_noise(title):
            title = ""
    if not title:
        for i in range(lo, hi):
            ln = lines[i].strip()
            m = _EM_TITLE_RE.match(ln) if len(ln) <= 400 and ("\"" in ln or "“" in ln) else None
            if m:
                title = m.group(1).strip()
                break
    first_field = min([f.i for f in fields if f.role in ("id", "status", "submitted")] +
                      [tb.hdr for tb in tables] + [n])
    if not title:
        head = [i for i in range(max(lo, first_field - 40), first_field) if kind[i] == "text"]
        ats = [i for i in head if _is_article_type(lines[i])]
        ti = None
        if ats:         # 文章类型下面那一行就是标题；那一行不像标题就留空，不去挑别的行
            ti = next((i for i in head if i > ats[-1]), None)
            if ti is not None and not _title_score(lines[ti]):
                ti = None
        else:
            sc = [(i, _title_score(lines[i])) for i in head]
            sc = [x for x in sc if x[1]]
            if sc:
                ti = max(sc, key=lambda x: x[1])[0]
        if ti is not None:
            title = _clean_title(lines[ti])
    if not title:
        tf = get("title_en")
        if tf:
            title = _clean_title(tf.value.split("\n")[0])
    if not mid and (strict or not (title and src in ("label", "table", "timeline"))):
        return None
    # 日期
    sub = ""
    f = next((f for f in fields if f.role == "submitted" and _field_date(f)), None)
    if f:
        sub = _field_date(f)
    if not sub:
        subs = sorted(e[0] for e in ev if _SUBMIT_EVENT_RE.match(_sk(e[2])))
        sub = subs[0] if subs else ""
    if not sd or src == "label":
        for f in fields:
            if f.role == "state_date" and _field_date(f):
                st2 = _lookup(f.state, "cell")
                if st2 is not _MISS and st2 and st2 == stage:
                    sd = sd or _field_date(f)
        f = next((f for f in fields if f.role == "status_date" and _field_date(f)), None)
        if f and not sd:
            sd = _field_date(f)
        if ev and src == "label":
            # 标签状态和处理记录里最新的一条是同一阶段：用那条（更具体，状态日期也来自它；Research Square
            # 「Status: Under Revision」+ 时间线「Major revision 12 Nov」）
            e, top = _latest(ev, stage), _latest(ev)
            if e and not sd:
                sd = e[0]
            if e and top is e:
                raw = e[2]
    if not sd and _sk(raw) in ("submitted", "submission confirmation"):
        sd = sub
    journal = ""
    jf = get("journal")
    if jf:
        journal = jf.value.split("\n")[0].strip()
    else:
        for i in range(lo, hi - 1):
            if lines[i + 1].strip().upper() == "JOURNAL HOME" and kind[i] == "text":
                journal = lines[i].strip()
                break
    return {"mid": mid, "title": title, "raw": raw, "stage": stage, "sd": sd, "sub": sub, "journal": journal,
            "span": (lo, hi - 1)}


# ---------------- 兜底：先找编号，再判断状态 / 标题在编号哪一侧 ----------------
def _find_ids(lines: list[str], kind=None) -> list[tuple[int, str]]:
    """[(行号, 编号)]：每行最多取一个（行内第一个），同一编号只在第一次出现处作为一篇稿件；邮件标题等引用行不算。"""
    found, seen = [], set()
    for i, ln in enumerate(lines):
        if kind is not None and kind[i] == "ref":
            continue
        hits = list(_ids_in(ln))
        if _ID_LABEL_HINT_RE.search(ln):
            for m in _LABELED_ID_RE.finditer(ln):
                v = _id_like(m.group(1))
                if v:
                    hits.append((m.start(1), v))
        if not hits:
            continue
        hits.sort()
        pos, mid = hits[0]
        if _REF_LINE_RE.search(ln[:pos + len(mid) + 2]):
            continue
        key = mid.upper()
        if key in seen:
            continue
        seen.add(key)
        found.append((i, mid))
    return found


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.split("\t") if c.strip()]


def _cell_status(cell: str):
    """（兜底用）一个单元格 → (阶段, 原文, 括号里的决定日期)；不是状态返回 None。"""
    if _is_due_line(cell):
        return None
    st = _status_of(cell)
    if not st:
        return None
    m = _PAREN_DATE_RE.search(cell)
    return st[0], st[1], (_first_date(m.group(1)) if m else "")


def _first_status(lines: list[str], reverse: bool = False):
    seq = reversed(lines) if reverse else lines
    for ln in seq:
        for cell in (reversed(_cells(ln)) if reverse else _cells(ln)):
            st = _cell_status(cell)
            if st:
                return st
    return None


def _best_title(cands: list[str]):
    best, bs = "", 0
    for c in cands:
        m = _LABEL_TITLE_RE.match(c)
        if m:
            return _clean_title(m.group(1))
        sc = _title_score(c)
        if sc > bs:
            best, bs = _clean_title(c), sc
    return best


def _fallback_records(lines, system, kind=None):
    anchors = _find_ids(lines, kind)[:_MAX_ITEMS]
    if not anchors:
        return []
    n = len(anchors)
    idx = [a[0] for a in anchors]

    def region(a, b):
        return [lines[j] for j in range(max(a, 0), min(b, len(lines)))]

    head = region(idx[0] - 8, idx[0])
    tail = region(idx[-1] + 1, idx[-1] + 9)
    same_line = sum(1 for i in idx if any(_cell_status(c) for c in _cells(lines[i])[1:])) >= max(1, n // 2 + n % 2)
    st_before = _first_status(head, reverse=True) is not None
    st_after = _first_status(tail) is not None
    status_mode = "same" if same_line else ("before" if st_before and not st_after else "after")
    ti_on_line = sum(1 for i in idx if _best_title(_cells(lines[i]))) >= max(1, n // 2 + n % 2)
    ti_before = bool(_best_title([c for ln in head[-3:] for c in _cells(ln)]))
    ti_after = bool(_best_title([c for ln in tail[:4] for c in _cells(ln)]))
    title_mode = "same" if ti_on_line else ("before" if ti_before and not ti_after else "after")
    recs = []
    for k, (li, mid) in enumerate(anchors):
        prev_end = idx[k - 1] + 1 if k else max(0, li - 8)
        next_start = idx[k + 1] if k + 1 < n else min(len(lines), li + 12)
        before = region(prev_end, li)
        after = region(li + 1, next_start)
        line_cells = _cells(lines[li])
        id_cell = next((j for j, c in enumerate(line_cells) if mid in c), 0)
        st = None
        if status_mode == "same":
            st = next((s for c in reversed(line_cells[id_cell + 1:]) for s in [_cell_status(c)] if s), None) or \
                next((s for c in line_cells[:id_cell] for s in [_cell_status(c)] if s), None)
        if not st:
            st = _first_status(before, reverse=True) if status_mode == "before" else _first_status(after)
        if not st:
            st = _first_status([lines[li]]) or _first_status(after[:6]) or _first_status(before[-4:], reverse=True)
        if title_mode == "same":
            title = _best_title(line_cells[id_cell + 1:] + line_cells[:id_cell])
        elif title_mode == "before":
            title = _best_title([c for ln in before[-4:] for c in _cells(ln)])
        else:
            title = _best_title([c for ln in after[:6] for c in _cells(ln)])
        if not title:
            title = _best_title(line_cells + [c for ln in (after[:4] + before[-3:]) for c in _cells(ln)])
        cont = []
        for ln in after[:3]:
            if "\t" not in ln:
                break
            cont.append(ln)
        row_text = "\n".join([lines[li]] + cont)
        own = "\n".join([row_text] + (before if status_mode == "before" else after[len(cont):8]))
        stage, raw, pdate = st or ("", "", "")
        sub, sd = _pick_dates(row_text if _plain_dates(row_text) else own, system, pdate)
        journal = ""
        for ln in [lines[li]] + after[:6] + before[-4:]:
            m = _LABEL_JOURNAL_RE.match(ln.strip())
            if m:
                journal = m.group(1).strip()
                break
        recs.append({"mid": mid, "title": title, "raw": raw, "stage": stage, "sd": sd, "sub": sub,
                     "journal": journal, "span": (li, li)})
    return recs


def _pick_dates(context, system, decision_date=""):
    """（兜底用）一段文本里的日期 → (投稿日期, 状态日期)：有标签按标签，没有按各系统的列顺序；截止日期不算。
    decision_date：状态格里「Major Revision (12-Nov-2025)」括号中的决定日期。"""
    sub, stat, ds = "", "", []
    for ln in (context or "").split("\n"):
        if _is_due_line(ln):
            continue
        for pos, d in _dates_in(ln):
            lead = ln[max(0, pos - 40):pos].split("\t")[-1]
            if _DUE_LEAD_RE.search(lead):
                continue
            ds.append(d)
            if re.search(r"(?:initial\s+date\s+submitted|date\s+submitted|submission\s+date|submitted(?:\s+on)?|"
                         r"date\s+received|received|提交日期|投稿日期|收稿日期|投稿时间|提交时间)\s*[:：]?\s*$", lead, re.I):
                sub = sub or d
            elif re.search(r"(?:status\s+date|last\s+updated?|updated(?:\s+on)?|last\s+modified|decision\s+date|"
                           r"状态日期|更新时间|更新日期|处理日期|处理时间|最后更新)\s*[:：]?\s*$", lead, re.I):
                stat = stat or d
    stat = decision_date or stat
    ds = [d for d in ds if d != decision_date] or ds
    if not ds:
        return "", stat
    if system == "scholarone":
        sub = sub or min(ds)        # 没有表头时分不清 Created / Submitted / Decisioned：取行里最早的
    elif system == "editorial_manager":
        if len(ds) >= 2:
            sub, stat = sub or ds[0], stat or ds[1]
        else:
            stat = stat or ds[0]
    else:
        if not sub and len(ds) >= 2:
            sub = min(ds)
        if not stat and len(ds) >= 2:
            stat = max(ds)
        elif not stat and not sub:
            stat = ds[0]
    return sub, stat


# ---------------- 主流程 ----------------
def _page_ids(lines, kind) -> set:
    """页面正文里出现的不同稿件编号（不算引用行）。"""
    out = set()
    for i, ln in enumerate(lines):
        ids = _ids_in(ln) if kind[i] not in ("table", "ref") else ()
        if ids and not _REF_LINE_RE.search(ln):
            out.update(ms_base(v) for _pos, v in ids)
    return out


def _collapse(recs):
    """同一稿件的多个版本（XXX、XXX.R1、XXX.R2）只留最新一版（缺的标题 / 期刊从旧版补）；同一编号只留第一次出现。"""
    best = {}
    for k, r in enumerate(recs):
        if not r["mid"]:
            continue
        b = ms_base(r["mid"])
        if b not in best or _rev_no(r["mid"]) > _rev_no(recs[best[b]]["mid"]):
            best[b] = k
    for k, r in enumerate(recs):
        if r["mid"]:
            keep = recs[best[ms_base(r["mid"])]]
            for f in ("title", "journal"):
                keep[f] = keep.get(f) or r.get(f) or ""
            if r.get("sub") and (not keep.get("sub") or r["sub"] < keep["sub"]):
                keep["sub"] = r["sub"]          # 投稿日期取各版本里最早的（原稿那一版）
    keep = set(best.values())
    return [r for k, r in enumerate(recs) if not r["mid"] or k in keep]


def _gaps(n, recs, tables, min_len=4):
    """没被列表行 / 卡片占用的连续行段 [(起, 止)]。"""
    covered = [False] * n
    for r in recs:
        for x in range(r["span"][0], min(n, r["span"][1] + 1)):
            covered[x] = True
    for tb in tables:
        if tb.rows and ({"id", "title"} & set(tb.roles)):
            for x in range(tb.hdr, min(n, tb.end)):
                covered[x] = True
    out, x = [], 0
    while x < n:
        if covered[x]:
            x += 1
            continue
        y = x
        while y < n and not covered[y]:
            y += 1
        if y - x >= min_len:
            out.append((x, y))
        x = y
    return out


def _records(lines, system):
    tables = _find_tables(lines)
    recs, page_status = _table_records(tables, lines, system)
    skip = set()
    for tb in tables:
        if tb.rows:
            skip.update(range(tb.hdr, tb.end))
    kind, fields = _classify(lines, skip)
    for i in _email_lines(lines, kind):         # ScholarOne「最近 5 封邮件」里的标题提到编号，但不是稿件列表
        kind[i] = "ref"
    ev = _timeline(lines, kind, tables)
    bare = [_bare_status(lines[i]) if kind[i] == "text" else None for i in range(len(lines))]
    recs += _card_records(lines, kind, fields, bare)
    if not recs:
        # 单篇稿件页面；认不出版式、但列着好几篇稿件的，交给最后「按编号切分」的兜底
        if len(_page_ids(lines, kind)) < 2:
            r = _single_record(lines, kind, fields, tables, page_status, ev, bare)
            recs = [r] if r else []
    else:
        # 列表 / 卡片之后还接着粘了单篇稿件页（EM History、Research Square、Frontiers 审稿论坛…）
        known = set(ms_base(r["mid"]) for r in recs if r["mid"])
        for lo, hi in (_gaps(len(lines), recs, tables)[:20] if len(recs) < _MAX_ITEMS else []):
            r = _single_record(lines, kind, fields, tables, page_status, ev, bare, lo, hi, strict=True)
            if r and ms_base(r["mid"]) not in known:
                recs.append(r)
    if len(recs) == 1 and ev:          # 单篇：处理记录里补状态日期、投稿日期（T&F 展开的时间线、勤云处理过程…）
        r = recs[0]
        e = _latest(ev, r["stage"]) if r["stage"] else None
        if e and not r["sd"]:
            r["sd"] = e[0]
        if not r["sub"]:
            subs = sorted(e[0] for e in ev if _SUBMIT_EVENT_RE.match(_sk(e[2])))
            r["sub"] = subs[0] if subs else ""
    if not recs:
        recs = _fallback_records(lines, system, kind)
    recs.sort(key=lambda r: r.get("span", (0, 0))[0])
    return _collapse(recs)[:_MAX_ITEMS]


_EMAIL_HEAD_RE = re.compile(r"most\s+recent\s+e-?mails", re.I)


def _email_lines(lines, kind):
    """「5 Most Recent E-mails」/「…your five most recent e-mails.」下面紧跟着的几行（到空行、文件夹标题或表格为止）。"""
    out = []
    for i, ln in enumerate(lines):
        if len(ln) < 200 and "mail" in ln.lower() and _EMAIL_HEAD_RE.search(ln):
            j = i + 1
            while j < len(lines) and j - i <= 12 and lines[j].strip() and kind[j] != "table" \
                    and not _folder_of(lines[j].strip()):
                out.append(j)
                j += 1
    return out


_MAX_LINE = 5000        # 再长的一行只看前面这些字（正文行不会这么长；防止长行把正则拖慢）
_MAX_ITEMS = 500        # 最多认这么多篇（真实的作者中心远少于此；防止超大粘贴卡住服务）
_MAX_LOOK = 5000        # 最多看这么多行表格 / 卡片（重复粘贴的同一篇不算新稿件，但也不能无限看下去）


def parse(text: str) -> dict:
    t = _prep(text)
    system = detect_system(t)
    # 只去尾部空格：尾部的 TAB 是空格子，要留着
    lines = [ln[:_MAX_LINE].rstrip(" ") for ln in t.split("\n")]
    token = _SLASH.set(_slash_mode(t, system))
    try:
        recs = _records(lines, system)
    finally:
        _SLASH.reset(token)
    if not system:
        for r in recs:
            sysk = next((k for k, rx in _ID_RES if r["mid"] and rx.fullmatch(r["mid"])), "")
            if sysk in SYSTEMS:
                system = sysk
                break
    label = SYSTEMS[system][0] if system in SYSTEMS else ""
    first = min([r.get("span", (60, 60))[0] for r in recs] + [60])
    journal = _guess_journal(lines[:first], system)
    today = _today()
    items = []
    for r in recs:
        title = (r.get("title") or "").strip()
        sub = r.get("sub") or ""
        sd = r.get("sd") or ""
        items.append({"manuscript_id": r.get("mid") or "", "title": title,
                      "title_truncated": title.endswith(("...", "…")),
                      "raw_status": (r.get("raw") or "")[:200], "stage": r.get("stage") or "",
                      # 晚于今天的日期一定是认错了（截止日期之类），不要
                      "status_date": sd if sd <= today else "", "submitted_date": sub if sub <= today else "",
                      "journal": r.get("journal") or ""})
    return {"system": system, "system_label": label, "journal": journal, "items": items}


_MULTI_JOURNAL = {"mdpi", "rex", "snapp", "frontiers", "tandf", "researchsquare"}
_LANG_LINKS = {"english", "english version", "中文", "中文版", "chinese", "简体中文", "繁體中文", "english (us)"}


def _guess_journal(head: list[str], system: str) -> str:
    """页面顶部的期刊名（ScholarOne / EM 每个期刊一个站点，页头就是刊名）：只认分级库里有的刊名，宁缺毋滥。
    MDPI、ReX、Snapp、Frontiers、T&F 这类一个账号管多本刊的页面，刊名在每篇稿件上，页头的不算。"""
    if system in _MULTI_JOURNAL:
        return ""
    try:
        from . import rankings_build
        from . import rankings as rk
        if not rk.available():
            return ""
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(rk.DB_PATH)) as c:
            for ln in head[:60]:
                for cell in _cells(ln):
                    # 「English」「English Version」是中文站点的语言切换链接，不是刊名
                    if 6 <= len(cell) <= 120 and not _is_noise(cell) and _nkey(cell) not in _LANG_LINKS:
                        r = c.execute("SELECT journal FROM journals WHERE norm_key=?",
                                      (rankings_build.norm(cell),)).fetchone()
                        if r:
                            return cell
    except Exception:
        return ""
    return ""
