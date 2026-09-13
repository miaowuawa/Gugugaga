# -*- coding: utf-8 -*-
"""任务配置文件的本地存储。

任务配置与全局 ``config.json`` 分开保存：前者可以分享/备份任务选择，
后者仍只保存 DeepSeek Key、代理和 Server 酱等全局敏感设置。
"""
import json
import os
import re
import sys
import time


def task_config_dir() -> str:
    """返回任务配置目录（冻结版放在 exe 同级）。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 本仓库本身就是 qigumi_grabber 包目录；配置应与源码同级，
        # 不能错误落到其父目录（例如 Documents/）。
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "task_configs")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe_name(name: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", str(name or "").strip())
    value = value.strip(". ")
    return value[:80] or "未命名任务"


class TaskConfigManager:
    """读写 ``task_configs/*.json``，并对旧/手工文件保持宽容。"""

    # 这些字段属于本机全局配置或运行时内容，绝不能固化到任务文件。
    _TRANSIENT_KEYS = {
        "deepseek_api_key", "materials", "serverchan_enabled",
        # 这些值是账号私有 ID 或账号手机号，不能写进可复用的任务配置。
        "address_id", "buyer_ids", "write_off_phone", "store_choice_id",
    }

    def __init__(self, directory: str = None):
        self.directory = directory or task_config_dir()
        os.makedirs(self.directory, exist_ok=True)

    def list_configs(self) -> list:
        result = []
        for filename in os.listdir(self.directory):
            if not filename.lower().endswith(".json"):
                continue
            path = os.path.join(self.directory, filename)
            try:
                item = self.load(path)
            except (OSError, ValueError):
                continue
            item["path"] = path
            result.append(item)
        # 创建时间优先；无法解析时按文件名保证顺序稳定。
        return sorted(result, key=lambda x: (x.get("created_at") or "", x["path"]))

    def load(self, path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("配置文件根节点必须是对象")
        params = data.get("params", data)
        if not isinstance(params, dict) or not params.get("goods_id") or not params.get("sku_id"):
            raise ValueError("配置文件缺少 goods_id 或 sku_id")
        return {
            "version": data.get("version", 1),
            "name": data.get("name") or os.path.splitext(os.path.basename(path))[0],
            "created_at": data.get("created_at", ""),
            "updated_at": data.get("updated_at", ""),
            "params": dict(params),
        }

    def save(self, name: str, params: dict, path: str = None) -> str:
        safe_name = _safe_name(name)
        path = path or os.path.join(self.directory, safe_name + ".json")
        previous = {}
        if os.path.exists(path):
            try:
                previous = self.load(path)
            except (OSError, ValueError):
                pass
        clean = {k: v for k, v in dict(params).items() if k not in self._TRANSIENT_KEYS}
        data = {
            "version": 1,
            "name": safe_name,
            "created_at": previous.get("created_at") or _now(),
            "updated_at": _now(),
            "params": clean,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path

    def remove(self, path: str) -> bool:
        # 仅允许删除本任务配置目录下的普通 json，避免误删任意文件。
        absolute = os.path.abspath(path)
        root = os.path.abspath(self.directory) + os.sep
        if not absolute.startswith(root) or not absolute.lower().endswith(".json"):
            return False
        if os.path.exists(absolute):
            os.remove(absolute)
            return True
        return False
