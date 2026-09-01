# -*- coding: utf-8 -*-
"""巨量代理管理：提取 IP、测试可用性、有效期管理。

代理提取链接（巨量代理 postpay/getips）返回 JSON：
  {"code":200, "data":{"proxy_list":[{"ip","port","http_user","http_pass","ip_remain",...}]}}
ip_remain 为剩余秒数，过期后需重新提取。
"""
import json
import threading
import time

import requests

from .config import Config


class ProxyManager:
    def __init__(self, cfg: Config = None):
        self.cfg = cfg or Config()
        self._lock = threading.Lock()
        self._current = None  # {"ip","port","user","pass","expire_ts","city"}

    # ---------- 配置 ----------

    def extract_url(self) -> str:
        return self.cfg.get("proxy_extract_url", "")

    def is_configured(self) -> bool:
        return bool(self.extract_url())

    # ---------- 提取 ----------

    def extract(self, num: int = 1) -> dict:
        """从提取链接获取代理。返回 {"ok", "proxy", "msg"}。"""
        url = self.extract_url()
        if not url:
            return {"ok": False, "msg": "未配置代理提取链接"}
        try:
            resp = requests.get(url, timeout=20)
            data = resp.json()
        except Exception as e:
            return {"ok": False, "msg": f"提取代理失败: {e}"}
        if data.get("code") != 200:
            return {"ok": False, "msg": f"提取代理失败: code={data.get('code')} msg={data.get('msg')}"}
        plist = (data.get("data") or {}).get("proxy_list") or []
        if not plist:
            return {"ok": False, "msg": "提取代理返回空列表"}
        p = plist[0]
        proxy = {
            "ip": p.get("ip"),
            "port": str(p.get("port")),
            "user": p.get("http_user"),
            "pass": p.get("http_pass"),
            "remain": int(p.get("ip_remain") or 0),
            "city": p.get("city") or "",
        }
        if not proxy["ip"] or not proxy["port"]:
            return {"ok": False, "msg": f"代理信息不完整: {p}"}
        return {"ok": True, "proxy": proxy, "msg": "提取成功"}

    # ---------- 测试 ----------

    def test(self, proxy: dict = None) -> dict:
        """测试代理可用性（访问奇谷米配置接口）。返回 {"ok", "msg", "elapsed"}。"""
        p = proxy or self._current
        if not p:
            return {"ok": False, "msg": "无代理可测试"}
        proxies = self._proxies_dict(p)
        start = time.time()
        try:
            resp = requests.get("https://app.qigumi.com/api/v3/configuration/query",
                                proxies=proxies, timeout=15)
            ok = resp.status_code == 200
            return {"ok": ok, "msg": f"HTTP {resp.status_code}", "elapsed": time.time() - start}
        except Exception as e:
            return {"ok": False, "msg": f"测试失败: {e}", "elapsed": time.time() - start}

    # ---------- 使用 ----------

    def get(self, force_refresh: bool = False) -> dict:
        """获取当前可用代理；无/过期/强制时重新提取。返回 {"ok", "proxy", "msg"}。"""
        with self._lock:
            if not force_refresh and self._current and not self._expired(self._current):
                return {"ok": True, "proxy": self._current, "msg": "使用当前代理"}
            result = self.extract()
            if not result["ok"]:
                return result
            p = result["proxy"]
            p["expire_ts"] = time.time() + max(p.get("remain", 0) - 5, 10)
            self._current = p
            return {"ok": True, "proxy": p, "msg": "已提取新代理"}

    def refresh_if_expiring(self, threshold: int = 10) -> bool:
        """剩余有效期不足 threshold 秒时换 IP。返回是否已更换。"""
        with self._lock:
            if self._current and self._expired(self._current, threshold):
                result = self.extract()
                if result["ok"]:
                    p = result["proxy"]
                    p["expire_ts"] = time.time() + max(p.get("remain", 0) - 5, 10)
                    self._current = p
                    return True
            return False

    def current(self) -> dict:
        return self._current

    def _expired(self, p: dict, threshold: int = 0) -> bool:
        return time.time() + threshold >= p.get("expire_ts", 0)

    def _proxies_dict(self, p: dict) -> dict:
        auth = ""
        if p.get("user") and p.get("pass"):
            auth = f"{p['user']}:{p['pass']}@"
        url = f"http://{auth}{p['ip']}:{p['port']}"
        return {"http": url, "https": url}

    def requests_proxies(self) -> dict:
        """返回当前代理的 requests 格式（无代理时返回 None）。"""
        if not self._current:
            return None
        return self._proxies_dict(self._current)
