# -*- coding: utf-8 -*-
"""凭据保护（DPAPI / 钥匙串 / 拒绝明文）与分级库构建（随附数据、升级重建、导入自己的 CSV，用合成 CSV）。"""
import csv
import hashlib
import os
import re
import sqlite3
import sys

import pytest

from app import rankings_build, vault
from conftest import FakeKeyring


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_roundtrip():
    s = vault.protect("pw-1", strict=True)
    assert s.startswith("dpapi:") and "pw-1" not in s and vault.reveal(s) == "pw-1"
    s2 = vault.protect("dpapi:looks-encrypted", force=True, strict=True)      # 用户密码恰好以 dpapi: 开头
    assert vault.reveal(s2) == "dpapi:looks-encrypted"
    with pytest.raises(vault.VaultError):
        vault.reveal("dpapi:AAAAAAAA")


def test_keyring_backend(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(vault.sys, "platform", "linux")
    monkeypatch.setattr(vault, "_keyring", lambda: kr)
    s = vault.protect("pw-2", strict=True, key="openreview")
    assert s == "keyring:openreview" and vault.reveal(s) == "pw-2" and vault.storage_of(s) == "keyring"
    vault.forget(s)
    with pytest.raises(vault.VaultError):
        vault.reveal(s)


def test_no_secure_store_refuses_plaintext(monkeypatch):
    monkeypatch.setattr(vault.sys, "platform", "linux")
    monkeypatch.setattr(vault, "_keyring", lambda: None)
    assert vault.backend() == "none"
    with pytest.raises(vault.VaultError):
        vault.protect("pw-3", strict=True)


def _csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_build_rankings_with_user_imported_files(tmp_path):
    cas, xr, jcr = tmp_path / "cas.csv", tmp_path / "xr.csv", tmp_path / "jcr.csv"
    _csv(cas, ["Journal", "年份", "ISSN/EISSN", "大类", "大类分区", "Top", "Web of Science"],
         [["Journal of Synthetic Tests", "2099", "0000-0000/1111-1111", "计算机科学", "2 [10/100]", "否", "SCIE"]])
    _csv(xr, ["Journal", "年份", "ISSN", "EISSN", "大类中文名", "大类新锐分区", "Top", "预警标记"],
         [["Journal of Synthetic Tests", "2099", "0000-0000", "1111-1111", "计算机科学", "1", "Top", ""]])
    _csv(jcr, ["Journal", "ISSN", "EISSN", "IF(2098)", "Category_1", "IF Quartile(2098)_1"],
         [["Journal of Synthetic Tests", "0000-0000", "1111-1111", "4.2", "COMPUTER SCIENCE", "Q1"]])
    out = tmp_path / "rankings.db"
    counts = rankings_build.build(out_path=str(out), cas=str(cas), xr=str(xr), jcr=str(jcr), log=lambda *a: None)
    assert counts["cas"] == counts["xr"] == counts["jcr"] == 1 and counts["ccf"] > 600
    c = sqlite3.connect(out)
    meta = dict(c.execute("SELECT key, value FROM meta").fetchall())
    row = c.execute("SELECT cas_part, xr_part, xr_top, jcr_if, jcr_quartile FROM journals WHERE journal=?",
                    ("Journal of Synthetic Tests",)).fetchone()
    c.close()
    assert meta["cas_edition"] == "2099" and meta["jcr_edition"] == "2098"
    assert meta["imported"] == "cas,xr,jcr" and meta["cas_source"] == meta["xr_source"] == meta["jcr_source"] == "imported"
    assert row == ("2", "1", "是", "4.2", "Q1")


def test_build_rankings_reads_gbk_csv(tmp_path):
    """中文 Windows 上 Excel 的「CSV（逗号分隔）」默认存成 GBK。"""
    cas = tmp_path / "cas_gbk.csv"
    with open(cas, "w", encoding="gb18030", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Journal", "年份", "ISSN/EISSN", "大类", "大类分区", "Top"])
        w.writerow(["Journal of Synthetic Tests", "2099", "0000-0000/1111-1111", "计算机科学", "1 [1/100]", "是"])
    with pytest.raises(UnicodeDecodeError):
        cas.read_bytes().decode("utf-8")            # 确实不是 UTF-8，走的是 GBK 回退
    out = tmp_path / "rankings.db"
    assert rankings_build.build(out_path=str(out), cas=str(cas), log=lambda *a: None, bundled=False)["cas"] == 1
    c = sqlite3.connect(out)
    row = c.execute("SELECT cas_field, cas_part, cas_top FROM journals WHERE journal=?",
                    ("Journal of Synthetic Tests",)).fetchone()
    c.close()
    assert row == ("计算机科学", "1", "是")


def test_bundled_data_matches_checksums():
    """随附的上游 CSV 与 SHA256SUMS 一致：原样副本，也能发现 Git 换行转换改动了文件。"""
    from app import config
    sums = {}
    with open(os.path.join(config.SOURCES_DIR, "SHA256SUMS"), encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line.rstrip("\r\n"))     # sha256sum 文本 / 二进制两种写法
            assert m, line
            sums[m.group(2)] = m.group(1).lower()
    assert sorted(sums) == sorted(n for n in os.listdir(config.SOURCES_DIR) if n.endswith(".csv"))
    for name, h in sums.items():
        with open(os.path.join(config.SOURCES_DIR, name), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == h, name


def _legacy_db(path, **kw):
    """模拟 0.1.0 生成的分级库：没有 bundle / imported / *_source 元信息，也没有随附的中科院 / 新锐表。"""
    rankings_build.build(out_path=str(path), bundled=False, log=lambda *a: None, **kw)
    c = sqlite3.connect(path)
    c.execute("DELETE FROM meta WHERE key IN ('bundle', 'imported') OR key LIKE '%\\_source' ESCAPE '\\'")
    c.commit()
    c.close()


def test_upgrade_rebuilds_bundled_db_but_keeps_user_imports(tmp_path, monkeypatch):
    from app import config, rankings as rk
    db_path = tmp_path / "rankings" / "rankings.db"
    monkeypatch.setattr(config, "RANKINGS_DB", str(db_path))
    monkeypatch.setattr(rk, "DB_PATH", str(db_path))
    logs = []
    # 旧库里没有你自己导入的数据：启动时自动重建，用上随附的中科院 2025 / 新锐 2026
    _legacy_db(db_path)
    assert not rk.datasets()["cas"]
    assert rk.ensure_db(log=logs.append)
    assert rk.datasets()["cas"] and rk.datasets()["xr"] and rk.editions()["cas"] == "2025"
    assert rk.imported() == [] and not rk.outdated() and rk.source("cas") == "bundled"
    assert os.path.exists(str(db_path) + ".bak")                # 自动重建前留了旧库
    # 旧库里有你导入的中科院表：不覆盖，只提示你带上原来的 CSV 重新构建
    mine = tmp_path / "my_cas.csv"
    _csv(mine, ["Journal", "年份", "ISSN/EISSN", "大类", "大类分区", "Top"],
         [["Journal of Synthetic Tests", "2099", "0000-0000/1111-1111", "计算机科学", "3 [1/9]", "否"]])
    db_path.unlink()
    _legacy_db(db_path, cas=str(mine))
    assert rk.ensure_db(log=logs.append)
    assert rk.imported() == ["cas"] and rk.outdated() and rk.source("cas") == "imported"
    assert rk.editions()["cas"] == "2099" and not rk.datasets()["xr"]
    assert any("自己导入的数据" in m for m in logs)


def test_same_name_different_journals_stay_apart(tmp_path):
    """归一后同名、ISSN 不同的两本刊分开存；「-」之类的 ISSN 占位符不会覆盖已有 ISSN。"""
    cas, xr = tmp_path / "cas.csv", tmp_path / "xr.csv"
    _csv(cas, ["Journal", "年份", "ISSN/EISSN", "大类", "大类分区", "Top"],
         [["Synthetic & Studies", "2099", "0000-0019/0000-0027", "教育学", "3 [1/9]", "否"],
          ["SYNTHETIC STUDIES", "2099", "0000-0035/0000-0043", "教育学", "2 [1/9]", "否"]])
    _csv(xr, ["Journal", "年份", "ISSN", "EISSN", "大类中文名", "大类新锐分区", "Top"],
         [["SYNTHETIC STUDIES", "2099", "0000-0035", "-", "教育学", "1", "Top"],
          ["Synthetic & Studies", "2099", "-", "-", "教育学", "4", ""]])
    out = tmp_path / "r.db"
    rankings_build.build(out_path=str(out), cas=str(cas), xr=str(xr), log=lambda *a: None, bundled=False)
    c = sqlite3.connect(out)
    rows = {r[0]: r[1:] for r in c.execute("SELECT issn, cas_part, xr_part, eissn FROM journals")}
    c.close()
    assert rows["0000-0035"][:2] == ("2", "1") and rows["0000-0035"][2] == "0000-0043"
    assert rows["0000-0019"][:2] == ("3", "4") and rows["0000-0019"][2] == "0000-0027"


def test_unreadable_bundled_data_keeps_existing_db(client, monkeypatch):
    from app import rankings as rk

    def boom(*a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(rankings_build, "bundle_fingerprint", boom)
    monkeypatch.setitem(rk._FP, "value", None)
    assert rk.ensure_db(log=lambda *a: None) and not rk.outdated()
    assert client.get("/api/stages").status_code == 200


def test_static_files_revalidate(client):
    assert client.get("/").headers.get("cache-control") == "no-cache"
    assert "no-cache" not in (client.get("/api/stages").headers.get("cache-control") or "")


def test_build_rankings_rejects_wrong_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    _csv(bad, ["Name", "Rank"], [["X", "1"]])
    with pytest.raises(ValueError):
        rankings_build.build(out_path=str(tmp_path / "r.db"), cas=str(bad), log=lambda *a: None)
