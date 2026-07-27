#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
解析 PDF 凭证文件，提取发票、行程单、登机牌、酒店账单等关键信息。

用法:
    python parse_pdf_vouchers.py <classified_json> <output_json>
"""

import re
import sys
import json
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium


# ─── 中文大写金额 → 数字 ───────────────────────────────────────────────
CN_NUM = {
    "零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4,
    "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}
CN_UNIT = {
    "拾": 10, "佰": 100, "仟": 1000,
    "万": 10_000, "亿": 100_000_000,
    "角": 0.1, "分": 0.01,
}


def cn_amount_to_number(text: str) -> float | None:
    """将中文大写金额（如'壹佰贰拾叁元肆角伍分'）转换为数字。"""
    text = text.replace("圆", "元").replace("正", "").replace("整", "")
    if "元" in text:
        int_part, frac_part = text.split("元", 1)
    else:
        int_part, frac_part = text, ""

    int_val = 0
    sec_val = 0
    for ch in int_part:
        if ch in CN_NUM:
            sec_val = CN_NUM[ch]
        elif ch in CN_UNIT:
            unit = CN_UNIT[ch]
            if unit >= 10_000:
                int_val += sec_val * unit
                sec_val = 0
            else:
                sec_val *= unit
                int_val += sec_val
                sec_val = 0
    int_val += sec_val

    frac_val = 0.0
    last_digit = 0
    for ch in frac_part:
        if ch in CN_NUM:
            last_digit = CN_NUM[ch]
        elif ch == "角":
            frac_val += last_digit * 0.1
        elif ch == "分":
            frac_val += last_digit * 0.01

    return int_val + frac_val


# ─── 正则模式 ──────────────────────────────────────────────────────────
RE_AMOUNT_YEN = re.compile(r"¥\s*([\d,]+\.?\d*)")
RE_AMOUNT_NUM = re.compile(
    r"(?:金额|合计|总[计额]|小计|价税合计)[：:\s]*¥?\s*([\d,]+\.?\d*)", re.IGNORECASE
)
RE_CN_AMOUNT = re.compile(r"[零壹贰叁肆伍陆柒捌玖一二三四五六七八九拾佰仟万亿角分元圆整正]+")
RE_DATE_CN = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
RE_DATE_SHORT = re.compile(
    r"(\d{2})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})"
)
RE_INVOICE_NO = re.compile(r"\b(\d{20})\b")
RE_BUYER_SELLER = re.compile(
    r"(购买方|销售方|卖方|买方|名称)\s*[：:]\s*(.+)"
)
RE_TAX_ID = re.compile(r"纳税人识别号\s*[：:]\s*(\S+)")
RE_FLIGHT_NO = re.compile(r"([A-Z]{2}\d{3,4}[A-Z]?)")
RE_TRAIN_NO = re.compile(r"[GDCKZTY]\d{1,4}")
RE_DISTANCE_KM = re.compile(r"([\d.]+)\s*km|([\d.]+)\s*公里|里程[：:\s]*([\d.]+)")
RE_ORIGIN_DEST = re.compile(r"(.+?)\s*[→>－\-\u2014]\s*(.+)")
RE_CITY_KEYWORDS = re.compile(
    r"(?:出发|到达|起飞|降落|始发|终到|城市|地点)[：:\s]*(.+?)(?:\s|$|[,，。])"
)

# 类型推断关键词
TYPE_KEYWORDS = {
    "invoice": ["电子发票", "普通发票", "增值税", "发票代码", "发票号码"],
    "trip_itinerary": ["行程单", "滴滴出行", "曹操出行", "第三方网约车", "网约车"],
    "boarding_pass": ["登机", "航班", "BOARDING PASS", "BOARDINGPASS"],
    "hotel_bill": ["酒店", "住客", "客房", "入住", "退房", "HOTEL"],
    "gas_invoice": ["加油", "石油", "中石化", "中石油", "油品", "汽油", "柴油"],
    "toll": ["高速", "ETC", "通行费", "路桥", "收费站", "通行记录"],
    "train_ticket": ["铁路", "12306", "火车票", "车票", "旅客"],
}


def infer_type(text: str) -> str:
    """根据文本关键词推断凭证类型。"""
    for type_name, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return type_name
    return "unknown"


def parse_amount(text: str) -> float | None:
    """从文本中提取金额（优先取 ¥ 标记的数值，其次取合计行，最后尝试中文大写）。"""
    # 优先：¥ 符号
    amounts = RE_AMOUNT_YEN.findall(text)
    if amounts:
        # 清除千分位并取最大值（通常是合计）
        vals = [float(a.replace(",", "")) for a in amounts]
        return max(vals) if vals else None

    # 其次：合计行
    m = RE_AMOUNT_NUM.search(text)
    if m:
        return float(m.group(1).replace(",", ""))

    # 最后：中文大写
    cn = RE_CN_AMOUNT.search(text)
    if cn:
        try:
            val = cn_amount_to_number(cn.group(0))
            if val and val > 0:
                return round(val, 2)
        except Exception:
            pass

    return None


def parse_date(text: str) -> str | None:
    """提取日期，优先完整中文日期。"""
    m = RE_DATE_CN.search(text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = RE_DATE_SHORT.search(text)
    if m:
        y = m.group(1)
        mo = int(m.group(2))
        d = int(m.group(3))
        full_y = f"20{y}" if len(y) == 2 else y
        return f"{full_y}-{mo:02d}-{d:02d}"
    return None


def parse_invoice_no(text: str) -> str | None:
    """提取 20 位发票号。"""
    m = RE_INVOICE_NO.search(text)
    return m.group(1) if m else None


def parse_buyer_seller(text: str) -> dict:
    """提取购买方和销售方名称。"""
    result = {"buyer": None, "seller": None}
    lines = text.split("\n")
    current_section = None
    for line in lines:
        if "购买方" in line or "买方" in line:
            current_section = "buyer"
            m = RE_BUYER_SELLER.search(line)
            if m:
                result["buyer"] = m.group(2).strip()
        elif "销售方" in line or "卖方" in line:
            current_section = "seller"
            m = RE_BUYER_SELLER.search(line)
            if m:
                result["seller"] = m.group(2).strip()
        elif "名称" in line and current_section:
            m = RE_BUYER_SELLER.search(line)
            if m:
                name = m.group(2).strip()
                if current_section == "buyer" and result["buyer"] is None:
                    result["buyer"] = name
                elif current_section == "seller" and result["seller"] is None:
                    result["seller"] = name
    return result


def parse_city(text: str) -> str | None:
    """从文本推断城市。"""
    # 常见城市列表
    cities = [
        "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都",
        "武汉", "重庆", "西安", "天津", "长沙", "郑州", "青岛", "大连",
        "厦门", "昆明", "贵阳", "海口", "三亚", "福州", "合肥", "济南",
        "太原", "沈阳", "哈尔滨", "长春", "石家庄", "兰州", "银川",
        "西宁", "呼和浩特", "乌鲁木齐", "拉萨", "南宁", "宁波", "温州",
        "无锡", "东莞", "佛山", "珠海", "惠州", "中山", "嘉兴", "绍兴",
        "金华", "台州", "扬州", "常州", "徐州", "南通", "烟台", "洛阳",
        "桂林", "丽江", "大理", "三亚",
    ]
    for city in cities:
        if city in text:
            return city
    return None


def parse_origin_destination(text: str) -> tuple:
    """提取起终点。"""
    m = RE_ORIGIN_DEST.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def parse_flight_no(text: str) -> str | None:
    m = RE_FLIGHT_NO.search(text)
    return m.group(1) if m else None


def parse_train_no(text: str) -> str | None:
    m = RE_TRAIN_NO.search(text)
    return m.group(0) if m else None


def parse_distance(text: str) -> str | None:
    m = RE_DISTANCE_KM.search(text)
    if m:
        return m.group(1) or m.group(2) or m.group(3)
    return None


def extract_trip_details(tables: list) -> list:
    """从表格中提取行程明细。"""
    details = []
    col_names = {"序号", "起点", "终点", "里程", "金额", "时间", "日期", "出发", "到达", "距离", "费用"}

    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [str(c).strip() if c else "" for c in table[0]]
        # 检查是否为行程表
        matched = sum(1 for h in header if h in col_names)
        if matched < 2:
            continue

        # 建立列索引
        idx = {}
        for i, h in enumerate(header):
            for key in col_names:
                if key in h and key not in idx:
                    idx[key] = i

        for row in table[1:]:
            if not row:
                continue
            row_str = [str(c).strip() if c else "" for c in row]
            entry = {}
            def get_col(col_name):
                if col_name in idx:
                    ci = idx[col_name]
                    return row_str[ci] if ci < len(row_str) else ""
                return None
            origin_val = get_col("起点")
            if origin_val is None:
                origin_val = get_col("出发")
            if origin_val:
                entry["origin"] = origin_val
            dest_val = get_col("终点")
            if dest_val is None:
                dest_val = get_col("到达")
            if dest_val:
                entry["destination"] = dest_val
            dist_val = get_col("里程") or get_col("距离")
            if dist_val:
                entry["distance"] = dist_val
            amt_val = get_col("金额") or get_col("费用")
            if amt_val:
                entry["amount"] = amt_val
            date_val = get_col("日期") or get_col("时间")
            if date_val:
                entry["date"] = date_val
            if entry:
                details.append(entry)

    return details


def pdf_to_images(pdf_path: Path, dpi: int = 200) -> list[str]:
    """将 PDF 每页转为 PNG 图片，返回图片路径列表。"""
    images = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    for i in range(len(pdf)):
        page = pdf[i]
        bitmap = page.render(scale=dpi / 72)
        img = bitmap.to_pil()
        out_name = f"{pdf_path.stem}_page_{i + 1}.png"
        out_path = pdf_path.parent / out_name
        img.save(str(out_path), "PNG")
        images.append(str(out_path))
        page.close()
    pdf.close()
    return images


def process_pdf(pdf_path: Path, base_dir: Path) -> dict:
    """处理单个 PDF 文件，提取所有信息。"""
    rel_path = str(pdf_path.relative_to(base_dir)).replace("\\", "/")
    print(f"  处理: {rel_path}")

    full_text = ""
    tables = []
    page_count = 0
    is_scanned = False

    # 使用 pdfplumber 提取文本和表格
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                full_text += text + "\n"
                page_tables = page.extract_tables()
                if page_tables:
                    # 过滤全空表格
                    for t in page_tables:
                        non_empty = [row for row in t if any(cell and str(cell).strip() for cell in row)]
                        if non_empty:
                            tables.append(non_empty)
    except Exception as e:
        print(f"    [警告] pdfplumber 读取失败: {e}")
        is_scanned = True

    # 判断是否为扫描件
    if not full_text.strip():
        is_scanned = True

    # PDF 转图片
    page_images = []
    try:
        page_images = pdf_to_images(pdf_path)
    except Exception as e:
        print(f"    [警告] PDF 转图片失败: {e}")

    # 智能类型推断
    inferred_type = infer_type(full_text)

    # 提取关键信息
    amount = parse_amount(full_text)
    date = parse_date(full_text)
    invoice_no = parse_invoice_no(full_text)
    buyer_seller = parse_buyer_seller(full_text)
    city = parse_city(full_text)
    origin, destination = parse_origin_destination(full_text)
    flight_no = parse_flight_no(full_text)
    train_no = parse_train_no(full_text)
    distance = parse_distance(full_text)

    # 行程明细提取
    trip_details = []
    if inferred_type == "trip_itinerary" and tables:
        trip_details = extract_trip_details(tables)

    result = {
        "file_path": rel_path,
        "pages": page_count,
        "page_images": page_images,
        "full_text": full_text.strip(),
        "tables": tables,
        "extracted": {
            "amount": amount,
            "date": date,
            "invoice_no": invoice_no,
            "buyer": buyer_seller.get("buyer"),
            "seller": buyer_seller.get("seller"),
            "city": city,
            "origin": origin,
            "destination": destination,
            "distance_km": distance,
            "flight_no": flight_no,
            "train_no": train_no,
            "trip_details": trip_details if trip_details else None,
        },
        "inferred_type": inferred_type,
    }

    if is_scanned:
        result["is_scanned"] = True

    # 打印摘要
    type_label = inferred_type
    amt_str = f"¥{amount}" if amount else "未提取"
    print(f"    类型={type_label}, 金额={amt_str}, 日期={date or '未提取'}, 扫描件={is_scanned}")

    return result


def main():
    if len(sys.argv) != 3:
        print(f"用法: python {sys.argv[0]} <classified_json> <output_json>")
        sys.exit(1)

    classified_json = Path(sys.argv[1])
    output_json = Path(sys.argv[2])

    if not classified_json.exists():
        print(f"[错误] 文件不存在: {classified_json}")
        sys.exit(1)

    # 读取 classified.json
    with open(classified_json, "r", encoding="utf-8") as f:
        classified = json.load(f)

    base_dir = classified_json.parent
    output_json.parent.mkdir(parents=True, exist_ok=True)

    # 收集所有 type="pdf" 的文件
    pdf_files = []
    for cat_key, cat_val in classified.get("categories", {}).items():
        for file_info in cat_val.get("files", []):
            if file_info.get("type") == "pdf":
                pdf_files.append(file_info)

    if not pdf_files:
        print("未找到任何 PDF 文件。")
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump({"results": [], "total": 0}, f, ensure_ascii=False, indent=2)
        return

    print(f"共发现 {len(pdf_files)} 个 PDF 文件，开始解析...\n")

    results = []
    for i, file_info in enumerate(pdf_files, 1):
        pdf_path = base_dir / file_info["path"]
        if not pdf_path.exists():
            print(f"  [跳过] 文件不存在: {file_info['path']}")
            continue
        print(f"[{i}/{len(pdf_files)}]", end="")
        try:
            result = process_pdf(pdf_path, base_dir)
            results.append(result)
        except Exception as e:
            print(f"    [错误] 处理失败: {e}")

    # 写出结果
    output = {"results": results, "total": len(results)}
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 打印统计
    print(f"\n{'='*50}")
    print(f"解析完成: {output_json}")
    print(f"成功: {len(results)}/{len(pdf_files)}")
    type_counts = {}
    for r in results:
        t = r.get("inferred_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    if type_counts:
        print("类型分布:")
        for t, c in sorted(type_counts.items()):
            print(f"  {t}: {c}")
    scanned = sum(1 for r in results if r.get("is_scanned"))
    if scanned:
        print(f"扫描件: {scanned} 个（无法提取文本，需 OCR 处理）")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()