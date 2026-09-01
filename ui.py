# -*- coding: utf-8 -*-
"""终端 UI：账号管理 + 新建抢票任务。"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from qigumi_grabber.account import AccountManager, DEVICE_PHONE, DEVICE_TABLET
from qigumi_grabber.client import parse_resp
from qigumi_grabber.config import Config
from qigumi_grabber.goods import (GOODS_TYPE_COMMON, GOODS_TYPE_TICKET, GOODS_TYPE_VOUCHER,
                                 GOODS_TYPE_WRITE_OFF, classify_goods, extract_goods_info,
                                 extract_reservation_options)
from qigumi_grabber.notify import MODE_AUDIO, MODE_BEEP, MODE_NONE, MODE_TTS, MODE_NAMES

console = Console()

PHONE_RE = re.compile(r"^1\d{10}$")
GOODS_URL_RE = re.compile(r"goods_id=(\d+)")

_cfg = Config()


# ===================================================================
# 设置（DeepSeek API Key 等）
# ===================================================================

def menu_config():
    while True:
        console.clear()
        console.print(Panel.fit("[bold cyan]设置[/bold cyan]", border_style="cyan"))
        console.print(f"DeepSeek API Key: [bold]{_cfg.get('deepseek_api_key', '') or '[dim]未配置[/dim]'}[/bold]")
        console.print(f"DeepSeek Base URL: {_cfg.get('deepseek_base_url', 'https://api.deepseek.com')}")
        console.print(f"DeepSeek Model: {_cfg.get('deepseek_model', 'deepseek-v4-flash')}")
        proxy_url = _cfg.get("proxy_extract_url", "")
        console.print(f"代理提取链接: [bold]{proxy_url[:60] + '...' if len(proxy_url) > 60 else (proxy_url or '[dim]未配置[/dim]')}[/bold]")
        serverchan_sendkey = _cfg.get("serverchan_sendkey", "")
        serverchan_status = (f"已配置（...{serverchan_sendkey[-6:]}）"
                             if serverchan_sendkey else "[dim]未配置[/dim]")
        console.print(f"Server酱³ SendKey: [bold]{serverchan_status}[/bold]")
        console.print(f"默认刷新延迟: {_cfg.get('default_refresh_delay_ms')}ms  "
                      f"默认下单延迟: {_cfg.get('default_order_delay_ms')}ms  "
                      f"默认最大重试: {_cfg.get('default_max_retries')}")
        console.print(f"默认支付方式: {'支付宝' if _cfg.get('default_pay_type') == '2' else '微信'}")
        console.print(f"配置文件: {_cfg.path}")
        console.print("[dim]1[/dim] 设置 DeepSeek API Key  "
                      "[dim]2[/dim] 默认延迟/重试  [dim]3[/dim] 默认支付方式  "
                      "[dim]4[/dim] 设置代理提取链接  [dim]5[/dim] 测试代理  "
                      "[dim]6[/dim] 设置 Server酱³ SendKey  "
                      "[dim]7[/dim] 测试 Server酱³  [dim]0[/dim] 返回")
        choice = Prompt.ask("选择", choices=["0", "1", "2", "3", "4", "5", "6", "7"], default="0")
        if choice == "0":
            return
        if choice == "1":
            key = Prompt.ask("DeepSeek API Key").strip()
            if key:
                _cfg.set("deepseek_api_key", key)
                console.print("[green]已保存[/green]")
        elif choice == "2":
            _cfg.set("default_refresh_delay_ms",
                     IntPrompt.ask("默认刷新延迟(ms)", default=int(_cfg.get("default_refresh_delay_ms", 500))))
            _cfg.set("default_order_delay_ms",
                     IntPrompt.ask("默认下单延迟(ms)", default=int(_cfg.get("default_order_delay_ms", 500))))
            _cfg.set("default_max_retries",
                     IntPrompt.ask("默认最大重试次数(0=无限)", default=int(_cfg.get("default_max_retries", 0))))
            console.print("[green]已保存[/green]")
        elif choice == "3":
            pay = Prompt.ask("默认支付方式", choices=["1", "2"], default=str(_cfg.get("default_pay_type", "2")))
            _cfg.set("default_pay_type", pay)
            console.print("[green]已保存[/green]")
        elif choice == "4":
            url = Prompt.ask("巨量代理提取链接（留空清除）").strip()
            _cfg.set("proxy_extract_url", url)
            console.print("[green]已保存[/green]")
        elif choice == "5":
            _test_proxy()
        elif choice == "6":
            _set_serverchan_sendkey()
        elif choice == "7":
            _test_serverchan()
        time.sleep(0.5)


def _set_serverchan_sendkey():
    """设置全局 Server酱³ SendKey；留空可清除已有配置。"""
    from qigumi_grabber.serverchan import parse_uid
    sk = Prompt.ask("Server酱³ SendKey（SCT 开头，留空清除）").strip()
    if sk and not parse_uid(sk):
        console.print("[red]SendKey 格式不正确，未保存[/red]")
        return
    _cfg.set("serverchan_sendkey", sk)
    console.print("[green]Server酱³ SendKey 已保存为全局设置[/green]" if sk
                  else "[green]Server酱³ SendKey 已清除[/green]")


def _test_serverchan():
    """使用全局 SendKey 发送一条 Server酱³ 测试通知。"""
    from qigumi_grabber.serverchan import send
    sk = _cfg.get("serverchan_sendkey", "").strip()
    if not sk:
        console.print("[red]未配置全局 SendKey，请先在设置中配置[/red]")
        return
    console.print("[dim]正在发送测试通知...[/dim]")
    r = send(sk, "奇谷米抢票器测试", "这是一条来自奇谷米抢票器的测试通知，收到即表示 Server酱³ 配置正常。")
    if r["ok"]:
        console.print("[green]测试通知已发送，请查看手机[/green]")
    else:
        console.print(f"[red]发送失败: {r['msg']}[/red]")


def _test_proxy():
    """测试代理：提取一个 IP 并验证可用性。"""
    from qigumi_grabber.proxy import ProxyManager
    pm = ProxyManager(_cfg)
    if not pm.is_configured():
        console.print("[red]未配置代理提取链接，请先设置[/red]")
        return
    console.print("[dim]正在提取代理...[/dim]")
    result = pm.extract()
    if not result["ok"]:
        console.print(f"[red]{result['msg']}[/red]")
        return
    p = result["proxy"]
    console.print(f"[green]提取成功: {p['ip']}:{p['port']}  城市={p.get('city')}  剩余={p.get('remain')}s[/green]")
    console.print("[dim]正在测试连通性...[/dim]")
    t = pm.test(p)
    if t["ok"]:
        console.print(f"[green]代理可用: {t['msg']}（{t.get('elapsed', 0):.2f}s）[/green]")
    else:
        console.print(f"[red]代理不可用: {t['msg']}[/red]")


def _input_phone() -> str:
    while True:
        phone = Prompt.ask("手机号").strip()
        if PHONE_RE.match(phone):
            return phone
        console.print("[red]手机号格式不正确（11 位，1 开头）[/red]")


def _input_region() -> str:
    return Prompt.ask("区号", default="86").strip() or "86"


def _input_device_type(current: str = DEVICE_PHONE) -> str:
    """登录时选择要模拟的 Android 设备类型。"""
    default = "2" if current == DEVICE_TABLET else "1"
    choice = Prompt.ask("模拟设备（1=手机，2=平板）", choices=["1", "2"], default=default)
    return DEVICE_TABLET if choice == "2" else DEVICE_PHONE


def _input_goods_id() -> int:
    while True:
        raw = Prompt.ask("商品链接或 goods_id").strip()
        m = GOODS_URL_RE.search(raw)
        if m:
            return int(m.group(1))
        if raw.isdigit():
            return int(raw)
        console.print("[red]无法识别 goods_id，请粘贴分享链接或直接输入数字 ID[/red]")


# ===================================================================
# 账号管理
# ===================================================================

def menu_accounts(mgr: AccountManager):
    while True:
        console.clear()
        console.print(Panel.fit("[bold cyan]奇谷米单机抢票器[/bold cyan] — 账号管理",
                                border_style="cyan"))
        accounts = mgr.list_accounts()
        table = Table(title=f"账号列表（{len(accounts)} 个）")
        table.add_column("#", justify="right")
        table.add_column("手机号")
        table.add_column("状态")
        table.add_column("模拟设备")
        table.add_column("昵称")
        table.add_column("UID")
        for i, acc in enumerate(accounts, 1):
            d = acc.data
            status = d.get("status", "logged_out")
            if d.get("auth_token"):
                status = f"[green]{status}[/green]"
            else:
                status = "[yellow]未登录[/yellow]"
            device_name = "平板" if acc.device_type() == DEVICE_TABLET else "手机"
            table.add_row(str(i), d.get("phone", ""), status, device_name,
                          d.get("nickname") or "-", d.get("uid") or "-")
        console.print(table)
        console.print("[dim]1[/dim] 注册/登录（短信验证码）  [dim]2[/dim] 密码登录  "
                      "[dim]3[/dim] 检查登录状态  [dim]4[/dim] 购买人管理  "
                      "[dim]5[/dim] 地址管理  [dim]6[/dim] 删除账号  [dim]0[/dim] 返回")
        choice = Prompt.ask("选择", choices=["0", "1", "2", "3", "4", "5", "6"], default="0")
        if choice == "0":
            return
        if choice == "1":
            _account_sms_login(mgr)
        elif choice == "2":
            _account_password_login(mgr)
        elif choice == "3":
            _account_check(mgr)
        elif choice == "4":
            _menu_buyers(mgr)
        elif choice == "5":
            _menu_addresses(mgr)
        elif choice == "6":
            _account_delete(mgr)


def _pick_account(mgr: AccountManager, require_login=True) -> object:
    accounts = mgr.list_accounts()
    if not accounts:
        console.print("[red]暂无账号，请先注册/登录[/red]")
        return None
    table = Table(title="选择账号")
    table.add_column("#", justify="right")
    table.add_column("手机号")
    table.add_column("状态")
    table.add_column("昵称")
    for i, acc in enumerate(accounts, 1):
        d = acc.data
        table.add_row(str(i), d.get("phone", ""), d.get("status", "logged_out"),
                      d.get("nickname") or "-")
    console.print(table)
    idx = IntPrompt.ask("选择账号编号", default=1)
    if idx < 1 or idx > len(accounts):
        console.print("[red]编号无效[/red]")
        return None
    acc = accounts[idx - 1]
    if require_login and not acc.data.get("auth_token"):
        console.print("[red]该账号未登录[/red]")
        return None
    return acc


def _relogin(mgr: AccountManager, acc) -> bool:
    """登录失效时重新登录（短信验证码）。返回是否成功。"""
    console.print(f"[yellow]账号 {acc.phone} 登录已失效，需要重新登录[/yellow]")
    acc.set_device_type(_input_device_type(acc.device_type()))
    console.print(acc.send_code())
    code = Prompt.ask("短信验证码")
    msg = acc.login_sms(code)
    console.print(msg)
    return "登录成功" in msg


def _account_sms_login(mgr: AccountManager):
    phone = _input_phone()
    region = _input_region()
    acc = mgr.get(phone)
    acc.set_device_type(_input_device_type(acc.device_type()))
    console.print(acc.send_code())
    code = Prompt.ask("短信验证码（发完告诉我，我帮你输入）")
    console.print(acc.login_sms(code))
    time.sleep(1)


def _account_password_login(mgr: AccountManager):
    phone = _input_phone()
    region = _input_region()
    acc = mgr.get(phone)
    acc.set_device_type(_input_device_type(acc.device_type()))
    password = Prompt.ask("密码", password=True)
    console.print(acc.login_password(password))
    time.sleep(1)


def _account_check(mgr: AccountManager):
    acc = _pick_account(mgr, require_login=False)
    if acc is None:
        return
    console.print(acc.check_status())
    Prompt.ask("按回车继续", default="")


def _account_delete(mgr: AccountManager):
    acc = _pick_account(mgr, require_login=False)
    if acc is None:
        return
    if Confirm.ask(f"确认删除账号 {acc.phone} 的环境文件？", default=False):
        mgr.remove(acc.phone)
        console.print("[green]已删除[/green]")
    time.sleep(1)


# ===================================================================
# 购买人管理
# ===================================================================

def _menu_buyers(mgr: AccountManager):
    acc = _pick_account(mgr)
    if acc is None:
        return
    while True:
        console.clear()
        console.print(Panel.fit(f"[bold cyan]购买人管理 — {acc.phone}[/bold cyan]",
                                border_style="cyan"))
        client = acc.get_client()
        try:
            resp = client.get_buyer_list()
            r = parse_resp(resp)
        except Exception as e:
            console.print(f"[red]获取购买人列表失败: {e}[/red]")
            return
        if not r["ok"]:
            if acc.is_token_invalid(r):
                console.print(f"[red]登录已失效: {r.get('msg')}[/red]")
                if _relogin(mgr, acc):
                    continue
                return
            console.print(f"[red]获取失败: code={r['code']} msg={r['msg']}[/red]")
            return
        body = r.get("body") or {}
        buyers = body.get("buyer_list") or []
        table = Table(title=f"实名购买人（{len(buyers)} 个）")
        table.add_column("#", justify="right")
        table.add_column("buyer_id")
        table.add_column("姓名")
        table.add_column("证件类型")
        table.add_column("选中")
        for i, b in enumerate(buyers, 1):
            table.add_row(str(i), str(b.get("buyer_id")), b.get("reg_real_name") or "-",
                          str(b.get("reg_type") or "-"),
                          "是" if b.get("is_selected") else "否")
        console.print(table)
        console.print("[dim]1[/dim] 新增购买人  [dim]2[/dim] 修改购买人  "
                      "[dim]3[/dim] 删除购买人  [dim]0[/dim] 返回")
        choice = Prompt.ask("选择", choices=["0", "1", "2", "3"], default="0")
        if choice == "0":
            return
        if choice == "1":
            _buyer_add(client)
        elif choice == "2":
            _buyer_edit(client, buyers)
        elif choice == "3":
            _buyer_del(client, buyers)


def _buyer_input() -> tuple:
    name = Prompt.ask("真实姓名").strip()
    reg_id = Prompt.ask("证件号码").strip()
    reg_type = IntPrompt.ask("证件类型（1=身份证 2=港澳 3=台湾 4=港澳通行证 5=台湾通行证 6=外国人永居 7=护照）",
                             default=1)
    return name, reg_id, reg_type


def _buyer_add(client):
    name, reg_id, reg_type = _buyer_input()
    try:
        resp = client.add_buyer(name, reg_id, reg_type)
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]新增失败: {e}[/red]")
        return
    if r["ok"]:
        console.print(f"[green]新增成功[/green] buyer_id={r.get('body', {}).get('buyer_id')}")
    else:
        console.print(f"[red]新增失败: code={r['code']} msg={r['msg']}[/red]")
    Prompt.ask("按回车继续", default="")


def _buyer_edit(client, buyers):
    if not buyers:
        console.print("[yellow]暂无购买人[/yellow]")
        return
    idx = IntPrompt.ask("选择要修改的购买人编号", default=1)
    if idx < 1 or idx > len(buyers):
        return
    b = buyers[idx - 1]
    console.print(f"当前: {b.get('reg_real_name')} 证件类型={b.get('reg_type')}")
    name = Prompt.ask("真实姓名", default=b.get("reg_real_name") or "").strip()
    reg_id = Prompt.ask("证件号码（留空保持不变）").strip()
    reg_type = IntPrompt.ask("证件类型", default=int(b.get("reg_type") or 1))
    if not reg_id:
        console.print("[red]修改需重新输入证件号码（App 端编辑复用 addBuyer 接口）[/red]")
        return
    try:
        resp = client.add_buyer(name, reg_id, reg_type)
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]修改失败: {e}[/red]")
        return
    if r["ok"]:
        console.print("[green]修改成功[/green]")
    else:
        console.print(f"[red]修改失败: code={r['code']} msg={r['msg']}[/red]")
    Prompt.ask("按回车继续", default="")


def _buyer_del(client, buyers):
    if not buyers:
        console.print("[yellow]暂无购买人[/yellow]")
        return
    idx = IntPrompt.ask("选择要删除的购买人编号", default=1)
    if idx < 1 or idx > len(buyers):
        return
    b = buyers[idx - 1]
    if not Confirm.ask(f"确认删除 {b.get('reg_real_name')}？", default=False):
        return
    try:
        resp = client.del_buyer(b.get("buyer_id"))
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]删除失败: {e}[/red]")
        return
    if r["ok"]:
        console.print("[green]删除成功[/green]")
    else:
        console.print(f"[red]删除失败: code={r['code']} msg={r['msg']}[/red]")
    Prompt.ask("按回车继续", default="")


# ===================================================================
# 地址管理
# ===================================================================

def _menu_addresses(mgr: AccountManager):
    acc = _pick_account(mgr)
    if acc is None:
        return
    while True:
        console.clear()
        console.print(Panel.fit(f"[bold cyan]收货地址管理 — {acc.phone}[/bold cyan]",
                                border_style="cyan"))
        client = acc.get_client()
        try:
            resp = client.get_address_list()
            r = parse_resp(resp)
        except Exception as e:
            console.print(f"[red]获取地址列表失败: {e}[/red]")
            return
        if not r["ok"]:
            if acc.is_token_invalid(r):
                console.print(f"[red]登录已失效: {r.get('msg')}[/red]")
                if _relogin(mgr, acc):
                    continue
                return
            console.print(f"[red]获取失败: code={r['code']} msg={r['msg']}[/red]")
            return
        body = r.get("body") or {}
        addrs = body.get("userAddressListRes") or body.get("address_list") or []
        if not isinstance(addrs, list):
            addrs = []
        table = Table(title=f"收货地址（{len(addrs)} 个）")
        table.add_column("#", justify="right")
        table.add_column("id")
        table.add_column("收货人")
        table.add_column("电话")
        table.add_column("地址")
        table.add_column("默认")
        for i, a in enumerate(addrs, 1):
            table.add_row(str(i), str(a.get("id")), a.get("user_name") or "-",
                          a.get("phone") or "-",
                          f"{a.get('province') or ''}{a.get('city') or ''}{a.get('region') or ''}{a.get('detail') or ''}",
                          "是" if a.get("is_default") else "否")
        console.print(table)
        console.print("[dim]1[/dim] 新增地址  [dim]2[/dim] 修改地址  "
                      "[dim]3[/dim] 删除地址  [dim]0[/dim] 返回")
        choice = Prompt.ask("选择", choices=["0", "1", "2", "3"], default="0")
        if choice == "0":
            return
        if choice == "1":
            _address_add(client)
        elif choice == "2":
            _address_edit(client, addrs)
        elif choice == "3":
            _address_del(client, addrs)


def _address_input() -> dict:
    user_name = Prompt.ask("收货人").strip()
    phone = Prompt.ask("手机号").strip()
    province = Prompt.ask("省").strip()
    city = Prompt.ask("市").strip()
    region = Prompt.ask("区/县").strip()
    detail = Prompt.ask("详细地址").strip()
    is_default = Confirm.ask("设为默认地址？", default=False)
    return {
        "user_name": user_name, "phone": phone,
        "province": province, "city": city, "region": region,
        "detail": detail, "is_default": 1 if is_default else 0,
    }


def _address_add(client):
    a = _address_input()
    try:
        resp = client.add_user_address(action=0, **a)
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]新增失败: {e}[/red]")
        return
    if r["ok"]:
        console.print("[green]新增成功[/green]")
    else:
        console.print(f"[red]新增失败: code={r['code']} msg={r['msg']}[/red]")
    Prompt.ask("按回车继续", default="")


def _address_edit(client, addrs):
    if not addrs:
        console.print("[yellow]暂无地址[/yellow]")
        return
    idx = IntPrompt.ask("选择要修改的地址编号", default=1)
    if idx < 1 or idx > len(addrs):
        return
    old = addrs[idx - 1]
    a = _address_input()
    try:
        resp = client.add_user_address(action=1, address_id=old.get("id"), **a)
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]修改失败: {e}[/red]")
        return
    if r["ok"]:
        console.print("[green]修改成功[/green]")
    else:
        console.print(f"[red]修改失败: code={r['code']} msg={r['msg']}[/red]")
    Prompt.ask("按回车继续", default="")


def _address_del(client, addrs):
    if not addrs:
        console.print("[yellow]暂无地址[/yellow]")
        return
    idx = IntPrompt.ask("选择要删除的地址编号", default=1)
    if idx < 1 or idx > len(addrs):
        return
    a = addrs[idx - 1]
    if not Confirm.ask(f"确认删除 {a.get('user_name')} 的地址？", default=False):
        return
    try:
        resp = client.del_user_address(a.get("id"))
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]删除失败: {e}[/red]")
        return
    if r["ok"]:
        console.print("[green]删除成功[/green]")
    else:
        console.print(f"[red]删除失败: code={r['code']} msg={r['msg']}[/red]")
    Prompt.ask("按回车继续", default="")


# ===================================================================
# 新建抢票任务
# ===================================================================

def menu_grab(mgr: AccountManager):
    acc = _pick_account(mgr)
    if acc is None:
        return
    console.clear()
    console.print(Panel.fit(f"[bold cyan]新建抢票任务 — 账号 {acc.phone}[/bold cyan]",
                            border_style="cyan"))
    goods_id = _input_goods_id()

    # 1. 获取票务信息
    console.print("[dim]正在获取商品信息...[/dim]")
    client = acc.get_client()
    try:
        resp = client.get_goods_detail(goods_id)
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]获取商品详情失败: {e}[/red]")
        return
    if not r["ok"]:
        if acc.is_token_invalid(r):
            console.print(f"[red]登录已失效: {r.get('msg')}[/red]")
            if _relogin(mgr, acc):
                return menu_grab(mgr)
            return
        console.print(f"[red]获取商品详情失败: code={r['code']} msg={r['msg']}[/red]")
        return
    body = r.get("body") or {}
    info = extract_goods_info(body)
    gtype = classify_goods(body)

    console.print(f"[bold]商品:[/bold] {info.get('name')}")
    console.print(f"[bold]类型:[/bold] {gtype}  价格: {info.get('price')}  "
                  f"售卖状态: {info.get('sell_button_status_name')}")

    # 2. 选择 SKU
    skus = info.get("sku_list") or []
    if not skus:
        console.print("[red]未找到 SKU 列表[/red]")
        return
    sku_table = Table(title="SKU 列表")
    sku_table.add_column("#", justify="right")
    sku_table.add_column("sku_id")
    sku_table.add_column("名称")
    sku_table.add_column("价格")
    sku_table.add_column("库存")
    for i, s in enumerate(skus, 1):
        sku_table.add_row(str(i), str(s.get("sku_id")), s.get("name") or "-",
                          str(s.get("price") or "-"), str(s.get("store") or "-"))
    console.print(sku_table)
    sku_idx = IntPrompt.ask("选择 SKU 编号", default=1)
    if sku_idx < 1 or sku_idx > len(skus):
        console.print("[red]SKU 编号无效[/red]")
        return
    sku_id = int(skus[sku_idx - 1]["sku_id"])
    params_sku_name = skus[sku_idx - 1].get("name") or ""
    num = IntPrompt.ask("数量", default=1)

    # 3. 按类型填写信息
    params = {
        "goods_id": goods_id, "sku_id": sku_id, "num": num,
        "address_id": 0, "store_id": 0, "venue_id": 0,
        "reservation_date": "", "reservation_quantum_id": 0,
        "buyer_ids": "", "write_off_phone": "",
        "goods_name": info.get("name") or "",
        "sku_name": params_sku_name,
    }

    if gtype == GOODS_TYPE_COMMON:
        _fill_address(client, params)
    elif gtype == GOODS_TYPE_WRITE_OFF:
        _fill_store(client, params, info)
        _fill_reservation(client, params, body)
        params["write_off_phone"] = Prompt.ask("核销手机号（核销/预约商品必填，默认账号手机号）",
                                               default=acc.phone).strip()
    elif gtype == GOODS_TYPE_TICKET:
        _fill_venue(client, params, info)
        params["write_off_phone"] = Prompt.ask("核销手机号（票务商品必填，默认账号手机号）",
                                               default=acc.phone).strip()
    elif gtype == GOODS_TYPE_VOUCHER:
        params["write_off_phone"] = Prompt.ask("核销手机号（默认账号手机号）",
                                               default=acc.phone).strip()

    # 实名制购买人
    reg_type = info.get("ticket_reg_type")
    if reg_type in (1, 2):
        params["_ticket_reg_type"] = reg_type
        _fill_buyer(client, params, goods_id)

    # 4. 抢票参数
    console.print("\n[bold cyan]抢票参数[/bold cyan]")
    cfg = _cfg
    refresh_delay = IntPrompt.ask("刷新延迟(ms)（默认 500，自动加随机抖动）",
                                  default=int(cfg.get("default_refresh_delay_ms", 500)))
    order_delay = IntPrompt.ask("下单延迟(ms)（默认 500，自动加随机抖动）",
                                default=int(cfg.get("default_order_delay_ms", 500)))
    max_retries = IntPrompt.ask("最大重试次数（0=无限重试，默认 0）",
                                default=int(cfg.get("default_max_retries", 0)))
    target = Prompt.ask("开抢时间（留空=立即开始，格式 2026-08-11 20:00:00）").strip()
    target_ts = 0
    if target:
        try:
            target_ts = time.mktime(time.strptime(target, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            console.print("[red]时间格式错误，将立即开始[/red]")
            target_ts = 0

    # 5. 答题配置
    qcfg = info.get("question_config") or {}
    is_need = qcfg.get("is_need_question")
    timing = qcfg.get("answer_buy_timing_type")
    # 已开售（售卖中/已结束/已售罄等）且账号不需要答题 → 跳过答题设置；
    # 未开售（可预约/未开售）时 is_need 可能返回 0 但开售后强制答题，需询问用户
    sbs = info.get("sell_button_status")
    on_sale = sbs not in (1, -1)  # 1=可预约 -1=未开售，其余视为已开售/已过开售点
    answer_mode = "none"
    if on_sale:
        need_answer = is_need == 1
    else:
        need_answer = True  # 未开售：即使 is_need=0 也询问（开售后可能强制答题）
    if need_answer:
        console.print(f"[yellow]该商品需要答题（时机类型={timing}）[/yellow]")
        if timing == 2:
            answer_mode = "post_auto"
            console.print("[bold]开售后答题：[/bold]")
            if not _ask_answer_config(cfg, params):
                return
        elif timing == 1:
            # 售前答题：开售前先答题，通过后等待开抢
            console.print("[yellow]该商品为售前答题（开售前需先答题，通过后等待开抢）[/yellow]")
            if Confirm.ask("是否处理售前答题？", default=True):
                answer_mode = "pre_sale"
                console.print("[bold]售前答题：[/bold]")
                if not _ask_answer_config(cfg, params):
                    return
            else:
                console.print("[yellow]不处理售前答题，将直接等待开抢后下单（可能因未答题失败）[/yellow]")
        else:
            # 预售开始前：答题时机未确定，让用户选择
            console.print("[yellow]该商品需要答题，但答题时机未确定（预售开始前常见）[/yellow]")
            choice = Prompt.ask(
                "选择答题方式（1=开售前答题 2=开售后答题 0=不处理答题）",
                choices=["1", "2", "0"], default="1")
            if choice == "1":
                answer_mode = "pre_sale"
                console.print("[bold]开售前答题：[/bold]")
                if not _ask_answer_config(cfg, params):
                    return
            elif choice == "2":
                answer_mode = "post_auto"
                console.print("[bold]开售后答题：[/bold]")
                if not _ask_answer_config(cfg, params):
                    return
            else:
                console.print("[yellow]不处理答题，将直接下单（可能因未答题失败）[/yellow]")

    params.update({
        "answer_mode": answer_mode,
        "refresh_delay_ms": refresh_delay,
        "order_delay_ms": order_delay,
        "max_retries": max_retries,
        "target_ts": target_ts,
        "pay_type": cfg.get("default_pay_type", "2"),
    })

    # 6. 确认并启动（新窗口）
    console.print("\n[bold cyan]任务参数确认[/bold cyan]")
    proxy_configured = bool(cfg.get("proxy_extract_url"))
    if proxy_configured:
        proxy_mode = Confirm.ask("使用代理？（否=无代理模式直连）", default=True)
        params["no_proxy"] = not proxy_mode
        if params.get("no_proxy"):
            console.print("[yellow]已选择无代理模式（直连），重试延迟将不低于 500ms[/yellow]")
    else:
        params["no_proxy"] = True
        console.print("[yellow]未配置代理，本次任务使用无代理模式（直连）[/yellow]")
    open_pay = Confirm.ask("抢到后自动打开支付宝支付页面？", default=True)
    params["open_pay_page"] = open_pay

    # 抢到提示音：不播放 / 哔哔响 / 播放音频文件 / TTS 朗读
    notify_choice = Prompt.ask(
        "抢到提示音（0=不播放 1=哔哔响 2=播放音频文件 3=TTS 朗读）",
        choices=["0", "1", "2", "3"], default=str(_cfg.get("default_notify_mode", "1")))
    notify_mode = {0: MODE_NONE, 1: MODE_BEEP, 2: MODE_AUDIO, 3: MODE_TTS}[int(notify_choice)]
    params["notify_mode"] = notify_mode
    _cfg.set("default_notify_mode", notify_choice)
    if notify_mode == MODE_AUDIO:
        while True:
            audio_path = Prompt.ask("音频文件路径（mp3/wav 等）").strip().strip('"').strip("'")
            if os.path.exists(audio_path):
                params["notify_audio"] = audio_path
                break
            console.print("[red]文件不存在，请重试[/red]")
    elif notify_mode == MODE_TTS:
        params["notify_tts"] = Prompt.ask("TTS 朗读文本（默认：抢到了，请尽快支付）",
                                          default="抢到了，请尽快支付").strip()

    # Server酱³ 通知：SendKey 由全局设置统一管理
    serverchan_configured = bool(cfg.get("serverchan_sendkey", "").strip())
    if serverchan_configured:
        params["serverchan_enabled"] = Confirm.ask(
            "启用 Server酱³ 手机通知？（使用全局 SendKey）", default=False)
    else:
        params["serverchan_enabled"] = False
        console.print("[dim]Server酱³ 未配置全局 SendKey，已跳过手机通知[/dim]")

    for k, v in params.items():
        console.print(f"  {k}: {v}")
    if not Confirm.ask("确认启动抢票？（回车默认不启动）", default=False):
        console.print("[yellow]已取消[/yellow]")
        Prompt.ask("按回车返回主菜单", default="")
        return
    _launch_grab_window(mgr, acc, params)
    console.print("[green]任务已启动，抢票窗口已打开，可在新窗口查看进度[/green]")
    Prompt.ask("按回车返回主菜单", default="")


def _ask_answer_config(cfg, params: dict) -> bool:
    """询问 DeepSeek API Key 与答题材料（粘贴或 txt 文件）。返回是否配置完成。"""
    api_key = cfg.get("deepseek_api_key", "")
    if api_key:
        console.print(f"[dim]已从配置读取 API Key（{api_key[:8]}...）[/dim]")
        new_key = Prompt.ask("DeepSeek API Key（回车使用已保存的）").strip()
        if new_key:
            api_key = new_key
            cfg.set("deepseek_api_key", api_key)
    else:
        api_key = Prompt.ask("DeepSeek API Key").strip()
        if api_key:
            cfg.set("deepseek_api_key", api_key)
    if not api_key:
        console.print("[red]API Key 不能为空，请先在「设置」中配置[/red]")
        return False
    params["deepseek_api_key"] = api_key

    mode = Prompt.ask("答题材料来源", choices=["paste", "file"], default="file")
    if mode == "file":
        while True:
            path = Prompt.ask("txt 文件路径").strip().strip('"').strip("'")
            if not os.path.exists(path):
                console.print("[red]文件不存在，请重试[/red]")
                continue
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                params["materials"] = f.read()
            console.print(f"[green]已读取 {len(params['materials'])} 字符[/green]")
            break
    else:
        params["materials"] = Prompt.ask("答题材料（粘贴商品介绍/规则）").strip()
    if not params.get("materials"):
        console.print("[red]答题材料不能为空[/red]")
        return False
    return True


def _fill_address(client, params: dict):
    """普通商品：选择收货地址（默认地址优先）。"""
    try:
        resp = client.get_address_list()
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]获取地址失败: {e}[/red]")
        return
    if not r["ok"]:
        console.print(f"[red]获取地址失败: {r['msg']}[/red]")
        return
    body = r.get("body") or {}
    addrs = body.get("userAddressListRes") or body.get("address_list") or []
    if not addrs:
        console.print("[yellow]无收货地址，请先在「地址管理」中添加[/yellow]")
        return
    table = Table(title="选择收货地址")
    table.add_column("#", justify="right")
    table.add_column("id")
    table.add_column("收货人")
    table.add_column("地址")
    table.add_column("默认")
    for i, a in enumerate(addrs, 1):
        table.add_row(str(i), str(a.get("id")), a.get("user_name") or "-",
                      f"{a.get('province') or ''}{a.get('city') or ''}{a.get('region') or ''}{a.get('detail') or ''}",
                      "是" if a.get("is_default") else "否")
    console.print(table)
    idx = IntPrompt.ask("选择地址编号（默认地址优先，直接回车选默认）", default=1)
    if 1 <= idx <= len(addrs):
        params["address_id"] = int(addrs[idx - 1]["id"])


def _fill_store(client, params: dict, info: dict):
    """核销/预约：选择门店。"""
    stores = info.get("store_list") or []
    if not stores:
        console.print("[yellow]未找到门店列表，将自动尝试[/yellow]")
        return
    table = Table(title="选择核销门店")
    table.add_column("#", justify="right")
    table.add_column("id")
    table.add_column("store_id")
    table.add_column("名称")
    table.add_column("地址")
    table.add_column("状态")
    for i, s in enumerate(stores, 1):
        table.add_row(str(i), str(s.get("id") or "-"), str(s.get("store_id") or "-"),
                      s.get("store_name") or "-", s.get("store_address") or "-",
                      s.get("button_status_name") or str(s.get("button_status") or "-"))
    console.print(table)
    idx = IntPrompt.ask("选择门店编号（回车自动选第一个可售门店）", default=1)
    if 1 <= idx <= len(stores):
        s = stores[idx - 1]
        params["store_id"] = int(s.get("store_id") or s.get("id"))
        params["store_choice_id"] = s.get("id")


def _fill_reservation(client, params: dict, body: dict):
    """预约类：选择日期与场次（场次与门店绑定，需按门店拉取）。"""
    store_choice_id = params.get("store_choice_id")
    options = []
    if store_choice_id:
        try:
            resp = client.choose_reservation_goods_store(
                int(params["goods_id"]), int(store_choice_id))
            r = parse_resp(resp)
            if r["ok"]:
                options = extract_reservation_options(r.get("body") or {})
        except Exception as e:
            console.print(f"[yellow]按门店拉取场次失败: {e}，使用商品详情场次[/yellow]")
    if not options:
        options = extract_reservation_options(body)
    if not options:
        return
    table = Table(title="选择预约日期")
    table.add_column("#", justify="right")
    table.add_column("日期")
    table.add_column("显示")
    table.add_column("状态")
    for i, o in enumerate(options, 1):
        table.add_row(str(i), o.get("reservation_date") or "-", o.get("show_date") or "-",
                      o.get("reservation_status_name") or str(o.get("reservation_status") or "-"))
    console.print(table)
    idx = IntPrompt.ask("选择日期编号", default=1)
    if idx < 1 or idx > len(options):
        return
    opt = options[idx - 1]
    params["reservation_date"] = opt.get("reservation_date") or ""
    quantums = opt.get("quantum_list") or []
    if not quantums:
        return
    qtable = Table(title="选择场次")
    qtable.add_column("#", justify="right")
    qtable.add_column("场次ID")
    qtable.add_column("时间")
    qtable.add_column("状态")
    for i, q in enumerate(quantums, 1):
        qtable.add_row(str(i), str(q.get("id")), q.get("reservation_time_quantum") or "-",
                       q.get("quantum_status_name") or str(q.get("quantum_status") or "-"))
    console.print(qtable)
    qidx = IntPrompt.ask("选择场次编号", default=1)
    if 1 <= qidx <= len(quantums):
        params["reservation_quantum_id"] = int(quantums[qidx - 1]["id"])


def _fill_venue(client, params: dict, info: dict):
    """票务：选择场馆（含 SKU）。"""
    venues = info.get("venue_list") or []
    if not venues:
        console.print("[yellow]未找到场馆列表，将自动尝试[/yellow]")
        return
    table = Table(title="选择场馆")
    table.add_column("#", justify="right")
    table.add_column("venue_id")
    table.add_column("场次时间")
    table.add_column("状态")
    table.add_column("余量")
    for i, v in enumerate(venues, 1):
        table.add_row(str(i), str(v.get("venue_id")), v.get("venue_show_time") or "-",
                      v.get("button_status_name") or str(v.get("button_status") or "-"),
                      str(v.get("venue_surplus_store") or "-"))
    console.print(table)
    idx = IntPrompt.ask("选择场馆编号（回车自动选第一个可售场馆）", default=1)
    if 1 <= idx <= len(venues):
        params["venue_id"] = int(venues[idx - 1]["venue_id"])


def _fill_buyer(client, params: dict, goods_id: int):
    """实名制：多选购买人。

    逆向自 SelectBuyerDialogFragment + OrderCreatePresenter：
    - 限购数 = 商品详情 pre_purchase_buyer_num（超过提示"最多选择 N 个"）
    - ticket_reg_type=1（一单一证）：必须选 1 个
    - ticket_reg_type=2（一人一证）：已选数必须等于购买张数 num，且不能超过限购
    - 已选 buyer_id 逗号拼接 → saveSelectedBuyer → buyer_ids 透传
    """
    try:
        resp = client.get_buyer_list(goods_id)
        r = parse_resp(resp)
    except Exception as e:
        console.print(f"[red]获取购买人失败: {e}[/red]")
        return
    if not r["ok"]:
        if r.get("code") == 10:
            console.print(f"[red]登录已失效: {r.get('msg')}[/red]")
            console.print("[yellow]请返回主菜单 → 账号管理 → 检查登录状态 重新登录后重试[/yellow]")
        else:
            console.print(f"[red]获取购买人失败: {r['msg']}[/red]")
        return
    body = r.get("body") or {}
    buyers = body.get("buyer_list") or []
    if not buyers:
        console.print("[yellow]无实名购买人，请先在「购买人管理」中添加[/yellow]")
        return

    # 限购数（逆向自 SelectBuyerDialogFragment.oo0oOoo0：buyer_limit）
    buyer_limit = body.get("buyer_limit") or 0
    num = int(params.get("num") or 1)
    reg_type = params.get("_ticket_reg_type")
    if reg_type == 1:
        need = 1  # 一单一证：必须选 1 个
    else:
        need = num  # 一人一证：已选数必须等于购买张数
    if buyer_limit and need > buyer_limit:
        console.print(f"[red]购买张数 {need} 超过限购 {buyer_limit} 个，请减少数量或先添加更多购买人[/red]")
        return

    table = Table(title=f"选择实名购买人（需选 {need} 个，限购 {buyer_limit or '不限'}）")
    table.add_column("#", justify="right")
    table.add_column("buyer_id")
    table.add_column("姓名")
    table.add_column("证件类型")
    for i, b in enumerate(buyers, 1):
        table.add_row(str(i), str(b.get("buyer_id")), b.get("reg_real_name") or "-",
                      str(b.get("reg_type") or "-"))
    console.print(table)
    console.print("[dim]输入编号可多选（逗号分隔，如 1,3,5），回车确认[/dim]")
    while True:
        raw = Prompt.ask(f"选择购买人编号（需选 {need} 个）").strip()
        if not raw:
            continue
        try:
            idxs = [int(x) for x in raw.replace("，", ",").split(",") if x.strip()]
        except ValueError:
            console.print("[red]编号格式错误，用逗号分隔[/red]")
            continue
        if not idxs or any(i < 1 or i > len(buyers) for i in idxs):
            console.print("[red]编号超出范围[/red]")
            continue
        if len(idxs) != need:
            console.print(f"[red]需选择 {need} 个购买人（当前 {len(idxs)} 个）[/red]")
            continue
        if buyer_limit and len(idxs) > buyer_limit:
            console.print(f"[red]超过限购 {buyer_limit} 个[/red]")
            continue
        selected = [str(buyers[i - 1]["buyer_id"]) for i in idxs]
        params["buyer_ids"] = ",".join(selected)
        console.print(f"[green]已选择 {len(selected)} 个购买人: {params['buyer_ids']}[/green]")
        return


def _launch_grab_window(mgr: AccountManager, acc, params: dict):
    """在新终端窗口启动抢票。"""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="grab_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False)
    frozen = getattr(sys, "frozen", False)
    if frozen:
        # PyInstaller 冻结模式：以 --grab-window 参数重新拉起自身
        script = ""
        args = [sys.executable, "--grab-window", mgr.env_dir, acc.phone, path]
    else:
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "qigumi_grabber", "grab_window.py")
        args = [sys.executable, script, mgr.env_dir, acc.phone, path]
    try:
        if sys.platform == "win32":
            # 直接以新控制台窗口运行（避免 start 命令的引号/标题坑）
            subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(["x-terminal-emulator", "-e", "bash", "-c",
                              f'"{sys.executable}" --grab-window "{mgr.env_dir}" "{acc.phone}" "{path}"'])
    except Exception as e:
        console.print(f"[red]启动抢票窗口失败: {e}[/red]")
        manual = " ".join(f'"{a}"' for a in args) if not frozen else f'"{sys.executable}" --grab-window "{mgr.env_dir}" "{acc.phone}" "{path}"'
        console.print(f"[dim]可手动运行: {manual}[/dim]")
        return
    console.print("[green]抢票窗口已启动[/green]")
    time.sleep(1)


# ===================================================================
# 主菜单
# ===================================================================

def main_menu(mgr: AccountManager):
    while True:
        console.clear()
        console.print(Panel.fit(
            "[bold cyan]奇谷米单机抢票器[/bold cyan]\n"
            "[dim]逆向自奇谷米 App 4.9.1 · 单机运行 · 每账号独立环境[/dim]",
            border_style="cyan"))
        console.print("[bold]1[/bold] 账号管理（注册/登录/状态/购买人/地址）")
        console.print("[bold]2[/bold] 新建抢票任务")
        console.print("[bold]3[/bold] 设置（DeepSeek API Key / 答题材料 / 默认参数）")
        console.print("[bold]4[/bold] 测试通知（哔哔响/音频/TTS）")
        console.print("[bold]0[/bold] 退出")
        choice = Prompt.ask("选择", choices=["0", "1", "2", "3", "4"], default="1")
        if choice == "0":
            console.print("再见！")
            return
        if choice == "1":
            menu_accounts(mgr)
        elif choice == "2":
            menu_grab(mgr)
        elif choice == "3":
            menu_config()
        elif choice == "4":
            menu_test_notify()


def menu_test_notify():
    """测试通知：选择模式并试听，循环播放直到按任意键。"""
    from qigumi_grabber.notify import Notifier, wait_any_key
    while True:
        console.clear()
        console.print(Panel.fit("[bold cyan]测试通知[/bold cyan]", border_style="cyan"))
        console.print("[dim]1[/dim] 哔哔响")
        console.print("[dim]2[/dim] 播放音频文件")
        console.print("[dim]3[/dim] TTS 朗读")
        console.print("[dim]0[/dim] 返回")
        choice = Prompt.ask("选择", choices=["0", "1", "2", "3"], default="0")
        if choice == "0":
            return
        mode = {1: "beep", 2: "audio", 3: "tts"}[int(choice)]
        audio_file = ""
        text = ""
        if mode == "audio":
            while True:
                audio_file = Prompt.ask("音频文件路径").strip().strip('"').strip("'")
                if os.path.exists(audio_file):
                    break
                console.print("[red]文件不存在，请重试[/red]")
        elif mode == "tts":
            text = Prompt.ask("TTS 朗读文本（默认：抢到了，请尽快支付）",
                              default="抢到了，请尽快支付").strip()
        console.print("[green]开始播放，循环直到按下任意键...[/green]")
        n = Notifier(mode=mode, audio_file=audio_file, text=text)
        n.start()
        try:
            wait_any_key()
        finally:
            n.stop()
        console.print("[green]已停止[/green]")
        time.sleep(0.5)


if __name__ == "__main__":
    from qigumi_grabber.account import AccountManager as _AM
    main_menu(_AM())
