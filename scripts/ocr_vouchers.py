"""
OCR 识别图片类凭证，提取关键报销信息。
用法: python ocr_vouchers.py <classified_json> <output_json>

支持打包版 Tesseract（优先）和系统安装版 Tesseract（回退）。
"""

import json
import os
import re
import sys
from pathlib import Path

# ── Tesseract 定位：打包版优先，系统版回退 ──────────────────────
def _find_tesseract():
    """定位 tesseract 可执行文件。返回路径字符串或 None。"""
    # 1) 打包在 skill assets 中
    _skill_dir = Path(__file__).resolve().parent.parent
    _bundled = _skill_dir / "assets" / "tesseract" / "tesseract.exe"
    if _bundled.exists():
        return str(_bundled)

    # 2) 系统常见安装路径
    _candidates = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
    ]
    for c in _candidates:
        if c.exists():
            return str(c)

    # 3) PATH 中查找
    import shutil
    p = shutil.which("tesseract")
    if p:
        return p

    return None


def _find_tessdata_prefix():
    """返回 tessdata 目录前缀（用于 TESSDATA_PREFIX）。"""
    _skill_dir = Path(__file__).resolve().parent.parent
    _bundled_td = _skill_dir / "assets" / "tesseract" / "tessdata"
    if _bundled_td.exists() and any(_bundled_td.glob("*.traineddata")):
        return str(_bundled_td)
    return None


# 尝试导入 OCR 依赖
OCR_AVAILABLE = False
TESS_CMD = None
try:
    import pytesseract
    from PIL import Image, ExifTags
    TESS_CMD = _find_tesseract()
    if TESS_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESS_CMD
        # 设置 TESSDATA_PREFIX 以便找到打包的语言包
        _tdp = _find_tessdata_prefix()
        if _tdp:
            os.environ["TESSDATA_PREFIX"] = _tdp
        OCR_AVAILABLE = True
    else:
        # 尝试不设 cmd（依赖系统 PATH）
        try:
            pytesseract.get_tesseract_version()
            OCR_AVAILABLE = True
        except Exception:
            pass
except ImportError:
    pass


def correct_image_orientation(img):
    """根据 EXIF 信息校正图片方向。"""
    try:
        orientation = None
        for tag, value in img.getexif().items():
            if tag in ExifTags.TAGS and ExifTags.TAGS[tag] == "Orientation":
                orientation = value
                break
        if orientation == 3:
            img = img.rotate(180, expand=True)
        elif orientation == 6:
            img = img.rotate(270, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)
    except Exception:
        pass
    return img


def resize_if_large(img, max_width=2000):
    """如果图片宽度超过阈值则等比缩小。"""
    if img.width > max_width:
        ratio = max_width / img.width
        new_h = int(img.height * ratio)
        img = img.resize((max_width, new_h), Image.LANCZOS)
    return img


def ocr_image(file_path):
    """对单张图片执行 OCR，返回识别文本。"""
    img = Image.open(file_path)
    img = correct_image_orientation(img)
    img = resize_if_large(img)
    text = pytesseract.image_to_string(img, lang="chi_sim+eng")
    return text.strip()


# ── 正则模式 ──────────────────────────────────────────────

# 金额：¥xxx.xx 或纯数字含千分位
_AMOUNT_PATTERNS = [
    re.compile(r"[¥￥]\s*([\d,]+\.?\d*)"),
    re.compile(r"(?:合计|总[计额]|金额|总金额)[：:\s]*[¥￥]?\s*([\d,]+\.?\d*)"),
    re.compile(r"(?:实[收付]金额|应收金额|应付金额|价税合计)[：:\s]*[¥￥]?\s*([\d,]+\.?\d*)"),
]

# 日期
_DATE_PATTERNS = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})"),
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月"),
]

# 发票号
_INVOICE_NO_PATTERN = re.compile(r"(?:发票[号码编号：:\s]*|No[.\s：:]*)\s*([\dA-Za-z]+)")

# 城市
_CITY_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,4}(?:市|省|区|县|镇|州|盟))")

# 起终点
_ORIGIN_DEST_PATTERN = re.compile(
    r"([\u4e00-\u9fff\w\s]{2,20})\s*[→\->>－—~～至到]\s*([\u4e00-\u9fff\w\s]{2,20})"
)

# 公里数
_KM_PATTERNS = [
    re.compile(r"([\d]+\.?\d*)\s*(?:公里|km|KM|Km|\.km|\s*km)"),
    re.compile(r"(?:总|全程|距离|里程)[：:\s]*([\d]+\.?\d*)\s*(?:公里|km|KM)"),
]

# 航班号
_FLIGHT_PATTERN = re.compile(r"\b(CA|MU|CZ|GS|HU|ZH|3U|MF|SC|FM|KN|GS)\s*(\d{3,4})\b")

