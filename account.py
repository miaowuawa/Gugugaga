# -*- coding: utf-8 -*-
"""账号管理：每个账号独立一个环境文件（JSON），含设备指纹/请求头标识/auth_token。"""
import json
import os
import sys
import threading
import time

from . import crypto
from .client import QiGuMiClient, parse_resp
from .headers import gen_session_id, gen_uxid, gen_visitor_id

# 真实常见手机机型档案（与 qigumi-node internal/device 一致）
DEVICE_PHONE = "phone"
DEVICE_TABLET = "tablet"

_PHONE_DEVICE_PROFILES = [
    ("M2012K11AC", "13", "1080", "2400"), ("M2102J20SG", "13", "1080", "2400"),
    ("2201123G", "13", "1080", "2400"), ("22101316C", "14", "1080", "2460"),
    ("2304FPN6DC", "14", "1220", "2712"), ("23090RA98C", "14", "1220", "2670"),
    ("23116PN5BC", "14", "1440", "3200"), ("M2007J22C", "12", "1080", "2400"),
    ("2201117TL", "12", "1080", "2340"), ("22011211C", "13", "1080", "2400"),
    ("23013RK75C", "14", "1220", "2712"), ("23127PN0CC", "14", "1440", "3200"),
    ("24031PN0DG", "14", "1440", "3200"), ("PGFM10", "14", "1080", "2412"),
    ("PHB11000", "14", "1220", "2712"), ("PJF11000", "14", "1220", "2712"),
    ("CPH2581", "14", "1080", "2412"), ("CPH2609", "14", "1080", "2412"),
    ("RMX3710", "14", "1080", "2412"), ("RMX3835", "13", "1080", "2400"),
    ("RMX3900", "14", "1080", "2412"), ("V2304A", "14", "1080", "2340"),
    ("V2309A", "14", "1220", "2712"), ("V2324A", "14", "1440", "3200"),
    ("V2302A", "14", "1080", "2400"), ("V2328A", "14", "2800", "1260"),
    ("V2306A", "14", "1080", "2340"), ("NOH-AN00", "12", "1176", "2400"),
    ("CET-AL00", "12", "1228", "2700"), ("DCO-AL00", "12", "1260", "2844"),
    ("FMR-AN00", "14", "1224", "2700"), ("PGT-AN10", "13", "1212", "2664"),
    ("CMA-AN00", "13", "1080", "2388"), ("REP-AN00", "12", "1080", "2400"),
    ("SM-S9210", "14", "1080", "2340"), ("SM-S9280", "14", "1440", "3120"),
    ("SM-A5460", "13", "1080", "2400"), ("SM-F731B", "13", "1080", "2340"),
    ("M351", "13", "1080", "2400"), ("L100", "13", "1080", "2400"),
    ("ZTE7531", "13", "1080", "2400"), ("NX729J", "13", "1080", "2400"),
    ("XT2241-2", "13", "1080", "2400"),
]

# 平板档案。device_name 对应 APK 中 Base64(Build.BRAND + " " + Build.MODEL)。
_TABLET_DEVICE_PROFILES = [
    ("23043RP34C", "Xiaomi 23043RP34C", "14", "1800", "2880"),
    ("24018RPACC", "Xiaomi 24018RPACC", "14", "1800", "2880"),
    ("SM-X810", "samsung SM-X810", "14", "1752", "2800"),
    ("SM-X910", "samsung SM-X910", "14", "1848", "2960"),
    ("DBY-W09", "HUAWEI DBY-W09", "12", "1600", "2560"),
    ("HEY-W09", "HONOR HEY-W09", "13", "1600", "2560"),
]

_APP_VERSIONS = [("491", "4.9.1")]

def _default_env_dir() -> str:
    """默认账号环境目录：PyInstaller 冻结模式放在 exe 同级 accounts/。"""
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "accounts")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "accounts")


DEFAULT_DIR = _default_env_dir()


