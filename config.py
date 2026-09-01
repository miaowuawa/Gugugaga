# -*- coding: utf-8 -*-
"""工具配置文件：保存 DeepSeek API Key、默认参数等。

配置文件路径: <项目根>/config.json（可通过环境变量 QIGUMI_GRABBER_CONFIG 覆盖）。
"""
import json
import os
import sys

DEFAULT_CONFIG = {
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-v4-flash",
    "proxy_extract_url": "",  # 巨量代理提取链接（全局，每个任务复用）
    "serverchan_sendkey": "",  # Server酱³ SendKey（全局，每个任务复用）
    "default_refresh_delay_ms": 500,
    "default_order_delay_ms": 500,
    "default_max_retries": 0,
    "default_pay_type": "2",
}


def config_path() -> str:
    override = os.environ.get("QIGUMI_GRABBER_CONFIG")
    if override:
        return override
    # PyInstaller 冻结模式：config.json 放在 exe 同级目录（__file__ 指向临时解压目录）
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "config.json")


class Config:
    def __init__(self, path: str = None):
        self.path = path or config_path()
        self.data = dict(DEFAULT_CONFIG)
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except Exception:
                pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value
        self.save()
