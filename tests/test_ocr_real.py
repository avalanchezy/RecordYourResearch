# -*- coding: utf-8 -*-
"""真实 OCR 引擎的端到端测试：只在装了 requirements-ocr.txt 时运行（CI 的 OCR 任务），其余情况跳过。

图片是用 Pillow 现场画的合成页面，不含任何真实稿件。
"""
import difflib
import io
import os

import pytest

from app import ocr

pytestmark = pytest.mark.skipif(not ocr.available(), reason="OCR add-on not installed (requirements-ocr.txt)")

# 带中文字形的字体：Windows 自带微软雅黑 / 黑体；Linux、macOS 上有就用，没有就跳过中文用例
_CJK_FONTS = [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simsun.ttc",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"]


def _render(lines, font=None, size=28, width=900):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(font, size) if font else ImageFont.load_default(size=size)
    step = int(size * 1.9)
    im = Image.new("RGB", (width, 40 + step * len(lines)), "white")
    d = ImageDraw.Draw(im)
    for i, (x, text) in enumerate(lines):
        d.text((x, 20 + i * step), text, fill="black", font=f)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _new(client, title, number):
    r = client.post("/api/submissions", json={
        "title": title, "manuscript_number": number,
        "venue": {"name": "Pattern Recognition", "type": "journal", "tracker": "elsevier"}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_real_ocr_english_status_page(client):
    png = _render([(30, "Current status: Under Review"), (30, "Reviews completed: 2"),
                   (30, "Reviewers accepted: 3"), (30, "Reviewers invited: 8"),
                   (30, "Manuscript Number: PRD2600001")])
    r = client.post("/api/ocr", files={"file": ("shot.png", png, "image/png")})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["engine"] in ("rapidocr", "rapidocr_onnxruntime") and o["min_score"] > 0.5
    sid = _new(client, "RealOcrNet: Synthetic Paper", "PRD2600001")
    p = client.post(f"/api/submissions/{sid}/elsevier-paste", json={"text": o["text"]}).json()
    assert p["recognized"] and p["stage"] == "review", o["text"]
    assert p["reviewers"] == {"completed": 2, "accepted": 3, "invited": 8}, o["text"]


def test_real_ocr_chinese_wechat_page(client):
    font = next((f for f in _CJK_FONTS if os.path.exists(f)), None)
    if not font:
        pytest.skip("no CJK font on this machine")
    png = _render([(30, "同行评审进行中"), (30, "当前进度："), (30, "Under Review"),
                   (30, "评审完成：1"), (30, "接受评审邀请：2"), (30, "发出评审邀请：5")], font=font)
    o = client.post("/api/ocr", files={"file": ("shot.png", png, "image/png")}).json()
    sid = _new(client, "RealOcrNetZh: Synthetic Paper", "")
    p = client.post(f"/api/submissions/{sid}/elsevier-paste", json={"text": o["text"]}).json()
    assert p["stage"] == "review", o["text"]
    assert p["reviewers"] == {"completed": 1, "accepted": 2, "invited": 5}, o["text"]


def test_real_ocr_scanned_pdf_title(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    from app.parser import parse_pdf
    title = "Scanned Synthetic Paper on Tracking Reviews"
    png = _render([(150, title)], size=34, width=1190)
    body = _render([(330, "Alice Example, Bob Example")], size=22, width=1190)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 40, 595, 40 + 595 * 106 / 1190), stream=png)
    page.insert_image(pymupdf.Rect(0, 110, 595, 110 + 595 * 82 / 1190), stream=body)
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    d = parse_pdf(str(path))
    assert d["ocr"] and d["ocr"]["used"]
    assert difflib.SequenceMatcher(None, d["title"].lower(), title.lower()).ratio() > 0.9, d
