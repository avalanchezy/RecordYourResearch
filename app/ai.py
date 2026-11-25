# -*- coding: utf-8 -*-
"""可选的本地小模型（默认关闭）：在网页「设置」里一键下载、校验、部署，只在本机 127.0.0.1 上运行。

- 推理引擎：llama.cpp 官方发布的预编译 llama-server（CPU 版）。版本固定，下载后按 SHA-256 校验。
- 模型：Qwen3.5-0.8B（Apache-2.0）的 GGUF 量化文件，版本固定、SHA-256 校验；
  可以从 Hugging Face 或国内镜像下载（文件哈希固定，所以镜像也安全）。
- 所有文件都放在数据目录 data/ai/ 下，设置里可以一键删除。
- llama-server 由看门狗子进程（ai_watchdog.py）启动：本程序一退出（哪怕被强制结束），模型进程也跟着结束。
- 闲置 20 分钟自动停止释放内存，下次用到时再自动启动（0.8B 冷启动只要几秒）。

模型只给「建议」：先用 JSON schema 约束输出格式，再由调用方（app/smart.py）逐项对照原文和
规则校验，最后由用户确认才写入。稿件内容只发给本机的 llama-server，不出电脑。
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from app import config

# ---------------------------------------------------------------- 固定版本（升级时连同哈希一起改）
RUNTIME_TAG = "b12380"
_RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/{file}"
RUNTIMES = {
    "win-x64":   {"file": "llama-b12380-bin-win-cpu-x64.zip", "size": 25124554,
                  "sha256": "ea968ba15d183896b8b023897e6b4d84ecd66fa05eed720e74d3060e4e1e833b"},
    "linux-x64": {"file": "llama-b12380-bin-ubuntu-x64.tar.gz", "size": 23333771,
                  "sha256": "211fd3fae48f2d72e740b9aa72f961a6a8f0e7b73f0d0ed8ff498cb3b74a6233"},
    "mac-arm64": {"file": "llama-b12380-bin-macos-arm64.tar.gz", "size": 16922615,
                  "sha256": "1889ef442edf0bb6113f428bb0341b87f3ad26a02ef5275ebfd092f443463cca"},
    "mac-x64":   {"file": "llama-b12380-bin-macos-x64.tar.gz", "size": 12669542,
                  "sha256": "3e17ddc045809f64ed1ca7f78f27515bfd8ccb39f1c9af50a36085d073ed2d1c"},
}

MODEL_KEY = "qwen3.5-0.8b"
MODELS = {
    MODEL_KEY: {
        "label": "Qwen3.5-0.8B (Q8_0)", "license": "Apache-2.0",
        "repo": "unsloth/Qwen3.5-0.8B-GGUF",
        "revision": "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0",   # 固定到这个提交（不会被悄悄换掉）
        "file": "Qwen3.5-0.8B-Q8_0.gguf",
        "size": 811843840,
        "sha256": "0ad885ffd4bb022fc4f0d33a3308fa108ef8613159d3b3a67e23abca056b7a6c",
        # 升级：python tools/ai_probe.py 列出文件和哈希；sha256 留空时改用 Hugging Face 的 X-Linked-Etag 校验
    },
}
SOURCES = {                          # 模型下载源（文件按 SHA-256 校验，镜像与官方等价）
    "huggingface": {"label": "Hugging Face", "base": "https://huggingface.co"},
    "hf-mirror": {"label": "hf-mirror.com（国内镜像）", "base": "https://hf-mirror.com"},
}

CTX = 8192                           # 上下文：来信截到前几千字，够用
IDLE_STOP_SECS = 20 * 60             # 闲置多久自动停
START_TIMEOUT = 120                  # 启动（加载模型）最长等待
REQUEST_TIMEOUT = 180                # 单次推理最长等待（CPU 慢机器也留够）

# 可用环境变量 PAPER_TRACKER_AI_DIR 把模型放到别的盘（CI 也用它缓存下载）。
# 演示模式（--demo）和正常模式共用项目 data/ai/：模型不是个人数据，不必为演示再下一遍 0.84 GB。
_DEFAULT_AI_DIR = (os.path.join(config.BASE_DIR, "data", "ai") if config.DEMO
                   else os.path.join(config.DATA_DIR, "ai"))
AI_DIR = os.path.abspath(os.environ.get("PAPER_TRACKER_AI_DIR") or _DEFAULT_AI_DIR)
_STATE_FILE = os.path.join(AI_DIR, "installed.json")


class AiError(Exception):
    """给用户看的中文错误。"""


class AiUnavailable(AiError):
    """没安装 / 没启用 / 平台不支持。"""


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------- 状态
_lock = threading.RLock()            # 保护下面这些全局状态
_chat_lock = threading.Lock()        # llama-server 只开一个槽：推理请求排队
_start_lock = threading.Lock()       # 同时来两个请求也只启动一个 llama-server
_progress = {"phase": "idle", "step": "", "done": 0, "total": 0, "error": "", "source": ""}
_cancel = threading.Event()
_worker: threading.Thread | None = None
_proc: subprocess.Popen | None = None
_port = 0
_last_used = 0.0
_starting = False
_start_error = ""


def platform_key() -> str:
    """llama.cpp 预编译包对应的平台；不支持返回空串。"""
    m = platform.machine().lower()
    x64 = m in ("x86_64", "amd64", "x64")
    arm = m in ("arm64", "aarch64")
    if sys.platform == "win32" and x64:
        return "win-x64"
    if sys.platform.startswith("linux") and x64:
        return "linux-x64"
    if sys.platform == "darwin":
        return "mac-arm64" if arm else ("mac-x64" if x64 else "")
    return ""


def _load_state() -> dict:
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(d: dict):
    os.makedirs(AI_DIR, exist_ok=True)
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _STATE_FILE)


def _runtime_dir() -> str:
    return os.path.join(AI_DIR, "runtime", RUNTIME_TAG)


def _model_path(key: str = MODEL_KEY) -> str:
    return os.path.join(AI_DIR, "models", MODELS[key]["file"])


def _server_bin() -> str:
    """解压目录里找 llama-server（不同版本的包里可能套一层子目录）。"""
    root = _runtime_dir()
    names = ("llama-server.exe",) if os.name == "nt" else ("llama-server",)
    for dirpath, _dirs, files in os.walk(root):
        for n in names:
            if n in files:
                return os.path.join(dirpath, n)
    return ""


def installed() -> dict:
    st = _load_state()
    rt = st.get("runtime") or {}
    md = st.get("model") or {}
    rt_ok = rt.get("tag") == RUNTIME_TAG and bool(_server_bin())
    md_ok = md.get("key") == MODEL_KEY and os.path.isfile(_model_path())
    return {"runtime": rt_ok, "model": md_ok, "both": rt_ok and md_ok,
            "model_sha256": md.get("sha256", ""), "model_commit": md.get("commit", "")}


def enabled() -> bool:
    return bool(_load_state().get("enabled")) and installed()["both"]


def _disk_bytes() -> int:
    total = 0
    for dirpath, _d, files in os.walk(AI_DIR):
        for n in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, n))
            except OSError:
                pass
    return total


def running() -> bool:
    with _lock:
        return _proc is not None and _proc.poll() is None and _port > 0


def status() -> dict:
    inst = installed()
    m = MODELS[MODEL_KEY]
    with _lock:
        prog = dict(_progress)
        busy = _worker is not None and _worker.is_alive()
        starting, start_err = _starting, _start_error
    pk = platform_key()
    return {
        "supported": bool(pk), "platform": pk or f"{sys.platform}/{platform.machine()}",
        "enabled": bool(_load_state().get("enabled")) and inst["both"],
        "installed": inst["both"], "runtime_installed": inst["runtime"], "model_installed": inst["model"],
        "running": running(), "starting": starting, "start_error": start_err,
        "busy": busy, "progress": prog,
        "model": {"key": MODEL_KEY, "label": m["label"], "license": m["license"], "repo": m["repo"],
                  "size": m["size"] or None},
        "runtime": {"tag": RUNTIME_TAG, "size": RUNTIMES.get(pk, {}).get("size")},
        "sources": [{"key": k, "label": v["label"]} for k, v in SOURCES.items()],
        "disk_bytes": _disk_bytes() if os.path.isdir(AI_DIR) else 0,
        "dir": AI_DIR,
    }


# ---------------------------------------------------------------- 下载、校验、解压
class _Redirects(urllib.request.HTTPRedirectHandler):
    """记下重定向前的响应头：Hugging Face 把文件哈希（X-Linked-Etag）和提交号放在 302 上。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        seen = getattr(req, "_seen", None)
        if seen is not None:
            seen.append(dict(headers.items()))
            if new is not None:
                new._seen = seen
        return new


