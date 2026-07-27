#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
合并 OCR 和 PDF 解析结果，进行三单校验，生成排序后的明细数据。

用法: python analyze_and_validate.py <ocr_json> <pdf_json> <output_json>
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, date as dt_date
from pathlib import Path


# ─── 常量 ────────────────────────────────────────────────────────────────

FOLDER_CATEGORY_MAP = {
    "飞机": "flight", "高铁": "train", "网约车": "ride_hailing",
    "自驾": "self_drive", "住宿": "hotel", "其它": "other", "其他": "other",
    "路桥费": "toll", "路桥": "toll",
}

CATEGORY_CN = {
    "flight": "机票", "train": "高铁", "ride_hailing": "网约车",
    "self_drive": "自驾", "hotel": "住宿", "meal_allowance": "误餐补贴",
    "self_drive_subsidy": "自驾补贴", "toll": "路桥费", "other": "其它",
}

CATEGORY_ORDER = {
    "flight": 0, "train": 1, "ride_hailing": 2, "self_drive": 3,
    "hotel": 4, "meal_allowance": 5, "self_drive_subsidy": 3,
    "toll": 3, "other": 6,
}

TYPE_ROLE = {
    "invoice": "发票", "gas_invoice": "发票", "trip_itinerary": "行程单",
    "boarding_pass": "登机牌", "hotel_bill": "酒店账单", "navigation": "导航截图",
    "toll": "路桥费凭证", "payment": "支付凭证", "train_ticket": "火车票",
    "unknown": "证明文件", "image": "图片凭证",
}

VALIDATION_RULES = {
    "flight":       {"required": [["invoice"], ["boarding_pass"]]},
    "train":        {"required": [["invoice"], ["trip_itinerary", "boarding_pass"]]},
    "ride_hailing": {"required": [["invoice"], ["trip_itinerary", "unknown"]]},
    "self_drive":   {"required": [["gas_invoice"], ["navigation"]]},
    "hotel":        {"required": [["invoice"], ["hotel_bill", "unknown"]]},
    "other":        {"required": [["invoice"]]},
}


# ─── 地址概括 ────────────────────────────────────────────────────────────

def summarize_address(address, max_len=6):
    """将地址概括为简短名称。"""
    if not address:
        return ""
    s = address.strip()

    # 去除括号 / 方括号内容
    s = re.sub(r'[（(][^）)]*[）)]', '', s)
    s = re.sub(r'[【\[][^\]】]*[】\]]', '', s)
    s = s.strip()
    if not s:
        return ""

    # ── 特殊：机场 ──
    if "机场" in s:
        idx = s.index("机场")
        before = s[:idx].replace("国际", "").rstrip()
        if len(before) >= 4:
            name = before[:2]          # 取城市名
        elif before:
            name = before
        else:
            name = ""
        return (name + "机场") if name else "机场"

    # ── 特殊：火车站 / 南站 / 东站 … ──
    for kw in ("火车站", "南站", "东站", "西站", "北站"):
        if kw in s:
            idx = s.index(kw)
            before = s[:idx].rstrip()
            name = before[-2:] if len(before) >= 2 else before
            station = kw if kw == "火车站" else kw[-1:]
            return name + station

    # ── 特殊：酒店 / 宾馆 / 旅馆 ──
    for kw in ("酒店", "宾馆", "旅馆"):
        if kw in s:
            idx = s.index(kw)
            before = s[:idx].rstrip()
            # 去除行政区划前缀
            before = re.sub(r'[\u4e00-\u9fff]{1,4}(?:省|市|区|县|镇)', '', before).rstrip()
            # 去除路名前缀
            before = re.sub(r'[\u4e00-\u9fff]+(?:路|街|道|大道)', '', before).rstrip()
            # 取品牌名（末 2 字）
            brand = before[-2:] if len(before) >= 2 else (before or kw)
            return (brand + kw)[:max_len]

    # ── 通用处理 ──
    parts = re.split(r'[|｜\-\—~～]', s)
    cleaned = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 跳过纯行政区划
        if re.fullmatch(r'[\u4e00-\u9fff]{1,4}(?:省|市|区|县|镇)', part):
            continue
        # 去除前缀
        part = re.sub(r'^[\u4e00-\u9fff]{1,4}(?:省|市|区|县|镇)', '', part)
        part = re.sub(r'^[\u4e00-\u9fff]+(?:路|街|道|大道)', '', part)
        part = part.strip()
        if not part:
            continue
        # 跳过后缀片段
        if re.search(r'(?:号|栋|楼|层|室|门)$', part):
            continue
        cleaned.append(part)

    result = "".join(cleaned)
    return result[:max_len] if result else s[:max_len]


# ─── 使用内容生成 ────────────────────────────────────────────────────────

