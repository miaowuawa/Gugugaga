# -*- coding: utf-8 -*-
"""抢票引擎：等待开抢 -> (可选)开售后答题 -> 下单重试 -> 支付链接。

下单链路: commonDetail -> orderConfirm -> createOrder -> payOrder
延迟策略：刷新延迟默认 500ms、下单延迟默认 500ms，均加随机抖动。
答题：最短用时可配置（默认 6 秒；先 AI 作答，不足设定时间补齐再提交），最大重试 3 次。
代理：全局代理提取链接（兼容巨量、闪臣），IP 被限速（连续"频繁"且 code=400 超过 2 次）自动换 IP，
      剩余有效期不足时自动换 IP。
"""
import json
import random
import threading
import time

from . import deepseek
from .client import parse_resp
from .config import Config
from .goods import classify_goods, extract_goods_info, extract_reservation_options
from .proxy import ProxyManager

DEFAULT_ANSWER_SECONDS = 6.0
# 保留旧常量名，供外部调用方兼容；实际任务会读取全局配置。
MIN_ANSWER_SECONDS = DEFAULT_ANSWER_SECONDS
MAX_ANSWER_RETRIES = 3
DEFAULT_REFRESH_DELAY_MS = 500
DEFAULT_ORDER_DELAY_MS = 500
JITTER_MS = 100
WAIT_SPIN_NS = 5_000_000  # 最后 5ms 忙等，避免 sleep 调度延迟
# 闪臣短效代理有效期通常很短：开售前 10 秒批量测速，避免提前提取后过期。
PROXY_PREPARE_LEAD_SECONDS = 10
PROXY_PROBE_TIMEOUT_SECONDS = 0.3
PROXY_CANDIDATE_COUNT = 10
PROXY_LATENCY_SAMPLE_COUNT = 10
# 连续出现"频繁"且 code=400 超过该次数时判定 IP 被限速，换 IP
FREQUENT_LIMIT = 2