# ── 登机牌航线（如 "广州白云 T2" → "上海虹桥 T2"）─────────
_BOARDING_ROUTE_PATTERN = re.compile(
    r"([\u4e00-\u9fff]+(?:机场|白云|虹桥|浦东|首都|大兴|宝安|萧山|双流|江北|禄口|咸阳|长水|新桥|龙湾|地窝铺|正定))\s*T?\d*\s*[\s→\-—~＞>]*\s*"
    r"([\u4e00-\u9fff]+(?:机场|白云|虹桥|浦东|首都|大兴|宝安|萧山|双流|江北|禄口|咸阳|长水|新桥|龙湾|地窝铺|正定))",
    re.IGNORECASE,
)
# 简化版：城市+机场关键词
_BOARDING_AIRPORT_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,4})(?:白云|虹桥|浦东|首都|大兴|宝安|萧山|双流|江北|禄口|咸阳|长水|新桥|龙湾|地窝铺|正定)?\s*[Tt]\d*"
)

# ── 酒店账单：入住/离店日期、酒店名称、房号、晚数 ────────
_HOTEL_CHECKIN_PATTERN = re.compile(
    r"(?:到店|入住|入住时间)[：:\s]*(\d{4})[/-](\d{1,2})[/-](\d{1,2})"
)
_HOTEL_CHECKOUT_PATTERN = re.compile(
    r"(?:离店|离店时间|退房)[：:\s]*(\d{4})[/-](\d{1,2})[/-](\d{1,2})"
)
_HOTEL_NAME_PATTERN = re.compile(
    r"(?:如家精选|如家|汉庭|全季|亚朵|桔子水晶|锦江之星|维也纳|格林豪泰|"
    r"希尔顿|万豪|喜来登|洲际|假日|智选假日|宜必思|homeinn|meadin|aton|"
    r"维也纳国际|麗枫|希岸|潮漫|ZMAX|非繁城品|白玉兰|南苑|和颐|海友|"
    r"恒哲|莫泰|7天|IU|贝壳|城市便捷|精途)[^。\n，,]{0,20}"
)

# ── 支付确认金额（携程/美团等订单页）───────────────────
_PAYMENT_CONFIRM_PATTERN = re.compile(
    r"(?:订单金额|支付金额|实付金额|已付款|扣取房费|合计)[：:\s]*[¥￥]?\s*([\d,]+\.?\d*)"
)


def parse_amount(text):
    """提取金额，返回 float 或 None。"""
    for pat in _AMOUNT_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                return float(raw)
            except ValueError:
                continue
    return None


def parse_date(text):
    """提取日期，返回 yyyy-mm-dd 或 yyyy-mm 格式字符串，或 None。"""
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3) if m.lastindex >= 3 else None
            if d:
                return f"{y}-{mo}-{d.zfill(2)}"
            return f"{y}-{mo}"
    return None


def parse_invoice_no(text):
    """提取发票号，返回字符串或 None。"""
    m = _INVOICE_NO_PATTERN.search(text)
    return m.group(1) if m else None


def parse_city(text):
    """提取城市/地点，返回字符串或 None。"""
    m = _CITY_PATTERN.search(text)
    return m.group(1) if m else None


def parse_origin_destination(text):
    """提取起终点，返回 (origin, destination) 或 (None, None)。"""
    m = _ORIGIN_DEST_PATTERN.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return None, None


def parse_distance_km(text):
    """提取公里数，返回 float 或 None。"""
    for pat in _KM_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def parse_flight_no(text):
    """提取航班号，返回字符串或 None。"""
    m = _FLIGHT_PATTERN.search(text)
    return f"{m.group(1)}{m.group(2)}" if m else None


def parse_boarding_route(text):
    """从登机牌/订单详情提取航线 (origin_airport, destination_airport)。"""
    m = _BOARDING_ROUTE_PATTERN.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    # 回退：找两个机场名
    airports = _BOARDING_AIRPORT_PATTERN.findall(text)
    if len(airports) >= 2:
        return airports[0].strip(), airports[1].strip()
    return None, None


def parse_hotel_details(text):
    """从酒店账单/订单提取 {check_in, check_out, hotel_name, room_no}。"""
    result = {}
    ci = _HOTEL_CHECKIN_PATTERN.search(text)
    if ci:
        result["check_in"] = f"{ci.group(1)}-{ci.group(2).zfill(2)}-{ci.group(3).zfill(2)}"
    co = _HOTEL_CHECKOUT_PATTERN.search(text)
    if co:
        result["check_out"] = f"{co.group(1)}-{co.group(2).zfill(2)}-{co.group(3).zfill(2)}"
    hn = _HOTEL_NAME_PATTERN.search(text)
    if hn:
        result["hotel_name"] = hn.group(0).strip()
    # 房号
    rm = re.search(r"房号[：:\s]*(\d+)", text)
    if rm:
        result["room_no"] = rm.group(1)
    return result


