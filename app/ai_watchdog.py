# -*- coding: utf-8 -*-
"""看门狗：替本程序启动 llama-server，并在本程序退出时把它一起结束。

用法（由 app/ai.py 调用，不需要手动运行）：
    python ai_watchdog.py <pid 文件> <llama-server 路径> [参数 ...]

本程序通过管道连着看门狗的 stdin，但从不往里写。本程序正常退出、被关掉或被强制结束时，
操作系统都会关闭管道写端，看门狗读到 EOF，就结束 llama-server。所以模型进程不会变成
没人管、一直占着内存的「孤儿」。这个脚本只用标准库，也不导入项目里的其他模块。
"""
import os
import subprocess
import sys
import threading


def _stop(child: subprocess.Popen):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(8)
        except subprocess.TimeoutExpired:
            child.kill()


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: ai_watchdog.py <pidfile> <server> [args...]", file=sys.stderr)
        return 2
    pidfile, cmd = sys.argv[1], sys.argv[2:]
    flags = 0x08000000 if os.name == "nt" else 0          # CREATE_NO_WINDOW：Windows 上不弹黑窗口
    child = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, cwd=os.path.dirname(cmd[0]) or None,
                             creationflags=flags)
    try:
        with open(pidfile, "w", encoding="ascii") as f:
            f.write(str(child.pid))
    except OSError:
        pass

    def watch():
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except Exception:
            pass
        _stop(child)                                      # EOF：本程序退出了或要求停止

    threading.Thread(target=watch, daemon=True).start()
    try:
        return child.wait()
    except KeyboardInterrupt:
        _stop(child)
        return 130


if __name__ == "__main__":
    sys.exit(main())