def _opener(local: bool = False):
    handlers = [_Redirects()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))      # 连本机 llama-server 不走代理
    return urllib.request.build_opener(*handlers)


def _set_progress(**kw):
    with _lock:
        _progress.update(kw)


def _download(url: str, dest: str, sha256: str, size: int, step: str) -> dict:
    """流式下载到 dest.part，可断点续传；算 SHA-256，校验通过才改名成 dest。

    sha256 为空时改用服务器给的 X-Linked-Etag（Hugging Face 的 LFS 文件哈希），并把它返回，
    便于之后固定到代码里。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    h = hashlib.sha256()
    have = 0
    if os.path.exists(part):
        with open(part, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                have += len(chunk)
    headers = {"User-Agent": config.USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    req._seen = []
    _set_progress(step=step, done=have, total=size or 0)
    try:
        resp = _opener().open(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:                       # .part 已经完整（或比文件还长）：从头再来
            os.remove(part)
            return _download(url, dest, sha256, size, step)
        raise AiError(f"下载失败：HTTP {e.code}（{url.split('/')[2]}）") from None
    except (urllib.error.URLError, OSError) as e:
        raise AiError(f"下载失败：连不上 {url.split('/')[2]}（{getattr(e, 'reason', e)}）。"
                      "检查网络，或在设置里换一个下载源") from None
    seen = req._seen
    meta = {}
    for hd in seen + [dict(resp.headers.items())]:
        low = {k.lower(): v for k, v in hd.items()}
        if low.get("x-linked-etag"):
            meta["linked_sha256"] = low["x-linked-etag"].strip('"').strip().lower()
        if low.get("x-repo-commit"):
            meta["commit"] = low["x-repo-commit"].strip()
    with resp:
        if have and resp.status != 206:                  # 服务器不支持续传：从头下
            h, have = hashlib.sha256(), 0
            mode = "wb"
        else:
            mode = "ab" if have else "wb"
        clen = int(resp.headers.get("Content-Length") or 0)
        total = (have + clen) if clen else (size or 0)
        _set_progress(done=have, total=total)
        with open(part, mode) as f:
            while True:
                if _cancel.is_set():
                    raise _Cancelled()
                buf = resp.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                h.update(buf)
                have += len(buf)
                _set_progress(done=have)
    digest = h.hexdigest()
    expect = (sha256 or "").lower()
    if not expect:
        linked = meta.get("linked_sha256", "")
        expect = linked if re.fullmatch(r"[0-9a-f]{64}", linked) else ""
    if not expect:
        os.remove(part)
        raise AiError("下载源没有提供文件哈希，无法校验完整性，已放弃。请换一个下载源")
    if digest != expect:
        os.remove(part)
        raise AiError("文件校验失败（下载损坏或被篡改），已删除，请重试或换一个下载源")
    if size and have != size and sha256:
        os.remove(part)
        raise AiError("文件大小不对，已删除，请重试")
    os.replace(part, dest)
    meta["sha256"] = digest
    meta["size"] = have
    return meta


def _safe_extract(archive: str, dest: str):
    """解压到临时目录再整体换上；拒绝绝对路径、..、指向外面的链接（防路径穿越）。"""
    tmp = dest + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    root = os.path.realpath(tmp)

    def inside(p: str) -> bool:
        rp = os.path.realpath(os.path.join(root, p))
        return rp == root or rp.startswith(root + os.sep)

    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            for m in z.infolist():
                if not inside(m.filename):
                    raise AiError("引擎压缩包里有可疑路径，已停止")
            z.extractall(tmp)
    else:
        with tarfile.open(archive, "r:gz") as t:
            if hasattr(tarfile, "data_filter"):
                t.extractall(tmp, filter="data")
            else:                                          # 旧版 Python：手动检查
                for m in t.getmembers():
                    if not inside(m.name) or m.isdev() or (
                            (m.issym() or m.islnk()) and not inside(os.path.join(os.path.dirname(m.name), m.linkname))):
                        raise AiError("引擎压缩包里有可疑路径，已停止")
                t.extractall(tmp)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    for dirpath, _d, files in os.walk(dest):
        for n in files:
            if n.startswith("llama-server"):
                p = os.path.join(dirpath, n)
                os.chmod(p, os.stat(p).st_mode | 0o111)


def _install(source: str):
    try:
        pk = platform_key()
        if not pk:
            raise AiUnavailable(f"当前系统（{sys.platform} / {platform.machine()}）没有现成的引擎包")
        st = _load_state()
        # ① 推理引擎
        if not installed()["runtime"]:
            rt = RUNTIMES[pk]
            arch = os.path.join(AI_DIR, "downloads", rt["file"])
            _set_progress(phase="runtime")
            _download(_RUNTIME_URL.format(tag=RUNTIME_TAG, file=rt["file"]), arch, rt["sha256"],
                      rt["size"], "下载推理引擎 llama.cpp")
            _set_progress(phase="extract", step="解压推理引擎")
            _safe_extract(arch, _runtime_dir())
            if not _server_bin():
                raise AiError("引擎包里没有找到 llama-server")
            os.remove(arch)
            st["runtime"] = {"tag": RUNTIME_TAG, "platform": pk}
            _save_state(st)
        # ② 模型
        if not installed()["model"]:
            m = MODELS[MODEL_KEY]
            base = SOURCES.get(source, SOURCES["huggingface"])["base"]
            url = f"{base}/{m['repo']}/resolve/{m['revision']}/{m['file']}"
            _set_progress(phase="model")
            meta = _download(url, _model_path(), m["sha256"], m["size"], f"下载模型 {m['label']}")
            st["model"] = {"key": MODEL_KEY, "file": m["file"], "sha256": meta["sha256"],
                           "size": meta["size"], "commit": meta.get("commit", ""), "source": source}
            _save_state(st)
        st["enabled"] = True
        _save_state(st)
        _set_progress(phase="ready", step="已就绪", error="")
    except _Cancelled:
        _set_progress(phase="idle", step="已取消（下次会接着下）", error="")
    except AiError as e:
        _set_progress(phase="error", error=str(e))
    except Exception as e:                                 # 磁盘满、权限等
        _set_progress(phase="error", error=f"{type(e).__name__}: {e}")
    finally:
        _cancel.clear()


def install(source: str = "huggingface") -> dict:
    """后台开始下载 + 部署；立即返回状态，前端轮询 status() 看进度。"""
    global _worker
    if source not in SOURCES:
        raise AiError("未知的下载源")
    with _lock:
        if _worker is not None and _worker.is_alive():
            return status()
        _cancel.clear()
        _progress.update(phase="starting", step="准备下载", done=0, total=0, error="", source=source)
        _worker = threading.Thread(target=_install, args=(source,), daemon=True, name="ai-install")
        _worker.start()
    return status()


def cancel() -> dict:
    _cancel.set()
    return status()


def set_enabled(on: bool) -> dict:
    st = _load_state()
    st["enabled"] = bool(on)
    _save_state(st)
    if not on:
        stop()
    return status()


def remove() -> dict:
    """停掉模型、删掉 data/ai/ 下的全部文件（只删本程序下载的东西）。"""
    with _lock:
        if _worker is not None and _worker.is_alive():
            raise AiError("正在下载，先取消再删除")
    stop()
    shutil.rmtree(AI_DIR, ignore_errors=True)
    _set_progress(phase="idle", step="", done=0, total=0, error="")
    return status()


# ---------------------------------------------------------------- 运行 llama-server
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _threads() -> int:
    return max(1, min(8, (os.cpu_count() or 4) // 2))


def _health(port: int) -> bool:
    try:
        with _opener(local=True).open(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return r.status == 200 and json.loads(r.read() or b"{}").get("status") == "ok"
    except Exception:
        return False


def server_args(model_path: str, port: int) -> list[str]:
    return ["-m", model_path, "--host", "127.0.0.1", "--port", str(port),
            "-c", str(CTX), "-np", "1", "-t", str(_threads())]


def ensure_running() -> int:
    """确保 llama-server 在跑，返回端口；没装 / 没启用抛 AiUnavailable，启动失败抛 AiError。"""
    global _proc, _port, _starting, _start_error, _last_used
    if not enabled():
        raise AiUnavailable("智能助手没有启用：在「渠道查询 → 智能助手」里下载并启用本地模型")
    with _lock:
        if running():
            _last_used = time.time()
            return _port
    with _start_lock:
        with _lock:
            if running():
                _last_used = time.time()
                return _port
            _starting, _start_error = True, ""
        return _start()


def _start() -> int:
    global _proc, _port, _starting, _start_error, _last_used
    try:
        exe = _server_bin()
        port = _free_port()
        os.makedirs(AI_DIR, exist_ok=True)
        log = open(os.path.join(AI_DIR, "server.log"), "ab")
        pidfile = os.path.join(AI_DIR, "server.pid")
        env = dict(os.environ)
        bindir = os.path.dirname(exe)
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            env[var] = bindir + (os.pathsep + env[var] if env.get(var) else "")
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_watchdog.py"),
               pidfile, exe] + server_args(_model_path(), port)
        flags = 0x08000000 if os.name == "nt" else 0
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=log, env=env,
                             creationflags=flags)
        log.close()
        t0 = time.time()
        while time.time() - t0 < START_TIMEOUT:
            if p.poll() is not None:
                raise AiError("本地模型启动失败（详见 data/ai/server.log）")
            if _health(port):
                break
            time.sleep(0.5)
        else:
            _kill(p)
            raise AiError("本地模型启动超时（详见 data/ai/server.log）")
        with _lock:
            _proc, _port, _last_used = p, port, time.time()
        return port
    except AiError as e:
        with _lock:
            _start_error = str(e)
        raise
    except OSError as e:
        with _lock:
            _start_error = f"{type(e).__name__}: {e}"
        raise AiError(f"本地模型启动失败：{e}") from None
    finally:
        with _lock:
            _starting = False


def _kill(p: subprocess.Popen):
    """先关管道让看门狗体面地结束 llama-server；不行再强制。"""
    try:
        if p.stdin:
            p.stdin.close()
    except OSError:
        pass
    try:
        p.wait(10)
        return
    except subprocess.TimeoutExpired:
        pass
    pid = 0
    try:
        with open(os.path.join(AI_DIR, "server.pid"), encoding="ascii") as f:
            pid = int(f.read().strip() or 0)
    except (OSError, ValueError):
        pass
    p.kill()
    if pid:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=0x08000000)
            else:
                os.kill(pid, 9)
        except Exception:
            pass


def stop():
    global _proc, _port
    with _lock:
        p, _proc, _port = _proc, None, 0
    if p is not None and p.poll() is None:
        _kill(p)


atexit.register(stop)


def _idle_watch():
    while True:
        time.sleep(60)
        with _lock:
            idle = running() and time.time() - _last_used > IDLE_STOP_SECS and not _chat_lock.locked()
        if idle:
            stop()


threading.Thread(target=_idle_watch, daemon=True, name="ai-idle").start()


# ---------------------------------------------------------------- 推理
def chat_json(system: str, user: str, schema: dict, max_tokens: int = 256) -> dict:
    """一次结构化推理：输出被 JSON schema 约束（llama.cpp 按语法采样，保证能解析），温度 0。"""
    global _last_used
    port = ensure_running()
    body = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0, "top_k": 1, "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with _chat_lock:
        t0 = time.time()
        try:
            with _opener(local=True).open(req, timeout=REQUEST_TIMEOUT) as r:
                resp = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise AiError(f"本地模型返回错误：HTTP {e.code}") from None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise AiError(f"本地模型没有响应（{getattr(e, 'reason', e)}）") from None
        finally:
            _last_used = time.time()
    try:
        content = resp["choices"][0]["message"]["content"]
        out = json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError):
        raise AiError("本地模型的输出无法解析") from None
    if not isinstance(out, dict):
        raise AiError("本地模型的输出格式不对")
    out["_ms"] = round((time.time() - t0) * 1000)
    return out


def model_tag() -> str:
    """写进事件来源的模型标识。"""
    return f"{MODEL_KEY}@{RUNTIME_TAG}"
