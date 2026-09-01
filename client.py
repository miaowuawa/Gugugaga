# -*- coding: utf-8 -*-
"""奇谷米 App HTTP API 客户端。

接口、签名、请求头均逆向自 com.uxin.mall.network.OooOO0 / com.uxin.base.network.o0OoOo0。
下单链路: commonDetail -> orderConfirm -> createOrder -> payOrder
"""
import gzip
import io
import json
import random
import time

import requests

from . import crypto
from .headers import HeaderGenerator

BASE_URL = "https://app.qigumi.com"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRY = 3
RETRYABLE_CODES = {300, -9999, 429, 502, 503, 504}

# 页面名（request-page 头）
PAGE_LOGIN = "Android_BaseLoginActivity"
PAGE_MINE = "Android_MineFragment"
PAGE_GOODS = "Android_GoodsDetailsActivity"
PAGE_ORDER_CREATE = "Android_OrderCreateActivity"
PAGE_PAYMENT = "Android_PaymentActivity"
PAGE_BUYER = "Android_RealNameBuyersActivity"
PAGE_ADD_BUYER = "Android_AddBuyerActivity"
PAGE_ADDRESS = "Android_MyAddressActivity"
PAGE_ANSWER = "Android_AnswerActivity"
PAGE_APP = "Android_App"


def _is_retryable(resp):
    if resp is None:
        return True
    if resp.status_code in {429, 502, 503, 504}:
        return True
    try:
        h = resp.json().get("h") or {}
        if h.get("code") in RETRYABLE_CODES:
            return True
    except Exception:
        pass
    return False


def _do_request(method, url, headers, data=None, json_body=None, params=None,
                timeout=DEFAULT_TIMEOUT, retry=DEFAULT_RETRY, proxies=None):
    last_resp = None
    last_err = None
    for attempt in range(retry + 1):
        try:
            if method.upper() == "GET":
                last_resp = requests.get(url, headers=headers, params=params,
                                         timeout=timeout, proxies=proxies)
            elif json_body is not None:
                last_resp = requests.post(url, headers=headers, json=json_body,
                                          timeout=timeout, proxies=proxies)
            else:
                last_resp = requests.post(url, headers=headers, data=data,
                                          timeout=timeout, proxies=proxies)
            if not _is_retryable(last_resp):
                return last_resp
        except requests.RequestException as e:
            last_err = e
            last_resp = None
        if attempt < retry:
            backoff = 0.8 * (2 ** attempt) + random.uniform(0, 0.3)
            time.sleep(backoff)
    if last_resp is not None:
        return last_resp
    raise last_err or requests.RequestException("请求失败且重试用尽")


def _read_body(resp):
    raw = resp.content
    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        except Exception:
            pass
    return raw


def parse_resp(resp):
    """解析统一响应 {h:{code,msg,success}, b:...}。"""
    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "code": None, "msg": "响应非JSON",
                "http_status": getattr(resp, "status_code", None), "body": None, "raw": None}
    h = data.get("h") if isinstance(data, dict) else None
    b = data.get("b") if isinstance(data, dict) else None
    ok = bool(isinstance(h, dict) and h.get("success") and h.get("code") == 200)
    return {
        "ok": ok,
        "code": h.get("code") if isinstance(h, dict) else None,
        "msg": h.get("msg") if isinstance(h, dict) else None,
        "body": b,
        "raw": data,
    }


