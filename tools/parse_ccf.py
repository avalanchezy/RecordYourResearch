# -*- coding: utf-8 -*-
"""解析 CCF 第七版（2026）正式版 PDF → data-sources/ccf_2026.json（维护者工具）。

用法：python tools/parse_ccf.py 下载好的ccf目录.pdf [--out data-sources/ccf_2026.json]
官方 PDF：https://www.ccf.org.cn/Academic_Evaluation/By_category/ （本项目不附带 PDF 原件）

PDF 结构（实测）：
  封面 → 每领域开始处：
    「中国计算机学会推荐国际学术期刊」/「…会议」   ← 节标题
    「（领域名）」                                  ← 领域（全角括号行）
    「一、A 类」「二、B 类」「三、C 类」             ← 类别
    表格（序号/简称/全称/出版社/网址）
产出 items: {field, type(journal|conference), rank(A|B|C), seq, abbr, name, publisher, url}
"""
import argparse
import json
import re
from pathlib import Path

import pymupdf

_ap = argparse.ArgumentParser(description="解析 CCF 推荐目录 PDF")
_ap.add_argument("pdf", help="官方 PDF 路径")
_ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "data-sources" / "ccf_2026.json"))
_args = _ap.parse_args()
PDF = Path(_args.pdf)
OUT = Path(_args.out)

CN = "一二三四五六七八九十"
RANK_RE = re.compile(rf"^([{CN}])、\s*([ABC])\s*类$")
SECTION_RE = re.compile(r"^中国计算机学会推荐国际学术(期刊|会议)$")
FIELD_RE = re.compile(r"^[(（]([^()（）]{4,40})[)）]$")


def _is_field_content(s: str) -> bool:
    """领域名：纯中文（可含 / 与 、），排除「2026年」等。"""
    return bool(re.fullmatch(r"[\u4e00-\u9fff/、]+", s)) and len(s) >= 4


doc = pymupdf.open(PDF)

# ---------- 收集所有页面：标题事件流 + 表格流 ----------
events = []       # (page, y, kind, value, hint)
table_stream = []  # (page, y, table)
for pno in range(doc.page_count):
    page = doc[pno]
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            text = "".join(s["text"] for s in ln["spans"]).strip()
            if not text:
                continue
            y = ln["bbox"][1]
            m = RANK_RE.match(text)
            if m:
                events.append((pno, y, "rank", m.group(2), None))
                continue
            m = SECTION_RE.match(text)
            if m:
                events.append((pno, y, "section", "", "journal" if m.group(1) == "期刊" else "conference"))
                continue
            m = FIELD_RE.match(text)
            if m and _is_field_content(m.group(1)):
                events.append((pno, y, "field", m.group(1), None))
                continue
    for t in page.find_tables().tables:
        table_stream.append((pno, t.bbox[1] if t.bbox else 0, t))

events.sort(key=lambda e: (e[0], e[1]))
table_stream.sort(key=lambda x: (x[0], x[1]))

# ---------- 状态机归并 ----------
records = []
state = {"field": "", "venue_type": "", "rank": ""}
ei = 0

def consume_events_until(page, y):
    global ei
    while ei < len(events):
        ev_p, ev_y, kind, val, hint = events[ei]
        if (ev_p, ev_y) >= (page, y):
            break
        if kind == "field":
            state["field"] = val
        elif kind == "section":
            state["venue_type"] = hint
        elif kind == "rank":
            state["rank"] = val
        ei += 1

seen = set()

def _cell_text(page, cell):
    """按 cell bbox 用 clip 提取文本（保留空格）。"""
    if cell is None:
        return ""
    clip = pymupdf.Rect(cell[0] + 1, cell[1], cell[2] - 1, cell[3])
    return page.get_text("text", clip=clip).strip().replace("\n", " ")

for page_no, y, t in table_stream:
    consume_events_until(page_no, y)
    page = doc[page_no]
    if not t.rows or not t.rows[0].cells:
        continue
    if not (state["field"] and state["venue_type"] and state["rank"]):
        continue
    first_cells = [_cell_text(page, c) for c in t.rows[0].cells]
    first_text = "".join(first_cells)
    if "序号" in first_text and "全称" in first_text:
        data_rows = t.rows[1:]            # 正常表：跳过表头
    elif first_cells and first_cells[0].isdigit():
        data_rows = t.rows                  # 跨页续表：无表头直接数据
    else:
        continue
    for row in data_rows:
        cells = [_cell_text(page, c) for c in row.cells]
        if len(cells) < 3:
            continue
        seq, abbr, full = cells[0].strip(), cells[1].strip(), cells[2].strip()
        publisher = cells[3].strip() if len(cells) > 3 else ""
        url = cells[4].strip() if len(cells) > 4 else ""
        if not seq.isdigit():
            continue
        key = (state["field"], state["venue_type"], state["rank"], seq)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "field": state["field"],
            "type": state["venue_type"],
            "rank": state["rank"],
            "seq": int(seq),
            "abbr": re.sub(r"\s+", " ", abbr).strip(),
            "name": re.sub(r"\s+", " ", full).strip(),
            "publisher": re.sub(r"\s+", " ", publisher).strip(),
            "url": re.sub(r"\s+", "", url),
        })

doc.close()

# ---------- 校验 & 落盘 ----------
fields = {}
for r in records:
    f = fields.setdefault(r["field"], {"journal": 0, "conference": 0})
    f[r["type"]] += 1
print(f"fields: {len(fields)}, total: {len(records)}")
for f, c in fields.items():
    print(f"  {f}: journal={c['journal']} conf={c['conference']}")
print("by rank:", {k: sum(1 for r in records if r['rank'] == k) for k in 'ABC'})

for probe in ("NeurIPS", "ICLR", "IJCAI", "ICML", "TPAMI",
              "Neurocomputing", "Pattern Recognition", "TPAMI", "ESWA"):
    hits = [r for r in records if r["abbr"] == probe or r["name"] == probe]
    print(f"probe {probe!r}:", [(h["type"], h["rank"], h["field"]) for h in hits])

OUT.write_text(json.dumps({
    "version": "CCF 第七版正式版（2026）",
    "source": "https://www.ccf.org.cn/Academic_Evaluation/By_category/",
    "released": "2026-03-31（4月9日勘误更新）",
    "retrieved": "2026-11-17",
    "items": records,
}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")      # Windows 上也写 LF
print("saved →", OUT)
