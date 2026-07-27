---
name: "travellingexpenses-form-gen"
label: "出差报销附件生成器"
description: "出差报销附件生成器。当用户需要制作报销单、生成出差报销附件、整理报销凭证、填写报销明细表时触发此Skill。支持从ZIP压缩包中自动识别发票、行程单、支付凭证等文件，生成包含明细表和附件页的DOCX文件。"
last_updated: "2026-07-23 12:05"
---

> **变更记录**
> - 2026-07-23 12:05：美观+准确统一 —— 核心原则："宽度永不超过自然宽度（不放大→不模糊），高度按原始比例（不变形）"。撤回窄图 solo 放大，统一用自然宽或 HALF_W 较小值；发票/文档凭证目标 18cm 同样不超自然宽。窄图独行时上下均匀留白替代放大填满。MAX_H_CM 统一 26.7（页面内容区全高）。输出 v9：0空白页 0变形 0异常放大。
> - 2026-07-23 11:24：窄图 solo 放大 —— 单张窄图独处一行时放大到占满页面可用高度（如 12.2×26.5cm），消除"半空页"（原 8.9×19.3cm 仅占半页）。已撤回（见 12:05）。
> - 2026-07-23 11:07：标签防孤立加固 —— 修复类别标签与首图分离问题（如"【住宿】"在上一页底部、图片全在下一页）。方案：标签渲染前先收集本类图片并预估首行高度，若"当前页已用高度 + 标签高 + 首行高 > 26.5cm"则提前换页，确保标签至少与首张图同页。
> - 2026-07-23 10:33：数据完整性修复 —— 误餐补贴 description 去除"误餐补贴"前缀（与 item 列去重）；返程条目（机票/网约车回程）共享去程 page_images，避免附件页凭证缺失假象；`exp.get("item", project)` → `exp.get("item") or project` 增强空字符串回退。
> - 2026-07-22 18:00：附件拼版重构 —— PDF 文档型凭证（发票/行程单/报销单/账单）统一与页面同宽 18cm；图片只指定宽度、高度自动按原始比例计算，杜绝拉伸变形；跨类别连续拼版（仅整行放不下才换页），消除空白页；明细表"姓名"去除中间空格。补充 `requirements.txt`，Tesseract 二进制（含 chi_sim/eng）已打包至 `assets/tesseract/`。

# 出差报销附件生成器

## 功能概述

从用户提供的 ZIP 压缩包中自动提取报销凭证信息，按公司固定模板生成可编辑的出差报销单 DOCX 文件，包含明细表格和附件凭证页。

支持的费用类型：飞机、高铁、网约车、自驾、住宿、其它。

---

## 触发条件

- 用户需要制作/生成出差报销单
- 用户需要填写/整理报销附件
- 用户提到"报销"、"报销单"、"报销附件"、"差旅费"

---

## 用户输入要求

使用此 Skill 前，必须向用户收集以下信息：

| 信息 | 是否必填 | 说明 |
|------|----------|------|
| ZIP 文件路径 | 必填 | 包含分类子文件夹的报销凭证压缩包 |
| 姓名 | 必填 | 报销人姓名，填入明细表"姓名"栏 |
| 项目编号 | 必填 | 项目工作号，如 N-JKQC/26-0429；无则填"无" |
| 填表日期 | 选填 | 格式 yyyy-mm-dd，默认今天 |

---

## ZIP 文件结构约定

报销凭证应按费用类型分文件夹打包：

```
报销凭证.zip
├── 飞机/          — 机票费：发票PDF、支付截图、登机牌
├── 高铁/          — 火车票：发票PDF、支付截图、订单截图
├── 网约车/        — 网约车费：发票PDF、行程单PDF/截图、支付截图
├── 自驾/          — 自驾车费：加油发票PDF、导航截图（标注公里数）
├── 住宿/          — 住宿费：发票PDF、酒店账单、支付截图
└── 其它/          — 其它费用：发票PDF、相关证明
```

> **注意**：行程单 PDF 可能包含多笔行程，脚本会尝试自动拆分。

---

## 每组凭证的三单要求

| 费用类型 | 支付凭证 | 发票 | 证明文件 |
|----------|----------|------|----------|
| 飞机 | 支付截图 | 机票发票PDF | 登机牌图片 |
| 高铁 | 支付截图 | 火车票发票PDF | 订单截图 |
| 网约车 | 支付截图 | 发票PDF | 行程单PDF/截图 |
| 自驾 | — | 加油发票PDF(>=里程金额) | 导航截图(标注公里数) |
| 住宿 | 支付截图 | 发票PDF | 酒店账单/订单 |
| 其它 | 支付截图 | 发票PDF | 其他证明 |