def normalize_device_type(device_type: str) -> str:
    """将设备类型规范为 phone/tablet，拒绝悄悄生成错误指纹。"""
    value = str(device_type or DEVICE_PHONE).strip().lower()
    aliases = {"0": DEVICE_PHONE, "1": DEVICE_TABLET, "pad": DEVICE_TABLET}
    value = aliases.get(value, value)
    if value not in (DEVICE_PHONE, DEVICE_TABLET):
        raise ValueError(f"不支持的设备类型: {device_type}")
    return value


def random_device(device_type: str = DEVICE_PHONE) -> dict:
    import random
    device_type = normalize_device_type(device_type)
    if device_type == DEVICE_TABLET:
        model, device_name, osv, w, h = random.choice(_TABLET_DEVICE_PROFILES)
    else:
        model, osv, w, h = random.choice(_PHONE_DEVICE_PROFILES)
        device_name = model  # 兼容原有手机账号的指纹格式
    vc, vn = random.choice(_APP_VERSIONS)
    return {
        "device_type": device_type,
        "device_type_code": 1 if device_type == DEVICE_TABLET else 0,
        "android_version": osv,
        "device_model": model,
        "device_name": device_name,
        "screen_wid": w,
        "screen_hig": h,
        "version_code": vc,
        "version_num": vn,
    }


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Account:
    def __init__(self, env_dir: str, phone: str, region: str = "86"):
        self.env_dir = env_dir
        self.phone = phone
        self.region = region
        self.path = os.path.join(env_dir, f"{phone}.json")
        self.data = self._load()
        self.client = None
        self._lock = threading.Lock()

    # ---------- 环境文件 ----------

    def _load(self) -> dict:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"phone": self.phone, "region": self.region, "created_at": _now()}

    def save(self):
        os.makedirs(self.env_dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def ensure_env(self):
        """首次使用时生成独立设备指纹与请求头标识。"""
        changed = False
        if not self.data.get("device"):
            self.data["device"] = random_device()
            changed = True
        else:
            device = self.data["device"]
            # 兼容旧环境文件：旧版全部是手机且没有显式设备类型。
            device_type = normalize_device_type(device.get("device_type", DEVICE_PHONE))
            if device.get("device_type") != device_type:
                device["device_type"] = device_type
                changed = True
            device_type_code = 1 if device_type == DEVICE_TABLET else 0
            if device.get("device_type_code") != device_type_code:
                device["device_type_code"] = device_type_code
                changed = True
            if not device.get("device_name"):
                device["device_name"] = device.get("device_model", "")
                changed = True
        if not self.data.get("uxid"):
            self.data["uxid"] = gen_uxid()
            changed = True
        if not self.data.get("visitor_id"):
            self.data["visitor_id"] = gen_visitor_id()
            changed = True
        if not self.data.get("session_id"):
            self.data["session_id"] = gen_session_id()
            changed = True
        if changed:
            self.save()

    def set_device_type(self, device_type: str):
        """切换账号模拟设备；类型变化时生成一套自洽的新设备档案。"""
        device_type = normalize_device_type(device_type)
        current = self.data.get("device") or {}
        current_type = normalize_device_type(current.get("device_type", DEVICE_PHONE))
        if not current or current_type != device_type:
            self.data["device"] = random_device(device_type)
            self.data["updated_at"] = _now()
            self.client = None
            self.save()
        else:
            self.ensure_env()

    def device_type(self) -> str:
        device = self.data.get("device") or {}
        return normalize_device_type(device.get("device_type", DEVICE_PHONE))

    # ---------- 客户端 ----------

    def get_client(self) -> QiGuMiClient:
        self.ensure_env()
        if self.client is None:
            self.client = QiGuMiClient(
                device=self.data["device"],
                uxid=self.data.get("uxid"),
                visitor_id=self.data.get("visitor_id"),
                session_id=self.data.get("session_id"),
                auth_token=self.data.get("auth_token", ""),
            )
        return self.client

    def refresh_config(self):
        """拉取 configuration/query 更新动态 AES key（登录前先调用）。"""
        return self.get_client().fetch_config()

    # ---------- 登录 ----------

    def send_code(self) -> str:
        self.refresh_config()
        resp = self.get_client().send_validate_code(self.phone, self.region)
        r = parse_resp(resp)
        if not r["ok"]:
            return f"发送失败: code={r['code']} msg={r['msg']}"
        return "验证码已发送"

    def login_sms(self, code: str) -> str:
        resp = self.get_client().sms_login(self.phone, self.region, code)
        r = parse_resp(resp)
        if not r["ok"]:
            return f"登录失败: code={r['code']} msg={r['msg']}"
        token = self.get_client().auth_token
        if not token:
            return "登录失败: 响应未包含 auth_token"
        self.data["auth_token"] = token
        self.data["status"] = "active"
        self.data["updated_at"] = _now()
        self.save()
        self._refresh_user_info()
        return "登录成功"

    def login_password(self, password: str) -> str:
        self.refresh_config()
        resp = self.get_client().password_login(self.phone, self.region, password)
        r = parse_resp(resp)
        if not r["ok"]:
            return f"登录失败: code={r['code']} msg={r['msg']}"
        token = self.get_client().auth_token
        if not token:
            return "登录失败: 响应未包含 auth_token"
        self.data["auth_token"] = token
        self.data["status"] = "active"
        self.data["updated_at"] = _now()
        self.save()
        self._refresh_user_info()
        return "登录成功"

    def _refresh_user_info(self):
        try:
            resp = self.get_client().get_user_info()
            r = parse_resp(resp)
            if r["ok"] and isinstance(r["body"], dict):
                b = r["body"]
                self.data["uid"] = str(b.get("id") or "")
                self.data["nickname"] = b.get("nickname") or ""
                self.data["status"] = "active"
            else:
                self.data["status"] = "token_invalid"
        except Exception:
            self.data["status"] = "error"
        self.data["last_check"] = _now()
        self.save()

    def check_status(self) -> str:
        """检查登录态，返回状态描述。"""
        if not self.data.get("auth_token"):
            return "未登录"
        try:
            resp = self.get_client().get_user_info()
            r = parse_resp(resp)
            if r["ok"] and isinstance(r["body"], dict):
                b = r["body"]
                self.data["uid"] = str(b.get("id") or "")
                self.data["nickname"] = b.get("nickname") or ""
                self.data["status"] = "active"
                self.data["last_check"] = _now()
                self.save()
                return f"已登录 (UID={self.data['uid']}, 昵称={self.data['nickname']})"
            self.data["status"] = "token_invalid"
            self.data["last_check"] = _now()
            self.save()
            return f"登录已失效: code={r['code']} msg={r['msg']}"
        except Exception as e:
            return f"检查异常: {e}"

    def is_token_invalid(self, r: dict) -> bool:
        """判断接口响应是否为登录失效（code=10 请重新登录）。"""
        return r is not None and not r.get("ok") and r.get("code") == 10

    def logout(self):
        self.data["auth_token"] = ""
        self.data["status"] = "logged_out"
        self.data["updated_at"] = _now()
        self.save()

    # ---------- 展示 ----------

    def summary(self) -> str:
        status = self.data.get("status", "logged_out")
        nick = self.data.get("nickname") or "-"
        if self.data.get("auth_token"):
            return f"{self.phone} [{status}] 昵称={nick}"
        return f"{self.phone} [未登录]"


class AccountManager:
    def __init__(self, env_dir: str = None):
        self.env_dir = env_dir or DEFAULT_DIR
        os.makedirs(self.env_dir, exist_ok=True)
        self._cache = {}

    def _path(self, phone: str) -> str:
        return os.path.join(self.env_dir, f"{phone}.json")

    def list_accounts(self) -> list:
        out = []
        for name in sorted(os.listdir(self.env_dir)):
            if not name.endswith(".json"):
                continue
            phone = name[:-5]
            out.append(self.get(phone))
        return out

    def get(self, phone: str) -> Account:
        if phone not in self._cache:
            self._cache[phone] = Account(self.env_dir, phone)
        return self._cache[phone]

    def remove(self, phone: str) -> bool:
        self._cache.pop(phone, None)
        p = self._path(phone)
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
