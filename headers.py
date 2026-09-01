# -*- coding: utf-8 -*-
"""请求头生成器。

逆向自 com.uxin.base.network.o0OoOo0.OooO0o0()。每个账号独立一份
（uxid/visitor_id/session_id 独立），与 qigumi-node internal/antirisk 一致。
"""
import base64
import hashlib
import random
import time
import uuid


def gen_uxid() -> str:
    """uxid = MD5(UUID)。逆向自 com.uxin.base.utils.device.OooO0O0.OooO0oO()。"""
    return hashlib.md5(str(uuid.uuid4()).encode("utf-8")).hexdigest()


def gen_visitor_id() -> str:
    """visitor_id：毫秒时间戳 + 6 位随机数，补齐 19 位。"""
    ts = int(time.time() * 1000)
    r = random.randint(100000, 999999)
    s = f"{ts}{r}"
    return (s[:19] + "0" * 19)[:19] if len(s) < 19 else s[:19]


def gen_session_id() -> str:
    return str(uuid.uuid4())


class HeaderGenerator:
    def __init__(self, uxid: str = None, visitor_id: str = None, session_id: str = None):
        self.uxid = uxid or gen_uxid()
        self.visitor_id = visitor_id or gen_visitor_id()
        self.session_id = session_id or gen_session_id()
        self._counter = 0

    def _request_id(self) -> str:
        self._counter += 1
        return f"{uuid.uuid4().hex[:8]}-{self._counter}"

    def header(self, request_page: str, auth_token: str, device: dict) -> dict:
        """生成完整请求头。auth_token 为空串时不注入 x-auth-token（登录前接口）。"""
        model = device["device_model"]
        device_type = int(device.get("device_type_code", 0))
        if device_type not in (0, 1):
            raise ValueError(f"device_type_code 只允许 0(手机) 或 1(平板): {device_type}")
        ua = (
            f"os={device['android_version']}&m={model}&s={device['screen_wid']}x{device['screen_hig']}"
            f"&c=2&vc={device['version_code']}&vn={device['version_num']}&n=qigumimall&cn=05&cm=00"
            f"&appid=11&_c=27&deviceType={device_type}&net=0&rid={self._request_id()}&isOrientation=0"
        )
        did = self.uxid[:16] if len(self.uxid) >= 16 else (self.uxid + "0" * 16)[:16]
        h = {
            "request-page": request_page,
            "ua": ua,
            "_c": "27",
            "Connection": "keep-alive",
            "Accept": "*/*",
            "device_name": base64.b64encode(
                device.get("device_name", model).encode("utf-8")
            ).decode("utf-8"),
            "identify": f"hid=Android&uxid={self.uxid}",
            "appId": "11",
            "requestId": self._request_id(),
            "isV8aAbi": "1",
            "visitor_id": self.visitor_id,
            "expand": f"sessionId={self.session_id}&did={did}",
            "ie": "0",
            "dark_mode": "0",
            "Host": "app.qigumi.com",
            "Accept-Encoding": "gzip",
            "User-Agent": "okhttp/4.9.0",
        }
        if auth_token:
            h["x-auth-token"] = auth_token
        return h