---

## 处理流程

### Step 1: 解压并分类文件

**脚本**：`scripts/extract_and_classify.py`

**调用方式**：
```python
python scripts/extract_and_classify.py <zip_path> <output_dir>
```

**功能**：
- 解压 ZIP 到 `output_dir/extracted/`
- 自动跳过中间层包裹目录（如"新建文件夹"）
- 按子文件夹名识别费用类型
- 按文件扩展名分类：PDF、图片（png/jpg/jpeg）、Excel
- 输出 `classified.json`

**输出**：
```json
{
  "categories": {
    "ride_hailing": {
      "name": "网约车",
      "files": [
        {"file_path": "extracted/.../网约车/发票.pdf", "type": "pdf"},
        {"file_path": "extracted/.../网约车/截图.jpg", "type": "image"}
      ]
    }
  },
  "statistics": {...}
}
```

**依赖**：Python 标准库（zipfile, pathlib）

---

### Step 2: OCR 识别图片凭证

**脚本**：`scripts/ocr_vouchers.py`

**调用方式**：
```python
python scripts/ocr_vouchers.py <classified.json> <output_dir>/ocr_results.json
```

**功能**：
- 对所有图片类文件（type="image"）进行 OCR 识别
- 提取关键信息：金额、日期、城市、起终点、发票号等
- 如果系统未安装 Tesseract，则标记为 `ocr_available: false`，跳过识别

**输出**：
```json
{
  "files_processed": 5,
  "ocr_available": true,
  "results": [
    {"file": "截图.jpg", "status": "success", "amount": 28.89, ...}
  ]
}
```

**依赖**：
- `pytesseract` + `Pillow`（OCR 引擎）
- `Tesseract-OCR` 系统安装（可选，未安装则跳过）

---

### Step 3: 解析 PDF 凭证

**脚本**：`scripts/parse_pdf_vouchers.py`

**调用方式**：
```python
python scripts/parse_pdf_vouchers.py <classified.json> <output_dir>/pdf_results.json
```

**功能**：
- 对所有 PDF 文件提取文字和表格（使用 pdfplumber）
- 将 PDF 每页转为 PNG 图片（使用 pypdfium2）
- 识别发票信息：发票号、金额、日期、购买方、销售方
- 识别行程单信息：起终点、时间、里程、金额
- 识别登机牌信息：航班号、航线、日期
- 识别酒店账单信息：入住退房日期、天数、金额

**输出**：
```json
{
  "files_processed": 9,
  "results": [
    {
      "file": "发票.pdf",
      "page_images": ["发票_page_1.png"],
      "amount": 82.80,
      "date": "2026-07-16",
      ...
    }
  ]
}
```

**依赖**：
- `pdfplumber`（PDF 文字/表格提取）
- `pypdfium2`（PDF 转 PNG）

---

### Step 4: 综合分析与三单校验

**脚本**：`scripts/analyze_and_validate.py`

**调用方式**：
```python
python scripts/analyze_and_validate.py <ocr_results.json> <pdf_results.json> <output_dir>/analysis.json
```

**功能**：
- 将 OCR 结果和 PDF 解析结果合并
- 按费用类别分组合并（同文件夹内文件合并为一条记录）
- 三单匹配校验：每组凭证是否齐全
- 金额一致性检查（允许 ±0.05 误差）
- 日期一致性检查
- 路桥费识别和时间归类
- 地址概括：原始地址 → ≤6 字地标名
- 自动生成"使用内容"描述
- 按规则排序（大类→日期→路桥费就近插入）
- 误餐补贴自动计算（住宿天数 × ¥100）
- 自驾补贴自动计算（公里数 × ¥1）

**输出**：`analysis.json`（完整结构见下方）

**依赖**：Python 标准库（json, re, datetime）

---

### Step 5: 生成 DOCX 报销单

**脚本**：`scripts/generate_docx.py`

**调用方式**：
```python
python scripts/generate_docx.py <analysis.json> <output_docx> --name <姓名> --project <项目编号> --date <日期>
```

**功能**：
- 第1部分：按 Excel 模板格式生成明细表格
  - 标题："出差报销单明细表"（楷体 20pt 粗体居中）
  - 信息行：姓名、项目工作号、日期（中文冒号，分两行防溢出）
  - 表格：5列（日期/项目/使用地/使用内容/金额）
  - 合计行 + 汇率行 + 预支暂支金行
