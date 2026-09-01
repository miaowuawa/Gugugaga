# -*- coding: utf-8 -*-
"""商品详情解析：判断商品类型与下单所需字段。

逆向自 com.uxin.mall.details.network.data.DataGoodsDetails：
  - 普通商品: type==2 且 write_off_type==0（isCommonGoods），需 address_id
  - 核销/预约类: normal_write_off_goods_sell_data / reservation_goods_data_more，需 store_id
  - 票务类: ticket_goods_data（DataTicketGoods: venue_list + calendar_data），需 venue_id + 场次
  - 实名制: ticket_reg_data.ticket_reg_type 1/2 需 buyer_ids
  - 答题: pre_purchase_question_config.is_need_question==1
"""

GOODS_TYPE_COMMON = "common"        # 普通商品（需地址）
GOODS_TYPE_WRITE_OFF = "write_off"  # 核销/预约（需门店）
GOODS_TYPE_TICKET = "ticket"        # 票务（需场馆+场次）
GOODS_TYPE_VOUCHER = "voucher"      # 券类（需核销手机号）

# 售卖状态（DataTicketGoods.BUTTON_STATUS_* / sell_button_status）
SELL_BUTTON_STATUS = {
    -1: "未开售",
    1: "可预约",
    2: "已预约",
    3: "售卖中",
    4: "已结束",
    5: "已售罄",
    6: "线下售卖",
    7: "三线售卖",
}

# 门店状态（DataGoodsStore.SELL_STATUS_*）
STORE_BUTTON_STATUS = {
    1: "未开始",
    2: "已预约",
    3: "可售",
    4: "已结束",
    5: "已售罄",
}

# 预约日期状态（DataReservationGoodsTime.canSell: 1/3 可售, 2 售罄）
RESERVATION_STATUS = {
    1: "可预约",
    2: "已售罄",
    3: "售卖中",
}

# 场次状态（DataReservationGoodsQuantum.canSell: 1/3 可售, 2 售罄）
QUANTUM_STATUS = {
    1: "可售",
    2: "已售罄",
    3: "售卖中",
}

# 票务 SKU 状态（DataTicketSku.TICKET_SKU_SELL_*）
TICKET_SKU_STATUS = {
    1: "正常",
    2: "已售罄",
}

# 实名制类型（DataTicketReg.TYPE_*）
TICKET_REG_TYPE = {
    0: "无需实名",
    1: "一单一证",
    2: "一人一证",
}


def status_name(mapping: dict, code, unknown="未知") -> str:
    """状态码转可读文本。"""
    if code is None:
        return unknown
    return mapping.get(code, f"{unknown}({code})")


def classify_goods(body: dict) -> str:
    """根据 commonDetail body 判断商品类型。"""
    if not isinstance(body, dict):
        return GOODS_TYPE_COMMON
    gtype = body.get("type")
    write_off_type = body.get("write_off_type") or 0
    is_write_off = body.get("is_write_off") == 1 or gtype == 2 and write_off_type != 0
    if body.get("ticket_goods_data"):
        return GOODS_TYPE_TICKET
    if body.get("voucher_goods_data"):
        return GOODS_TYPE_VOUCHER
    if is_write_off:
        return GOODS_TYPE_WRITE_OFF
    return GOODS_TYPE_COMMON


def extract_goods_info(body: dict) -> dict:
    """提取商品关键信息（供展示与下单参数选择）。"""
    sbs = _sell_button_status(body)
    g = {
        "id": body.get("id") or body.get("goods_id"),
        "name": body.get("name") or body.get("goods_name") or "",
        "type": classify_goods(body),
        "price": body.get("price") or body.get("shop_price") or "",
        "store": body.get("store"),
        "sell_button_status": sbs,
        "sell_button_status_name": status_name(SELL_BUTTON_STATUS, sbs),
        "sell_start_time": _sell_start_time(body),
        "system_time": body.get("system_time"),
    }

    # 答题配置
    cfg = body.get("pre_purchase_question_config") or {}
    g["question_config"] = {
        "is_need_question": cfg.get("is_need_question"),
        "answer_buy_timing_type": cfg.get("answer_buy_timing_type"),
        "question_notice": cfg.get("question_notice"),
    }

    # 实名制（ticket_reg_type 可能在顶层或 ticket_reg_data 内）
    reg = body.get("ticket_reg_data") or {}
    reg_type = body.get("ticket_reg_type")
    if reg_type is None:
        reg_type = reg.get("ticket_reg_type")
    g["ticket_reg_type"] = reg_type
    g["ticket_reg_type_name"] = status_name(TICKET_REG_TYPE, reg_type)
    g["ticket_reg_notice"] = reg.get("ticket_reg_notice") or body.get("ticket_reg_notice")

    # SKU 列表
    g["sku_list"] = _extract_skus(body)

    # 门店列表（核销/预约）
    g["store_list"] = _extract_stores(body)

    # 票务：场馆 + 日期 + 场次
    g["venue_list"] = _extract_venues(body)
    g["calendar_data"] = _extract_calendar(body)

    return g


