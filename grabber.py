# -*- coding: utf-8 -*-
"""抢票引擎：等待开抢 -> (可选)开售后答题 -> 下单重试 -> 支付链接。

下单链路: commonDetail -> orderConfirm -> createOrder -> payOrder
延迟策略：刷新延迟默认 500ms、下单延迟默认 500ms，均加随机抖动。
答题：最短用时 6 秒（先 AI 作答，不足 6s 补齐再提交），最大重试 3 次。
代理：全局代理提取链接（巨量代理），IP 被限速（连续"频繁"且 code=400 超过 2 次）自动换 IP，
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

MIN_ANSWER_SECONDS = 6
MAX_ANSWER_RETRIES = 3
DEFAULT_REFRESH_DELAY_MS = 500
DEFAULT_ORDER_DELAY_MS = 500
JITTER_MS = 100
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
                need, detail = self._check_need_answer(client)
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
                while time.time() < target_ts:
                    if self._stop.is_set():
                        self._set(status="stopped", msg="已停止（等待阶段）")
                        return
                    remaining = target_ts - time.time()
                    self._set(msg=f"距开抢 {int(remaining)} 秒")
                    time.sleep(min(0.2, max(0.05, remaining)))

            if self._stop.is_set():
                self._set(status="stopped", msg="已停止")
                return

            # 3. 开售后答题（若需要）
            if p.get("answer_mode") == "post_auto":
                self._set(status="answering", msg="检查是否需要答题")
                need, detail = self._check_need_answer(client)
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

    @staticmethod
    def _jittered(base_secs: float) -> float:
        return max(0.0, base_secs + random.uniform(-JITTER_MS, JITTER_MS) / 1000.0)

    # ---------- 答题 ----------

    def _check_need_answer(self, client) -> tuple:
        """检查该账号当前是否需要答题。

        逆向自 App：isNeedQuestion 判定 = is_need_question==1 且
        answer_buy_timing_type==2（开售后答题）且已开售。若商品详情显示
        is_need_question==0（已通过答题/无需答题），则跳过答题直接下单。
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
        if timing != 2:
            return False, f"答题时机类型={timing}（非开售后答题）"
        return True, "需要答题"

    def _do_answer(self, client) -> tuple:
        """开售后答题：拉题(重试3次) -> DeepSeek -> 补齐6秒 -> checkAnswer。"""
        p = self.params
        goods_id = int(p["goods_id"])
        api_key = p.get("deepseek_api_key", "")
        materials = p.get("materials", "")
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

            # 补齐最短答题时间（6 秒）
            elapsed = time.time() - answer_start
            if elapsed < MIN_ANSWER_SECONDS:
                self._set(msg=f"答题完成，补齐至 {MIN_ANSWER_SECONDS} 秒")
                if self._sleep(MIN_ANSWER_SECONDS - elapsed):
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
            self._log("checkAnswer", None, "答题未通过: " + str(body.get("answer_result_detail") or ""))
            if self._sleep(0.5):
                return False, "已停止"
        return False, f"答题重试 {MAX_ANSWER_RETRIES} 次均未通过"

    # ---------- 下单 ----------

    def _try_order(self, client, attempt: int) -> dict:
        """单次下单尝试：commonDetail -> orderConfirm -> createOrder -> payOrder。"""
        p = self.params
        goods_id = int(p["goods_id"])
        sku_id = int(p["sku_id"])
        num = int(p.get("num") or 1)
        result = {"ok": False, "stage": "refresh", "attempt": attempt}

        # 1. 商品详情（刷新阶段）
        try:
            resp = client.get_goods_detail(goods_id)
            detail = parse_resp(resp)
        except Exception as e:
            result["msg"] = f"获取商品详情失败: {e}"
            return result
        self._log("commonDetail", detail.get("code"), detail.get("msg") or "")
        if not detail["ok"]:
            result["msg"] = f"获取商品详情失败: code={detail['code']} msg={detail['msg']}"
            return result
        body = detail.get("body") or {}
        gtype = classify_goods(body)
        info = extract_goods_info(body)

        # 售罄提示
        sbs = info.get("sell_button_status")
        if sbs in (4, 5):
            result["msg"] = f"商品已售罄/结束 (sell_button_status={sbs})"
            return result

        # 2. 组装下单参数
        store_id = int(p.get("store_id") or 0)
        venue_id = int(p.get("venue_id") or 0)
        address_id = int(p.get("address_id") or 0)
        reservation_date = p.get("reservation_date") or ""
        reservation_quantum_id = int(p.get("reservation_quantum_id") or 0)
        buyer_ids = p.get("buyer_ids") or ""
        write_off_phone = p.get("write_off_phone") or ""

        # 商品类型细分（逆向自 DataOrderGoodsModel）：
        #   write_off_type: 0=普通商品 1=核销 2=预约 3=票务 4=券
        # 所有非普通商品（write_off_type != 0）都需要核销手机号
        write_off_type = body.get("write_off_type") or 0
        is_common = gtype == "common" and write_off_type == 0

        if gtype == "ticket":
            # 票务：venue_id 必填；未指定时自动取第一个可售场馆
            if not venue_id:
                for v in info.get("venue_list") or []:
                    if v.get("button_status") in (1, 3):
                        venue_id = int(v["venue_id"])
                        break
            if not venue_id:
                result["msg"] = "票务商品未找到可售场馆"
                return result
        elif gtype == "write_off":
            # 核销/预约：store_id 必填；未指定时自动取第一个可售门店
            if not store_id:
                for s in info.get("store_list") or []:
                    if s.get("button_status") == 3 and (s.get("surplus_store") or 0) > 0:
                        store_id = int(s["store_id"])
                        break
            if not store_id:
                result["msg"] = "核销商品未找到可售门店"
                return result

        # 非普通商品必须提供核销手机号
        if not is_common and not write_off_phone:
            result["msg"] = f"该商品类型(write_off_type={write_off_type})需要核销手机号"
            return result

        # 3. 按类型下单前校验
        if gtype == "ticket":
            # 票务类（write_off_type=3）：checkTicketGoods（venue_id + write_off_phone）
            try:
                resp = client.check_ticket_goods(
                    goods_id, sku_id, num, venue_id=venue_id,
                    write_off_phone=write_off_phone)
                ck = parse_resp(resp)
            except Exception as e:
                ck = {"ok": False, "msg": str(e)}
            self._log("checkTicket", ck.get("code"), ck.get("msg") or "")
            if not ck["ok"]:
                result["msg"] = f"库存校验失败: code={ck['code']} msg={ck['msg']}"
                return result
        elif gtype == "write_off":
            if write_off_type == 1:
                # 核销类：checkNormalReservationGoods（无 reservation_date）
                try:
                    resp = client.check_normal_reservation_goods(
                        goods_id, num, store_id=store_id,
                        write_off_phone=write_off_phone)
                    ck = parse_resp(resp)
                except Exception as e:
                    ck = {"ok": False, "msg": str(e)}
                self._log("checkNormal", ck.get("code"), ck.get("msg") or "")
                if not ck["ok"]:
                    result["msg"] = f"库存校验失败: code={ck['code']} msg={ck['msg']}"
                    return result
            else:
                # 预约类（write_off_type=2）：场次与门店绑定，需先按门店拉取专属场次列表
                if store_id and not reservation_quantum_id:
                    try:
                        resp = client.choose_reservation_goods_store(goods_id, store_id)
                        cs = parse_resp(resp)
                    except Exception as e:
                        cs = {"ok": False, "msg": str(e)}
                    self._log("chooseStore", cs.get("code"), cs.get("msg") or "")
                    if cs["ok"]:
                        for t in (cs.get("body") or {}).get("reservation_goods_time_list") or []:
                            if not isinstance(t, dict):
                                continue
                            if not reservation_date:
                                reservation_date = t.get("reservation_date") or ""
                            for q in t.get("quantum_list") or []:
                                if isinstance(q, dict) and q.get("quantum_status") in (1, 3):
                                    reservation_quantum_id = int(q["id"])
                                    break
                            if reservation_quantum_id:
                                break
                try:
                    resp = client.check_reservation_goods_v2(
                        goods_id, num, store_id=store_id,
                        reservation_date=reservation_date,
                        reservation_quantum_id=reservation_quantum_id,
                        write_off_phone=write_off_phone)
                    ck = parse_resp(resp)
                except Exception as e:
                    ck = {"ok": False, "msg": str(e)}
                self._log("checkV2", ck.get("code"), ck.get("msg") or "")
                if not ck["ok"]:
                    result["msg"] = f"库存校验失败: code={ck['code']} msg={ck['msg']}"
                    return result
        elif gtype == "voucher":
            # 券类（write_off_type=4）：checkVoucherGoods
            try:
                resp = client.check_voucher_goods(
                    goods_id, sku_id, num, write_off_phone=write_off_phone)
                ck = parse_resp(resp)
            except Exception as e:
                ck = {"ok": False, "msg": str(e)}
            self._log("checkVoucher", ck.get("code"), ck.get("msg") or "")
            if not ck["ok"]:
                result["msg"] = f"库存校验失败: code={ck['code']} msg={ck['msg']}"
                return result

        # 4. 实名制：同步选中购买人
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

        # 5. orderConfirm
        result["stage"] = "order"
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
        payment = (confirm.get("body") or {}).get("payment")
        if not payment:
            result["msg"] = "orderConfirm 未返回 payment"
            return result

        # 6. createOrder
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

        # 7. payOrder（金额 > 0 时发起支付，最多 3 次）
        pay_info = None
        try:
            if float(payment or 0) > 0:
                pay_type = p.get("pay_type") or "1"
                for pay_attempt in range(3):
                    if pay_attempt > 0 and self._sleep(0.8 + 0.4 * pay_attempt):
                        break
                    try:
                        resp = client.pay_order(order_code, pay_type)
                        pr = parse_resp(resp)
                    except Exception as e:
                        pr = {"ok": False, "msg": str(e)}
                    self._log("payOrder", pr.get("code"), pr.get("msg") or "")
                    if pr["ok"]:
                        pay_info = _extract_pay_info(pr.get("body"), pay_type)
                        break
        except Exception:
            pass

        result["ok"] = True
        result["pay_info"] = pay_info
        result["msg"] = f"下单成功 order_code={order_code}"
        return result


# ---------- 解析辅助 ----------

def _normalize_questions(raw_questions: list) -> list:
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


def _extract_order_code(body) -> str:
    if not isinstance(body, dict):
        return ""
    order = body.get("order") or {}
    if isinstance(order, dict):
        return order.get("order_code") or ""
    return body.get("order_code") or body.get("order_no") or ""


def _extract_pay_info(body, pay_type: str) -> dict:
    """从 payOrder 响应提取支付链接/参数。

    逆向自 qigumi-node extractPayInfo：payType=1 返回微信支付参数，
    payType=2 返回支付宝 alipay_sdk 串。
    """
    info = {"pay_type": pay_type, "raw": body}
    if not isinstance(body, dict):
        return info
    pay_params = body.get("payParams") or ""
    if pay_type == "1":  # 微信
        if pay_params:
            info["wechat"] = pay_params
        elif body.get("prepay_id"):
            info["wechat"] = f"prepay_id={body['prepay_id']}"
        if not info.get("wechat"):
            info["wechat"] = json.dumps(body, ensure_ascii=False)
    else:  # 支付宝
        for k in ("payParams", "alipay", "pay_info", "order_str"):
            v = body.get(k)
            if v:
                info["alipay"] = v
                break
    return info