def parse_payment_confirm(text):
    """从支付确认页/订单完成页提取金额。"""
    m = _PAYMENT_CONFIRM_PATTERN.search(text)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def infer_type(text):
    """根据关键词推断文件类型。"""
    kw_map = [
        (["加油", "石油", "中石化", "中石油"], "gas_invoice"),
        (["登机", "boarding", "BOARDING", "登机口", "值机座位", "航班"], "boarding_pass"),
        (["高速", "ETC", "路桥", "通行费"], "toll"),
        (["酒店", "住客", "账单", "入住", "离店", "宾客账单", "结账", "房号", "预付款金额"], "hotel_bill"),
        (["导航", "地图"], "navigation"),
        (["行程单", "行程", "滴滴", "曹操", "高德打车", "美团打车", "网约车"], "trip_itinerary"),
        (["支付", "付款", "微信", "支付宝", "微信支付", "付款码", "订单金额", "已出票", "已完成", "报销凭证"], "payment"),
        (["发票", "电子发票"], "invoice"),
    ]
    for keywords, t in kw_map:
        for kw in keywords:
            if kw in text:
                return t
    # 导航截图额外判断：包含公里数
    if parse_distance_km(text) is not None:
        return "navigation"
    return "unknown"


def process_one_image(file_path):
    """处理单张图片，返回结果字典。"""
    if not OCR_AVAILABLE:
        return {
            "file_path": file_path,
            "ocr_text": None,
            "ocr_available": False,
            "tesseract_path": TESS_CMD or "(not found)",
            "extracted": {
                "amount": None, "date": None, "invoice_no": None,
                "city": None, "origin": None, "destination": None,
                "distance_km": None, "flight_no": None,
                # 新增字段
                "boarding_origin": None, "boarding_dest": None,
                "check_in": None, "check_out": None,
                "hotel_name": None, "room_no": None,
                "payment_amount": None,
            },
            "inferred_type": "unknown",
        }

    text = ocr_image(file_path)
    origin, dest = parse_origin_destination(text)
    b_orig, b_dest = parse_boarding_route(text)
    hotel = parse_hotel_details(text)
    pay_amt = parse_payment_confirm(text)
    return {
        "file_path": file_path,
        "ocr_text": text,
        "ocr_available": True,
        "tesseract_path": TESS_CMD or "",
        "extracted": {
            "amount": parse_amount(text),
            "date": parse_date(text),
            "invoice_no": parse_invoice_no(text),
            "city": parse_city(text),
            "origin": origin, "destination": dest,
            "distance_km": parse_distance_km(text),
            "flight_no": parse_flight_no(text),
            # 登机牌航线
            "boarding_origin": b_orig, "boarding_dest": b_dest,
            # 酒店详情
            "check_in": hotel.get("check_in"),
            "check_out": hotel.get("check_out"),
            "hotel_name": hotel.get("hotel_name"),
            "room_no": hotel.get("room_no"),
            # 支付确认金额
            "payment_amount": pay_amt,
        },
        "inferred_type": infer_type(text),
    }


def main():
    if len(sys.argv) < 3:
        print("用法: python ocr_vouchers.py <classified_json> <output_json>")
        sys.exit(1)

    classified_path = sys.argv[1]
    output_path = sys.argv[2]

    classified_path = Path(classified_path)
    output_path = Path(output_path)
    base_dir = classified_path.parent

    with open(classified_path, "r", encoding="utf-8") as f:
        classified = json.load(f)

    # 定位 classified.json 中 type="image" 的文件
    files = []
    if isinstance(classified, list):
        files = [item for item in classified if item.get("type") == "image"]
    elif isinstance(classified, dict):
        if "categories" in classified:
            for cat_key, cat_val in classified["categories"].items():
                for f_item in cat_val.get("files", []):
                    if f_item.get("type") == "image":
                        f_item["category"] = cat_key
                        files.append(f_item)
        elif "files" in classified:
            files = [item for item in classified["files"] if item.get("type") == "image"]
        elif "results" in classified:
            files = [item for item in classified["results"] if item.get("type") == "image"]

    print(f"共发现 {len(files)} 个图片凭证")

    if not OCR_AVAILABLE:
        print(f"[警告] Tesseract 未找到 (搜索路径: {TESS_CMD or '(无)'})，将跳过 OCR 处理")
        print("  请安装 Tesseract 或确认 skill/assets/tesseract/ 中包含打包版")
    else:
        print(f"[OK] Tesseract: {TESS_CMD}")
        # 检查可用语言
        try:
            langs = pytesseract.get_languages()
            has_chi = "chi_sim" in langs
            print(f"  可用语言: {', '.join(langs)} ({'含中文' if has_chi else '无中文'})")
        except Exception:
            print("  (无法列出语言)")

    results = []
    for i, item in enumerate(files, 1):
        fp = item.get("file_path") or item.get("path", "")
        full_path = base_dir / fp
        print(f"[{i}/{len(files)}] 处理: {fp}")
        result = process_one_image(str(full_path))
        results.append(result)
        ext = result["extracted"]
        print(f"  类型推断: {result['inferred_type']}  金额: {ext['amount']}  日期: {ext['date']}")

    output = {"results": results}
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n处理完成，结果已保存至: {output_path}")

    # 摘要
    types = {}
    for r in results:
        t = r["inferred_type"]
        types[t] = types.get(t, 0) + 1
    print("类型统计:", json.dumps(types, ensure_ascii=False))


if __name__ == "__main__":
    main()