- 第2部分：附件凭证页
  - 按费用大类分组（飞机→高铁→网约车→自驾→住宿→其它）
  - 每组上方标注：`【类型】日期范围 ¥合计金额`（标签加 `keepNext` 防孤立）
  - 连续拼版（`layout_images_continuous`）：跨类别连续排版，仅整行放不下才换页 → 无空白页
  - PDF 文档型凭证（发票/行程单/报销单/账单）统一占满整行 18cm（与页面同宽）
  - 手机截图等窄图按原始比例放大铺满半行、两张并排
  - 图片只指定宽度、高度由 python-docx 自动按比例计算 → 绝不拉伸变形
- 自动计算合计、误餐补贴、自驾补贴

**后处理**（`_post_process_docx`）：
1. python-docx 清空页眉页脚内容并移除 sectPr 引用
2. ZIP 级清除 rsid 指纹和元数据（creator/description/时间戳等）

**输出**：`<姓名>_出差报销单.docx`

**依赖**：
- `python-docx`（DOCX 生成）
- `Pillow`（图片尺寸计算）
- `lxml`（XML 后处理）

---

### Step 6: 手动清理（重要）

生成 DOCX 后，用 WPS 或 Office 打开文件，执行以下操作：

1. 进入 **页面设置 → 页眉页脚**
2. 删除页脚中可能出现的 "AI 生成" 文字框
3. 保存文件

> **说明**：由于 python-docx 库会在页脚区域写入元数据标记，WPS/Office 打开时可能会在页脚显示 "AI 生成" 字样。这不会影响文档内容的真实性和可编辑性，但为避免财务审核时的误解，请务必手动删除。

---

## analysis.json 结构

```json
{
  "validation_summary": {
    "total_groups": 6,
    "complete": 5,
    "missing_items": 1,
    "issues": [{"category": "ride_hailing", "status": "missing_proof", "message": "..."}]
  },
  "expenses": [
    {
      "seq": 1,
      "date": "2026-07-16",
      "date_display": "7月16日",
      "category": "flight",
      "category_cn": "飞机",
      "project": "",
      "city": "广州",
      "description": "1，机票：广州 - 上海",
      "amount": 730.0,
      "amount_display": "730.00",
      "files": [...],
      "page_images": ["path/to/发票_page_1.png", "path/to/截图.jpg"],
      "is_meal_allowance": false,
      "is_self_drive_subsidy": false
    }
  ],
  "hotel_info": {"check_in": "2026-07-16", "check_out": "2026-07-19", "nights": 3, "rooms": 1, "total_amount": 1146.00},
  "self_drive_info": {"total_km": 0, "subsidy_amount": 0},
  "meal_allowance": {"nights": 3, "rate": 100, "total_amount": 300},
  "grand_total": 3103.38
}
```

---

## 地址概括规则

从行程单/导航截图中提取的原始地址，需概括为每段不超过6个字的地标名称：

| 原始地址 | 概括结果 |
|----------|----------|
| 闻堰全季酒店(杭州湘湖万达中路店) | 全季酒店 |
| 萧山区\|奥斯奇园区-西北门 | 奥斯奇园区 |
| 杭州萧山国际机场T3 | 杭州机场 |
| 广州市白云国际机场 | 广州机场 |
| 湘山路[奥斯奇园区-西北门] | 奥斯奇园区 |

**处理规则**：
- 去除行政区划前缀（省、市、区、县、镇）
- 去除常见后缀（路、号、栋、楼、层、室、门）
- 去除括号内容和分隔符（`\|`, `-`）
- 特殊处理："机场"→保留到"机场"、"火车站"→保留站名、"酒店"→保留酒店名

---

## 使用内容生成规则

| 费用类型 | 格式 | 示例 |
|----------|------|------|
| 网约车 | 序号，网约车：概括起点 - 概括终点 | 1，网约车：新大厦 - 广州机场 |
| 自驾 | 自驾：概括起点 - 概括终点（XX公里） | 自驾：广州机场 - 家（35公里） |
| 飞机 | 序号，机票：出发城市 - 到达城市 | 1，机票：广州 - 上海 |
| 高铁 | 序号，高铁：出发站 - 到达站（车次） | 1，高铁：广州南 - 杭州东（Gxxx） |
| 路桥费 | 路桥费：概括起点 - 概括终点 | 路桥费：广州机场 - 家 |
| 住宿 | 住宿费：X晚/Y间 | 住宿费：3晚/1间 |
| 误餐补贴 | 误餐补贴（X晚-姓名） | 误餐补贴（3晚-陆航） |
| 其它 | 根据发票内容概括 | 办公用品：打印纸/墨盒 |

---

## 排序规则

