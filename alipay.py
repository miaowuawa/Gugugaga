# -*- coding: utf-8 -*-
"""将奇谷米下单返回的 alipay_sdk 串转换成可在浏览器打开的 H5 支付链接。

移植自 qianggou-platform/alipay_convert.py 与 internal/alipayconv/convert.go：
  - 3DES（DES3）ECB + PKCS7 填充，密钥为固定 24 字节串
  - RSA（PKCS1v15）加密 3DES 密钥，附带在请求头里
  - POST 到 http://mcgw.alipay.com/gateway.do，解析返回的 res_data
  - res_data 解密后 JSON 的 form.onload.name 按单引号切片取第 8 段即为支付 URL
  - 优先支持 form.quickpay（base64(gzip) 压缩的 H5 支付页面/链接）
"""
import base64
import gzip
import json
import re

import requests
from Crypto.Cipher import DES3, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad

GATEWAY_URL = "http://mcgw.alipay.com/gateway.do"
DES_KEY = "23h4fhdilenbs741kogue1tl"
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDENksAVqDoz5SMCZq0bsZwE+I3NjrANyTTwUVSf1+ec1PfPB4tiocEpYJFCYju9MIbawR8ivECbUWjpffZq5QllJg+19CB7V5rYGcEnb/M7CS3lFF2sNcRFJUtXUUAqyR3/l7PmpxTwObZ4DLG258dhE2vFlVGXjnuLs+FI2hg4QIDAQAB
-----END PUBLIC KEY-----"""

TID = "1612f577ee44ef450cc06232719e3404a5d1d855cc6246013f296702c6bdddf5"
USER_AGENT = "Msp/9.1.5 (Android 12;Linux 4.4.146;zh_CN;http;540*960;21.0;WIFI;87699552;32617;1;000000000000000;000000000000000;8efce46e85;GOOGLE;H002;false;00:00:00:00:00:00;-1.0;-1.0;sdk-and-lite;65r7u2pfruicqrn;r2agza5c56pzmev;<unknown ssid>;02:00:00:00:00:00)"
APP_KEY = "2022002145675770"
UTDID = "87314235C8AE4E773747689735D33F58"
NEW_CLIENT_KEY = "8efcf8b134"


def _encrypt_3des(data: bytes) -> bytes:
    cipher = DES3.new(DES_KEY.encode(), DES3.MODE_ECB)
    return cipher.encrypt(pad(data, DES3.block_size))


def _decrypt_3des(data_b64: str) -> bytes:
    ct = base64.b64decode(data_b64)
    cipher = DES3.new(DES_KEY.encode(), DES3.MODE_ECB)
    return unpad(cipher.decrypt(ct), DES3.block_size)


def _rsa_encrypt(message: str) -> str:
    pub = RSA.import_key(PUBLIC_KEY_PEM.encode())
    cipher = PKCS1_v1_5.new(pub)
    return base64.b64encode(cipher.encrypt(message.encode())).decode()


def _extract_from_quickpay(quickpay: str) -> str:
    """form.quickpay：base64 → gzip 解压 → 提取支付 URL。"""
    data = base64.b64decode(quickpay)
    text = gzip.decompress(data).decode("utf-8", errors="replace").strip()
    if text.startswith("http") and "<" not in text:
        return text
    parts = text.split("'")
    if len(parts) > 7 and parts[7].startswith("http"):
        return parts[7]
    m = re.search(r"https?://[^\s\"'<>]+", text)
    if m:
        return m.group(0)
    raise ValueError(f"quickpay 解压后未找到支付链接: {text[:200]}")


def convert_alipay_to_h5(alipay_sdk: str) -> str:
    """将 alipay_sdk 串转换成 H5 支付链接。"""
    if not alipay_sdk:
        raise ValueError("alipay_sdk 为空")

    # 1. 构造请求 JSON 并 3DES ECB 加密
    json_request = {
        "tid": TID,
        "user_agent": USER_AGENT,
        "has_alipay": False,
        "has_msp_app": False,
        "external_info": alipay_sdk,
        "app_key": APP_KEY,
        "utdid": UTDID,
        "new_client_key": NEW_CLIENT_KEY,
        "action": {"type": "cashier", "method": "main"},
        "gzip": True,
    }
    encrypted_data = base64.b64encode(
        _encrypt_3des(json.dumps(json_request, ensure_ascii=False).encode())
    ).decode()

    # 2. RSA 加密 3DES 密钥，拼接 req_data
    parameter1 = _rsa_encrypt(DES_KEY)
    parameter2 = format(len(parameter1), "08X")
    parameter3 = format(len(encrypted_data), "08X")
    req_data = parameter2 + parameter1 + parameter3 + encrypted_data

    # 3. 构造外层请求体
    data = {
        "data": {
            "device": "GOOGLE-H002",
            "namespace": "com.alipay.mobilecashier",
            "api_name": "com.alipay.mcpay",
            "api_version": "4.0.2",
            "params": {"req_data": req_data},
        }
    }
    headers = {
        "Accept-Charset": "UTF-8",
        "Connection": "Keep-Alive",
        "Content-Type": "application/octet-stream;binary/octet-stream",
        "Cookie": "zone=RZ43A",
        "Cookie2": "$Version=1",
        "Host": "mcgw.alipay.com",
        "Keep-Alive": "timeout=180, max=100",
        "User-Agent": "msp",
    }
    response = requests.post(GATEWAY_URL, headers=headers, json=data,
                             verify=False, timeout=20)
    if response.status_code != 200:
        raise ValueError(f"支付宝网关 HTTP {response.status_code}: {response.text[:300]}")
    json_data = response.json()

    res_data = json_data["data"]["params"]["res_data"]
    decrypted = _decrypt_3des(res_data).decode("utf-8")
    parsed = json.loads(decrypted)

    # 4. 优先 form.quickpay（新版），回退 form.onload.name（旧版）
    form = parsed.get("form") or {}
    quickpay = form.get("quickpay") or ""
    if quickpay:
        return _extract_from_quickpay(quickpay)

    onload_name = (form.get("onload") or {}).get("name") or ""
    parts = onload_name.split("'")
    if len(parts) <= 7 or not parts[7]:
        raise ValueError(f"onload.name 切片不足 8 段: {onload_name[:200]}")
    return parts[7]
