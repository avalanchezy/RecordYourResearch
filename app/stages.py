# -*- coding: utf-8 -*-
"""状态归一词典：把各渠道原始状态名统一到一套阶段。"""
import re

STAGES = {
    "submitted":    {"label": "已提交", "label_en": "Submitted", "color": "#64748b", "order": 1,
                      "hint": "系统技术检查阶段（常见 1–3 天）"},
    "editor":       {"label": "编辑处理", "label_en": "With editor", "color": "#0369a1", "order": 2,
                      "hint": "编辑分配与初筛；超 3–4 周提示可礼貌催稿"},
    "review":       {"label": "外审中", "label_en": "Under review", "color": "#2563eb", "order": 3,
                      "hint": "审稿人邀请/审阅中，周期数周至数月"},
    "reviews_done": {"label": "审稿齐", "label_en": "Reviews complete", "color": "#7c3aed", "order": 4,
                      "hint": "意见已回收齐，等待编辑决策"},
    "decision":     {"label": "决策中", "label_en": "Decision pending", "color": "#b45309", "order": 5,
                      "hint": "编辑形成决定，通常数天内出结果"},
    "revise":       {"label": "返修", "label_en": "Revision", "color": "#c2410c", "order": 6,
                      "hint": "系统抓取返修截止日，自动倒计时提醒"},
    "accepted":     {"label": "已录用", "label_en": "Accepted", "color": "#15803d", "order": 7,
                      "hint": "进入版权与校样流程"},
    "rejected":     {"label": "已拒稿", "label_en": "Rejected", "color": "#dc2626", "order": 8,
                      "hint": "触发「改投」流程：建议下一个候选期刊"},
    "withdrawn":    {"label": "已撤稿", "label_en": "Withdrawn", "color": "#78716c", "order": 9,
                      "hint": "作者主动撤回投稿"},
    "production":   {"label": "制作中", "label_en": "In production", "color": "#0f766e", "order": 10,
                      "hint": "排版校样直至见刊"},
}

ACTIVE_STAGES = ["submitted", "editor", "review", "reviews_done", "decision", "revise"]
TERMINAL_STAGES = ["accepted", "production", "rejected", "withdrawn"]

# 首页分类口径（前后端共用）
CATEGORY_STAGES = {
    "invest": ACTIVE_STAGES,
    "accepted": ["accepted", "production"],
    "rejected": ["rejected"],
    "withdrawn": ["withdrawn"],
}

