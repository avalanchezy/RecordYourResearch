# -*- coding: utf-8 -*-
"""可选 OCR：投稿系统页面截图 / 扫描版 PDF → 文本，再交给现有的规则解析器。

引擎是 RapidOCR（PaddleOCR 模型的 ONNX 版本）：纯 CPU、离线，中英文模型随安装包附带，
不联网下载。没有安装时 available() 为 False，界面上只提示怎么装，其余功能完全不受影响：

    pip install -r requirements-ocr.txt

OCR 只把图片变成文字，不做判断；状态、人数、日期仍由 server / syspaste 里的规则解析，
写入前照常预览、由用户确认，事件里记下「这段文字来自 OCR」以及最低置信度。
"""
from __future__ import annotations

import importlib.util
import io
import statistics
import threading
import time

INSTALL_HINT = "pip install -r requirements-ocr.txt"
MAX_IMAGE_BYTES = 15 * 1024 * 1024        # 一张整页截图（4K、PNG）也就几 MB
MAX_PIXELS = 60_000_000                    # 防解压炸弹；超大长截图先等比缩小
_LONG_SIDE = 4096                          # 识别前把长边缩到这以内（检测模型本身也会再缩放）

_lock = threading.Lock()
_engine = None
_engine_name = ""
_load_error = ""


class OcrError(Exception):
    """图片无法识别（格式不对、太大、引擎加载失败）。消息是给用户看的中文。"""