class GrabTask:
    def __init__(self, account, params: dict):
        self.account = account
        self.params = params
        self.status = "pending"      # pending/waiting/answering/grabbing/success/failed/stopped
        self.msg = "任务已创建"
        self.attempts = 0
        self.answer_passed = False
        self.order_code = ""
        self.pay_info = None
        self.last_result = None
        self.logs = []
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._frequent_count = 0
        self._proxy_mgr = None
        self._proxy_used = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "msg": self.msg,
                "attempts": self.attempts,
                "answer_passed": self.answer_passed,
                "order_code": self.order_code,
                "pay_info": self.pay_info,
                "last_result": self.last_result,
                "logs": list(self.logs[-50:]),
            }

    def _set(self, status=None, msg=None, **kw):
        with self._lock:
            if status:
                self.status = status
            if msg:
                self.msg = msg
            for k, v in kw.items():
                setattr(self, k, v)

    def _log(self, stage, code, msg, body=None):
        with self._lock:
            self.logs.append({"stage": stage, "code": code, "msg": msg,
                              "body": body, "time": time.strftime("%H:%M:%S")})

    # ---------- 主流程 ----------

    def _run(self):
        try:
            p = self.params
            client = self.account.get_client()

            # 0. 代理初始化（全局配置，每个任务复用）
            self._init_proxy(client)

            # 1. 开售前答题（pre_sale 模式）：先答题，通过后等待开抢
            if p.get("answer_mode") == "pre_sale":
                self._set(status="answering", msg="开售前答题中")
                need, detail = self._check_need_answer(client, expected_timing=1)
                if need:
                    ok, detail = self._do_answer(client)
                    if not ok:
                        self._set(status="failed", msg=f"答题失败: {detail}")
                        return
                    self._set(answer_passed=True, msg="答题通过，等待开抢时间")
                else:
                    self._set(answer_passed=True, msg=f"无需答题（{detail}），等待开抢时间")

            # 2. 等待开抢时间
            target_ts = p.get("target_ts") or 0
            if target_ts > time.time():
                self._set(status="waiting", msg="等待开抢时间")
                if self._wait_until_target(target_ts,
                                           on_pre_target=lambda: self._select_fastest_proxy(client)):
                    self._set(status="stopped", msg="已停止（等待阶段）")
                    return

            if self._stop.is_set():
                self._set(status="stopped", msg="已停止")
                return

            # 3. 开售后答题（若需要）
            if p.get("answer_mode") == "post_auto":
                self._set(status="answering", msg="检查是否需要答题")
                need, detail = self._check_need_answer(client, expected_timing=2)
                if not need:
                    self._set(answer_passed=True, msg=f"无需答题（{detail}），直接下单")
                else:
                    ok, detail = self._do_answer(client)
                    if not ok:
                        self._set(status="failed", msg=f"答题失败: {detail}")
                        return
                    self._set(answer_passed=True, msg="答题通过，开始下单")

            # 4. 下单重试循环
            self._set(status="grabbing", msg="开始抢票")
            refresh_delay = p.get("refresh_delay_ms", DEFAULT_REFRESH_DELAY_MS) / 1000.0
            order_delay = p.get("order_delay_ms", DEFAULT_ORDER_DELAY_MS) / 1000.0
            max_retries = p.get("max_retries", 3)
            # 无代理模式：重试延迟不能小于 500ms
            if not self._proxy_mgr:
                refresh_delay = max(refresh_delay, 0.5)
                order_delay = max(order_delay, 0.5)
            while True:
                if self._stop.is_set():
                    self._set(status="stopped", msg="已停止")
                    return
                self.attempts += 1
                attempt = self.attempts
                self._set(msg=f"第 {attempt} 次尝试...")

                # 每次尝试前检查代理有效期，过期自动换 IP
                if self._proxy_mgr and self._proxy_mgr.refresh_if_expiring(threshold=5):
                    p = self._proxy_mgr.current()
                    client.set_proxies(self._proxy_mgr.requests_proxies())
                    self._log("proxy", 0, f"代理即将过期，已更换 {p['ip']}:{p['port']}（剩余 {p.get('remain', 0)}s）")
                    self._set(msg=f"【代理更换】IP 即将过期，已换为新 IP {p['ip']}:{p['port']}")

                result = self._try_order(client, attempt)
                self._set(last_result=result)

                if result.get("ok"):
                    self._set(status="success",
                              msg=f"抢票成功！订单号: {result.get('order_code', '')}",
                              order_code=result.get("order_code", ""),
                              pay_info=result.get("pay_info"))
                    return

                if max_retries > 0 and attempt >= max_retries:
                    self._set(status="failed", msg=f"达到最大重试次数 {max_retries}: {result.get('msg', '')}")
                    return

                # 限速检测：连续"频繁"且 code=400 超过阈值时换 IP
                if self._is_frequent(result):
                    self._frequent_count += 1
                    if self._frequent_count > FREQUENT_LIMIT:
                        self._frequent_count = 0
                        if self._switch_proxy(client):
                            self._set(msg=f"【代理更换】IP 被限速，已更换代理 IP，继续重试")
                            continue
                else:
                    self._frequent_count = 0

                # 代理连接失败（ProxyError/ConnectionReset/ConnectionError）也换 IP
                if self._proxy_mgr and self._is_proxy_error(result):
                    if self._switch_proxy(client):
                        self._set(msg=f"【代理更换】代理连接失败，已更换代理 IP，继续重试")
                        continue

                # 失败：按阶段延迟重试（刷新延迟/下单延迟 + 随机抖动）
                delay = self._jittered(refresh_delay if result.get("stage") == "refresh" else order_delay)
                self._set(msg=f"第 {attempt} 次失败: {result.get('msg', '')}，{delay * 1000:.0f}ms 后重试")
                if self._sleep(delay):
                    self._set(status="stopped", msg="已停止")
                    return
        except Exception as e:
            self._set(status="error", msg=f"任务异常: {e}")

    # ---------- 代理 ----------

    def _init_proxy(self, client):
        """初始化代理：读取全局配置，提取首个 IP 并绑定到客户端。

        任务参数 no_proxy=True 时强制无代理模式（直连）。
        """
        if self.params.get("no_proxy"):
            self._set(msg="无代理模式（任务指定直连），重试延迟不低于 500ms")
            return
        cfg = Config()
        if not cfg.get("proxy_extract_url"):
            self._set(msg="无代理模式（未配置代理提取链接），使用直连")
            return
        self._proxy_mgr = ProxyManager(cfg)
        result = self._proxy_mgr.get()
        if result["ok"]:
            client.set_proxies(self._proxy_mgr.requests_proxies())
            self._proxy_used = True
            p = result["proxy"]
            self._set(msg=f"已启用代理 {p['ip']}:{p['port']}（剩余 {p.get('remain', 0)}s）")
        else:
            self._set(msg=f"代理提取失败: {result['msg']}，使用直连")

    def _switch_proxy(self, client) -> bool:
        """强制更换代理 IP。返回是否成功。"""
        if not self._proxy_mgr:
            return False
        result = self._proxy_mgr.get(force_refresh=True)
        if result["ok"]:
            client.set_proxies(self._proxy_mgr.requests_proxies())
            p = result["proxy"]
            self._log("proxy", 0, f"已更换代理 {p['ip']}:{p['port']}（剩余 {p.get('remain', 0)}s）")
            self._set(msg=f"【代理更换】已换为新 IP {p['ip']}:{p['port']}（剩余 {p.get('remain', 0)}s）")
            return True
        self._log("proxy", -1, f"更换代理失败: {result['msg']}")
        return False

    def _select_fastest_proxy(self, client):
        """在开售前批量测速并切换到最低延迟代理；失败时保留当前代理。"""
        if not self._proxy_mgr or self._stop.is_set():
            return
        self._set(msg=f"开抢前提取 {PROXY_CANDIDATE_COUNT} 个代理并发测速")
        result = self._proxy_mgr.select_fastest(
            count=PROXY_CANDIDATE_COUNT, samples=PROXY_LATENCY_SAMPLE_COUNT,
            timeout=PROXY_PROBE_TIMEOUT_SECONDS)
        if not result.get("ok"):
            self._log("proxy", -1, f"开抢前代理测速失败，保留当前代理: {result.get('msg', '')}")
            self._set(msg=f"开抢前代理测速失败，保留当前代理: {result.get('msg', '')}")
            return
        client.set_proxies(self._proxy_mgr.requests_proxies())
        proxy = result["proxy"]
        latency_ms = proxy.get("latency", 0) * 1000
        sample_text = "、".join(
            f"{sample['index']}={sample.get('elapsed', 0) * 1000:.0f}ms"
            if sample.get("ok") else f"{sample['index']}=失败"
            for sample in proxy.get("latency_samples", [])
        )
        message = (f"开抢前测速完成：{result['tested']}/{result['received']} 个可用，"
                   f"已选择 {proxy['ip']}:{proxy['port']}（平均 {latency_ms:.0f}ms）\n"
                   f"10 次延迟：{sample_text}")
        self._log("proxy", 0, message)
        self._set(msg=message)

    @staticmethod
    def _is_frequent(result: dict) -> bool:
        """判断是否为 IP 限速：code=400 且消息含"频繁"。"""
        if not result:
            return False
        if result.get("code") != 400:
            return False
        msg = str(result.get("msg") or "")
        return "频繁" in msg

    @staticmethod
    def _is_proxy_error(result: dict) -> bool:
        """判断是否为代理连接错误（需换 IP）。"""
        if not result:
            return False
        msg = str(result.get("msg") or "")
        return any(k in msg for k in (
            "ProxyError", "ConnectionResetError", "Connection aborted",
            "ConnectionError", "Unable to connect to proxy",
            "Failed to establish a new connection",
            "Connection refused", "10054", "10061",
            "timed out", "Timeout",
        ))

    def _sleep(self, secs) -> bool:
        """可中断 sleep，返回 True 表示被停止。"""
        end = time.time() + secs
        while time.time() < end:
            if self._stop.is_set():
                return True
            time.sleep(min(0.05, end - time.time()))
        return False

    def _wait_until_target(self, target_ts: float, on_pre_target=None) -> bool:
        """等待到 Unix 时间戳；返回 True 表示被停止。

        先用墙上时钟将目标时间换算成单调时钟的 deadline，避免等待途中
        因系统自动校时而跳变。最后 5ms 不再调用 sleep，以避免操作系统
        调度造成额外延迟。
        """
        remaining = target_ts - time.time()
        if remaining <= 0:
            return False
        deadline_ns = time.monotonic_ns() + int(remaining * 1_000_000_000)
        # 回调可能需要数秒的网络时间；目标已很近时宁可跳过，不能拖慢开抢。
        prepare_at_ns = deadline_ns - int(PROXY_PREPARE_LEAD_SECONDS * 1_000_000_000)
        prepare_pending = on_pre_target is not None and remaining > PROXY_PREPARE_LEAD_SECONDS
        while True:
            if self._stop.is_set():
                return True
            now_ns = time.monotonic_ns()
            if prepare_pending and now_ns >= prepare_at_ns:
                prepare_pending = False
                on_pre_target()
                # 测速期间可能已经到点，马上重新检查 deadline。
                continue
            remaining_ns = deadline_ns - now_ns
            if remaining_ns <= 0:
                return False
            # 最后 5ms 忙等；不会再被 sleep 的系统调度粒度拖后。
            if remaining_ns <= WAIT_SPIN_NS:
                continue
            remaining = remaining_ns / 1_000_000_000
            self._set(msg=f"距开抢 {int(remaining)} 秒")
            # 长时间等待时分段唤醒，临近目标时提前 5ms 进入忙等。
            time.sleep(min(0.2, remaining - WAIT_SPIN_NS / 1_000_000_000))

    @staticmethod
    def _jittered(base_secs: float) -> float:
        return max(0.0, base_secs + random.uniform(-JITTER_MS, JITTER_MS) / 1000.0)

    # ---------- 答题 ----------

    def _check_need_answer(self, client, expected_timing=None) -> tuple:
        """检查该账号当前是否需要答题。

        逆向自 App：isNeedQuestion 判定 = is_need_question==1 且
        is_need_question==1 且答题时机与当前流程一致。商品详情显示
        is_need_question==0（已通过答题/无需答题）时，不会读取答题材料。
        """
        p = self.params
        goods_id = int(p["goods_id"])
        try:
            resp = client.get_goods_detail(goods_id)
            r = parse_resp(resp)
        except Exception as e:
            return True, f"获取商品详情失败({e})，按需答题处理"
        if not r["ok"]:
            return True, f"获取商品详情失败(code={r.get('code')})，按需答题处理"
        body = r.get("body") or {}
        cfg = body.get("pre_purchase_question_config") or {}
        is_need = cfg.get("is_need_question")
        timing = cfg.get("answer_buy_timing_type")
        if is_need != 1:
            return False, "该账号无需答题"
        if expected_timing is not None and timing != expected_timing:
            label = "开售前" if expected_timing == 1 else "开售后"
            return False, f"答题时机类型={timing}（非{label}答题）"
        return True, "需要答题"

    def _do_answer(self, client) -> tuple:
        """开售后答题：拉题(重试3次) -> DeepSeek -> 补齐设定时间 -> checkAnswer。"""
        p = self.params
        goods_id = int(p["goods_id"])
        try:
            min_answer_seconds = float(Config().get(
                "default_answer_time_seconds", DEFAULT_ANSWER_SECONDS))
            if min_answer_seconds <= 0:
                raise ValueError
        except (TypeError, ValueError):
            min_answer_seconds = DEFAULT_ANSWER_SECONDS
        # API Key 是全局设置；材料路径是任务配置。两者都不写入临时运行参数。
        api_key = p.get("deepseek_api_key", "") or Config().get("deepseek_api_key", "")
        materials = p.get("materials", "")
        material_path = p.get("materials_path", "")
        if not materials and material_path:
            try:
                with open(material_path, "r", encoding="utf-8", errors="replace") as f:
                    materials = f.read()
            except OSError as e:
                return False, f"答题材料文件无法读取: {e}"
        if not api_key:
            return False, "DeepSeek API Key 未配置"
        if not materials:
            return False, "答题材料未填写"

        answer_start = time.time()
        for retry in range(1, MAX_ANSWER_RETRIES + 1):
            if self._stop.is_set():
                return False, "已停止"
            self._set(msg=f"拉取题目（第 {retry}/{MAX_ANSWER_RETRIES} 次）")
            try:
                resp = client.get_answer_list(goods_id)
                r = parse_resp(resp)
            except Exception as e:
                r = {"ok": False, "msg": str(e)}
            if not r["ok"]:
                self._log("answerList", r.get("code"), r.get("msg") or "拉题失败")
                if self._sleep(0.5 + 0.2 * retry):
                    return False, "已停止"
                continue
            questions = _normalize_questions((r.get("body") or {}).get("question_list") or [])
            if not questions:
                self._log("answerList", None, "未拉到题目")
                if self._sleep(0.5):
                    return False, "已停止"
                continue

            # DeepSeek 作答
            self._set(msg="DeepSeek 自动作答中")
            ds = deepseek.solve_questions(api_key, materials, questions)
            if not ds.get("ok"):
                self._log("deepseek", None, ds.get("error", "DeepSeek 失败"))
                if self._sleep(0.5):
                    return False, "已停止"
                continue

            # 补齐用户设置的最短答题时间
            elapsed = time.time() - answer_start
            if elapsed < min_answer_seconds:
                self._set(msg=f"答题完成，补齐至 {min_answer_seconds:g} 秒")
                if self._sleep(min_answer_seconds - elapsed):
                    return False, "已停止"
            used_time = max(0, int(time.time() - answer_start))

            # 提交答案
            self._set(msg="提交答案")
            try:
                resp = client.check_answer(goods_id, used_time, ds["answers"])
                chk = parse_resp(resp)
            except Exception as e:
                chk = {"ok": False, "msg": str(e)}
            self._log("checkAnswer", chk.get("code"), chk.get("msg") or "")
            if not chk["ok"]:
                if self._sleep(0.5):
                    return False, "已停止"
                continue
            body = chk.get("body") or {}
            if body.get("answer_result") is True:
                return True, "答题通过"
            result_detail = str(body.get("answer_result_detail") or "")
            diagnostic = format_answer_diagnostic(questions, ds["answers"])
            failure_msg = "答题未通过" + (f": {result_detail}" if result_detail else "")
            # 失败诊断只包含本次题目和提交的答案，不包含答题材料或 API Key。
            self._log("answerDiagnosis", None, failure_msg, body=diagnostic)
            # 抢票窗口会实时输出任务消息；在重试前显示诊断，便于核对题目、选项与选择。
            self._set(msg=f"{failure_msg}\n{diagnostic}")
            if self._sleep(0.5):
                return False, "已停止"
        return False, f"答题重试 {MAX_ANSWER_RETRIES} 次均未通过"

    # ---------- 下单 ----------

    def _try_order(self, client, attempt: int) -> dict:
        """回流模式的单次下单：orderConfirm -> createOrder -> payOrder。

        SKU、场次、门店等参数在创建任务时已确定。重试时不再请求商品详情或
        check* 库存接口，避免把“扫描库存”混入回流下单链路。
        """
        p = self.params
        goods_id = int(p["goods_id"])
        sku_id = int(p["sku_id"])
        num = int(p.get("num") or 1)
        result = {"ok": False, "stage": "order", "attempt": attempt}

        # 所有运行时参数均来自任务配置/当前账号，不在回流时动态扫描或补全。
        store_id = int(p.get("store_id") or 0)
        venue_id = int(p.get("venue_id") or 0)
        address_id = int(p.get("address_id") or 0)
        reservation_date = p.get("reservation_date") or ""
        reservation_quantum_id = int(p.get("reservation_quantum_id") or 0)
        buyer_ids = p.get("buyer_ids") or ""
        write_off_phone = p.get("write_off_phone") or ""

        # 实名购买人是账号状态，保留同步；它不是库存扫描。
        if buyer_ids:
            try:
                resp = client.save_selected_buyer(goods_id, buyer_ids)
                sel = parse_resp(resp)
            except Exception as e:
                sel = {"ok": False, "msg": str(e)}
            self._log("saveSelectedBuyer", sel.get("code"), sel.get("msg") or "")
            if not sel["ok"]:
                result["msg"] = f"同步购买人失败: {sel['msg']}"
                return result

        # 1. orderConfirm
        try:
            resp = client.order_confirm(
                sku_id=sku_id, num=num, store_id=store_id, venue_id=venue_id,
                address_id=address_id, reservation_date=reservation_date,
                reservation_quantum_id=reservation_quantum_id,
                buyer_ids=buyer_ids, write_off_phone=write_off_phone)
            confirm = parse_resp(resp)
        except Exception as e:
            confirm = {"ok": False, "msg": str(e)}
        self._log("orderConfirm", confirm.get("code"), confirm.get("msg") or "")
        if not confirm["ok"]:
            result["msg"] = f"订单确认失败: code={confirm['code']} msg={confirm['msg']}"
            return result
        payment = _extract_payment_amount(confirm.get("body") or {})
        if payment is None:
            result["msg"] = "orderConfirm 未返回 payment"
            return result

        # 2. createOrder
        try:
            resp = client.create_order(
                goods_id=goods_id, sku_id=sku_id, num=num, payment=payment,
                buyer_ids=buyer_ids, venue_id=venue_id, address_id=address_id,
                store_id=store_id, reservation_date=reservation_date,
                reservation_quantum_id=reservation_quantum_id,
                write_off_phone=write_off_phone)
            create = parse_resp(resp)
        except Exception as e:
            create = {"ok": False, "msg": str(e)}
        self._log("createOrder", create.get("code"), create.get("msg") or "")
        if not create["ok"]:
            result["msg"] = f"创建订单失败: code={create['code']} msg={create['msg']}"
            return result
        order_code = _extract_order_code(create.get("body"))
        if not order_code:
            result["msg"] = "createOrder 未返回 order_code"
            return result
        result["order_code"] = order_code

        # 3. payOrder：即便 orderConfirm 的金额为 0，也请求一次以确认服务端
        # 是否返回支付参数。此前只在 payment>0 时调用，导致需支付订单被误报为免费。
        pay_type = p.get("pay_type") or "1"
        payment_required = _payment_is_required(payment)
        pay_info = {
            "pay_type": pay_type,
            "payment": payment,
            "payment_required": payment_required,
        }
        last_pay_error = ""
        for pay_attempt in range(3 if payment_required else 1):
            if pay_attempt > 0 and self._sleep(0.8 + 0.4 * pay_attempt):
                break
            try:
                resp = client.pay_order(order_code, pay_type)
                pr = parse_resp(resp)
            except Exception as e:
                pr = {"ok": False, "msg": str(e)}
            self._log("payOrder", pr.get("code"), pr.get("msg") or "")
            if pr["ok"]:
                pay_info.update(_extract_pay_info(pr.get("body"), pay_type))
                # 服务端实际给出支付参数时优先认为需要支付，避免误报免费。
                if pay_info.get("alipay") or pay_info.get("wechat") or pay_info.get("pay_url"):
                    pay_info["payment_required"] = True
                break
            last_pay_error = str(pr.get("msg") or "payOrder 请求失败")
        if last_pay_error and not pay_info.get("alipay") and not pay_info.get("wechat"):
            pay_info["pay_error"] = last_pay_error

        result["ok"] = True
        result["pay_info"] = pay_info
        result["msg"] = f"下单成功 order_code={order_code}"
        return result


