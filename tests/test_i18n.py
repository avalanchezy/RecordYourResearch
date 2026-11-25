# -*- coding: utf-8 -*-
"""界面英文版（一键切换）的完整性检查：每个 $t() / tt() 键都有英文，占位符一致，模板里不留裸中文。"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAN = re.compile(r"[一-鿿]")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _en():
    src = _read("web/i18n.en.js")
    body = src[src.index("window.RYR_I18N_EN = ") + len("window.RYR_I18N_EN = "):src.rindex(";")]
    return json.loads(body)


def _literals(text, fn):
    out = []
    for m in re.finditer(re.escape(fn) + r"\('((?:[^'\\]|\\.)*)'", text):
        out.append(re.sub(r"\\(.)", lambda x: {"n": "\n"}.get(x.group(1), x.group(1)), m.group(1)))
    return out


def _template():
    html = _read("web/index.html")
    return html[html.index('<div id="app">'):html.index('<script src="vendor/vue.global.prod.js">')]


def _strip_t_calls(expr):
    """去掉表达式里所有 $t(...) 调用（括号配对，跳过引号里的内容）。"""
    out, i = [], 0
    while i < len(expr):
        if expr.startswith("$t(", i):
            depth, j, q = 0, i + 2, None
            while j < len(expr):
                c = expr[j]
                if q:
                    if c == "\\":
                        j += 1
                    elif c == q:
                        q = None
                elif c in "'\"`":
                    q = c
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1
            continue
        out.append(expr[i])
        i += 1
    return "".join(out)


def _text_nodes_and_attrs(tpl):
    """按标签切分模板（跳过属性值里的 > 和插值），返回 (文本节点列表, [(属性名, 值)])。"""
    texts, attrs, i = [], [], 0
    while i < len(tpl):
        if tpl[i] == "<":
            j, q = i + 1, None
            while j < len(tpl):
                c = tpl[j]
                if q:
                    if c == q:
                        q = None
                elif c in "\"'":
                    q = c
                elif c == ">":
                    break
                j += 1
            attrs += re.findall(r'\s([:@#]?[\w\-.:\[\]]+)="([^"]*)"', tpl[i:j + 1])
            i = j + 1
            continue
        j = i
        while j < len(tpl) and tpl[j] != "<":
            j = tpl.index("}}", j) + 2 if tpl.startswith("{{", j) else j + 1
        texts.append(tpl[i:j])
        i = j
    return texts, attrs


def test_dictionary_is_valid_json_with_patterns():
    en = _en()
    assert len(en["dict"]) > 500
    for rx, rep in en["patterns"]:
        re.compile(rx)                        # JS 与 Python 正则在这里用到的子集上一致
        assert rx.startswith("^") and rx.endswith("$")


def test_every_ui_key_has_english():
    en = _en()["dict"]
    keys = [k for k in _literals(_template(), "$t") + _literals(_read("web/app.js"), "tt") if HAN.search(k)]
    missing = sorted({k for k in keys if k not in en})
    assert not missing, f"缺少英文的界面文字（请加到 web/i18n.en.js）：{missing[:20]}"


def test_placeholders_match():
    for zh, en in _en()["dict"].items():
        assert sorted(re.findall(r"\{\d+\}", zh)) == sorted(re.findall(r"\{\d+\}", en)), zh


def test_no_bare_chinese_left_in_template():
    """模板里的中文都要经过 $t()：文本节点和 title / placeholder / aria-label 属性。"""
    tpl = re.sub(r"<!--.*?-->", "", _template(), flags=re.S)
    texts, attrs = _text_nodes_and_attrs(tpl)
    bad = [t.strip()[:40] for t in texts if HAN.search(re.sub(r"\{\{.*?\}\}", "", t, flags=re.S))]
    bad += [v[:40] for n, v in attrs if n in ("title", "placeholder", "aria-label", "alt") and HAN.search(v)]
    assert not bad, f"模板里有没有翻译的中文：{bad[:10]}"


def test_chinese_inside_interpolations_goes_through_t():
    """插值和绑定表达式里的中文字面量必须在 $t() 里（语言切换按钮的「切换到中文」「中文」除外）。"""
    tpl = re.sub(r"<!--.*?-->", "", _template(), flags=re.S)
    texts, attrs = _text_nodes_and_attrs(tpl)
    exprs = [m for t in texts for m in re.findall(r"\{\{(.*?)\}\}", t, flags=re.S)]
    exprs += [v for n, v in attrs if n.startswith(":")]
    bad = []
    for e in exprs:
        stripped = _strip_t_calls(e).replace("'切换到中文'", "").replace("'中文'", "")
        if HAN.search(stripped):
            bad.append(e.strip()[:60])
    assert not bad, bad[:10]


def test_stage_labels_translated():
    from app import stages
    en = _en()["dict"]
    for st in stages.STAGES.values() if isinstance(stages.STAGES, dict) else stages.STAGES:
        label = st.get("label") if isinstance(st, dict) else st[1]
        assert label in en, label


def test_option_values_stay_chinese():
    """下拉选项的显示文字翻成英文，但提交给后端的值必须是原来的中文（数据库里存的就是中文）。"""
    for m in re.finditer(r"<option([^>]*)>\{\{ \$t\('([^']+)'\) \}\}</option>", _template()):
        attrs, text = m.group(1), m.group(2)
        assert 'value="' in attrs, f"选项「{text}」缺少 value，英文界面下会把英文提交给后端"
