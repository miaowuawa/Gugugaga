# -*- coding: utf-8 -*-
"""签名与敏感字段加密。

逆向自 com.uxin.mall.utils.OooOo00（签名）与 com.uxin.base.utils.encrypt.OooO00o（AES）。
AES key/iv 不是硬编码：启动时从 GET configuration/query 拉取（acd 明文 / cfg RSA 解密），
与 qigumi-node internal/crypto/config.go 一致。
"""
import base64
import hashlib

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad

# 签名盐：encrypt_salt("6zF0R8;/") -> "3aU9I1;/"
_SALT = "3aU9I1;/"

# RSA 公钥三段（逆向自 com.qigumi.mall.app.OooOOOO.OooOOoo）：
#   hpe = RSA_PREFIX + res[0x7f12080e] + RSA_SUFFIX
_RSA_PREFIX = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsCV/iJTAs3cjNuyTneuzX6iI4K8o1hJhoX/uC0MgwzafVQRceihBKsnmtxG1ZJdtCkj1sqXdCKvK1gAcw6Zjp7g2MgxgZZTuqu1dFDrRkuJXTrgL+3pl4e"
_RSA_MIDDLE = "zHz6K/mk1iFUAkuWMzBVTbjgPnklb7s2sMyxTjUDP6feycGBIHPRtqAoSymBg9raRS8onRj6KfvJXwz8FLv1nRL71El4RHZLw/D47x4tBwSr7wXkbay712NTneGzjNIFTmBsIk73IyWFAIFchbmzcFW2k7lTpwqMX"
_RSA_SUFFIX = "L1t/qJNO5ChWHXMaIBjF3Yf0y5jQaHMe3gxtwWdZ692Rfbspj2l6HidiQKrySEwIDAQAB"

# 兜底 AES key/iv（仅当 configuration/query 拉取失败时使用，与 App 内置一致）
_FALLBACK_KEY = "obDgEZjhwBNMaNVf"
_FALLBACK_IV = "93x0ue23c2c9h8km"


def encrypt_salt(raw: str) -> str:
    """encrypt_salt：字母/数字按 0x9B/0xDB/0x69 取补，其余原样。"""
    out = []
    for ch in raw:
        c = ord(ch)
        if ch.isalpha():
            out.append(chr(0x9B - c) if ch.isupper() else chr(0xDB - c))
        elif ch.isdigit():
            out.append(chr(0x69 - c))
        else:
            out.append(ch)
    return "".join(out)


def _val_str(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def sign(params: dict) -> str:
    """计算签名：跳过 sign/null，按 key 排序拼 k=v&k=v，md5(salt + query)。"""
    query = "&".join(
        f"{k}={_val_str(v)}"
        for k, v in sorted(params.items())
        if k != "sign" and v is not None
    )
    return hashlib.md5((_SALT + query).encode("utf-8")).hexdigest()


def aes_encrypt(plaintext: str, key: str, iv: str) -> str:
    """AES/CBC/PKCS5Padding + Base64(NO_WRAP)，用于手机号/密码/证件号/地址电话。"""
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    encrypted = cipher.encrypt(pad(plaintext.encode("utf-8"), AES.block_size))
    return base64.b64encode(encrypted).decode("utf-8")


class Signer:
    """签名器：AES key/iv 可动态更新（configuration/query）。"""

    def __init__(self):
        self.aes_key = _FALLBACK_KEY
        self.aes_iv = _FALLBACK_IV

    def set_key_iv(self, key: str, iv: str):
        if key:
            self.aes_key = key
        if iv:
            self.aes_iv = iv

    def encrypt_sensitive(self, plaintext: str) -> str:
        return aes_encrypt(plaintext, self.aes_key, self.aes_iv)

    def sign(self, params: dict) -> str:
        return sign(params)


def rsa_public_decrypt(ciphertext_b64: str) -> str:
    """RSA/ECB/PKCS1Padding 公钥解密（Java cipher.init(DECRYPT_MODE, publicKey)）。

    对应 o00OOOOo.OooO00o.OooO0OO：用硬编码公钥解密 configuration/query 返回的 cfg。
    Java 的 DECRYPT_MODE+publicKey 在 Python 无原生支持，需做原始 RSA 运算
    m = c^e mod n 后手动移除 PKCS1 padding（与 qigumi-node internal/crypto/rsa.go 一致）。
    """
    pub_der = base64.b64decode(_RSA_PREFIX + _RSA_MIDDLE + _RSA_SUFFIX)
    pub = RSA.import_key(pub_der)
    n, e = pub.n, pub.e
    ct = base64.b64decode(ciphertext_b64)
    if len(ct) != n.bit_length() // 8:
        raise ValueError(f"RSA 密文长度 {len(ct)} 与公钥位数不匹配")
    m = pow(int.from_bytes(ct, "big"), e, n)
    plain = m.to_bytes(n.bit_length() // 8, "big")

    # 移除 PKCS1 padding（type 1: 00 01 FF..FF 00 msg；type 2: 00 02 random 00 msg）
    if len(plain) < 11 or plain[0] != 0x00:
        raise ValueError("PKCS1 padding 格式错误（非法头部）")
    if plain[1] == 0x01:
        i = 2
        while i < len(plain) and plain[i] == 0xFF:
            i += 1
        if i == len(plain) or plain[i] != 0x00 or i < 10:
            raise ValueError("PKCS1 type 1 padding 格式错误")
    elif plain[1] == 0x02:
        i = 2
        while i < len(plain) and plain[i] != 0x00:
            i += 1
        if i == len(plain) or i < 10:
            raise ValueError("PKCS1 type 2 padding 格式错误")
    else:
        raise ValueError("PKCS1 padding 格式错误（期望 type 1 或 type 2）")
    return plain[i + 1:].decode("utf-8")
