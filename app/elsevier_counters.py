# -*- coding: utf-8 -*-
"""爱思唯尔查稿页审稿人计数解析（评审完成 / 接受评审邀请 / 发出评审邀请）。

官方查稿页（webapps.elsevier.cn/st-wechat）对「自上次以来有变化」的计数，用
e-transition 组件渲染为：

    <span>旧值</span><svg 箭头图标/><span>新值</span>

箭头是没有文字的图标，复制出来的文本就成了 "02"（0→2）、"23"（2→3）。
浏览器不同，也可能复制成 "0 2"、"0\\n2" 或 "0→2"。
人数超过阈值时官方隐藏具体人数，显示为 "5+" 后跟 "**"，
页脚有一行 "**超过5人则不显示具体人数…"（阈值就是隐藏时显示的那个数）。

本模块只负责这三个计数；状态、日期、稿号等仍由 server._parse_official_paste 处理。
"""
from __future__ import annotations

import itertools
import re
import unicodedata

FIELDS = ('completed', 'accepted', 'invited')
FIELD_ZH = {'completed': '评审完成', 'accepted': '接受评审邀请', 'invited': '发出评审邀请'}

# 标签可以出现在行首，也可以出现在单行粘贴（换行丢失）的中间
_LABELS = {
    'completed': r'(?<!同行)评审完成|(?<!required )reviews?\s+completed|completed\s+reviews?',
    'accepted': r'接受评审邀请|reviewers?\s+accepted|review(?:er)?\s+invitations?\s+accepted|'
                r'accepted\s+invitations?|invitations?\s+accepted',
    'invited': r'发出评审邀请|reviewers?\s+invited|review(?:er)?\s+invitations?\s+sent|'
               r'invitations?\s+sent',
}
_LABEL_RE = {f: re.compile(r'(?:' + p + r')[ \t]*\*{0,2}[ \t]*:?', re.I) for f, p in _LABELS.items()}

_ARROWS = '→⟶➜➔➝➞►▶⇒⇨>'
_VCHARS = r'0-9+*\s' + re.escape(_ARROWS)
_VALUE_PREFIX = re.compile(r'[' + _VCHARS.replace(r'\s', r' \t') + r']*')
_VALUE_LINE = re.compile(r'[' + _VCHARS + r']*')
_TOKEN = re.compile(r'\+?\d+\+?')
_HIDDEN = re.compile(r'\d\+?[ \t]*\*\*')
_MASK = re.compile(r'超过\s*(\d+)\s*人则不显示具体人数')
# 数字后面紧跟月份/日期成分 → 这是事件日期（如 Research Square 时间线「Reviewers accepted 15 Oct, 2026」），不是人数
_DATE_TAIL = re.compile(r'(?:[年月日号]|[-/.,]\s*\d|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b)', re.I)

AMBIGUITY_MARGIN = 1.0


def _normalize(text: str) -> str:
    t = unicodedata.normalize('NFKC', text or '')          # 全角冒号/数字/星号 → 半角，NBSP → 空格
    t = re.sub(r'[​-‏⁠﻿]', '', t)       # 零宽字符
    t = re.sub(r'-+>|=+>', '→', t)                          # 0->2 / 0=>2
    return t.replace('\r\n', '\n').replace('\r', '\n')


def _segment(t: str, field: str) -> str | None:
    """标签后的数值文本：同一行紧跟的数值；若该行余下全是数值，再接上后续的纯数值行。"""
    for m in _LABEL_RE[field].finditer(t):
        start = m.end()
        line_end = t.find('\n', start)
        if line_end < 0:
            line_end = len(t)
        rest = t[start:line_end]
        val = _VALUE_PREFIX.match(rest).group(0)
        if _DATE_TAIL.match(rest[len(val):]):
            continue
        parts = [val]
        if val == rest:                                      # 值在下一行（微信端两行结构）
            for nxt in t[line_end + 1:].split('\n'):
                if _VALUE_LINE.fullmatch(nxt) is None:
                    break                                    # 脚注、下一个标签、日期行都会让收集停下
                parts.append(nxt)
        seg = '\n'.join(parts)
        if _TOKEN.search(seg):
            return seg
    return None


def _candidates(seg: str) -> list[dict]:
    """一个计数可能的读法：plain（未变化）/ split（粘连的旧值新值）/ explicit（有分隔的旧值新值）。"""
    hidden = bool(_HIDDEN.search(seg))
    toks = _TOKEN.findall(seg.replace('*', ' '))
    if not toks:
        return []

    def num(tok: str) -> tuple[int, bool]:
        return int(tok.strip('+')), '+' in tok

    if len(toks) >= 2:
        old, _ = num(toks[0])
        new, plus = num(toks[-1])
        return [dict(kind='explicit', old=old, new=new, at_least=plus or hidden)]

    tok = toks[0]
    plus = '+' in tok or hidden
    digits = tok.strip('+')
    out = []
    if len(digits) == 1 or digits[0] != '0':
        out.append(dict(kind='plain', old=None, new=int(digits), at_least=plus))
    for k in range(1, len(digits)):
        a, b = digits[:k], digits[k:]
        if (len(a) > 1 and a[0] == '0') or (len(b) > 1 and b[0] == '0'):
            continue                                         # 计数不会带前导零
        if int(a) == int(b):
            continue                                         # 官方只在数值变化时才显示旧值
        out.append(dict(kind='split', old=int(a), new=int(b), at_least=plus))
    return out


