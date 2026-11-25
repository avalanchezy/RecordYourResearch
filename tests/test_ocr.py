# -*- coding: utf-8 -*-
"""截图 OCR（可选组件）：版面还原、接口、来源记录、扫描版 PDF 兜底。

这里用假的识别引擎，不需要装 RapidOCR；真实引擎的端到端测试在 tests/test_ocr_real.py
（CI 的 OCR 任务里安装 requirements-ocr.txt 后运行）。只用合成数据。
"""
import io

import pytest

from app import ocr


def _box(x0, y0, x1, y1, text, score=0.97):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text, "score": score}


# ---------- 版面还原 ----------

def test_layout_groups_rows_and_uses_tabs_for_wide_gaps():
    items = [
        _box(420, 102, 520, 126, "PR-D-26-00001"),         # 同一行、隔得远 → 制表符（表格列）
        _box(20, 100, 200, 124, "Manuscript Number"),
        _box(20, 140, 130, 164, "评审完成："),
        _box(136, 141, 150, 163, "0"),                     # 挨着 → 空格
        _box(162, 141, 176, 163, "2"),
        _box(20, 180, 300, 204, "Under Review"),
    ]
    assert ocr.layout_text(items) == "Manuscript Number\tPR-D-26-00001\n评审完成： 0 2\nUnder Review"


def test_layout_tolerates_slightly_tilted_boxes_and_empty_input():
    items = [_box(10, 10, 100, 30, "Status"), _box(300, 14, 400, 34, "Under Review"),
             _box(10, 60, 100, 80, "Date")]
    assert ocr.layout_text(items).splitlines() == ["Status\tUnder Review", "Date"]
    assert ocr.layout_text([]) == ""


def test_sniff_accepts_images_only():
    assert ocr.sniff(b"\x89PNG\r\n\x1a\n....") == "png"
    assert ocr.sniff(b"\xff\xd8\xff\xe0") == "jpeg"
    assert ocr.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert ocr.sniff(b"%PDF-1.7") == ""
    assert ocr.sniff(b"<svg") == ""


# ---------- 假引擎 ----------

class _FakeOut:
    def __init__(self, items):
        self.boxes = [[(i["x0"], i["y0"]), (i["x1"], i["y0"]), (i["x1"], i["y1"]), (i["x0"], i["y1"])]
                      for i in items] or None
        self.txts = tuple(i["text"] for i in items)
        self.scores = tuple(i["score"] for i in items)


class _FakeEngine:
    def __init__(self, items):
        self.items, self.calls = items, 0

    def __call__(self, img):
        self.calls += 1
        return _FakeOut(self.items)


ELSEVIER_SHOT = [
    _box(20, 20, 220, 44, "Review Status"),
    _box(20, 60, 260, 84, "同行评审进行中"),
    _box(20, 100, 150, 124, "当前进度："),
    _box(20, 130, 180, 154, "Under Review"),
    _box(20, 170, 130, 194, "评审完成：", 0.95),
    _box(140, 171, 154, 193, "0", 0.91),
    _box(170, 171, 184, 193, "2", 0.83),
    _box(20, 210, 170, 234, "接受评审邀请："),
    _box(180, 211, 194, 233, "3"),
    _box(20, 250, 180, 274, "发出评审邀请*："),
    _box(190, 251, 204, 273, "8"),
    _box(20, 290, 220, 314, "Manuscript Number"),
    _box(20, 320, 230, 344, "PR-D-26-00001"),
]


@pytest.fixture()
def fake_ocr(monkeypatch):
    def install(items):
        eng = _FakeEngine(items)
        monkeypatch.setattr(ocr, "available", lambda: True)
        monkeypatch.setattr(ocr, "_engine", eng)
        monkeypatch.setattr(ocr, "_engine_name", "rapidocr")
        return eng
    return install


def _png(w=400, h=360):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (255, 255, 255, 0)).save(buf, "PNG")   # 带透明通道的截图
    return buf.getvalue()


def test_image_to_text_with_fake_engine(fake_ocr):
    eng = fake_ocr(ELSEVIER_SHOT)
    r = ocr.image_to_text(_png())
    assert eng.calls == 1
    assert "评审完成： 0 2" in r["text"] and "Under Review" in r["text"]
    assert r["min_score"] == 0.83 and r["boxes"] == len(ELSEVIER_SHOT) and r["engine"] == "rapidocr"


def test_image_to_text_rejects_non_images_and_huge_files(fake_ocr):
    fake_ocr(ELSEVIER_SHOT)
    with pytest.raises(ocr.OcrError, match="不是图片"):
        ocr.image_to_text(b"%PDF-1.7 not an image")
    with pytest.raises(ocr.OcrError, match="太大"):
        ocr.image_to_text(b"\x89PNG\r\n\x1a\n" + b"0" * (ocr.MAX_IMAGE_BYTES + 1))


