# -*- coding: utf-8 -*-
"""构建本地分级库 <数据目录>/rankings/rankings.db。

- 随项目发布、开箱即用（首次启动自动构建）：
  data-sources/ccf_2026.json         CCF 推荐国际学术会议和期刊目录（第七版，2026）
  data-sources/cas_warning_lists.json 中科院文献情报中心《国际期刊预警名单》2024 / 2025
- 可选：你有权使用的 中科院分区表（升级版）/ 新锐期刊分区表 / JCR 的 CSV，用 tools/build_rankings.py 导入。
  这些是受许可保护的数据，本项目不附带、也不会替你下载（见 DATA_SOURCES.md）。

产出两张表 + 元信息：
- journals(norm_key, journal, issn, eissn, cas_*, xr_*, jcr_*, warn2025, warn2024)
- ccf(field, type, rank, seq, abbr, name, publisher, url, norm_abbr, norm_name)
- meta(key, value)：各数据集的版本（徽章提示里显示「来源 + 版本」）
"""
import csv
import json
import os
import re
import sqlite3
from datetime import datetime

from . import config

PUNCT = re.compile(r"[^a-z0-9一-鿿]+")
FIELDS = ("journal", "issn", "eissn", "cas_field", "cas_part", "cas_top", "cas_wos",
          "xr_field", "xr_part", "xr_top", "xr_warning", "jcr_if", "jcr_quartile", "jcr_cat",
          "warn2025", "warn2024")


def norm(s: str) -> str:
    return PUNCT.sub("", (s or "").lower()).strip()


def parse_part(s: str) -> str:
    """「3 [168/495]」→「3」；「4 区」→「4」。"""
    m = re.match(r"\s*(\d)", s or "")
    return m.group(1) if m else ""