def _penalty(field: str, c: dict, prev, mask: int | None) -> float:
    prev = prev if isinstance(prev, int) else None
    p = 0.0
    new = c['new']
    if c['kind'] == 'plain':
        if not (c['at_least'] and new == mask):
            if new >= 30:
                p += 5
            elif new >= 10:
                p += 1 if field == 'invited' else 3          # 一篇稿 10 位以上接受/完成几乎不可能
        if prev is not None and new == prev:
            p -= 2                                           # 与上次记录相同，未变化
    else:
        if new < c['old']:
            p += 2 if field == 'completed' else 1            # 计数通常只增不减
        if prev is not None and c['old'] == prev:
            p -= 2                                           # 官方旧值 = 我们上次记录的值
        elif prev is not None and new == prev:
            p -= 1.5                                         # 同一页面重复粘贴
        p += 0.1 * abs(new - c['old'])
    if mask is not None:
        if not c['at_least'] and new > mask:
            p += 5                                           # 超过阈值官方会隐藏具体人数
        if c['at_least'] and new != mask:
            p += 5                                           # 隐藏时显示的就是阈值本身，如「5+」
    if prev is not None and new < prev and field in ('completed', 'invited'):
        p += 2
    return p


def _order_violations(news: dict) -> int:
    c, a, i = news.get('completed'), news.get('accepted'), news.get('invited')
    pairs = ((c, a), (a, i), (c, i))
    return sum(1 for x, y in pairs if x is not None and y is not None and x > y)


def _fmt(c: dict) -> str:
    n = f"{c['new']}+" if c['at_least'] else str(c['new'])
    return n if c['old'] is None else f"{c['old']}→{n}"


def parse_reviewer_counters(text: str, previous: dict | None = None) -> dict:
    """解析三个审稿人计数。

    previous: 该稿上次记录的计数（如 {'completed': 0, 'accepted': 2, 'invited': 5}），
              用来判断粘连数字怎么拆，可为 None。
    返回 {} 表示文本里没有这三个计数；否则返回：
        reviewers            {'completed': 2, 'accepted': 3, 'invited': 8}  新值（结构与旧版相同）
        reviewers_prev       {'completed': 0, 'accepted': 2}                官方显示的旧值（仅有变化的项）
        reviewers_at_least   ['invited']                                    「5+」这种只知下限的项
        reviewers_ambiguous  bool                                           存在另一种接近的读法
        reviewers_note       str                                            给人看的解释，可能为空
    """
    t = _normalize(text)
    m = _MASK.search(t)
    mask = int(m.group(1)) if m else None
    previous = previous if isinstance(previous, dict) else {}

    cands = {}
    for f in FIELDS:
        seg = _segment(t, f)
        if seg is not None:
            cs = _candidates(seg)
            if cs:
                cands[f] = cs
    if not cands:
        return {}

    fields = list(cands)
    scored = []
    for combo in itertools.product(*(cands[f] for f in fields)):
        pick = dict(zip(fields, combo))
        viol = _order_violations({f: c['new'] for f, c in pick.items()})
        score = sum(_penalty(f, c, previous.get(f), mask) for f, c in pick.items()) + 10 * viol
        scored.append((score, viol, pick))
    scored.sort(key=lambda x: x[0])
    best_score, best_viol, best = scored[0]

    notes = []
    changed = [f for f in fields if best[f]['old'] is not None]
    if changed:
        notes.append('官方页对有变化的计数显示「旧值→新值」：'
                     + '、'.join(f'{FIELD_ZH[f]} {_fmt(best[f])}' for f in changed))

    ambiguous = False
    best_news = {f: best[f]['new'] for f in fields}
    for score, _, pick in scored[1:]:
        if {f: pick[f]['new'] for f in fields} == best_news:
            continue
        if score - best_score < AMBIGUITY_MARGIN:
            ambiguous = True
            diff = [f for f in fields if pick[f]['new'] != best[f]['new'] or pick[f]['old'] != best[f]['old']]
            notes.append('也可能是：' + '、'.join(f'{FIELD_ZH[f]} {_fmt(pick[f])}' for f in diff)
                         + '，请对照官方页核对')
        break
    if best_viol:
        notes.append('计数不满足「完成 ≤ 接受 ≤ 邀请」，请对照官方页核对')

    return {
        'reviewers': {f: best[f]['new'] for f in FIELDS if f in best},
        'reviewers_prev': {f: best[f]['old'] for f in FIELDS if f in best and best[f]['old'] is not None},
        'reviewers_at_least': [f for f in FIELDS if f in best and best[f]['at_least']],
        'reviewers_ambiguous': ambiguous,
        'reviewers_note': '；'.join(notes),
    }