def test_not_installed_is_reported(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    with pytest.raises(ocr.OcrError, match="没有安装"):
        ocr.image_to_text(_png())
    st = ocr.status()
    assert st["available"] is False and "requirements-ocr.txt" in st["install"]


# ---------- 接口 ----------

def _new(client, title="OcrNet: Synthetic Paper"):
    r = client.post("/api/submissions", json={
        "title": title, "manuscript_number": "PR-D-26-00001",
        "venue": {"name": "Pattern Recognition", "type": "journal", "tracker": "elsevier"}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_api_reports_ocr_status(client):
    st = client.get("/api/stages").json()
    assert set(st["ocr"]) >= {"available", "install"}


def test_api_ocr_501_when_not_installed(client, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    r = client.post("/api/ocr", files={"file": ("s.png", _png(), "image/png")})
    assert r.status_code == 501 and "requirements-ocr.txt" in r.json()["detail"]


def test_api_ocr_then_paste_records_provenance(client, fake_ocr):
    fake_ocr(ELSEVIER_SHOT)
    sid = _new(client)
    r = client.post("/api/ocr", files={"file": ("s.png", _png(), "image/png")})
    assert r.status_code == 200, r.text
    o = r.json()
    prev = client.post(f"/api/submissions/{sid}/elsevier-paste", json={"text": o["text"]}).json()
    assert prev["recognized"] and prev["stage"] == "review"
    assert prev["reviewers"] == {"completed": 2, "accepted": 3, "invited": 8}    # 「0 2」= 0→2
    assert prev["manuscript_number"] == "PR-D-26-00001" and not prev["warning"]
    w = client.post(f"/api/submissions/{sid}/elsevier-paste/confirm",
                    json={"text": o["text"], "via": "ocr", "ocr_score": o["min_score"]}).json()
    assert w["written"]
    ev = client.get(f"/api/submissions/{sid}").json()["events"]
    pl = [e["payload"] for e in ev if e["payload"].get("official_paste")][-1]
    assert pl["via"] == "ocr" and pl["ocr_min_score"] == 0.83


def test_api_ocr_rejects_bad_input(client, fake_ocr):
    fake_ocr(ELSEVIER_SHOT)
    r = client.post("/api/ocr", files={"file": ("x.png", b"%PDF-1.7", "image/png")})
    assert r.status_code == 422 and "不是图片" in r.json()["detail"]
    fake_ocr([])
    r = client.post("/api/ocr", files={"file": ("s.png", _png(), "image/png")})
    assert r.status_code == 422 and "没有认出文字" in r.json()["detail"]


def test_paste_rejects_unknown_via(client):
    sid = _new(client, "ViaNet: Synthetic Paper")
    r = client.post(f"/api/submissions/{sid}/elsevier-paste/confirm",
                    json={"text": "当前进度：\nUnder Review", "via": "llm"})
    assert r.status_code == 400


def test_system_paste_records_provenance(client):
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "docs", "demo", "scholarone-author-dashboard.txt"), encoding="utf-8") as f:
        text = f.read()                                  # 合成的 ScholarOne 作者中心页面
    prev = client.post("/api/import/system-paste", json={"text": text}).json()
    idx = next(i for i, it in enumerate(prev["items"]) if it["action"] == "create")
    r = client.post("/api/import/system-paste/confirm", json={
        "text": text, "via": "ocr", "ocr_score": 0.9,
        "picks": [{"index": idx, "action": "create", "venue_name": "Synthetic Journal of Imaging"}]})
    assert r.status_code == 200, r.text
    sid = r.json()["ids"][0]
    pl = [e["payload"] for e in client.get(f"/api/submissions/{sid}").json()["events"]
          if e["payload"].get("system_paste")]
    assert pl and pl[0]["via"] == "ocr" and pl[0]["ocr_min_score"] == 0.9


# ---------- 扫描版 PDF ----------

def _scanned_pdf(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    if not hasattr(pymupdf, "Document"):
        pytest.skip("pymupdf stub")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(pymupdf.Rect(50, 50, 545, 120), color=(0, 0, 0))   # 只有图形，没有文字层
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def test_scanned_pdf_uses_ocr_when_installed(tmp_path, fake_ocr):
    from app.parser import parse_pdf
    path = _scanned_pdf(tmp_path)
    # 渲染图是 2 倍：标题字高 40 像素 ≈ 16 pt，正文 24 像素 ≈ 10 pt
    fake_ocr([
        _box(160, 120, 1030, 160, "Scanned Synthetic Paper on Tracking Reviews", 0.96),
        _box(400, 200, 790, 224, "Alice Example, Bob Example", 0.93),
        _box(120, 300, 260, 324, "Abstract", 0.97),
        _box(120, 330, 1070, 354, "We study a synthetic problem that exists only in this test.", 0.9),
    ])
    d = parse_pdf(path)
    assert d["ocr"]["used"] is True and d["ocr"]["min_score"] == 0.9
    assert d["title"] == "Scanned Synthetic Paper on Tracking Reviews"


def test_scanned_pdf_without_ocr_says_how_to_install(tmp_path, monkeypatch):
    from app.parser import parse_pdf
    path = _scanned_pdf(tmp_path)
    monkeypatch.setattr(ocr, "available", lambda: False)
    d = parse_pdf(path)
    assert d["ocr"]["used"] is False and d["ocr"]["reason"] == "not_installed"
    assert d["title"] == ""


def test_text_pdf_does_not_touch_ocr(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    if not hasattr(pymupdf, "Document"):
        pytest.skip("pymupdf stub")
    from app.parser import parse_pdf
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "A Born-Digital Synthetic Paper Title", fontsize=18)
    path = tmp_path / "text.pdf"
    doc.save(str(path))
    doc.close()
    monkeypatch.setattr(ocr, "available", lambda: pytest.fail("OCR must not run on PDFs with a text layer"))
    d = parse_pdf(str(path))
    assert d["ocr"] is None