# ---------- 解析辅助 ----------

def normalize_questions(raw_questions: list) -> list:
    """将 answerList 的原始题目整理成统一的题目/选项结构。"""
    out = []
    for q in raw_questions:
        if not isinstance(q, dict):
            continue
        out.append({
            "question_id": q.get("question_id"),
            "question_name": q.get("question_name") or "",
            "options": [
                {"answer_id": o.get("answer_id"), "answer_name": o.get("answer_name")}
                for o in (q.get("answer_list") or []) if isinstance(o, dict)
            ],
        })
    return out


def format_answer_diagnostic(questions: list, answers: list) -> str:
    """格式化答题失败诊断：题干、全部选项和实际提交的答案。"""
    selected_by_question = {
        answer.get("question_id"): answer.get("answer_id")
        for answer in answers if isinstance(answer, dict)
    }
    lines = ["【答题失败诊断】"]
    for index, question in enumerate(questions, 1):
        question_id = question.get("question_id")
        selected_id = selected_by_question.get(question_id)
        lines.append(f"第 {index} 题（question_id={question_id}）：{question.get('question_name') or '（题干为空）'}")
        selected_text = "（未找到提交答案）"
        for option_index, option in enumerate(question.get("options") or [], 1):
            answer_id = option.get("answer_id")
            answer_name = option.get("answer_name") or "（选项为空）"
            lines.append(f"  {option_index}. {answer_name}（answer_id={answer_id}）")
            if answer_id == selected_id:
                selected_text = f"{option_index}. {answer_name}（answer_id={answer_id}）"
        lines.append(f"  已选：{selected_text}")
    return "\n".join(lines)