def generate_description(expense_type, data, seq_num, name=""):
    """根据费用类型和数据生成使用内容字符串。"""
    if expense_type == "ride_hailing":
        o = summarize_address(data.get("origin", ""))
        d = summarize_address(data.get("destination", ""))
        return f"{seq_num}，网约车：{o} - {d}"

    if expense_type == "self_drive":
        o = summarize_address(data.get("origin", ""))
        d = summarize_address(data.get("destination", ""))
        km = data.get("distance_km", 0)
        km_s = str(int(km)) if isinstance(km, float) and km == int(km) else str(km)
        return f"自驾：{o} - {d}（{km_s}公里）"

    if expense_type == "flight":
        dep = data.get("departure_city", data.get("origin", ""))
        arr = data.get("arrival_city", data.get("destination", ""))
        return f"{seq_num}，机票：{dep} - {arr}（{data.get('flight_no', '')}）"

    if expense_type == "train":
        return (f"{seq_num}，高铁：{data.get('origin', '')} - "
                f"{data.get('destination', '')}（{data.get('train_no', '')}）")

    if expense_type == "toll":
        o = summarize_address(data.get("origin", ""))
        d = summarize_address(data.get("destination", ""))
        return f"路桥费：{o} - {d}"

    if expense_type == "hotel":
        n = data.get("nights", 1)
        r = data.get("rooms", 1)
        return f"住宿费：{n}晚/{r}间"

    if expense_type == "meal_allowance":
        n = data.get("nights", 1)
        return f"误餐补贴（{n}晚-{name}）"

    if expense_type == "self_drive_subsidy":
        km = data.get("distance_km", 0)
        km_s = str(int(km)) if isinstance(km, float) and km == int(km) else str(km)
        return f"自驾补贴（{km_s}公里）"

    # other
    seller = data.get("seller", "") or data.get("description", "") or "其它费用"
    return str(seller)[:20]


# ─── 辅助函数 ────────────────────────────────────────────────────────────

def get_category(file_path):
    """从文件路径中提取费用类别。"""
    for part in Path(file_path).parts:
        if part in FOLDER_CATEGORY_MAP:
            return FOLDER_CATEGORY_MAP[part]
    return "other"


def format_date_display(date_str):
    """yyyy-mm-dd → X月X日"""
    if not date_str:
        return ""
    try:
        p = date_str.split("-")
        if len(p) >= 3:
            return f"{int(p[1])}月{int(p[2])}日"
        if len(p) == 2:
            return f"{int(p[1])}月"
    except (IndexError, ValueError):
        pass
    return date_str


def to_float(val, default=0.0):
    """安全转换为 float。"""
    if val is None:
        return default
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return default


def fmt_amount(v):
    return f"{to_float(v):.2f}"


def parse_dt(date_str):
    """日期字符串 → datetime，失败返回 None。"""
    if not date_str or len(date_str) < 10:
        return None
    try:
        return datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        return None


def date_sort_key(date_str):
    dt = parse_dt(date_str)
    return dt or datetime.max


def extract_trip_date(text):
    """从行程单/发票原文中提取真实『行程时间/上车时间』日期（YYYY-MM-DD）。"""
    if not text:
        return None
    for pat in (r'行程时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})',
                r'上车时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})',
                r'出行时间[:：]\s*(\d{4}-\d{1,2}-\d{1,2})'):
        m = re.search(pat, text)
        if m:
            y, mo, d = m.group(1).split("-")
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return None


def norm_path(p):
    return str(p).replace("\\", "/")


def _first_nonnull(files, field):
    """从文件列表中取第一个非空的 extracted 字段值。"""
    for f in files:
        v = f.get("extracted", {}).get(field)
        if v is not None:
            return v
    return None


def _sum_amount(files, *types):
    """累加指定类型文件的金额。"""
    tset = set(types) if types else None
    return sum(
        to_float(f["extracted"].get("amount"))
        for f in files
        if (tset is None or f["inferred_type"] in tset)
    )


def _effective_amount(files, invoice_types=("invoice",), pay_types=("payment", "hotel_bill")):
    """
    规则5：支付截图金额与发票金额不一致时，取较低者。
    返回 (effective_amount, source)。
    """
    inv_total = sum(
        to_float(f["extracted"].get("amount"))
        for f in files if f["inferred_type"] in invoice_types
    )
    pay_total = sum(
        to_float(f["extracted"].get("payment_amount"))
        or to_float(f["extracted"].get("amount"))
        for f in files if f["inferred_type"] in pay_types
    )
    if inv_total > 0 and pay_total > 0:
        if pay_total < inv_total:
            return pay_total, f"payment(lower:{pay_total:.2f}<invoice:{inv_total:.2f})"
        return inv_total, "invoice"
    return inv_total or pay_total, "single_source"


