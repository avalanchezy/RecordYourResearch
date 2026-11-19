# -*- coding: utf-8 -*-
"""测试环境：临时数据目录、关闭后台任务、拦截一切外网请求。只用合成数据。"""
import os
import shutil
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="ryr-test-")
os.environ["PAPER_TRACKER_DATA_DIR"] = os.path.join(_TMP, "data")
os.environ["PAPER_TRACKER_TESTING"] = "1"       # 允许 TestClient 的 Host: testserver
os.environ["PAPER_TRACKER_NO_DAILY"] = "1"
sys.path.insert(0, ROOT)

import httpx  # noqa: E402


def _blocked(self, request):
    raise RuntimeError(f"network access blocked in tests: {request.url.host}")


httpx.HTTPTransport.handle_request = _blocked      # TestClient 用自己的 transport，不受影响


class FakeKeyring:
    """macOS / Linux 测试用的内存钥匙串（CI 里没有真实的钥匙串服务）。"""

    def __init__(self):
        self.store = {}

    def set_password(self, service, user, pw):
        self.store[(service, user)] = pw

    def get_password(self, service, user):
        return self.store.get((service, user))

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


@pytest.fixture(scope="session")
def server_mod():
    import server
    return server


@pytest.fixture()
def client(server_mod):
    from fastapi.testclient import TestClient
    return TestClient(server_mod.app)


@pytest.fixture()
def secure_store(monkeypatch):
    """保证有可用的安全存储：Windows 用真实 DPAPI，其他系统换成内存钥匙串。"""
    from app import vault
    if sys.platform != "win32":
        kr = FakeKeyring()
        monkeypatch.setattr(vault, "_keyring", lambda: kr)
        return kr
    return None


@pytest.fixture()
def fake_openreview(monkeypatch, server_mod):
    """把 OpenReview 登录 / 拉取投稿换成假的；subs 列表由测试自己填。"""
    state = {"subs": [], "logins": 0}
    o = server_mod.openreview

    def get_token(email, pw):
        state["logins"] += 1
        state["last_password"] = pw
        return "fake-token"

    monkeypatch.setattr(o, "get_token", get_token)
    monkeypatch.setattr(o, "get_my_profile", lambda tok: {"id": "~Test_User1", "names": "Test User"})
    monkeypatch.setattr(o, "fetch_my_submissions", lambda tok, pid: [dict(s) for s in state["subs"]])
    return state


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
