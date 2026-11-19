# -*- coding: utf-8 -*-
"""解析逻辑（合成数据）：PDF 元数据、状态归一、会议名 / 年份、OpenReview 阶段推断。"""
import pymupdf

from app import openreview, stages, venueutil as vu
from app.parser import parse_pdf


def _make_pdf(path):
    doc = pymupdf.open()
    pg = doc.new_page(width=612, height=792)
    pg.insert_text((72, 40), "ICCV 2025 Submission #1234. CONFIDENTIAL REVIEW COPY. DO NOT DISTRIBUTE.", fontsize=8)
    pg.insert_text((72, 110), "DemoNet: A Synthetic Network for Testing", fontsize=18)
    pg.insert_text((72, 132), "PDF Metadata Extraction", fontsize=18)
    pg.insert_text((72, 165), "Anonymous ICCV submission", fontsize=11)
    pg.insert_text((72, 200), "Abstract", fontsize=12)
    pg.insert_textbox(pymupdf.Rect(72, 210, 540, 330),
                      "We present DemoNet, a purely synthetic model used only to test the extractor. " * 4,
                      fontsize=10)
    pg.insert_text((72, 350), "Keywords: synthetic data, testing, pdf parsing", fontsize=10)
    doc.save(path)


def test_pdf_metadata_extraction(tmp_path):
    p = str(tmp_path / "demo.pdf")
    _make_pdf(p)
    r = parse_pdf(p)
    assert r["title"] == "DemoNet: A Synthetic Network for Testing PDF Metadata Extraction"
    assert r["conf_name"] == "ICCV" and str(r["conf_year"]) == "2025" and r["double_blind"]
    assert r["keywords"] == ["synthetic data", "testing", "pdf parsing"]
    assert r["abstract"].startswith("We present DemoNet")


def test_status_normalisation():
    assert stages.normalize("Under Review") == "review"
    assert stages.normalize("Required Reviews Complete") == "reviews_done"
    assert stages.normalize("Revise - Major") == "revise"
    assert stages.normalize("Reject") == "rejected"
    assert stages.normalize("Accept") == "accepted"


def test_venue_name_and_year():
    assert vu.split_year("NeurIPS 2025", "") == ("NeurIPS", "2025")
    assert vu.display("NeurIPS", "2025") == "NeurIPS 2025"
    assert vu.clean_name("ICML 2025 Conference Submission") == "ICML 2025"


def _note(venueid, venue="", domain="ICLR.cc/2025/Conference"):
    return {"content": {"venueid": {"value": venueid}, "venue": {"value": venue}}, "domain": domain}


def _reply(kind, **content):
    return {"invitations": [f"ICLR.cc/2025/Conference/Submission1/-/{kind}"],
            "content": {k: {"value": v} for k, v in content.items()}}


def test_openreview_stage_inference():
    base = "ICLR.cc/2025/Conference"
    assert openreview.infer_stage(_note(base + "/Withdrawn_Submission"))[0] == "withdrawn"
    assert openreview.infer_stage(_note(base + "/Rejected_Submission"))[0] == "rejected"
    assert openreview.infer_stage(_note(base + "/Submission", "ICLR 2025 Conference Submission"))[0] == "review"
    reviews = [_reply("Official_Review", rating="6") for _ in range(3)]
    st, raw = openreview.infer_stage(_note(base + "/Submission", "ICLR 2025 Conference Submission"), reviews)
    assert st == "reviews_done" and "3 reviews" in raw
    st, raw = openreview.infer_stage(_note(base + "/Submission"), [_reply("Decision", decision="Accept (poster)")])
    assert st == "accepted" and raw == "Accept (poster)"
    st, _ = openreview.infer_stage(_note(base + "/Submission"), [_reply("Decision", decision="Not Accepted")])
    assert st == "rejected"
    assert openreview.base_venue_id(base + "/Submission") == base
    assert openreview.normalize_forum("https://openreview.net/forum?id=AbC123xyz") == "AbC123xyz"