def _has(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def available() -> bool:
    """装了 RapidOCR（v2/v3 的 rapidocr + onnxruntime，或旧版 rapidocr_onnxruntime）。"""
    return (_has("rapidocr") and _has("onnxruntime")) or _has("rapidocr_onnxruntime")


def status() -> dict:
    return {"available": available(), "engine": _engine_name, "error": _load_error,
            "install": INSTALL_HINT}


def _load():
    """首次使用时才加载模型（约 1–2 秒、几百 MB 内存），之后复用。调用方持有 _lock。"""
    global _engine, _engine_name, _load_error
    if _engine is not None:
        return _engine
    try:
        if _has("rapidocr") and _has("onnxruntime"):
            from rapidocr import RapidOCR
            _engine, _engine_name = RapidOCR(), "rapidocr"
        else:
            from rapidocr_onnxruntime import RapidOCR
            _engine, _engine_name = RapidOCR(), "rapidocr_onnxruntime"
        _load_error = ""
    except Exception as e:                     # 依赖缺一个（opencv / numpy 版本不合）等
        _load_error = f"{type(e).__name__}: {e}"
        raise OcrError(f"OCR 组件加载失败（{_load_error}）。请重新安装：{INSTALL_HINT}") from None
    return _engine


def sniff(data: bytes) -> str:
    """按文件头判断图片格式；不是图片返回空串（不信任浏览器给的文件名 / 类型）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return ""


def _prepare(data: bytes):
    """解码成 RGB 数组：去透明通道（截图常带 alpha）、限制像素数、长边过长时等比缩小。"""
    try:
        from PIL import Image
        import numpy as np
    except ImportError:                        # RapidOCR 依赖里都有；直接交给引擎处理字节
        return data, 1.0
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception as e:
        raise OcrError(f"图片无法解码（{type(e).__name__}）") from None
    if im.width * im.height > MAX_PIXELS:
        raise OcrError("图片太大（超过 6000 万像素），请截取需要的部分")
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    scale = 1.0
    long_side = max(im.width, im.height)
    if long_side > _LONG_SIDE:
        scale = _LONG_SIDE / long_side
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))))
    # OpenCV 风格的 BGR 数组（RapidOCR 内部按 BGR 处理）
    return np.asarray(im)[:, :, ::-1].copy(), scale


def _run(img) -> list[dict]:
    """调用引擎，统一成 [{x0,y0,x1,y1,text,score}]（坐标是送进引擎的图上的像素）。"""
    with _lock:
        eng = _load()
        res = eng(img)
    out = []
    if _engine_name == "rapidocr":             # v2/v3：RapidOCROutput(boxes, txts, scores)
        boxes = getattr(res, "boxes", None)
        txts = getattr(res, "txts", None) or ()
        scores = getattr(res, "scores", None) or ()
        if boxes is None:
            return []
        triples = zip(boxes, txts, scores)
    else:                                      # 旧版：([[box, txt, score], ...], elapse)
        rows = res[0] if isinstance(res, tuple) else res
        triples = ((r[0], r[1], r[2]) for r in (rows or []))
    for box, txt, sc in triples:
        txt = str(txt or "").strip()
        if not txt:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        out.append({"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                    "text": txt, "score": float(sc)})
    return out


def group_rows(items: list[dict]) -> list[list[dict]]:
    """把文字框按视觉行分组：竖直方向重叠超过较矮者一半的算同一行；行内按 x 排序。"""
    rows: list[dict] = []
    for it in sorted(items, key=lambda r: ((r["y0"] + r["y1"]) / 2, r["x0"])):
        h = max(1.0, it["y1"] - it["y0"])
        for row in reversed(rows[-4:]):        # 只和最近几行比：页面从上往下排
            rh = max(1.0, row["y1"] - row["y0"])
            ov = min(row["y1"], it["y1"]) - max(row["y0"], it["y0"])
            if ov >= 0.5 * min(h, rh):
                row["items"].append(it)
                row["y0"], row["y1"] = min(row["y0"], it["y0"]), max(row["y1"], it["y1"])
                break
        else:
            rows.append({"y0": it["y0"], "y1": it["y1"], "items": [it]})
    rows.sort(key=lambda r: r["y0"])
    return [sorted(r["items"], key=lambda x: x["x0"]) for r in rows]


def layout_text(items: list[dict]) -> str:
    """还原成「复制网页」时的样子：一行一行；同一行里隔得远的（表格的列、标签和值）用制表符，
    挨着的用空格。投稿系统导入的解析器正是按制表符认表格列的。"""
    lines = []
    for row in group_rows(items):
        hs = [max(1.0, it["y1"] - it["y0"]) for it in row]
        ch = statistics.median(hs)
        parts = [row[0]["text"]]
        for prev, it in zip(row, row[1:]):
            gap = it["x0"] - prev["x1"]
            parts.append("\t" if gap > 1.6 * ch else " ")
            parts.append(it["text"])
        lines.append("".join(parts))
    return "\n".join(lines)


def image_to_text(data: bytes) -> dict:
    """图片字节 → {text, lines, boxes, min_score, mean_score, engine, ms}。"""
    if not data:
        raise OcrError("图片为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise OcrError("图片太大（超过 15 MB），请截取需要的部分")
    if not sniff(data):
        raise OcrError("不是图片（支持 PNG / JPEG / WebP / BMP / GIF）")
    if not available():
        raise OcrError(f"OCR 组件没有安装：{INSTALL_HINT}")
    t0 = time.perf_counter()
    img, _scale = _prepare(data)
    items = _run(img)
    scores = [it["score"] for it in items]
    return {"text": layout_text(items), "lines": len(group_rows(items)), "boxes": len(items),
            "min_score": round(min(scores), 3) if scores else None,
            "mean_score": round(sum(scores) / len(scores), 3) if scores else None,
            "engine": _engine_name, "ms": round((time.perf_counter() - t0) * 1000)}


def page_lines(png: bytes, zoom: float) -> list[dict]:
    """扫描版 PDF：把首页渲染图 OCR 成 parser._page_lines 同样结构的行（坐标换回 PDF 点）。

    字号用框高近似（框高 ≈ 1.2 × 字号），标题 / 作者的判断只看相对大小，够用。"""
    img, scale = _prepare(png)
    k = 1.0 / (zoom * scale)
    out = []
    for row in group_rows(_run(img)):
        for it in row:
            h = (it["y1"] - it["y0"]) * k
            out.append({"text": it["text"], "size": round(h / 1.2, 1),
                        "y": round(it["y0"] * k, 1), "y1": round(it["y1"] * k, 1),
                        "x0": round(it["x0"] * k, 1), "x1": round(it["x1"] * k, 1),
                        "bold": False, "nosup": it["text"], "core": it["text"],
                        "score": it["score"]})
    out.sort(key=lambda x: (x["y"], x["size"]))
    return out