def _page_images(files):
    """收集所有文件中的 page_images；原始图片（无渲染页）直接引用原图路径。"""
    imgs = []
    for f in files:
        imgs.extend(f.get("page_images", []))
        # PDF 以外（如微信截图等原始图片）没有 page_image 渲染，直接引用原文件
        if f.get("inferred_type") in ("unknown", "image") and f.get("path"):
            imgs.append(f["path"])
    return imgs


def _file_list(files):
    return [
        {"path": f["path"], "type": f["inferred_type"],
         "role": TYPE_ROLE.get(f["inferred_type"], "文件")}
        for f in files
    ]


def extract_person_name(file_index):
    """从凭证数据中提取报销人姓名。"""
    for info in file_index.values():
        ext = info.get("extracted", {})
        for key in ("buyer", "passenger_name", "name", "passenger"):
            n = ext.get(key)
            if n and 2 <= len(str(n)) <= 10:
                return str(n).strip()
    return ""


# ─── 数据加载与合并 ──────────────────────────────────────────────────────

def load_and_merge(ocr_path, pdf_path):
    """加载 OCR 和 PDF 结果，合并为统一的文件索引。"""
    with open(ocr_path, "r", encoding="utf-8") as f:
        ocr = json.load(f)
    with open(pdf_path, "r", encoding="utf-8") as f:
        pdf = json.load(f)

    idx = {}

    # 1) OCR 结果
    for item in ocr.get("results", []):
        p = norm_path(item.get("file_path", ""))
        if p:
            idx[p] = {
                "path": p, "source": "ocr",
                "extracted": item.get("extracted", {}),
                "inferred_type": item.get("inferred_type", "unknown"),
                "page_images": [],
                "full_text": item.get("full_text", ""),
            }

    # 2) PDF 结果（优先，覆盖 / 补充 OCR）
    for item in pdf.get("results", []):
        p = norm_path(item.get("file_path", ""))
        if not p:
            continue
        pdf_ext = item.get("extracted", {})
        if p in idx:
            ocr_ext = idx[p].get("extracted", {})
            merged = {}
            for k in set(list(ocr_ext) + list(pdf_ext)):
                merged[k] = pdf_ext.get(k, ocr_ext.get(k))
            idx[p].update({
                "source": "pdf+ocr", "extracted": merged,
                "inferred_type": item.get("inferred_type", idx[p]["inferred_type"]),
                "page_images": item.get("page_images", []),
                "full_text": item.get("full_text", idx[p].get("full_text", "")),
            })
        else:
            idx[p] = {
                "path": p, "source": "pdf",
                "extracted": pdf_ext,
                "inferred_type": item.get("inferred_type", "unknown"),
                "page_images": item.get("page_images", []),
                "full_text": item.get("full_text", ""),
            }
    return idx


def group_by_category(file_index):
    """按费用类别分组。"""
    groups = defaultdict(list)
    for path, info in file_index.items():
        groups[get_category(path)].append(info)
    return dict(groups)


# ─── 三单校验 ────────────────────────────────────────────────────────────

def validate_category(category, files):
    """对一个费用类别进行三单校验，返回 {status, issues}。"""
    rules = VALIDATION_RULES.get(category, VALIDATION_RULES["other"])
    types = [f["inferred_type"] for f in files]
    issues = []
    status = "complete"

    for req_group in rules["required"]:
        if not any(t in types for t in req_group):
            names = "/".join(TYPE_ROLE.get(t, t) for t in req_group)
            if any(t in ("invoice", "gas_invoice") for t in req_group):
                status = "missing_invoice"
            else:
                status = "missing_proof"
            issues.append(f"缺少{names}")

    # 金额校验
    if status == "complete":
        amounts = defaultdict(list)
        for f in files:
            a = to_float(f["extracted"].get("amount"))
            if a > 0:
                amounts[f["inferred_type"]].append(a)

        if category in ("flight", "train", "ride_hailing"):
            inv_t = sum(amounts.get("invoice", []))
            trip_t = sum(amounts.get("trip_itinerary", []))
            if inv_t > 0 and trip_t > 0 and abs(inv_t - trip_t) > 0.05:
                status = "amount_mismatch"
                issues.append(
                    f"金额不一致：发票¥{inv_t:.2f} vs 行程单¥{trip_t:.2f}")

        elif category == "self_drive":
            gas_t = sum(amounts.get("gas_invoice", []))
            km = to_float(_first_nonnull(
                [f for f in files if f["inferred_type"] == "navigation"],
                "distance_km"))
            if gas_t > 0 and km > 0 and gas_t < km:
                status = "amount_mismatch"
                issues.append(f"加油金额¥{gas_t:.2f} < 里程{km}公里")

        elif category == "hotel":
            inv_t = sum(amounts.get("invoice", []))
            bill_t = sum(amounts.get("hotel_bill", []))
            if inv_t > 0 and bill_t > 0 and abs(inv_t - bill_t) > 0.05:
                status = "amount_mismatch"
                issues.append(
                    f"金额不一致：发票¥{inv_t:.2f} vs 账单¥{bill_t:.2f}")

    return {"status": status, "issues": issues}