# 原始状态名 → 归一阶段（Editorial Manager / ScholarOne / 爱思唯尔 Track your submission 为基线 + OpenReview）
# 键为 status_key() 归一后的写法：小写、破折号统一为「 - 」、空白折叠、去首尾标点
RAW_STATUS_MAP = {
    # ---- 提交 / 技术检查 / 退回作者 ----
    "submitted to journal": "submitted",
    "submission received": "submitted",
    "submitted": "submitted",
    "technical check in progress": "submitted",
    "technical check": "submitted",
    "incomplete": "submitted",
    "incomplete submission": "submitted",
    "sent back to author": "submitted",
    "sent back to authors": "submitted",
    "needs approval": "submitted",
    "needs approval by author": "submitted",
    # ---- 编辑处理 ----
    "with editor": "editor",
    "with journal": "editor",
    "editor assigned": "editor",
    "editor invited": "editor",
    "editor declined invitation": "editor",
    "with journal administrator": "editor",
    "under editor evaluation": "editor",
    "awaiting editor assignment": "editor",
    "awaiting ae assignment": "editor",
    "awaiting reviewer selection": "editor",
    "awaiting reviewer assignment": "editor",
    "awaiting reviewer invitation": "editor",
    "no reviewers invited": "editor",
    # 返修稿已提交：回到编辑处理（返修倒计时结束）
    "required revision submitted": "editor",
    "revision submitted to journal": "editor",
    "revised version submitted to journal": "editor",
    "revision submitted": "editor",
    "revision received": "editor",
    "revision being processed": "editor",
    # ---- 外审 ----
    "reviewer invited": "review",
    "reviewers invited": "review",
    "reviewer invited, reviewer invited": "review",
    "under review": "review",
    "reviewers assigned": "review",
    "awaiting reviewer scores": "review",
    "awaiting reviewer response": "review",
    "required reviews incomplete": "review",
    # ---- 审稿齐 ----
    "required reviews complete": "reviews_done",
    "required reviews completed": "reviews_done",
    "reviews complete": "reviews_done",
    "reviews completed": "reviews_done",
    "review complete": "reviews_done",
    "review completed": "reviews_done",
    "awaiting ae recommendation": "reviews_done",      # 审稿意见已齐，AE 撰写推荐意见
    # ---- 决策 ----
    "complete": "decision",                            # 爱思唯尔官方「Complete」= 审稿完成（待出决定）
    "completed": "decision",
    "decision in process": "decision",
    "decision pending": "decision",
    "awaiting decision": "decision",
    "awaiting editor decision": "decision",
    "evaluating recommendation": "decision",
    "editor decision started": "decision",
    "decision letter being prepared": "decision",
    # ---- 返修 ----
    "revise": "revise",
    "revise - major": "revise",
    "revise - minor": "revise",
    "major revision": "revise",
    "minor revision": "revise",
    "revision requested": "revise",
    "revision required": "revise",
    # 条件录用（规则 ④ 也认；列成精确键是为了粘贴解析「整行即状态」的兜底也能认出）
    "accept with minor revision": "revise",
    "accept with minor revisions": "revise",
    "accept with major revision": "revise",
    "accept with major revisions": "revise",
    "accept subject to minor revision": "revise",
    "accept subject to minor revisions": "revise",
    "accept with revisions": "revise",
    "accept after major revision": "revise",
    # ---- 录用 / 制作 ----
    "accept": "accepted",
    "accepted": "accepted",
    "provisional accept": "accepted",
    "completed - accept": "accepted",
    "final decision accept": "accepted",
    "article in press": "production",
    "in production": "production",
    "in press": "production",
    "published": "production",
    "proof sent": "production",
    "corrected proof": "production",
    "completed - accept (production)": "production",
    "accepted - in production": "production",
    # ---- 拒稿 / 撤稿 ----
    "reject": "rejected",
    "rejected": "rejected",
    "not accepted": "rejected",
    "not accepted for publication": "rejected",
    "declined": "rejected",
    "declined by editor": "rejected",
    "completed - reject": "rejected",
    "final decision reject": "rejected",
    "reject & resubmit": "rejected",
    "reject and resubmit": "rejected",
    "desk reject": "rejected",
    "withdrawn": "withdrawn",
    "withdraw": "withdrawn",
    "withdrawn submission": "withdrawn",
    "transferred": "withdrawn",
    "removed": "withdrawn",
    "retracted": "withdrawn",
    # ---- OpenReview 特征串 ----
    "active": "review",
    "desk rejected submission": "rejected",
    "desk reject submission": "rejected",
    "rejected submission": "rejected",
    "accepted submission": "accepted",
    # ---- 中文状态名（爱思唯尔官方查稿页通栏 / 本系统阶段名；与粘贴解析口径一致）----
    "已提交": "submitted", "初审阶段": "submitted",
    "编辑初审": "editor", "编辑处理中": "editor", "编辑处理": "editor",
    "同行评审进行中": "review", "正在评审": "review", "外审中": "review",
    "所需审稿意见完成": "reviews_done", "同行评审完成": "reviews_done", "审稿齐": "reviews_done",
    "审稿完成": "decision", "决策中": "decision",
    "返修": "revise", "修改中": "revise",
    "已录用": "accepted", "已接受": "accepted",
    "已拒稿": "rejected", "已退稿": "rejected",
    "已撤稿": "withdrawn", "制作中": "production",
}

