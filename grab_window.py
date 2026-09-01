# -*- coding: utf-8 -*-
"""抢票窗口：在新终端窗口中运行，实时显示抢票进度。

用法: python -m qigumi_grabber.grab_window <env_dir> <phone> <params.json>
"""
import json
import os
import sys
import time
import webbrowser

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qigumi_grabber.account import AccountManager
from qigumi_grabber.grabber import GrabTask


def _play_success_sound():
    """抢到后播放提示音（Windows beep，循环 3 次）。"""
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 300)   # 1000Hz 300ms
            time.sleep(0.2)
            winsound.Beep(1500, 300)   # 1500Hz 300ms
            time.sleep(0.2)
    except Exception:
        try:
            print("\a")  # 终端 bell 兜底
        except Exception:
            pass


def _fmt_pay(pay_info) -> str:
    if not pay_info:
        return "（无支付信息，金额为 0）"
    if pay_info.get("alipay"):
        return "支付宝支付参数已生成"
    if pay_info.get("wechat"):
        return "微信支付参数已生成"
    return "支付参数: " + str(pay_info.get("raw"))[:200]


def main():
    if len(sys.argv) < 4:
        print("用法: python -m qigumi_grabber.grab_window <env_dir> <phone> <params.json>")
        sys.exit(1)
    env_dir, phone, params_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(params_path, "r", encoding="utf-8") as f:
        params = json.load(f)

    mgr = AccountManager(env_dir)
    account = mgr.get(phone)
    if not account.data.get("auth_token"):
        print(f"[错误] 账号 {phone} 未登录")
        sys.exit(1)

    task = GrabTask(account, params)
    task.start()

    print("=" * 60)
    print(f"  奇谷米抢票任务  goods_id={params.get('goods_id')}  sku_id={params.get('sku_id')}")
    print(f"  账号: {phone}  模式: {params.get('answer_mode', 'none')}")
    print("  按 Ctrl+C 停止任务")
    print("=" * 60)

    last_msg = ""
    try:
        while task.status in ("pending", "waiting", "answering", "grabbing"):
            snap = task.snapshot()
            if snap["msg"] != last_msg:
                print(f"[{time.strftime('%H:%M:%S')}] {snap['msg']}")
                last_msg = snap["msg"]
            if snap["status"] == "success":
                break
            time.sleep(0.2)
    except KeyboardInterrupt:
        task.stop()
        print("\n[已发送停止信号]")
        time.sleep(0.5)

    snap = task.snapshot()
    print("-" * 60)
    if snap["status"] == "success":
        # 抢到后输出完整信息
        nickname = account.data.get("nickname") or phone
        date = params.get("reservation_date") or ""
        sku_name = params.get("sku_name") or ""
        num = params.get("num") or 1
        pay_url = ""
        if snap.get("pay_info") and snap["pay_info"].get("alipay"):
            try:
                from qigumi_grabber.alipay import convert_alipay_to_h5
                pay_url = convert_alipay_to_h5(snap["pay_info"]["alipay"])
            except Exception:
                pay_url = ""
        date_part = f"{date}的" if date else ""
        print(f"手机号{phone}用户{nickname}已抢到{date_part}{sku_name}*{num}张，请及时打开链接{pay_url}链接支付！")
        if pay_url and params.get("open_pay_page", True):
            try:
                webbrowser.open(pay_url)
                print("[已尝试在浏览器中打开支付宝支付链接]")
            except Exception as e:
                print(f"[打开浏览器失败: {e}]")

        # Server酱³ 手机通知：SendKey 从全局配置读取，不写入任务参数文件
        if params.get("serverchan_enabled"):
            try:
                from qigumi_grabber.config import Config
                from qigumi_grabber.serverchan import send
                sendkey = Config().get("serverchan_sendkey", "").strip()
                if not sendkey:
                    print("[Server酱³] 发送失败: 全局 SendKey 未配置")
                else:
                    title = f"抢到啦！{sku_name or '商品'}*{num}张"
                    if pay_url:
                        desp = (f"手机号{phone}用户{nickname}已抢到{date_part}{sku_name}*{num}张\n\n"
                                f"订单号: {snap.get('order_code', '')}\n\n"
                                f"### [点击支付]({pay_url})\n\n"
                                f"支付链接: {pay_url}")
                    else:
                        desp = (f"手机号{phone}用户{nickname}已抢到{date_part}{sku_name}*{num}张\n\n"
                                f"订单号: {snap.get('order_code', '')}\n\n"
                                f"（金额为0，无需支付）")
                    r = send(sendkey, title, desp=desp,
                             short=f"订单号 {snap.get('order_code', '')}")
                    if r["ok"]:
                        print("[Server酱³] 手机通知已发送（含支付链接）")
                    else:
                        print(f"[Server酱³] 发送失败: {r['msg']}")
            except Exception as e:
                print(f"[Server酱³] 发送异常: {e}")

        # 抢到提示音：循环播放直到按下任意键
        notify_mode = params.get("notify_mode", "none")
        if notify_mode in ("beep", "audio", "tts"):
            from qigumi_grabber.notify import Notifier, wait_any_key
            n = Notifier(
                mode=notify_mode,
                audio_file=params.get("notify_audio", ""),
                text=params.get("notify_tts", "抢到了，请尽快支付"),
            )
            n.start()
            try:
                wait_any_key()
            finally:
                n.stop()
    print(f"最终状态: {snap['status']}")
    print(f"消息: {snap['msg']}")
    if snap.get("order_code"):
        print(f"订单号: {snap['order_code']}")
    if snap.get("pay_info"):
        print(f"支付: {_fmt_pay(snap['pay_info'])}")
        pay_info = snap["pay_info"]
        pay_type = pay_info.get("pay_type")
        alipay = pay_info.get("alipay")
        if alipay and not (snap["status"] == "success"):
            print("[正在将 alipay_sdk 转换为 H5 支付链接...]")
            try:
                from qigumi_grabber.alipay import convert_alipay_to_h5
                url = convert_alipay_to_h5(alipay)
                print(f"支付宝 H5 支付链接: {url}")
                if params.get("open_pay_page", True):
                    webbrowser.open(url)
                    print("[已尝试在浏览器中打开支付链接]")
            except Exception as e:
                print(f"[alipay_sdk 转换失败: {e}]")
                fallback = "https://render.alipay.com/p/s/i?scheme=" + alipay
                print(f"备用链接: {fallback}")
                if params.get("open_pay_page", True):
                    try:
                        webbrowser.open(fallback)
                    except Exception:
                        pass
        elif pay_type == "1" and pay_info.get("wechat"):
            print("微信支付参数: " + str(pay_info.get("wechat"))[:300])
    print("=" * 60)
    print("窗口将在 10 秒后自动关闭...")
    time.sleep(10)


if __name__ == "__main__":
    main()
