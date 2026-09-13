# -*- coding: utf-8 -*-
"""代理管理：提取 IP、测试可用性、有效期管理。

代理提取链接（巨量代理 postpay/getips）返回 JSON：
  {"code":200, "data":{"proxy_list":[{"ip","port","http_user","http_pass","ip_remain",...}]}}
ip_remain 为剩余秒数，过期后需重新提取。
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

    def extract(self, num: int = 1, timeout: float = 20) -> dict:
        """从提取链接获取一个代理。返回 {"ok", "proxy", "msg"}。"""
        result = self.extract_many(num=num, timeout=timeout)
        if not result["ok"]:
            return result
        return {"ok": True, "proxy": result["proxies"][0], "msg": result["msg"]}

    def extract_many(self, num: int = 1, timeout: float = 20) -> dict:
        """从提取链接获取一批代理，尽量保留服务商返回的全部地址。"""
        url = self.extract_url()
        if not url:
            return {"ok": False, "msg": "未配置代理提取链接"}
        try:
            resp = requests.get(_url_with_requested_count(url, num), timeout=timeout)
            data = resp.json()
        except Exception as e:
            return {"ok": False, "msg": f"提取代理失败: {e}"}
        if not isinstance(data, dict):
            return {"ok": False, "msg": "提取代理失败: 返回不是 JSON 对象"}

        # 闪臣短效代理：status="0" 表示成功，地址字段拼作 sever。
        if "status" in data:
            return self._parse_shanchen_response(data)

        # 巨量代理 postpay/getips 格式。
        if data.get("code") != 200:
            return {"ok": False, "msg": f"提取代理失败: code={data.get('code')} msg={data.get('msg')}"}
        plist = (data.get("data") or {}).get("proxy_list") or []
        if not plist:
            return {"ok": False, "msg": "提取代理返回空列表"}
        proxies = []
        for item in plist:
            if not isinstance(item, dict) or not item.get("ip") or not item.get("port"):
                continue
            proxies.append({
                "ip": str(item["ip"]),
                "port": str(item["port"]),
                "user": item.get("http_user"),
                "pass": item.get("http_pass"),
                "remain": int(item.get("ip_remain") or 0),
                "city": item.get("city") or "",
            })
        if not proxies:
            return {"ok": False, "msg": "提取代理返回的地址不完整"}
        return {"ok": True, "proxy": proxies[0], "proxies": proxies,
                "msg": f"提取成功（{len(proxies)} 个）"}

    @staticmethod
    def _parse_shanchen_response(data: dict) -> dict:
        """解析闪臣 get_ip 接口的 JSON 响应。"""
        status = str(data.get("status"))
        if status != "0":
            detail = data.get("info") or data.get("msg") or data.get("message") or "未知错误"
            return {"ok": False, "msg": f"闪臣提取失败: status={status} msg={detail}"}
        plist = data.get("list") or []
        if not isinstance(plist, list) or not plist or not isinstance(plist[0], dict):
            return {"ok": False, "msg": "闪臣提取失败: 返回代理列表为空"}

        proxies = []
        expire_ts = _parse_expire_timestamp(data.get("expire"))
        for item in plist:
            ip = item.get("sever") or item.get("server") or item.get("ip")
            port = item.get("port")
            if not ip or not port:
                continue
            proxy = {
                "ip": str(ip),
                "port": str(port),
                "user": item.get("http_user") or item.get("user"),
                "pass": item.get("http_pass") or item.get("pass"),
                "remain": 0,
                "city": item.get("city") or "",
                "net_type": item.get("net_type"),
            }
            item_expire_ts = _parse_expire_timestamp(item.get("expire")) or expire_ts
            if item_expire_ts is not None:
                proxy["expire_ts"] = item_expire_ts
                proxy["remain"] = max(0, int(item_expire_ts - time.time()))
            proxies.append(proxy)
        if not proxies:
            return {"ok": False, "msg": "闪臣提取失败: 返回的代理信息不完整"}
        return {"ok": True, "proxy": proxies[0], "proxies": proxies,
                "msg": f"闪臣提取成功（{len(proxies)} 个）"}

    # ---------- 测试 ----------

    def test(self, proxy: dict = None, timeout: float = 15) -> dict:
        """测试代理可用性（访问奇谷米配置接口）。返回 {"ok", "msg", "elapsed"}。"""
        p = proxy or self._current
        if not p:
            return {"ok": False, "msg": "无代理可测试"}
        proxies = self._proxies_dict(p)
        start = time.time()
        try:
            resp = requests.get("https://app.qigumi.com/api/v3/configuration/query",
                                proxies=proxies, timeout=timeout)
            ok = resp.status_code == 200
            return {"ok": ok, "msg": f"HTTP {resp.status_code}", "elapsed": time.time() - start}
        except Exception as e:
            return {"ok": False, "msg": f"测试失败: {e}", "elapsed": time.time() - start}

    def select_fastest(self, count: int = 10, timeout: float = 2.5) -> dict:
        """批量提取并并发测速，选出延迟最低的可用代理。"""
        result = self.extract_many(num=count, timeout=timeout)
        if not result["ok"]:
            return result
        candidates = result["proxies"]
        tested = []
        # 并发测试保证总耗时接近最慢的单个请求，而不是 10 倍串行耗时。
        with ThreadPoolExecutor(max_workers=min(len(candidates), count)) as executor:
            futures = {executor.submit(self.test, proxy, timeout): proxy for proxy in candidates}
            for future in as_completed(futures):
                proxy = futures[future]
                try:
                    probe = future.result()
                except Exception as e:
                    probe = {"ok": False, "msg": str(e), "elapsed": timeout}
                if probe.get("ok"):
                    proxy["latency"] = probe.get("elapsed", timeout)
                    tested.append(proxy)
        if not tested:
            return {"ok": False, "msg": f"已提取 {len(candidates)} 个代理，但测速均失败"}
        fastest = min(tested, key=lambda proxy: proxy["latency"])
        self._set_expire_ts(fastest)
        with self._lock:
            self._current = fastest
        return {"ok": True, "proxy": fastest, "tested": len(tested),
                "received": len(candidates), "msg": "已选择最低延迟代理"}

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
            self._set_expire_ts(p)
            self._current = p
            return {"ok": True, "proxy": p, "msg": "已提取新代理"}

    def refresh_if_expiring(self, threshold: int = 10) -> bool:
        """剩余有效期不足 threshold 秒时换 IP。返回是否已更换。"""
        with self._lock:
            if self._current and self._expired(self._current, threshold):
                result = self.extract()
                if result["ok"]:
                    p = result["proxy"]
                    self._set_expire_ts(p)
                    self._current = p
                    return True
            return False

    @staticmethod
    def _set_expire_ts(proxy: dict):
        """保留服务端给出的绝对过期时间；其余格式按剩余秒数推算。"""
        if proxy.get("expire_ts"):
            return
        try:
            remain = float(proxy.get("remain") or 0)
        except (TypeError, ValueError):
            remain = 0
        # 未返回有效期的服务商无法精确预刷新，按 60 秒保守处理。
        proxy["expire_ts"] = time.time() + (remain if remain > 0 else 60)

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


def _parse_expire_timestamp(value):
    """解析闪臣的本地时间到期字符串；失败时返回 None。"""
    if value in (None, "", 0, "0"):
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000
        if numeric > 0:
            return numeric
    except (TypeError, ValueError):
        pass
    raw = str(value).strip().replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return time.mktime(time.strptime(raw, fmt))
        except ValueError:
            continue
    return None


def _url_with_requested_count(url: str, count: int) -> str:
    """只改写提取链接中已有的 count/num 参数，避免破坏其他服务商链接。"""
    if count <= 1:
        return url
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    names = {name for name, _ in pairs}
    if "count" not in names and "num" not in names:
        return url
    query = [(name, str(count) if name in ("count", "num") else value)
             for name, value in pairs]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