# 兜底规则：(正则, 阶段)，按顺序取第一个命中——否定、条件式与更具体的短语必须排在泛化词之前
# （如 Not accepted 不能落到 accept，Accept with Minor Revision 不能落到 accepted，
#   No Reviewers Invited 不能落到 reviewers invited）
_RULES = tuple((re.compile(p), st) for p, st in (
    # ① 否定与终态：Not accepted / Desk rejected by TMLR / Withdrawn by Authors / Retracted by TMLR
    (r"\bnot\s+accept", "rejected"),
    (r"\bdesk[\s-]*reject", "rejected"),
    (r"\bwithdr[ae]w|\bretract", "withdrawn"),
    (r"\breject", "rejected"),
    (r"^declined?\b|\bdeclined\s+by\b", "rejected"),
    (r"\btransferred\b|^removed\b", "withdrawn"),
    # ② 制作 / 见刊（先于 accept：Completed - Accept (Production)、Accepted - In Production）
    (r"\bproduction\b|\bin\s+press\b|\bpublished\b|\bproofs?\b", "production"),
    # ③ 返修稿已交回期刊：回到编辑处理（先于 revise / submitted；含 Revised Manuscript Resubmitted）
    (r"\brevis\w*\b.*\b(?:re(?:\s-\s)?)?(?:submitted|received|being\s+processed)\b", "editor"),
    # ④ 条件录用 = 返修（Accept with Minor Revision(s) / Subject to … / after major revision；
    #   分隔符写法 Accept - Minor Revision / Accept (Minor Revisions) / Accepted: Major Revision 同理）
    (r"\baccept\w*\W+(?:with(?!\s+no\b)|subject\s+to|after|pending|upon)\b.*\brevis"
     r"|\baccept\w*[\s(:,-]+(?:minor|major)\s+revis", "revise"),
    # ⑤ 审稿齐
    (r"\breviews?\s+complete", "reviews_done"),
    (r"\bawaiting\s+(?:(?:ae|associate\s+editor|handling\s+editor|editor)\s+)?recommendation",
     "reviews_done"),
    # ⑥ 编辑处理（No Reviewers Invited / Editor Declined Invitation / 等待分配编辑或审稿人）
    (r"\bno\s+reviewers?\s+invited|\beditor\s+declined"
     r"|\bawaiting\s+reviewer\s+(?:selection|invitation|assignment)"
     r"|\bawaiting\s+(?:editor|ae|associate\s+editor|handling\s+editor|eic)\s+assignment"
     r"|\bwith\s+(?:the\s+)?editor|\beditors?(?:\s?\(s\))?\s+(?:assigned|invited)|\bunder\s+editor",
     "editor"),
    # ⑦ 外审（含 EM 的「Reviewer(s) Invited」写法）
    (r"\bunder\s+review\b|\breviewers?(?:\s?\(s\))?\s+(?:invited|assigned|accepted|declined)"
     r"|\bawaiting\s+reviewer\s+(?:scores?|responses?|reports?|comments?)"
     r"|\breviews?\s+incomplete", "review"),
    # ⑧ 决策
    (r"\bdecision\s+(?:in\s+process|in\s+progress|pending|started)"
     r"|\bawaiting\s+(?:\w+\s+){0,2}decision|\bevaluating\s+recommendation|\bdecision\s+letter",
     "decision"),
    # ⑨ 录用（Accepted to ICLR 2026 / Accept (Poster) / Accepted by TMLR）
    (r"\baccept", "accepted"),
    # ⑩ OpenReview 录用展示类别：ICLR 2025 Poster / NeurIPS 2024 oral
    #   （「Submitted to …」与以 Submission 结尾的投稿态 venue 串不算，与 openreview.infer_stage 口径一致）
    (r"^(?!.*\bsubmitted\s+to\b)(?!.*\bsubmissions?$).*\b(?:poster|oral|spotlight|highlight|notable)s?\b",
     "accepted"),
    # ⑪ 返修
    (r"\brevis", "revise"),
    # ⑫ 提交 / 技术检查 / 退回作者补全（Resubmitted 同属重新提交）
    (r"\bincomplete\b|\bsent\s+back\s+to\s+authors?\b|\bneeds?\s+(?:\w+\s+)?approval\b"
     r"|\btechnical\s+check|\b(?:re(?:\s-\s)?)?submitted\b", "submitted"),
))

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff\u00ad]")


def status_key(raw) -> str:
    """原始状态文本 → 词典键：小写、去零宽字符、各类破折号统一为「 - 」、空白折叠、去首尾标点。"""
    k = _ZERO_WIDTH.sub("", str(raw or "")).strip().lower()
    k = re.sub(r"[\u2010-\u2015\u2212\uff0d]", "-", k)      # 各类破折号 → -
    k = re.sub(r"\s*-\s*", " - ", k)
    k = re.sub(r"\s+", " ", k)
    return k.strip(" .;:，。：；、*")


_key = status_key      # 兼容旧名


# 用户确认过的状态词规则（数据库 status_rules 表，由 app/smart.py 载入）：优先于内置词典
USER_RULES: dict[str, str] = {}


def normalize(raw: str) -> str | None:
    """把原始状态文本归一到阶段 key；认不出返回 None（由调用方决定兜底，不在这里猜）。"""
    if not raw:
        return None
    key = status_key(raw)
    if not key:
        return None
    if key in USER_RULES:
        return USER_RULES[key]
    if key in RAW_STATUS_MAP:
        return RAW_STATUS_MAP[key]
    # 规则兜底（如 "Submitted to NeurIPS 2026" / "Accepted to ICLR 2026" / "ICLR 2025 Poster"）
    for pat, stage in _RULES:
        if pat.search(key):
            return stage
    return None


def stage_label(stage: str) -> str:
    return STAGES.get(stage, {}).get("label", stage)