def _rows(path: str) -> list[dict]:
    """UTF-8（含 BOM）或 GBK（中文 Windows 上 Excel 默认的「CSV（逗号分隔）」）都能读。"""
    for enc in ("utf-8-sig", "gb18030"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{os.path.basename(path)} 的编码无法识别：请在 Excel 里另存为「CSV UTF-8（逗号分隔）」后重试")


def _need(rows: list[dict], cols: tuple, label: str):
    have = set((rows[0] if rows else {}).keys())
    missing = [c for c in cols if c not in have]
    if missing:
        raise ValueError(f"{label} 缺少列：{'、'.join(missing)}（需要的列见 DATA_SOURCES.md）")


def _col(keys, pattern: str):
    rx = re.compile(pattern)
    for k in keys:
        if rx.fullmatch((k or "").strip()):
            return k
    return None


def build(out_path: str | None = None, cas: str | None = None, xr: str | None = None,
          jcr: str | None = None, editions: dict | None = None,
          sources_dir: str | None = None, log=print) -> dict:
    """构建 rankings.db；返回各数据集条数。cas / xr / jcr 是可选的 CSV 路径。"""
    out_path = out_path or config.RANKINGS_DB
    sources_dir = sources_dir or config.SOURCES_DIR
    ed = dict(editions or {})
    rows: dict[str, dict] = {}
    counts = {}

    def upsert(key: str, patch: dict):
        r = rows.setdefault(key, {k: "" for k in FIELDS})
        r.update({k: v for k, v in patch.items() if v})

    if cas:
        data = _rows(cas)
        _need(data, ("Journal", "ISSN/EISSN", "大类", "大类分区", "Top"), "中科院分区表 CSV")
        n = 0
        for r in data:
            j = (r.get("Journal") or "").strip()
            if not j:
                continue
            ie = (r.get("ISSN/EISSN") or "").split("/")
            upsert(norm(j), {
                "journal": j, "issn": ie[0].strip(), "eissn": ie[1].strip() if len(ie) > 1 else "",
                "cas_field": (r.get("大类") or "").strip(),
                "cas_part": parse_part(r.get("大类分区") or ""),
                "cas_top": "是" if (r.get("Top") or "").strip() == "是" else "",
                "cas_wos": (r.get("Web of Science") or "").strip(),
            })
            n += 1
        counts["cas"] = n
        ed.setdefault("cas", (data[0].get("年份") or "").strip() if data else "")

    if xr:
        data = _rows(xr)
        _need(data, ("Journal", "大类中文名", "大类新锐分区"), "新锐期刊分区表 CSV")
        n = 0
        for r in data:
            j = (r.get("Journal") or "").strip()
            if not j:
                continue
            upsert(norm(j), {
                "journal": j, "issn": r.get("ISSN") or "", "eissn": r.get("EISSN") or "",
                "xr_field": (r.get("大类中文名") or "").strip(),
                "xr_part": parse_part(r.get("大类新锐分区") or ""),
                "xr_top": "是" if str(r.get("Top") or "").strip() in ("是", "Top") else "",
                "xr_warning": (r.get("预警标记") or "").strip(),
            })
            n += 1
        counts["xr"] = n
        ed.setdefault("xr", (data[0].get("年份") or "").strip() if data else "")

    if jcr:
        data = _rows(jcr)
        keys = list((data[0] if data else {}).keys())
        k_if, k_q = _col(keys, r"IF\(\d{4}\)"), _col(keys, r"IF Quartile\(\d{4}\)_1")
        if not (k_if and k_q and "Journal" in keys):
            raise ValueError("JCR CSV 需要列：Journal、IF(<年份>)、IF Quartile(<年份>)_1（可选 ISSN、EISSN、Category_1）")
        n = 0
        for r in data:
            j = (r.get("Journal") or "").strip()
            if not j:
                continue
            upsert(norm(j), {
                "journal": j, "issn": r.get("ISSN") or "", "eissn": r.get("EISSN") or "",
                "jcr_if": (r.get(k_if) or "").strip(), "jcr_quartile": (r.get(k_q) or "").strip(),
                "jcr_cat": (r.get("Category_1") or "").strip(),
            })
            n += 1
        counts["jcr"] = n
        ed.setdefault("jcr", re.search(r"\d{4}", k_if).group(0))

    # ---- 随项目发布的公开数据 ----
    warn = json.load(open(os.path.join(sources_dir, "cas_warning_lists.json"), encoding="utf-8"))
    nw = 0
    for e in warn.get("editions", []):
        year = str(e.get("year") or "")
        if year not in ("2024", "2025"):
            continue
        for it in e.get("items", []):
            j = (it.get("journal") or "").strip()
            if j:
                upsert(norm(j), {"journal": j, f"warn{year}": (it.get("reason") or "预警").strip()})
                nw += 1
    counts["warning_lists"] = nw
    ccf_doc = json.load(open(os.path.join(sources_dir, "ccf_2026.json"), encoding="utf-8"))
    ccf = ccf_doc["items"]
    counts["ccf"] = len(ccf)
    ed.setdefault("ccf", "2026")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    part = out_path + ".part"
    if os.path.exists(part):
        os.remove(part)
    conn = sqlite3.connect(part)
    try:
        conn.executescript("""
        CREATE TABLE journals (
          norm_key TEXT PRIMARY KEY,
          journal TEXT, issn TEXT, eissn TEXT,
          cas_field TEXT, cas_part TEXT, cas_top TEXT, cas_wos TEXT,
          xr_field TEXT, xr_part TEXT, xr_top TEXT, xr_warning TEXT,
          jcr_if TEXT, jcr_quartile TEXT, jcr_cat TEXT,
          warn2025 TEXT, warn2024 TEXT
        );
        CREATE INDEX idx_journals_issn ON journals(issn);
        CREATE TABLE ccf (
          field TEXT, type TEXT, rank TEXT, seq INTEGER,
          abbr TEXT, name TEXT, publisher TEXT, url TEXT,
          norm_abbr TEXT, norm_name TEXT
        );
        CREATE INDEX idx_ccf_abbr ON ccf(norm_abbr);
        CREATE INDEX idx_ccf_name ON ccf(norm_name);
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        conn.executemany("INSERT INTO journals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         [(k, *(r[f] for f in FIELDS)) for k, r in rows.items()])
        conn.executemany("INSERT INTO ccf VALUES (?,?,?,?,?,?,?,?,?,?)",
                         [(c["field"], c["type"], c["rank"], c["seq"], c["abbr"], c["name"],
                           c["publisher"], c["url"], norm(c["abbr"]), norm(c["name"])) for c in ccf])
        meta = {"built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "datasets": ",".join(k for k in ("ccf", "warning_lists", "cas", "xr", "jcr") if counts.get(k))}
        for k in ("ccf", "cas", "xr", "jcr"):
            if counts.get(k):
                meta[f"{k}_edition"] = str(ed.get(k) or "")
        conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()
    os.replace(part, out_path)
    log(f"rankings.db 已生成：{out_path}  " + "，".join(f"{k}={v}" for k, v in counts.items()))
    return counts
