# -*- coding: utf-8 -*-
"""本地模型管理（app/ai.py）：下载校验、断点续传、安全解压、看门狗启停。不联网、不用真模型。"""
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import ai

HERE = os.path.dirname(os.path.abspath(__file__))
BLOB = os.urandom(300_000)
SHA = hashlib.sha256(BLOB).hexdigest()


class _Files(BaseHTTPRequestHandler):
    """/blob 支持 Range；/redir 先 302（带 X-Linked-Etag / X-Repo-Commit，像 Hugging Face）再给文件。"""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redir":
            self.send_response(302)
            self.send_header("Location", "/blob")
            self.send_header("X-Linked-Etag", f'"{SHA}"')
            self.send_header("X-Repo-Commit", "abc123")
            self.end_headers()
            return
        if self.path != "/blob":
            self.send_response(404)
            self.end_headers()
            return
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.send_response(206)
        else:
            self.send_response(200)
        body = BLOB[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def http():
    srv = HTTPServer(("127.0.0.1", 0), _Files)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def ai_dir(tmp_path, monkeypatch):
    d = tmp_path / "ai"
    monkeypatch.setattr(ai, "AI_DIR", str(d))
    monkeypatch.setattr(ai, "_STATE_FILE", str(d / "installed.json"))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    return d


def test_download_verifies_and_resumes(http, ai_dir):
    dest = str(ai_dir / "x.bin")
    os.makedirs(ai_dir, exist_ok=True)
    with open(dest + ".part", "wb") as f:          # 上次下了一半
        f.write(BLOB[:100_000])
    meta = ai._download(http + "/blob", dest, SHA, len(BLOB), "t")
    assert meta["sha256"] == SHA and open(dest, "rb").read() == BLOB
    assert not os.path.exists(dest + ".part")


def test_download_rejects_wrong_hash(http, ai_dir):
    dest = str(ai_dir / "y.bin")
    with pytest.raises(ai.AiError, match="校验失败"):
        ai._download(http + "/blob", dest, "0" * 64, len(BLOB), "t")
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")


def test_download_uses_linked_etag_when_unpinned(http, ai_dir):
    meta = ai._download(http + "/redir", str(ai_dir / "z.bin"), "", 0, "t")
    assert meta["sha256"] == SHA and meta["commit"] == "abc123"


def test_download_unreachable_host_is_a_clear_error(ai_dir):
    with pytest.raises(ai.AiError, match="下载失败"):
        ai._download("http://127.0.0.1:9/none", str(ai_dir / "n.bin"), SHA, 0, "t")


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_safe_extract_finds_server_and_blocks_traversal(tmp_path, ai_dir, monkeypatch):
    good = tmp_path / "good.zip"
    good.write_bytes(_zip([("build/bin/llama-server", b"x"), ("build/bin/llama-server.exe", b"x"),
                           ("build/bin/libllama.so", b"y")]))
    ai._safe_extract(str(good), ai._runtime_dir())
    assert ai._server_bin().endswith(("llama-server", "llama-server.exe"))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(_zip([("../../evil.txt", b"x")]))
    with pytest.raises(ai.AiError, match="可疑路径"):
        ai._safe_extract(str(bad), str(tmp_path / "out"))
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_tar(tmp_path, ai_dir):
    p = tmp_path / "rt.tar.gz"
    with tarfile.open(p, "w:gz") as t:
        data = b"#!/bin/sh\n"
        info = tarfile.TarInfo("llama-b1-bin/llama-server")
        info.size, info.mode = len(data), 0o755
        t.addfile(info, io.BytesIO(data))
    ai._safe_extract(str(p), str(tmp_path / "rt"))
    assert (tmp_path / "rt" / "llama-b1-bin" / "llama-server").exists()


def test_status_and_unknown_source(ai_dir):
    st = ai.status()
    assert st["installed"] is False and st["enabled"] is False
    assert {s["key"] for s in st["sources"]} == {"huggingface", "hf-mirror"}
    with pytest.raises(ai.AiError):
        ai.install("somewhere-else")
    with pytest.raises(ai.AiUnavailable):
        ai.ensure_running()


def test_pins_look_sane():
    for k, rt in ai.RUNTIMES.items():
        assert rt["file"].startswith(f"llama-{ai.RUNTIME_TAG}-bin-") and len(rt["sha256"]) == 64, k
    m = ai.MODELS[ai.MODEL_KEY]
    assert m["file"].endswith(".gguf") and m["license"] == "Apache-2.0"
    assert m["sha256"] == "" or len(m["sha256"]) == 64


# ---------------------------------------------------------------- 看门狗与进程生命周期（POSIX：假服务器是 shell 脚本）
posix_only = pytest.mark.skipif(os.name == "nt", reason="fake llama-server is a shell script")


def _fake_install(ai_dir, reply):
    rt = ai_dir / "runtime" / ai.RUNTIME_TAG / "bin"
    rt.mkdir(parents=True)
    exe = rt / "llama-server"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(HERE, "fake_llama_server.py")}" "$@"\n')
    exe.chmod(0o755)
    md = ai_dir / "models"
    md.mkdir(parents=True)
    (md / ai.MODELS[ai.MODEL_KEY]["file"]).write_bytes(b"gguf")
    (ai_dir / "installed.json").write_text(json.dumps({
        "runtime": {"tag": ai.RUNTIME_TAG}, "model": {"key": ai.MODEL_KEY}, "enabled": True}))
    os.environ["FAKE_LLM_REPLY"] = reply


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                   # 僵尸进程也算已结束
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return True


@posix_only
def test_start_chat_stop(ai_dir):
    _fake_install(ai_dir, '{"stage": "review"}')
    try:
        assert ai.enabled()
        out = ai.chat_json("sys", "user", {"type": "object"})
        assert out["stage"] == "review" and ai.running()
        pid = int((ai_dir / "server.pid").read_text())
        assert _alive(pid)
        ai.stop()
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.1)
        assert not _alive(pid) and not ai.running()
    finally:
        ai.stop()


@posix_only
def test_model_process_dies_with_the_app(ai_dir):
    """本程序被 kill -9 时，看门狗读到管道关闭，把 llama-server 一起结束。"""
    _fake_install(ai_dir, '{"stage": "review"}')
    code = ("import sys, time; sys.path.insert(0, %r)\n"
            "from app import ai\nai.ensure_running()\nprint('up', flush=True)\ntime.sleep(60)\n"
            % os.path.dirname(HERE))
    env = dict(os.environ, PAPER_TRACKER_AI_DIR=str(ai_dir), NO_PROXY="127.0.0.1")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env=env)
    try:
        assert p.stdout.readline().strip() == b"up"
        pid = int((ai_dir / "server.pid").read_text())
        assert _alive(pid)
        p.send_signal(signal.SIGKILL)
        p.wait(10)
        for _ in range(100):
            if not _alive(pid):
                break
            time.sleep(0.1)
        assert not _alive(pid), "llama-server outlived the app"
    finally:
        if p.poll() is None:
            p.kill()