# ─── 创建费用条目 ────────────────────────────────────────────────────────

def _merged_extracted(files):
    """合并多个文件的 extracted 字段，后出现的覆盖先出现的。"""
    merged = {}
    for f in files:
        for k, v in f.get("extracted", {}).items():
            if v is not None and k not in merged:
                merged[k] = v
    return merged


def create_expenses(cat_groups):
    """为每个费用类别生成费用条目。"""
    expenses = []

    for cat, files in cat_groups.items():
        # 分离路桥费
        toll_files = [f for f in files if f["inferred_type"] == "toll"]
        normal_files = [f for f in files if f["inferred_type"] != "toll"]

        page_imgs = _page_images(files)
        flist = _file_list(files)
        city = _first_nonnull(normal_files, "city") or ""
        date = _first_nonnull(normal_files, "date") or ""

        # ── 网约车 ──
        if cat == "ride_hailing":
            # 以「行程单」为拆分依据：每张行程单 = 一（多）笔行程，
            # 其金额即该行程真实费用；发票仅作凭证，不再单独计条目，
            # 避免与行程单重复计费（修复此前 T3/享道 等发票被漏计的问题）。
            itinerary_files = [f for f in normal_files
                               if f["inferred_type"] == "trip_itinerary"]
            seq = 0
            if itinerary_files:
                for f in itinerary_files:
                    td = f["extracted"].get("trip_details")
                    if td and isinstance(td, list) and td:
                        # 多行程行程单：逐笔拆分
                        for trip in td:
                            seq += 1
                            amt = to_float(trip.get("amount"))
                            td_date = trip.get("date", date)
                            dist_str = trip.get("distance",
                                                trip.get("distance_km", "0"))
                            km_m = re.search(r'([\d.]+)', str(dist_str))
                            km = to_float(km_m.group(1)) if km_m else 0
                            data = {
                                "origin": trip.get("origin", ""),
                                "destination": trip.get("destination", ""),
                                "distance_km": km, "amount": amt,
                            }
                            expenses.append({
                                "date": str(td_date), "category": "ride_hailing",
                                "city": city,
                                "description": generate_description("ride_hailing", data, seq),
                                "amount": amt, "files": flist, "page_images": page_imgs,
                                "is_meal_allowance": False, "is_self_drive_subsidy": False,
                            })
                    else:
                        # 单行程行程单
                        seq += 1
                        ext = f["extracted"]
                        data = {
                            "origin": ext.get("origin", ""),
                            "destination": ext.get("destination", ""),
                            "distance_km": to_float(ext.get("distance_km")),
                            "amount": to_float(ext.get("amount")),
                        }
                        expenses.append({
                            "date": extract_trip_date(f.get("full_text", "")) or ext.get("date", date) or date,
                            "category": "ride_hailing", "city": city,
                            "description": generate_description("ride_hailing", data, seq),
                            "amount": to_float(ext.get("amount")),
                            "files": flist, "page_images": page_imgs,
                            "is_meal_allowance": False, "is_self_drive_subsidy": False,
                        })
            else:
                # 无行程单时，回退到按发票建条目
                inv_files = [f for f in normal_files
                             if f["inferred_type"] in ("invoice", "trip_itinerary")]
                if not inv_files:
                    inv_files = normal_files
                for i, f in enumerate(inv_files, 1):
                    ext = f["extracted"]
                    data = {
                        "origin": ext.get("origin", ""),
                        "destination": ext.get("destination", ""),
                        "distance_km": to_float(ext.get("distance_km")),
                        "amount": to_float(ext.get("amount")),
                    }
                    expenses.append({
                        "date": ext.get("date", date) or date,
                        "category": "ride_hailing", "city": city,
                        "description": generate_description("ride_hailing", data, i),
                        "amount": to_float(ext.get("amount")),
                        "files": flist, "page_images": page_imgs,
                        "is_meal_allowance": False, "is_self_drive_subsidy": False,
                    })

        # ── 飞机（规则2：航线优先从登机牌OCR取，多航段拆分）───
        elif cat == "flight":
            # ① 收集登机牌文件，提取航线
            bp_files = [f for f in normal_files if f["inferred_type"] == "boarding_pass"]
            inv_files = [f for f in normal_files if f["inferred_type"] == "invoice"]
            pay_files = [f for f in normal_files if f["inferred_type"] == "payment"]

            boarding_routes = []  # [(date, origin_dest, flight_no, file), ...]
            for f in bp_files:
                ext = f["extracted"]
                bo = ext.get("boarding_origin") or ext.get("origin")
                bd = ext.get("boarding_dest") or ext.get("destination")
                fd = ext.get("date") or date
                fn = ext.get("flight_no", "")
                if bo and bd:
                    boarding_routes.append((fd, bo, bd, fn, f))

            # ② 收集发票金额（按金额排序）
            inv_amounts = sorted(
                [to_float(f["extracted"].get("amount")) for f in inv_files if to_float(f["extracted"].get("amount")) > 0]
            )

            # ③ 支付确认金额（用于规则5：取较低者）
            pay_amounts = [
                to_float(f["extracted"].get("payment_amount"))
                or to_float(f["extracted"].get("amount"))
                for f in pay_files
            ]
            pay_total = sum(a for a in pay_amounts if a > 0)

            if len(boarding_routes) >= 2 and len(inv_amounts) >= 2:
                # 多航段：每个登机牌对应一条明细
                # 按登机牌日期排序，与发票金额配对（假设顺序一致或按金额匹配）
                boarding_routes.sort(key=lambda x: date_sort_key(x[0]))
                for i, (fd, bo, bd, fn, bf) in enumerate(boarding_routes):
                    amt = inv_amounts[i] if i < len(inv_amounts) else 0
                    # 规则5：支付截图金额更低时取低
                    if pay_total > 0 and i == 0 and pay_total < sum(inv_amounts):
                        amt = min(amt, pay_total) if i == 0 else amt
                    data = {"departure_city": bo, "arrival_city": bd,
                            "flight_no": fn, "amount": amt}
                    expenses.append({
                        "date": str(fd), "category": "flight",
                        "city": city,
                        "description": generate_description("flight", data, i + 1),
                        "amount": amt, "files": flist, "page_images": page_imgs,
                        "is_meal_allowance": False, "is_self_drive_subsidy": False,
                    })
            elif len(boarding_routes) == 1:
                # 单个登机牌
                fd, bo, bd, fn, _bf = boarding_routes[0]
                amount = _sum_amount(normal_files, "invoice")
                data = {"departure_city": bo, "arrival_city": bd,
                        "flight_no": fn, "amount": amount}
                expenses.append({
                    "date": str(fd), "category": "flight", "city": city,
                    "description": generate_description("flight", data, 1),
                    "amount": amount, "files": flist, "page_images": page_imgs,
                    "is_meal_allowance": False, "is_self_drive_subsidy": False,
                })
            else:
                # 无登机牌：回退到合并模式
                ext = _merged_extracted(normal_files)
                amount = _sum_amount(normal_files, "invoice")
                dep = ext.get("departure_city") or ext.get("origin") or city
                arr = ext.get("arrival_city") or ext.get("destination") or ""
                data = {"departure_city": dep, "arrival_city": arr,
                        "flight_no": ext.get("flight_no", ""), "amount": amount}
                expenses.append({
                    "date": date, "category": "flight", "city": city,
                    "description": generate_description("flight", data, 1),
                    "amount": amount, "files": flist, "page_images": page_imgs,
                    "is_meal_allowance": False, "is_self_drive_subsidy": False,
                })

        # ── 高铁 ──
        elif cat == "train":
            ext = _merged_extracted(normal_files)
            amount = _sum_amount(normal_files, "invoice", "train_ticket")
            # 收集所有车次
            train_nos = []
            for f in normal_files:
                tn = f["extracted"].get("train_no")
                if tn and tn not in train_nos:
                    train_nos.append(tn)
            data = {
                "origin": ext.get("origin", ""),
                "destination": ext.get("destination", ""),
                "train_no": "/".join(train_nos), "amount": amount,
            }
            expenses.append({
                "date": date, "category": "train", "city": city,
                "description": generate_description("train", data, 1),
                "amount": amount, "files": flist, "page_images": page_imgs,
                "is_meal_allowance": False, "is_self_drive_subsidy": False,
            })

        # ── 自驾 ──
        elif cat == "self_drive":
            nav_files = [f for f in normal_files if f["inferred_type"] == "navigation"]
            km = 0
            for f in nav_files:
                v = to_float(f["extracted"].get("distance_km"))
                if v > km:
                    km = v
            origin = _first_nonnull(normal_files, "origin") or ""
            dest = _first_nonnull(normal_files, "destination") or ""
            gas_amt = _sum_amount(normal_files, "gas_invoice")
            data = {"origin": origin, "destination": dest,
                    "distance_km": km, "amount": gas_amt}
            expenses.append({
                "date": date, "category": "self_drive", "city": city,
                "description": generate_description("self_drive", data, 1),
                "amount": gas_amt,
                "files": _file_list(normal_files), "page_images": page_imgs,
                "is_meal_allowance": False, "is_self_drive_subsidy": False,
                "_km": km,
            })
            # 路桥费条目
            for tf in toll_files:
                t_ext = tf["extracted"]
                t_amt = to_float(t_ext.get("amount"))
                t_data = {"origin": t_ext.get("origin", ""),
                          "destination": t_ext.get("destination", "")}
                expenses.append({
                    "date": t_ext.get("date", date) or date,
                    "category": "toll", "city": city,
                    "description": generate_description("toll", t_data, 1),
                    "amount": t_amt, "files": _file_list([tf]),
                    "page_images": tf.get("page_images", []),
                    "is_meal_allowance": False, "is_self_drive_subsidy": False,
                })

        # ── 住宿（规则3：从所有源合并找最完整信息；规则5：取较低金额）──
        elif cat == "hotel":
            # 按信息完整度排序：hotel_bill > invoice > payment > unknown
            _info_priority = {"hotel_bill": 0, "invoice": 1, "payment": 2, "unknown": 3}
            sorted_files = sorted(normal_files, key=lambda f: _info_priority.get(f["inferred_type"], 99))

            # 从优先级最高的文件提取结构化信息
            best_hotel = {}
            for f in sorted_files:
                ext = f["extracted"]
                # 累积填充（后出现的非空值不覆盖已有的）
                for key in ("check_in", "check_out", "hotel_name", "room_no",
                            "city", "date", "origin", "destination"):
                    val = ext.get(key)
                    if val is not None and key not in best_hotel:
                        best_hotel[key] = val

            check_in = best_hotel.get("check_in") or best_hotel.get("date") or date
            check_out = best_hotel.get("check_out") or ""
            nights, rooms = 1, 1
            if check_in and check_out and len(check_in) >= 10 and len(check_out) >= 10:
                try:
                    d_in = datetime.strptime(check_in[:10], "%Y-%m-%d").date()
                    d_out = datetime.strptime(check_out[:10], "%Y-%m-%d").date()
                    nights = max((d_out - d_in).days, 1)
                except ValueError:
                    pass
            for f in normal_files:
                if f["inferred_type"] == "hotel_bill":
                    r = f["extracted"].get("rooms")
                    if r is not None:
                        rooms = int(to_float(r, 1))

            # 规则5：发票金额 vs 支付确认金额，取低者
            inv_amt = _sum_amount(normal_files, "invoice")
            pay_amts = [
                to_float(f["extracted"].get("payment_amount"))
                or to_float(f["extracted"].get("amount"))
                for f in normal_files if f["inferred_type"] in ("payment", "hotel_bill")
            ]
            pay_amt_total = sum(a for a in pay_amts if a > 0)
            if inv_amt > 0 and pay_amt_total > 0:
                amount = min(inv_amt, pay_amt_total)
            else:
                amount = inv_amt or pay_amt_total

            data = {
                "nights": nights, "rooms": rooms,
                "amount": amount,
                "hotel_name": best_hotel.get("hotel_name", ""),
            }
            expenses.append({
                "date": check_in, "category": "hotel",
                "city": best_hotel.get("city") or city,
                "description": generate_description("hotel", data, 1),
                "amount": amount, "files": flist, "page_images": page_imgs,
                "is_meal_allowance": False, "is_self_drive_subsidy": False,
                "_hotel": {"check_in": check_in, "check_out": check_out,
                           "nights": nights, "rooms": rooms,
                           "hotel_name": best_hotel.get("hotel_name", "")},
            })

        # ── 路桥费（独立文件夹） ──
        elif cat == "toll":
            for tf in files:
                t_ext = tf["extracted"]
                t_amt = to_float(t_ext.get("amount"))
                t_data = {"origin": t_ext.get("origin", ""),
                          "destination": t_ext.get("destination", "")}
                expenses.append({
                    "date": t_ext.get("date", "") or "",
                    "category": "toll", "city": city,
                    "description": generate_description("toll", t_data, 1),
                    "amount": t_amt, "files": _file_list([tf]),
                    "page_images": tf.get("page_images", []),
                    "is_meal_allowance": False, "is_self_drive_subsidy": False,
                })

        # ── 其它 ──
        else:
            inv_files = [f for f in normal_files
                         if f["inferred_type"] in ("invoice", "unknown")]
            if not inv_files:
                inv_files = normal_files
            for f in inv_files:
                ext = f["extracted"]
                amt = to_float(ext.get("amount"))
                data = {"seller": ext.get("seller", ""),
                        "description": ext.get("description", ""), "amount": amt}
                expenses.append({
                    "date": ext.get("date", date) or date,
                    "category": "other", "city": city,
                    "description": generate_description("other", data, 1),
                    "amount": amt,
                    "files": _file_list([f]) if len(normal_files) > 1 else flist,
                    "page_images": f.get("page_images", []),
                    "is_meal_allowance": False, "is_self_drive_subsidy": False,
                })

    return expenses


