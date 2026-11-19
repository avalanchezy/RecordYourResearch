# -*- coding: utf-8 -*-
"""【实验功能，默认关闭】爱思唯尔中国「稿件查询」服务的一键查稿。

非官方、与爱思唯尔（Elsevier）无任何关联；接口可能随时变化或失效，使用前请自行确认符合
爱思唯尔网站的使用条款。只在你点击时查询、每篇稿件 24 小时最多一次；用你自己的稿件编号、
通讯作者姓名拼音和期刊缩写；一旦遇到验证码或风控就停下，提示你去官方页面人工查询后
用「粘贴官方结果」记录（绝不绕过验证码）。请求使用如实的 User-Agent。

推荐的爱思唯尔用法是「粘贴官方查询结果自动解析」（server.py 的 /elsevier-paste），不调用任何接口。
"""
import os
import time
import uuid

import httpx

from . import config
from .stages import normalize, status_key

BASE = "https://dsp-api.elsevier.cn"
QUERY_V2 = "/st/manuscript/v2/query-by-author"
JOURNALS = "/st/manuscript/candidate-journals"

_client = httpx.Client(timeout=20, headers={"User-Agent": config.USER_AGENT,
                                            "Accept": "application/json"})

# 每个安装固定一个随机设备标识（首次使用时生成并存在数据目录里；不随请求变化，不含任何个人信息）
_DEVICE = {"id": ""}


def _device_id() -> str:
    if _DEVICE["id"]:
        return _DEVICE["id"]
    p = os.path.join(config.DATA_DIR, "elsevier_device_id")
    try:
        with open(p, encoding="ascii") as f:
            v = f.read().strip()
    except OSError:
        v = ""
    if not (len(v) == 32 and all(ch in "0123456789abcdef" for ch in v)):
        v = uuid.uuid4().hex
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            with open(p, "w", encoding="ascii") as f:
                f.write(v)
        except OSError:
            pass
    _DEVICE["id"] = v
    return v

# 验证码 / 风控相关的返回码：遇到即停止，转人工
CAPTCHA_CODES = {900003, 900004, 900005, 900006, 900008}

# 官方状态的中文名（用于展示）
STATUS_ZH = {
    "submitted to journal": "已提交",
    "with editor": "编辑初审",
    "under review": "正在评审",
    "required reviews complete": "所需审稿意见完成",
    "required reviews completed": "所需审稿意见完成",
    "review complete": "同行评审完成",
    "review completed": "同行评审完成",
    "complete": "审稿完成",
}
# 查表用的归一键（大小写、各类破折号、多余空白不敏感；与 stages.normalize 同一口径）
_STATUS_ZH_KEYS = {status_key(k): v for k, v in STATUS_ZH.items()}

# 风控层（WAF / 鉴权 / 限流）直接拦下的 HTTP 状态：拦截页常是 HTML，必须先于 JSON 解析判断
BLOCK_STATUS = (401, 403, 412, 429)


def _headers() -> dict:
    return {"X-Device-Id": _device_id(), "Content-Type": "application/json"}


def _has_cjk(s: str) -> bool:
    return any(0x4E00 <= ord(ch) <= 0x9FFF for ch in s)