def _sell_button_status(body: dict):
    for key in ("normal_goods_sell_data", "normal_write_off_goods_sell_data",
                "reservation_goods_data_more"):
        sd = body.get(key) or {}
        if isinstance(sd, dict) and sd.get("sell_button_status") is not None:
            return sd.get("sell_button_status")
    tg = body.get("ticket_goods_data") or {}
    if isinstance(tg, dict) and tg.get("button_status") is not None:
        return tg.get("button_status")
    return None


def _sell_start_time(body: dict):
    for key in ("normal_goods_sell_data", "normal_write_off_goods_sell_data",
                "reservation_goods_data_more"):
        sd = body.get(key) or {}
        if isinstance(sd, dict) and sd.get("sell_start_time"):
            return sd.get("sell_start_time")
    tg = body.get("ticket_goods_data") or {}
    if isinstance(tg, dict) and tg.get("sell_start_time"):
        return tg.get("sell_start_time")
    return None


def _extract_skus(body: dict) -> list:
    sku_raw = None
    for k in ("goods_price_set", "sku_list", "skus"):
        v = body.get(k)
        if isinstance(v, list) and v:
            sku_raw = v
            break
    out = []
    for s in sku_raw or []:
        if not isinstance(s, dict):
            continue
        sku_id = s.get("id") or s.get("sku_id")
        if sku_id is None:
            continue
        out.append({
            "sku_id": sku_id,
            "name": s.get("name") or s.get("spec_name") or "",
            "price": s.get("price"),
            "store": s.get("store", s.get("stock")),
        })
    return out


def _extract_stores(body: dict) -> list:
    store_raw = None
    for key in ("normal_write_off_goods_sell_data", "reservation_goods_data_more"):
        sd = body.get(key) or {}
        if isinstance(sd, dict) and isinstance(sd.get("store_list"), list) and sd["store_list"]:
            store_raw = sd["store_list"]
            break
    out = []
    for s in store_raw or []:
        if not isinstance(s, dict):
            continue
        bs = s.get("button_status")
        out.append({
            "id": s.get("id"),
            "store_id": s.get("store_id") or s.get("id"),
            "store_name": s.get("store_name") or "",
            "store_address": s.get("store_address") or "",
            "surplus_store": s.get("surplus_store"),
            "button_status": bs,
            "button_status_name": status_name(STORE_BUTTON_STATUS, bs),
        })
    return out


def _extract_venues(body: dict) -> list:
    tg = body.get("ticket_goods_data") or {}
    out = []
    for v in tg.get("venue_list") or []:
        if not isinstance(v, dict):
            continue
        skus = []
        for s in v.get("ticket_sku_list") or []:
            if isinstance(s, dict):
                tss = s.get("ticket_sku_status")
                skus.append({
                    "sku_id": s.get("sku_id"),
                    "name": s.get("name") or "",
                    "price": s.get("price"),
                    "ticket_surplus_store": s.get("ticket_surplus_store"),
                    "ticket_sku_status": tss,
                    "ticket_sku_status_name": status_name(TICKET_SKU_STATUS, tss),
                })
        vbs = v.get("button_status")
        out.append({
            "venue_id": v.get("id"),
            "venue_show_time": v.get("venue_show_time") or "",
            "venue_time": v.get("venue_time"),
            "button_status": vbs,
            "button_status_name": status_name(SELL_BUTTON_STATUS, vbs),
            "venue_surplus_store": v.get("venue_surplus_store"),
            "ticket_sku_list": skus,
        })
    return out


def _extract_calendar(body: dict) -> list:
    tg = body.get("ticket_goods_data") or {}
    out = []
    for d in tg.get("calendar_data") or []:
        if isinstance(d, dict):
            out.append({"venue_start_date": d.get("venue_start_date") or "",
                        "select_status": d.get("select_status")})
    return out


def extract_reservation_options(body: dict) -> list:
    """预约类商品：日期 -> 场次 选项。返回 [{reservation_date, show_date, quantum_list:[...]}]"""
    sd = body.get("reservation_goods_data_more") or {}
    out = []
    for t in sd.get("reservation_goods_time_list") or []:
        if not isinstance(t, dict):
            continue
        quantums = []
        for q in t.get("quantum_list") or []:
            if isinstance(q, dict):
                qs = q.get("quantum_status")
                quantums.append({
                    "id": q.get("id"),
                    "reservation_time_quantum": q.get("reservation_time_quantum") or "",
                    "quantum_status": qs,
                    "quantum_status_name": status_name(QUANTUM_STATUS, qs),
                    "store": q.get("store"),
                })
        rs = t.get("reservation_status")
        out.append({
            "reservation_date": t.get("reservation_date") or "",
            "show_date": t.get("show_date") or "",
            "reservation_status": rs,
            "reservation_status_name": status_name(RESERVATION_STATUS, rs),
            "quantum_list": quantums,
        })
    return out