# ─── 排序（含路桥费就近插入） ──────────────────────────────────────────

def sort_expenses(expenses):
    """按类别顺序 → 日期排序，路桥费就近插入 ride_hailing/self_drive 后。"""
    tolls = [e for e in expenses if e["category"] == "toll"]
    normals = [e for e in expenses if e["category"] != "toll"]

    normals.sort(key=lambda e: (
        CATEGORY_ORDER.get(e["category"], 99),
        date_sort_key(e["date"]),
    ))

    if not tolls:
        return normals

    # 按日期排序路桥费，逐个插入
    tolls.sort(key=lambda e: date_sort_key(e["date"]))
    for toll in tolls:
        toll_dt = parse_dt(toll["date"])
        best_idx = -1
        best_diff = float("inf")
        for i, e in enumerate(normals):
            if e["category"] not in ("ride_hailing", "self_drive"):
                continue
            e_dt = parse_dt(e["date"])
            if e_dt and toll_dt:
                diff = abs((toll_dt - e_dt).total_seconds())
            else:
                diff = float("inf")
            # 同日或之后：优先插在后面
            if diff < best_diff or (diff == best_diff and e_dt and toll_dt and e_dt <= toll_dt):
                best_diff = diff
                best_idx = i
        if best_idx >= 0:
            normals.insert(best_idx + 1, toll)
        else:
            # 没有匹配的交通条目，追加到末尾
            normals.append(toll)

    return normals


