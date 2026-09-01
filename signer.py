"""兼容旧版调用的签名辅助函数。

当前单机版主流程使用 crypto.py 中的 Signer；本文件保留原仓库的轻量签名
接口，方便旧脚本继续调用。
"""
import hashlib


def encrypt_salt(raw: str) -> str:
    result = []
    for ch in raw:
        c = ord(ch)
        if ch.isalpha():
            result.append(chr(0x9B - c) if ch.isupper() else chr(0xDB - c))
        elif ch.isdigit():
            result.append(chr(0x69 - c))
        else:
            result.append(ch)
    return "".join(result)


SALT = encrypt_salt("6zF0R8;/")


def sign(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hashlib.md5((SALT + query).encode("utf-8")).hexdigest()
