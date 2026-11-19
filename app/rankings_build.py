# -*- coding: utf-8 -*-
"""构建本地分级库 <数据目录>/rankings/rankings.db。

- 随项目发布、开箱即用（首次启动自动构建；新版本随附的数据有更新时自动重建）：
  data-sources/ccf_2026.json          CCF 推荐国际学术会议和期刊目录（第七版，2026）
  data-sources/cas_warning_lists.json 中科院文献情报中心《国际期刊预警名单》2024 / 2025
  data-sources/cas_2025.csv           中科院文献情报中心期刊分区表（升级版）2025
  data-sources/xr_2026.csv            新锐期刊分区表 2026（xr-scholar.com）
  出处、校验和与权利说明见 DATA_SOURCES.md 和 data-sources/SHA256SUMS。
- 可选（tools/build_rankings.py）：--cas / --xr 用你自己的 CSV 替换随附的表；
  --jcr 导入你有权使用的 JCR（受 Clarivate 许可保护，本项目不附带）。

产出两张表 + 元信息：
- journals(norm_key, journal, issn, eissn, cas_*, xr_*, jcr_*, warn2025, warn2024)
- ccf(field, type, rank, seq, abbr, name, publisher, url, norm_abbr, norm_name)
- meta(key, value)：各数据集的版本与来源（bundled 随附 / imported 你导入的）、随附数据指纹 bundle
"""
import csv
import hashlib
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


# 随项目附带的分区表（上游原样副本）；tools/build_rankings.py 的 --cas / --xr 可以替换
BUNDLED = {"cas": "cas_2025.csv", "xr": "xr_2026.csv"}
PUBLIC_FILES = ("ccf_2026.json", "cas_warning_lists.json")


_ISSN_RE = re.compile(r"\d{4}-\d{3}[\dX]")


def norm(s: str) -> str:
    return PUNCT.sub("", (s or "").lower()).strip()


def _issn(s) -> str:
    """规范 ISSN；占位符（「-」、「N/A」、空白）一律当作没有。"""
    s = (s or "").strip().upper()
    return s if _ISSN_RE.fullmatch(s) else ""


def bundled_files(sources_dir: str | None = None) -> dict:
    sd = sources_dir or config.SOURCES_DIR
    return {k: os.path.join(sd, f) for k, f in BUNDLED.items() if os.path.exists(os.path.join(sd, f))}


def bundle_fingerprint(sources_dir: str | None = None) -> str:
    """随附数据的指纹：data-sources/ 里任何一份数据变了（新版本带了更新的数据），已有的分级库就会被重建。"""
    sd = sources_dir or config.SOURCES_DIR
    h = hashlib.sha256()
    for f in (*PUBLIC_FILES, *BUNDLED.values()):
        h.update(f.encode("utf-8") + b"\0")
        p = os.path.join(sd, f)
        if os.path.exists(p):
            with open(p, "rb") as fh:
                data = fh.read()
            if not f.endswith(".csv"):
                data = data.replace(b"\r\n", b"\n")    # JSON 在 Windows 检出时可能是 CRLF：内容相同就算同一份
            h.update(data)
        h.update(b"\0")
    return h.hexdigest()[:16]


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
          sources_dir: str | None = None, log=print, bundled: bool = True) -> dict:
    """构建 rankings.db；返回各数据集条数。
    默认用随附的中科院分区表 2025 / 新锐分区表 2026；cas / xr 是你自己的 CSV，替换随附的同名表；
    jcr 是你自己的 JCR CSV（不随项目附带）。bundled=False 时不用随附的两张分区表（测试用）。"""
    out_path = out_path or config.RANKINGS_DB
    sources_dir = sources_dir or config.SOURCES_DIR
    ed = dict(editions or {})
    src = {k: ("bundled", p) for k, p in (bundled_files(sources_dir) if bundled else {}).items()}
    for k, p in (("cas", cas), ("xr", xr), ("jcr", jcr)):
        if p:
            src[k] = ("imported", p)
    rows: dict[str, dict] = {}
    counts = {}

    variants: dict[str, list] = {}       # 刊名归一键 → 同名但 ISSN 不同的另一本刊的行键

    def upsert(key: str, patch: dict):
        """按刊名归一键合并。同名但 ISSN 完全不同的是两本刊（如 Science & Education 与 SCIENCE EDUCATION），
        分开存（键加 ISSN 后缀）；已有的 ISSN 不会被覆盖。"""
        patch = dict(patch, issn=_issn(patch.get("issn")), eissn=_issn(patch.get("eissn")))
        issns = {i for i in (patch["issn"], patch["eissn"]) if i}
        target = key
        if issns:
            same_name = [key, *variants.get(key, [])]
            hit = next((k for k in same_name if k in rows
                        and ({rows[k]["issn"], rows[k]["eissn"]} - {""}) & issns), None)
            if hit:
                target = hit
            elif key in rows and ({rows[key]["issn"], rows[key]["eissn"]} - {""}):
                target = f"{key}#{sorted(issns)[0]}"
                variants.setdefault(key, []).append(target)
        r = rows.setdefault(target, {k: "" for k in FIELDS})
        for k, v in patch.items():
            if v and not (k in ("issn", "eissn") and r[k]):
                r[k] = v

    if "cas" in src:
        data = _rows(src["cas"][1])
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

    if "xr" in src:
        data = _rows(src["xr"][1])
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

    if "jcr" in src:
        data = _rows(src["jcr"][1])
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
                "datasets": ",".join(k for k in ("ccf", "warning_lists", "cas", "xr", "jcr") if counts.get(k)),
                "bundle": bundle_fingerprint(sources_dir) if bundled else "",
                "imported": ",".join(k for k in ("cas", "xr", "jcr") if src.get(k, ("",))[0] == "imported")}
        for k in ("ccf", "cas", "xr", "jcr"):
            if counts.get(k):
                meta[f"{k}_edition"] = str(ed.get(k) or "")
        for k, (how, _) in src.items():
            if counts.get(k):
                meta[f"{k}_source"] = how
        conn.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
        conn.commit()
    finally:
        conn.close()
    os.replace(part, out_path)
    log(f"rankings.db 已生成：{out_path}  " + "，".join(f"{k}={v}" for k, v in counts.items()))
    return counts
