# -*- coding: utf-8 -*-
"""启动入口 / entry point:
python run.py [--port 8765] [--host 127.0.0.1] [--data-dir PATH] [--no-daily] [--open]"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

if sys.version_info < (3, 10):
    print("需要 Python 3.10 或更高版本 / Python 3.10+ is required")
    sys.exit(1)

for _s in (sys.stdout, sys.stderr):      # 非中文系统控制台不会因中文输出崩溃；重定向到文件时也逐行写出
    try:
        _s.reconfigure(errors="replace", line_buffering=True)
    except Exception:
        pass

LOCAL = ("127.0.0.1", "localhost", "::1")


def _url(host: str, port: int) -> str:
    h = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"
    return f"http://{h}:{port}"


def _bind_error(host: str, port: int):
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(fam, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return None
        except OSError as e:
            return e


def _running_instance(url: str) -> bool:
    """这个端口上已经开着一个 RecordYourResearch？"""
    try:
        with urllib.request.urlopen(url + "/api/stages", timeout=2) as r:
            return "RecordYourResearch" in (json.loads(r.read().decode("utf-8")).get("repo") or "")
    except Exception:
        return False


def _open_when_ready(host: str, port: int, url: str):
    def run():
        target = "127.0.0.1" if host in ("0.0.0.0", "::") else host
        for _ in range(60):                       # 最多等 30 秒，服务开始接受连接再打开浏览器
            try:
                with socket.create_connection((target, port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        webbrowser.open(url)
    threading.Thread(target=run, daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description="RecordYourResearch：本地论文投稿追踪 / local paper-submission tracker")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址 / bind address（默认 127.0.0.1；没有登录鉴权，不要对外开放 / no login, keep it local）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PAPER_TRACKER_PORT", "8765")),
                    help="端口 / port（默认 8765，或环境变量 PAPER_TRACKER_PORT）")
    ap.add_argument("--data-dir", help="数据目录 / data folder（默认项目下 data/，或环境变量 PAPER_TRACKER_DATA_DIR）")
    ap.add_argument("--no-daily", action="store_true",
                    help="不启动每日后台任务：同步 / 备份 / 回收站清理（或环境变量 PAPER_TRACKER_NO_DAILY=1）")
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器 / open the browser")
    a = ap.parse_args()
    if a.data_dir:
        os.environ["PAPER_TRACKER_DATA_DIR"] = os.path.abspath(a.data_dir)
    if a.no_daily:
        os.environ["PAPER_TRACKER_NO_DAILY"] = "1"
    if a.host not in LOCAL:
        print("⚠ 警告：本系统没有登录鉴权，监听非本机地址会让同一网络里的人都能访问你的数据。\n"
              "⚠ Warning: there is no login; binding to a non-local address exposes your data to the network.")
    url = _url(a.host, a.port)
    err = _bind_error(a.host, a.port)
    if err is not None:
        if _running_instance(url):
            print(f"RecordYourResearch 已经在运行 / already running → {url}")
            if a.open:
                webbrowser.open(url)
            sys.exit(0)
        print(f"无法监听 {a.host}:{a.port}（{err}）。端口可能已被占用，可用 --port 换一个，例如 python run.py --port {a.port + 1}\n"
              f"Cannot listen on {a.host}:{a.port} ({err}). Try another port, e.g. python run.py --port {a.port + 1}")
        sys.exit(1)

    from app import config
    import uvicorn

    print(f"RecordYourResearch {config.VERSION} → {url}")
    print(f"数据目录 / data folder: {config.DATA_DIR}  |  Ctrl+C 停止 / stop")
    if a.open:
        _open_when_ready(a.host, a.port, url)
    uvicorn.run("server:app", host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
