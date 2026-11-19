# -*- coding: utf-8 -*-
"""本机凭据保护（OpenReview 密码等）。

- Windows：DPAPI（CryptProtectData）加密，密文与当前 Windows 用户绑定，数据库文件被复制、
  备份或拷到别的电脑都解不开。存储格式 "dpapi:<base64>"。
- macOS / Linux：存进系统钥匙串（keyring：macOS 钥匙串 / Linux Secret Service），
  数据库里只留 "keyring:<键名>" 标记。
- 没有可用的安全存储时：新绑定账号直接拒绝（不会悄悄存明文）。旧版明文原样兼容读取。
"""
import base64
import ctypes
import sys

PREFIX = "dpapi:"
KEYRING_PREFIX = "keyring:"
KEYRING_SERVICE = "RecordYourResearch"
_ENTROPY = b"paper-tracker/credentials/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class VaultError(RuntimeError):
    pass


if sys.platform == "win32":
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for _fn in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
        _fn.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
        _fn.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes) -> bytes:
        inp, _keep1 = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _BLOB()
        if not fn(ctypes.byref(inp), None, ctypes.byref(ent), None, None,
                  _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
            raise VaultError(f"DPAPI 调用失败（错误码 {ctypes.get_last_error()}）")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _keyring():
    """可用的系统钥匙串（keyring 包 + 真正的后端），没有就返回 None。"""
    try:
        import keyring
        from keyring.backends import fail
        kr = keyring.get_keyring()
        backends = list(getattr(kr, "backends", None) or [kr])     # ChainerBackend：看实际用的第一个
        first = backends[0] if backends else kr
        for b in (kr, first):
            mod, name = type(b).__module__, type(b).__name__
            # 失败后端、空后端，以及 keyrings.alt 的明文 / 文件后端都不算安全存储
            if isinstance(b, fail.Keyring) or "fail" in mod or mod.startswith("keyrings.alt") \
                    or "Plaintext" in name or "Null" in name:
                return None
        return keyring
    except Exception:
        return None


def key_for(channel: str) -> str:
    """钥匙串条目名：按数据目录区分（开发 / 演示用的数据目录绑定账号不会覆盖真实数据目录的密码）。"""
    import hashlib
    from . import config
    return f"{channel}:{hashlib.sha256(config.DATA_DIR.encode('utf-8')).hexdigest()[:12]}"


def backend() -> str:
    """dpapi / keyring / none"""
    if sys.platform == "win32":
        return "dpapi"
    return "keyring" if _keyring() is not None else "none"


def available() -> bool:
    return backend() != "none"


def is_protected(stored: str) -> bool:
    s = str(stored or "")
    return s.startswith(PREFIX) or s.startswith(KEYRING_PREFIX)


def storage_of(stored: str) -> str:
    s = str(stored or "")
    if s.startswith(PREFIX):
        return "dpapi"
    if s.startswith(KEYRING_PREFIX):
        return "keyring"
    return "plaintext" if s else ""


def protect(plain: str, force: bool = False, strict: bool = False, key: str = "openreview") -> str:
    """明文 → 存储串（Windows：DPAPI 密文；其他系统：存进钥匙串，返回 keyring:<key>）。
    force：用户新输入的密码一律保护（哪怕它本身以 dpapi: 开头）；
    strict：没有安全存储或保护失败时抛 VaultError（绑定账号时用，绝不悄悄存明文）；
    否则失败时原样返回、下次启动再试。"""
    plain = str(plain or "")
    if not plain or (is_protected(plain) and not force):
        return plain
    if sys.platform == "win32":
        try:
            return PREFIX + base64.b64encode(_call(_crypt32.CryptProtectData,
                                                   plain.encode("utf-8"))).decode("ascii")
        except Exception as e:
            if strict:
                raise VaultError(f"Windows 凭据保护（DPAPI）加密失败：{type(e).__name__}") from e
            return plain
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, key, plain)
            return KEYRING_PREFIX + key
        except Exception as e:
            if strict:
                raise VaultError(f"写入系统钥匙串失败：{type(e).__name__}") from e
            return plain
    if strict:
        raise VaultError("没有可用的系统钥匙串（macOS 钥匙串 / Linux Secret Service，"
                         "请先 pip install keyring 并确保桌面钥匙串服务在运行）")
    return plain


def forget(stored: str):
    """解除绑定时删掉钥匙串里的密码（DPAPI 密文随数据库行一起删，无需处理）。"""
    s = str(stored or "")
    if s.startswith(KEYRING_PREFIX):
        kr = _keyring()
        if kr is not None:
            try:
                kr.delete_password(KEYRING_SERVICE, s[len(KEYRING_PREFIX):])
            except Exception:
                pass


def reveal(stored: str) -> str:
    """存储串 → 明文；旧版明文原样返回。解不开（别的电脑 / 别的用户 / 钥匙串里没有）抛 VaultError。"""
    stored = str(stored or "")
    if not is_protected(stored):
        return stored
    if stored.startswith(KEYRING_PREFIX):
        kr = _keyring()
        if kr is None:
            raise VaultError("本机没有可用的系统钥匙串，读不到保存的密码")
        try:
            v = kr.get_password(KEYRING_SERVICE, stored[len(KEYRING_PREFIX):])
        except Exception as e:
            raise VaultError(f"读取系统钥匙串失败：{type(e).__name__}") from e
        if v is None:
            raise VaultError("系统钥匙串里找不到保存的密码")
        return v
    if sys.platform != "win32":
        raise VaultError("加密凭据只能在保存它的 Windows 电脑上解密")
    try:
        raw = base64.b64decode(stored[len(PREFIX):])
        return _call(_crypt32.CryptUnprotectData, raw).decode("utf-8")
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"凭据解密失败：{type(e).__name__}") from e
