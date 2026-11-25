# -*- coding: utf-8 -*-
"""列出模型仓库里的 GGUF 文件、大小和 SHA-256，以及当前提交号：升级 app/ai.py 里的固定版本时用。

    python tools/ai_probe.py [仓库名]
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import ai  # noqa: E402


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else ai.MODELS[ai.MODEL_KEY]["repo"]
    url = f"https://huggingface.co/api/models/{repo}?blobs=true"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ryr-probe"}), timeout=30) as r:
        d = json.load(r)
    print("repo:", repo, "commit:", d.get("sha"), "lastModified:", d.get("lastModified"))
    for s in d.get("siblings", []):
        if s["rfilename"].endswith(".gguf"):
            lfs = s.get("lfs") or {}
            print(f"  {s['rfilename']:48} {s.get('size') or lfs.get('size')}  {lfs.get('sha256')}")


if __name__ == "__main__":
    main()
