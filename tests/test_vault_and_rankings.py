# -*- coding: utf-8 -*-
"""凭据保护（DPAPI / 钥匙串 / 拒绝明文）与分级库构建（可选导入受许可数据的流程，用合成 CSV）。"""
import csv
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
    assert rankings_build.build(out_path=str(out), cas=str(cas), log=lambda *a: None)["cas"] == 1
    c = sqlite3.connect(out)
    row = c.execute("SELECT cas_field, cas_part, cas_top FROM journals WHERE journal=?",
                    ("Journal of Synthetic Tests",)).fetchone()
    c.close()
    assert row == ("计算机科学", "1", "是")


def test_build_rankings_rejects_wrong_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    _csv(bad, ["Name", "Rank"], [["X", "1"]])
    with pytest.raises(ValueError):
        rankings_build.build(out_path=str(tmp_path / "r.db"), cas=str(bad), log=lambda *a: None)