def status_zh(raw: str) -> str:
    """官方英文状态 → 官方中文名（STATUS_ZH）；原文本身是中文则原样返回；查不到返回空串。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    if _has_cjk(s):
        return s
    return _STATUS_ZH_KEYS.get(status_key(s), "")


def _text(v) -> str:
    """状态字段可能是字符串 / 数字，也可能是 {name/label/value…} 小对象。"""
    if isinstance(v, dict):
        for k in ("name", "label", "value", "text", "status", "desc", "description"):
            x = v.get(k)
            if x and not isinstance(x, (dict, list, bool)):
                return str(x).strip()
        return ""
    if v is None or isinstance(v, (list, bool)):
        return ""
    return str(v).strip()


def _extract_stage(result) -> tuple[str, dict]:
    """从 result 结构提取 (raw, detail)。"""
    if isinstance(result, dict):
        raw = ""
        for k in ("status", "currentStatus", "manuscriptStatus", "stage", "stageName"):
            raw = _text(result.get(k))
            if raw:
                break
        keys = ("manuscriptNumber", "journalTitle", "statusDate", "submitDate",
                "currentStage", "stage", "stageName", "status")
        detail = {k: result[k] for k in keys if result.get(k)}
        if not raw:  # 状态可能嵌在 stageHistory 等列表里
            for nested in ("stageHistory", "flowStatus", "statusList", "stageList"):
                seq = result.get(nested)
                if isinstance(seq, list) and seq:
                    last = seq[-1] if isinstance(seq[-1], dict) else {}
                    raw = _text(last.get("status")) or _text(last.get("stage")) or _text(last.get("name"))
                    if raw:
                        break
        return raw, detail
    if isinstance(result, list) and result:
        first = result[0] if isinstance(result[0], dict) else {}
        raw = _text(first.get("status")) or _text(first.get("currentStatus"))
        detail = {k: first[k] for k in
                  ("manuscriptNumber", "journalTitle", "statusDate", "submitDate") if first.get(k)}
        return raw, detail
    return "", {}


def _json_or_none(resp):
    try:
        return resp.json()
    except Exception:
        return None


def _code_int(code):
    try:
        return int(str(code).strip())
    except (TypeError, ValueError):
        return None


def _is_captcha(code, msg: str, success: bool = False) -> bool:
    """验证码挑战：官方 code（字符串或数字均可）或失败消息里带 captcha / 验证码 / 滑块。"""
    if _code_int(code) in CAPTCHA_CODES:
        return True
    m = msg or ""
    return (not success) and ("CAPTCHA" in m.upper() or "验证码" in m or "滑块" in m)


_CAPTCHA_ERROR = "官方要求图形验证码：请到爱思唯尔官方页面人工查询，再用「粘贴官方结果」记录"


def query(manuscript_number: str, last_name: str, first_name: str,
          journal_acronym: str) -> dict:
    """核心查询：稿件编号 + 通讯作者姓名拼音 + 期刊缩写。"""
    if not (manuscript_number and journal_acronym):
        return {"ok": False, "error": "缺少稿件编号或期刊缩写（爱思唯尔查询凭据）"}
    t0 = time.time()
    try:
        resp = _client.post(BASE + QUERY_V2, json={
            "manuscriptNumber": manuscript_number.strip(),
            "lastName": (last_name or "").strip().upper(),
            "firstName": (first_name or "").strip().upper(),
            "journalAcronym": journal_acronym.strip().upper(),
        }, headers=_headers())
    except Exception as e:
        return {"ok": False, "error": f"网络错误：{type(e).__name__}: {e}",
                "latency_ms": int((time.time() - t0) * 1000)}
    latency = int((time.time() - t0) * 1000)
    out = _interpret(resp, latency)
    out["credentials"] = {
        "manuscript_number": manuscript_number.strip(),
        "pinyin": f"{(first_name or '').strip().upper()} {(last_name or '').strip().upper()}",
        "journal_acronym": journal_acronym.strip().upper(),
        "official_web": "https://webapps.elsevier.cn/st-wechat/manuscript-query",
    }
    return out


def _interpret(resp: httpx.Response, latency: int) -> dict:
    """统一响应结构 {success, code, message, result} 的归一解析。

    顺序：HTTP 风控状态码（先于 JSON 解析）→ 非 JSON 页面 → 验证码 → 业务错误 → 状态归一。
    官方状态认不出时返回 ok=False（附 raw），绝不默认记成「外审中」写进时间线。
    """
    status = int(getattr(resp, "status_code", 0) or 0)

    # ① 风控 / 鉴权 / 限流：拦截页常是 HTML，不能因 JSON 解析失败丢掉「转人工」引导
    if status in BLOCK_STATUS:
        data = _json_or_none(resp)
        code = data.get("code") if isinstance(data, dict) else None
        msg = str(data.get("message") or "") if isinstance(data, dict) else ""
        out = {"ok": False, "latency_ms": latency, "need_manual": True,
               "error": f"官方风控拦截（HTTP {status}），请到官方通道人工查询后手动登记状态"}
        if code is not None:
            out["code"] = code
        if _is_captcha(code, msg):
            out["error"] = _CAPTCHA_ERROR
        return out

    data = _json_or_none(resp)
    if data is None:
        if status < 500:
            # 2xx/3xx/4xx 却不是 JSON：WAF 质询页 / 跳转页 / 接口改版 → 转人工
            return {"ok": False, "latency_ms": latency, "need_manual": True,
                    "error": f"官方返回了非 JSON 页面（HTTP {status}，可能是风控拦截或接口改版），"
                             f"请到官方通道人工查询后手动登记状态"}
        return {"ok": False, "latency_ms": latency,
                "error": f"响应解析失败（HTTP {status}）"}
    if not isinstance(data, dict):
        return {"ok": False, "latency_ms": latency,
                "error": f"响应结构异常（HTTP {status}）"}

    code = data.get("code")
    success = bool(data.get("success"))
    msg = str(data.get("message") or "")

    # ② 业务层：验证码挑战 → 转人工（不绕过）
    if _is_captcha(code, msg, success):
        return {"ok": False, "latency_ms": latency, "need_manual": True, "code": code,
                "error": _CAPTCHA_ERROR}

    if not success:
        hint = "请核对稿件编号、通讯作者姓名拼音与期刊缩写是否与投稿系统一致"
        return {"ok": False, "latency_ms": latency, "code": code,
                "error": (msg or f"官方返回错误 code={code}") + f"（{hint}）"}

    raw, detail = _extract_stage(data.get("result"))
    if not raw:
        return {"ok": False, "latency_ms": latency, "code": code,
                "error": "官方未返回稿件状态——编号/拼音可能不匹配，或该稿件不在这个查询服务的覆盖范围内"}
    raw_zh = status_zh(raw)
    stage = normalize(raw) or (normalize(raw_zh) if raw_zh else None)
    if not stage:
        out = {"ok": False, "latency_ms": latency, "code": code, "raw": raw, "detail": detail,
               "error": f"官方返回了未识别的状态：{raw}（未写入，请手动登记）"}
        if raw_zh:
            out["raw_zh"] = raw_zh
        return out
    out = {"ok": True, "latency_ms": latency, "code": code, "stage": stage,
           "raw": raw, "detail": detail}
    if raw_zh:
        out["raw_zh"] = raw_zh
    return out


def candidate_journals() -> dict:
    """期刊缩写字典（用于 journalAcronym 校验）。"""
    try:
        resp = _client.get(BASE + JOURNALS, headers=_headers())
        resp.raise_for_status()
        data = resp.json()
        items = data.get("result") if isinstance(data, dict) else data
        if isinstance(items, dict):
            items = items.get("journals") or items.get("list") or []
        if not isinstance(items, list):
            items = []
        out = []
        for j in items:
            if isinstance(j, dict):
                out.append({
                    "acronym": j.get("acronym") or j.get("journalAcronym") or j.get("code"),
                    "name": j.get("journalName") or j.get("name") or j.get("title"),
                })
        ok = bool(data.get("success", True)) if isinstance(data, dict) else True
        return {"ok": ok, "journals": [x for x in out if x.get("acronym")]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