class QiGuMiClient:
    """单账号客户端。auth_token 登录成功后更新。"""

    def __init__(self, device: dict, uxid: str = None, visitor_id: str = None,
                 session_id: str = None, auth_token: str = "", signer: crypto.Signer = None):
        self.device = device
        self.signer = signer or crypto.Signer()
        self.hg = HeaderGenerator(uxid, visitor_id, session_id)
        self.auth_token = auth_token
        self.proxies = None  # requests 格式代理 {"http":..., "https":...}，None=直连

    def set_proxies(self, proxies: dict):
        self.proxies = proxies

    def _do(self, method, url, headers, data=None, json_body=None, params=None):
        return _do_request(method, url, headers, data=data, json_body=json_body,
                           params=params, proxies=self.proxies)

    # ---------- 配置（动态 AES key） ----------

    def fetch_config(self) -> bool:
        """GET configuration/query，更新 AES key/iv（acd 明文优先，cfg RSA 解密兜底）。"""
        headers = self.hg.header(PAGE_APP, "", self.device)
        try:
            resp = self._do("GET", BASE_URL + "/api/v3/configuration/query", headers)
            data = resp.json()
            b = data.get("b") or {}
            key = b.get("acd") or ""
            if not key and b.get("cfg"):
                key = crypto.rsa_public_decrypt(b["cfg"])
            iv = b.get("iv") or ""
            if key or iv:
                self.signer.set_key_iv(key, iv)
                return True
        except Exception:
            pass
        return False

    # ---------- 登录 ----------

    def send_validate_code(self, phone: str, region: str = "86"):
        """发送短信验证码（登录前接口，不带 auth_token）。"""
        url = BASE_URL + "/api/v3/user/sendValidateCode"
        headers = self.hg.header(PAGE_LOGIN, "", self.device)
        form = {
            "mobile": self.signer.encrypt_sensitive(phone),
            "bizType": "0",
            "source": region,
        }
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def sms_login(self, phone: str, region: str, code: str):
        url = BASE_URL + "/api/v3/userCenter/phoneLogin"
        headers = self.hg.header(PAGE_LOGIN, "", self.device)
        form = {
            "mobile": self.signer.encrypt_sensitive(phone),
            "co": self.signer.encrypt_sensitive(code),
            "source": region,
        }
        form["sign"] = self.signer.sign(form)
        resp = self._do("POST", url, headers, data=form)
        token = resp.headers.get("x-auth-token")
        if token:
            self.auth_token = token
        return resp

    def password_login(self, phone: str, region: str, password: str):
        url = BASE_URL + "/api/v3/user/login/password"
        headers = self.hg.header(PAGE_LOGIN, "", self.device)
        form = {
            "mobile": self.signer.encrypt_sensitive(phone),
            "password": self.signer.encrypt_sensitive(password),
            "source": region,
        }
        form["sign"] = self.signer.sign(form)
        resp = self._do("POST", url, headers, data=form)
        token = resp.headers.get("x-auth-token")
        if token:
            self.auth_token = token
        return resp

    # ---------- 用户 ----------

    def get_user_info(self):
        url = BASE_URL + "/api/v3/userCenter/getUserInfo"
        headers = self.hg.header(PAGE_MINE, self.auth_token, self.device)
        return self._do("GET", url, headers)

    def get_order_list(self, order_type=None, page=1):
        """订单列表。type: 5=待付款 2=待发货 3=待收货 4=已完成。"""
        url = BASE_URL + "/api/v3/order/orderList"
        headers = self.hg.header("Android_OrderListActivity", self.auth_token, self.device)
        params = {"page": page}
        if order_type is not None:
            params["type"] = order_type
        return self._do("GET", url, headers, params=params)

    def cancel_order(self, order_code):
        """取消订单（pay/cancelCreate）。"""
        url = BASE_URL + "/api/v3/pay/cancelCreate"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        form = {"order_code": order_code}
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    # ---------- 商品详情 ----------

    def get_goods_detail(self, goods_id, lng=None, lat=None, address_id=None,
                         province=None, city=None, area=None):
        url = BASE_URL + "/api/v3/goods/commonDetail"
        headers = self.hg.header(PAGE_GOODS, self.auth_token, self.device)
        params = {"goods_id": goods_id}
        if lng is not None:
            params["lng"] = lng
        if lat is not None:
            params["lat"] = lat
        if address_id is not None:
            params["address_id"] = address_id
        if province:
            params["province"] = province
        if city:
            params["city"] = city
        if area:
            params["area"] = area
        return self._do("GET", url, headers, params=params)

    # ---------- 下单链路 ----------

    def order_confirm(self, sku_id, num, store_id=0, venue_id=0, address_id=0,
                      reservation_date="", reservation_quantum_id=0,
                      buyer_ids="", write_off_phone=""):
        url = BASE_URL + "/api/v3/pay/orderConfirm"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        form = {
            "sku_id": sku_id,
            "num": num,
            "reservation_date": reservation_date or "",
            "reservation_quantum_id": reservation_quantum_id or 0,
            "store_id": store_id or 0,
            "venue_id": venue_id or 0,
            "address_id": address_id or 0,
        }
        if buyer_ids:
            form["buyer_ids"] = buyer_ids
        if write_off_phone:
            form["write_off_phone"] = self.signer.encrypt_sensitive(write_off_phone)
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def check_normal_reservation_goods(self, goods_id, num, store_id=0, write_off_phone=""):
        """核销类商品（write_off_type=1）下单前校验。

        逆向自 com.uxin.mall.network.OooOO0.OooOoo：
          @Field("goods_id") Long, @Field("write_off_phone") String(明文),
          @Field("store_id") Long, @Field("num") Long
        """
        url = BASE_URL + "/api/v3/goods/checkNormalReservationGoods"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        form = {
            "goods_id": goods_id,
            "num": num,
            "store_id": store_id or 0,
        }
        if write_off_phone:
            form["write_off_phone"] = write_off_phone
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def check_voucher_goods(self, goods_id, sku_id, num, write_off_phone=""):
        """券类商品（write_off_type=4）下单前校验。

        逆向自 com.uxin.mall.network.OooOO0.OooOO0o：
          @Field("goods_id") Long, @Field("sku_id") Long,
          @Field("write_off_phone") String(明文), @Field("num") Long
        """
        url = BASE_URL + "/api/v3/goods/checkVoucherGoods"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        form = {
            "goods_id": goods_id,
            "sku_id": sku_id,
            "num": num,
        }
        if write_off_phone:
            form["write_off_phone"] = write_off_phone
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def check_reservation_goods_v2(self, goods_id, num, store_id=0, venue_id=0,
                                   reservation_date="", reservation_quantum_id=0,
                                   write_off_phone=""):
        """票务/预约类下单前校验。

        逆向自 com.uxin.mall.network.OooOO0.o0OoOo0：
          @Field("goods_id") Long, @Field("write_off_phone") String(明文),
          @Field("store_id") Long, @Field("num") Long,
          @Field("reservation_quantum_id") Long, @Field("reservation_date") String
        """
        url = BASE_URL + "/api/v3/goods/checkReservationGoodsV2"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        form = {
            "goods_id": goods_id,
            "num": num,
            "store_id": store_id or 0,
            "reservation_date": reservation_date or "",
            "reservation_quantum_id": reservation_quantum_id or 0,
        }
        if venue_id:
            form["venue_id"] = venue_id
        if write_off_phone:
            form["write_off_phone"] = write_off_phone
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def check_ticket_goods(self, goods_id, sku_id, num, venue_id=0, write_off_phone=""):
        """票务类商品下单前校验。

        逆向自 com.uxin.mall.network.OooOO0.OooOO0：
          @Field("goods_id") Long, @Field("sku_id") Long, @Field("num") Long,
          @Field("write_off_phone") String(明文), @Field("venue_id") Long
        """
        url = BASE_URL + "/api/v3/goods/checkTicketGoods"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        form = {
            "goods_id": goods_id,
            "sku_id": sku_id,
            "num": num,
        }
        if venue_id:
            form["venue_id"] = venue_id
        if write_off_phone:
            form["write_off_phone"] = write_off_phone
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def choose_reservation_goods_store(self, goods_id, store_id, lng=None, lat=None):
        """选择预约门店，返回该门店的场次列表。

        逆向自 com.uxin.mall.network.OooOO0.OooO0oo：
          @Field("goods_id") long, @Field("lng") Double, @Field("lat") Double,
          @Field("id") Long(门店 DataGoodsStore.id), @Field("province") String,
          @Field("city") String, @Field("area") String
        返回 DataReservationGoodsSell（reservation_goods_time_list 为该门店专属场次）。
        """
        url = BASE_URL + "/api/v3/goods/chooseReservationGoodsStore"
        headers = self.hg.header(PAGE_GOODS, self.auth_token, self.device)
        form = {"goods_id": goods_id, "id": store_id}
        if lng is not None:
            form["lng"] = lng
        if lat is not None:
            form["lat"] = lat
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def create_order(self, goods_id, sku_id, num, payment, buyer_ids="", venue_id=0,
                     address_id=0, store_id=0, reservation_date="",
                     reservation_quantum_id=0, write_off_phone="",
                     coupon_price=None, spend_red_bean_num=None,
                     use_fox_coupon=False, use_free_shiping_coupon=False, order_type=1):
        url = BASE_URL + "/api/v3/pay/createOrder"
        headers = self.hg.header(PAGE_ORDER_CREATE, self.auth_token, self.device)
        body = {
            "type": order_type,
            "goods_id": goods_id,
            "sku_id": sku_id,
            "num": num,
            "buyer_ids": buyer_ids or "",
            "venue_id": venue_id or 0,
            "address_id": address_id or 0,
            "store_id": store_id or 0,
            "reservation_date": reservation_date or "",
            "reservation_quantum_id": reservation_quantum_id or 0,
            "use_fox_coupon": use_fox_coupon,
            "use_free_shiping_coupon": use_free_shiping_coupon,
            "payment": payment or "0.00",
            "write_off_phone": write_off_phone or "",
        }
        if coupon_price is not None:
            body["coupon_price"] = coupon_price
        if spend_red_bean_num is not None:
            body["spend_red_bean_num"] = spend_red_bean_num
        body["sign"] = self.signer.sign(body)
        return self._do("POST", url, headers, json_body=body)

    def pay_order(self, order_code, pay_type="1"):
        url = BASE_URL + "/api/v3/pay/payOrder"
        headers = self.hg.header(PAGE_PAYMENT, self.auth_token, self.device)
        form = {"order_code": order_code, "pay_type": pay_type}
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    # ---------- 实名购买人 ----------

    def get_buyer_list(self, goods_id=None):
        url = BASE_URL + "/api/v3/buyer/getBuyerList"
        headers = self.hg.header(PAGE_BUYER, self.auth_token, self.device)
        params = {}
        if goods_id is not None:
            params["goods_id"] = goods_id
        return self._do("GET", url, headers, params=params)

    def add_buyer(self, reg_real_name, reg_id, reg_type=1):
        """新增/修改实名购买人（App 端编辑复用 addBuyer，无独立 edit 接口）。"""
        url = BASE_URL + "/api/v3/buyer/addBuyer"
        headers = self.hg.header(PAGE_ADD_BUYER, self.auth_token, self.device)
        form = {
            "reg_real_name": reg_real_name,
            "reg_id": self.signer.encrypt_sensitive(reg_id),
            "reg_type": str(reg_type),
        }
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def del_buyer(self, buyer_ids):
        url = BASE_URL + "/api/v3/buyer/delBuyer"
        headers = self.hg.header(PAGE_BUYER, self.auth_token, self.device)
        if isinstance(buyer_ids, (list, tuple)):
            buyer_ids = ",".join(str(x) for x in buyer_ids)
        form = {"buyer_ids": str(buyer_ids)}
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def save_selected_buyer(self, goods_id, buyer_ids):
        url = BASE_URL + "/api/v3/buyer/saveSelectedBuyer"
        headers = self.hg.header(PAGE_BUYER, self.auth_token, self.device)
        if isinstance(buyer_ids, (list, tuple)):
            buyer_ids = ",".join(str(x) for x in buyer_ids)
        form = {"goods_id": str(goods_id), "buyer_ids": str(buyer_ids)}
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    # ---------- 收货地址 ----------

    def get_address_list(self):
        url = BASE_URL + "/api/v3/order/userAddressList"
        headers = self.hg.header(PAGE_ADDRESS, self.auth_token, self.device)
        return self._do("GET", url, headers)

    def get_default_address(self):
        url = BASE_URL + "/api/v3/user/getDefaultAdd"
        headers = self.hg.header(PAGE_ADDRESS, self.auth_token, self.device)
        return self._do("GET", url, headers)

    def get_regions(self, version="", overseas=None):
        url = BASE_URL + "/api/v3/order/regions"
        headers = self.hg.header(PAGE_ADDRESS, self.auth_token, self.device)
        params = {}
        if version:
            params["version"] = version
        if overseas is not None:
            params["overseas"] = overseas
        return self._do("GET", url, headers, params=params)

    def add_user_address(self, user_name, phone, province, city, region, detail,
                         is_default=0, action=0, address_id=None):
        """新增(action=0)/修改(action=1)收货地址。

        逆向自 com.uxin.mall.userprofile.network.OooO0O0.OooOOOo：
          @POST("order/addUserAddress") @Field("is_default") Integer,
          @Field("action") Integer, @Field("province") String, @Field("city") String,
          @Field("region") String, @Field("detail") String, @Field("phone") String,
          @Field("user_name") String, @Field("id") Long
        phone 需 AES 加密（CreateOrEditAddressPresenter.o000OooO 用 getCfg() 加密）。
        """
        url = BASE_URL + "/api/v3/order/addUserAddress"
        headers = self.hg.header(PAGE_ADDRESS, self.auth_token, self.device)
        form = {
            "is_default": 1 if is_default else 0,
            "action": action,
            "province": province or "",
            "city": city or "",
            "region": region or "",
            "detail": detail or "",
            "phone": self.signer.encrypt_sensitive(phone),
            "user_name": user_name or "",
        }
        if address_id is not None:
            form["id"] = address_id
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    def del_user_address(self, address_id):
        url = BASE_URL + "/api/v3/order/delUserAddress"
        headers = self.hg.header(PAGE_ADDRESS, self.auth_token, self.device)
        form = {"id": str(address_id)}
        form["sign"] = self.signer.sign(form)
        return self._do("POST", url, headers, data=form)

    # ---------- 答题 ----------

    def get_answer_list(self, goods_id):
        url = BASE_URL + "/api/v3/answer/answerList"
        headers = self.hg.header(PAGE_ANSWER, self.auth_token, self.device)
        return self._do("GET", url, headers, params={"goods_id": goods_id})

    def check_answer(self, goods_id, used_time, answer_list):
        url = BASE_URL + "/api/v3/answer/checkAnswer"
        headers = self.hg.header(PAGE_ANSWER, self.auth_token, self.device)
        try:
            ut = int(used_time)
        except (TypeError, ValueError):
            ut = 0
        ut = max(ut, 0)
        ut_str = str(ut)
        body = {
            "goods_id": str(goods_id),
            "used_time": ut_str,
            "answer_list": answer_list,
        }
        body["sign"] = self.signer.sign({"goods_id": str(goods_id), "used_time": ut_str})
        return self._do("POST", url, headers, json_body=body)