1. 大类排序：飞机 → 高铁 → 网约车(+路桥费) → 自驾(+路桥费) → 住宿 → 误餐补贴 → 其它
2. 同类型内按日期升序
3. 路桥费根据时间就近插入对应的网约车或自驾项目后面

---

## 附件页连续拼版规则（`layout_images_continuous`）

| 图片类型 | 判定 | 排版方式 |
|----------|------|----------|
| PDF 文档型凭证 | 文件名含 发票/invoice/行程单/报销单/账单/凭证/itinerary/boarding（见 `is_doc_voucher`） | 占满整行 18cm（**与页面同宽**） |
| 宽图 | 原始宽高比 ≥ 1.1 | 占满整行 18cm |
| 窄图（手机截图等） | 上述之外 | 宽度 = min(自然宽, 半行宽≈8.85cm)，两张并排；独行时上下均匀留白居中对齐 |
| 换页触发 | 当前页累计高度 + 本行高度 > 26.7cm | 自动换页 |
| 跨大类 | —— | **连续拼版，不强制分页**（仅整行放不下才换页）|

**核心原则**：
1. **不拉伸变形** —— 所有图片仅通过 `width=Cm(dw)` 指定宽度，高度由 python-docx 按原始比例自动计算，绝不同时锁死宽高。
2. **不放大模糊** —— 所有图片宽度 ≤ 自然宽度（`dw = min(目标宽, w_cm)`），绝不放大像素导致模糊。
3. **无空白页** —— 用 `attach_state["page_used_h"]` 跨类别累计页高连续排版，标签行加 `w:keepNext` 与首图保持同页；窄图独行时上下均匀留白居中对齐。
4. **发票/文档凭证同宽** —— `is_doc_voucher()` 覆盖所有 PDF 转出的文档凭证，统一 18cm 满行（不超自然宽），保证小字清晰可读。

---

## 特殊计算

| 项目 | 计算方式 |
|------|----------|
| 误餐补贴 | 住宿天数 × ¥100/天（过夜才有） |
| 自驾补贴 | 导航截图公里数 × ¥1/公里 |
| 加油发票金额 | 需 >= 行程公里数（否则校验不通过） |

---

## Excel 模板样式参考

- **页面**：A4，上下左右边距 1.5cm
- **标题**："出差报销单明细表"，楷体 20pt 粗体居中
- **信息行**：分两行，楷体 12pt 粗体，带中文冒号
  - 第1行：`姓  名：{name}`
  - 第2行：`项目工作号：{project}                    日期：{year} 年 {month} 月 {day} 日 星期 {weekday}`
- **表头**：日期/项目/使用地/使用内容/金额，楷体 14pt 粗体居中，全细边框
- **数据行**：楷体 12pt，金额列粗体右对齐，其余居中，全细边框
- **合计行**：楷体 14pt 粗体，全细边框
- **底部**：汇率行 + 预支暂支金行，楷体 14pt 粗体

---

## 脚本依赖清单

| 脚本 | Python 依赖 | 系统依赖 |
|------|-------------|----------|
| extract_and_classify.py | 无 | 无 |
| ocr_vouchers.py | pytesseract, Pillow | Tesseract-OCR（**已打包** `assets/tesseract/`，含 chi_sim/eng） |
| parse_pdf_vouchers.py | pdfplumber, pypdfium2 | 无 |
| analyze_and_validate.py | 无 | 无 |
| generate_docx.py | python-docx, Pillow, lxml | 无 |

**一键安装**（推荐使用 requirements.txt）：
```bash
pip install -r requirements.txt
```

> **OCR 二进制已封装**：`ocr_vouchers.py` 会自动优先使用 `assets/tesseract/tesseract.exe` 及其 `tessdata/`（chi_sim + eng），无需用户单独安装 Tesseract；找不到打包版时才回退系统安装版。

---

## 完整串联命令

```bash
# 1. 解压分类
python scripts/extract_and_classify.py <zip_path> <output_dir>

# 2. OCR 图片
python scripts/ocr_vouchers.py <output_dir>/classified.json <output_dir>/ocr_results.json

# 3. 解析 PDF
python scripts/parse_pdf_vouchers.py <output_dir>/classified.json <output_dir>/pdf_results.json

# 4. 分析校验
python scripts/analyze_and_validate.py <output_dir>/ocr_results.json <output_dir>/pdf_results.json <output_dir>/analysis.json

# 5. 生成 DOCX
python scripts/generate_docx.py <output_dir>/analysis.json <output_dir>/报销人_出差报销单.docx --name "报销人" --project "项目编号" --date "2026-07-22"
```

> 最终生成后，请务必在 WPS/Office 中手动删除页脚的"AI 生成"文字框。