# ─── 主流程 ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 4:
        print(f"用法: python {sys.argv[0]} <ocr_json> <pdf_json> <output_json>")
        sys.exit(1)

    ocr_json, pdf_json, output_json = sys.argv[1], sys.argv[2], sys.argv[3]

    # 1. 加载与合并
    print("=" * 55)
    print("  报销单据分析 - 三单校验")
    print("=" * 55)
    print("\n[1/6] 加载数据...")
    file_index = load_and_merge(ocr_json, pdf_json)
    print(f"  共 {len(file_index)} 个凭证文件")

    # 2. 按类别分组
    cat_groups = group_by_category(file_index)
    cat_summary = ", ".join(
        f"{CATEGORY_CN.get(k, k)}({len(v)})"
        for k, v in sorted(cat_groups.items(), key=lambda x: CATEGORY_ORDER.get(x[0], 99))
    )
    print(f"  费用类别: {cat_summary}")

    # 3. 三单校验
    print("\n[2/6] 三单校验...")
    validation_issues = []
    total_groups = len(cat_groups)
    complete_count = 0
    for cat, cfiles in sorted(cat_groups.items(), key=lambda x: CATEGORY_ORDER.get(x[0], 99)):
        result = validate_category(cat, cfiles)
        cat_cn = CATEGORY_CN.get(cat, cat)
        if result["status"] == "complete":
            complete_count += 1
            print(f"  [{cat_cn}] 通过")
        else:
            msg = "; ".join(result["issues"])
            print(f"  [{cat_cn}] {result['status']}: {msg}")
            validation_issues.append({
                "category": cat, "group_index": 0,
                "status": result["status"], "message": msg,
            })

    # 4. 创建费用条目
    print("\n[3/6] 生成费用条目...")
    expenses = create_expenses(cat_groups)
    print(f"  共 {len(expenses)} 个条目")

    # 5. 排序
    print("\n[4/6] 排序...")
    expenses = sort_expenses(expenses)

    # 6. 住宿信息
    hotel_info = {"check_in": "", "check_out": "", "nights": 0,
                  "rooms": 1, "total_amount": 0}
    for e in expenses:
        if e["category"] == "hotel" and "_hotel" in e:
            hd = e["_hotel"]
            hotel_info["nights"] += hd.get("nights", 0)
            hotel_info["total_amount"] = round(
                hotel_info["total_amount"] + e["amount"], 2)
            if not hotel_info["check_in"]:
                hotel_info["check_in"] = hd.get("check_in", "")
            hotel_info["check_out"] = hd.get("check_out", "")
            hotel_info["rooms"] = max(hotel_info["rooms"], hd.get("rooms", 1))

    # 7. 自驾信息
    self_drive_entries = [e for e in expenses if e["category"] == "self_drive"]
    total_km = round(sum(e.get("_km", 0) for e in self_drive_entries), 1)
    subsidy = round(total_km * 1, 2)
    self_drive_info = {"total_km": total_km, "subsidy_amount": subsidy}
    merge_subsidy = len(self_drive_entries) <= 1

    # 8. 误餐补贴
    nights = hotel_info["nights"]
    meal_allowance = {"nights": nights, "rate": 100,
                      "total_amount": nights * 100}

    # 9. 添加衍生条目
    print("\n[5/6] 添加衍生条目...")
    person_name = extract_person_name(file_index)

    if meal_allowance["total_amount"] > 0:
        expenses.append({
            "date": hotel_info.get("check_out", ""),
            "category": "meal_allowance", "city": "",
            "description": generate_description(
                "meal_allowance", {"nights": nights}, 1, person_name),
            "amount": meal_allowance["total_amount"],
            "files": [], "page_images": [],
            "is_meal_allowance": True, "is_self_drive_subsidy": False,
        })
        print(f"  误餐补贴: {nights}晚 x ¥100 = ¥{meal_allowance['total_amount']:.2f}")

    if subsidy > 0:
        if merge_subsidy:
            for e in expenses:
                if e["category"] == "self_drive":
                    e["amount"] = round(e["amount"] + subsidy, 2)
                    e["is_self_drive_subsidy"] = True
                    break
            print(f"  自驾补贴: {total_km}公里 x ¥1 = ¥{subsidy:.2f}（已合并）")
        else:
            expenses.append({
                "date": "", "category": "self_drive_subsidy", "city": "",
                "description": generate_description(
                    "self_drive_subsidy", {"distance_km": total_km}, 1),
                "amount": subsidy, "files": [], "page_images": [],
                "is_meal_allowance": False, "is_self_drive_subsidy": True,
            })
            print(f"  自驾补贴: {total_km}公里 x ¥1 = ¥{subsidy:.2f}（单独条目）")

    # 10. 最终排序 & 序号分配
    expenses = sort_expenses(expenses)
    grand_total = 0.0
    for i, e in enumerate(expenses, 1):
        e["seq"] = i
        e["date_display"] = format_date_display(e.get("date", ""))
        e["category_cn"] = CATEGORY_CN.get(e["category"], e["category"])
        e["item"] = e["category_cn"]  # 明细表“项目”列＝具体发生项目（机票/网约车/住宿/误餐补贴）
        e["project"] = ""
        e["amount_display"] = fmt_amount(e["amount"])
        grand_total = round(grand_total + to_float(e["amount"]), 2)
        # 清理内部字段
        e.pop("_km", None)
        e.pop("_hotel", None)

    # 11. 构建输出
    output = {
        "validation_summary": {
            "total_groups": total_groups,
            "complete": complete_count,
            "missing_items": total_groups - complete_count,
            "issues": validation_issues,
        },
        "expenses": expenses,
        "hotel_info": hotel_info,
        "self_drive_info": self_drive_info,
        "meal_allowance": meal_allowance,
        "grand_total": grand_total,
    }

    # 写入文件
    out_dir = os.path.dirname(output_json)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 12. 打印摘要
    print(f"\n[6/6] 输出完成")
    print(f"\n{'=' * 55}")
    print(f"  结果文件: {output_json}")
    print(f"{'=' * 55}")
    print(f"  校验结果: {complete_count}/{total_groups} 通过")
    if validation_issues:
        print(f"  问题列表:")
        for iss in validation_issues:
            print(f"    - [{CATEGORY_CN.get(iss['category'], iss['category'])}] "
                  f"{iss['status']}: {iss['message']}")
    print(f"  费用条目: {len(expenses)} 项")
    print(f"  住宿: {hotel_info['nights']}晚/{hotel_info['rooms']}间, "
          f"¥{hotel_info['total_amount']:.2f}")
    if self_drive_info["total_km"] > 0:
        print(f"  自驾: {self_drive_info['total_km']}公里, "
              f"补贴¥{self_drive_info['subsidy_amount']:.2f}")
    if meal_allowance["total_amount"] > 0:
        print(f"  误餐补贴: {meal_allowance['nights']}晚 x ¥{meal_allowance['rate']} "
              f"= ¥{meal_allowance['total_amount']:.2f}")
    print(f"  ─────────────────────")
    print(f"  合计: ¥{grand_total:.2f}")
    print(f"{'=' * 55}")

    # 打印条目明细
    print("\n费用明细:")
    for e in expenses:
        marker = ""
        if e.get("is_meal_allowance"):
            marker = " [补贴]"
        elif e.get("is_self_drive_subsidy"):
            marker = " [补贴]"
        print(f"  {e['seq']:>2}. [{e['category_cn']}] "
              f"{e['date_display']:<8s} {e['description']:<36s} "
              f"¥{e['amount_display']}{marker}")


if __name__ == "__main__":
    main()