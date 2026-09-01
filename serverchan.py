# -*- coding: utf-8 -*-
"""Server酱³ 推送通知。

参照 https://sc3.ft07.com/doc：
  接口: https://<uid>.push.ft07.com/send/<sendkey>.send
  参数: title(必填), desp 或 text(正文,支持markdown), tags, short
  sendkey 格式: SCT<uid>...（uid 内嵌在 sendkey 中，如 SCT123456Tk8...）
"""
import re

import requests

DEFAULT_TIMEOUT = 15

# sendkey 形如 SCT<uid>... 或 sctp<uid>...（uid 内嵌），不区分大小写
_SENDKEY_RE = re.compile(r"^sctp?(\d+)", re.IGNORECASE)


def parse_uid(sendkey: str) -> str:
    """从 sendkey 解析 uid。返回 uid 或空串。"""
    m = _SENDKEY_RE.match(sendkey.strip())
    if m:
        return m.group(1)
    return ""


def send(sendkey: str, title: str, desp: str = "", tags: str = "",
         short: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """发送 Server酱³ 通知。

    返回: {"ok": bool, "code": int, "msg": str, "raw": dict}
    """
    sendkey = (sendkey or "").strip()
    if not sendkey:
        return {"ok": False, "code": -1, "msg": "sendkey 未配置"}
    uid = parse_uid(sendkey)
    if not uid:
        return {"ok": False, "code": -1, "msg": f"sendkey 格式不正确: {sendkey[:12]}..."}
    url = f"https://{uid}.push.ft07.com/send/{sendkey}.send"
    data = {"title": title}
    if desp:
        data["desp"] = desp
    if tags:
        data["tags"] = tags
    if short:
        data["short"] = short
    try:
        resp = requests.post(url, data=data, timeout=timeout)
        raw = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {"text": resp.text}
    except Exception as e:
        return {"ok": False, "code": -1, "msg": f"请求异常: {e}"}
    code = raw.get("code") if isinstance(raw, dict) else None
    msg = raw.get("message") or raw.get("msg") or raw.get("text") or ""
    ok = resp.status_code == 200 and (code in (0, 200) or code is None)
    return {"ok": ok, "code": code, "msg": str(msg), "raw": raw}