# 兼容可能从旧名称导入此内部辅助方法的外部脚本。
_normalize_questions = normalize_questions


def _extract_order_code(body) -> str:
    if not isinstance(body, dict):
        return ""
    order = body.get("order") or {}
    if isinstance(order, dict):
        order_code = order.get("order_code") or order.get("order_no")
        if order_code:
            return order_code
    return body.get("order_code") or body.get("order_no") or ""


def _find_nested_value(data, keys, depth: int = 0):
    """从支付接口可能嵌套的响应中取第一个非空字段。"""
    if depth > 3:
        return None
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value not in (None, ""):
                return value
        for value in data.values():
            found = _find_nested_value(value, keys, depth + 1)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_nested_value(value, keys, depth + 1)
            if found not in (None, ""):
                return found
    return None


def _extract_payment_amount(body):
    """兼容不同版本 orderConfirm 的应付金额字段；0 也是有效金额。"""
    return _find_nested_value(body, (
        "payment", "pay_amount", "payAmount", "payment_amount", "paymentAmount",
        "actual_payment", "actualPayment", "need_pay_amount", "needPayAmount",
    ))


def _payment_is_required(payment) -> bool:
    """无法解析的金额按需支付处理，绝不把它误报为免费。"""
    try:
        return float(str(payment).replace("¥", "").replace(",", "").strip()) > 0
    except (TypeError, ValueError):
        return True


def _extract_pay_info(body, pay_type: str) -> dict:
    """从 payOrder 响应提取支付链接/参数。

    逆向自 qigumi-node extractPayInfo：payType=1 返回微信支付参数，
    payType=2 返回支付宝 alipay_sdk 串。
    """
    info = {"pay_type": pay_type, "raw": body}
    if not isinstance(body, dict):
        return info
    pay_url = _find_nested_value(body, ("pay_url", "payUrl", "cashier_url", "cashierUrl"))
    if isinstance(pay_url, str) and pay_url.startswith(("http://", "https://")):
        info["pay_url"] = pay_url
    pay_params = _find_nested_value(body, ("payParams", "pay_params", "pay_params_str")) or ""
    if pay_type == "1":  # 微信
        if pay_params:
            info["wechat"] = pay_params
        elif _find_nested_value(body, ("prepay_id", "prepayId")):
            info["wechat"] = f"prepay_id={_find_nested_value(body, ('prepay_id', 'prepayId'))}"
    else:  # 支付宝
        alipay = _find_nested_value(body, (
            "payParams", "pay_params", "alipay", "alipay_sdk", "pay_info", "order_str",
        ))
        if alipay:
            info["alipay"] = alipay
    return info
