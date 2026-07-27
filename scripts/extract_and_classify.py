#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
解压 ZIP 文件，按子文件夹名分类识别费用类型，扫描文件类型，输出 JSON 分类结果。

用法:
    python extract_and_classify.py <zip_path> <output_dir>
"""

import zipfile
import json
import os
import sys
from pathlib import Path


# 子文件夹名 → 费用类型映射
FOLDER_CATEGORY_MAP = {
    "飞机": "flight",
    "高铁": "train",
    "网约车": "ride_hailing",
    "自驾": "self_drive",
    "住宿": "hotel",
    "其它": "other",
    "其他": "other",
}

# 文件扩展名 → 文件类型映射
EXT_TYPE_MAP = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".bmp": "image",
    ".tiff": "image",
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "excel",
    ".pptx": "ppt",
    ".ppt": "ppt",
}


def classify_file_ext(filename):
    """根据扩展名返回文件类型，未知扩展名返回 'unknown'。"""
    ext = Path(filename).suffix.lower()
    return EXT_TYPE_MAP.get(ext, "unknown")


def classify_folder_name(folder_name):
    """根据子文件夹名返回费用类型，未知名称返回 None。"""
    return FOLDER_CATEGORY_MAP.get(folder_name)


def extract_and_classify(zip_path, output_dir):
    zip_path = Path(zip_path)
    output_dir = Path(output_dir)

    extracted_dir = output_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    # 解压 ZIP（处理中文文件名）
    print(f"正在解压: {zip_path.name}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            fname = info.filename
            if not info.flag_bits & 0x800:
                try:
                    fname = fname.encode("cp437").decode("utf-8")
                except Exception:
                    pass
            target = extracted_dir / fname
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())

    # 构建分类结果
    result = {
        "source_zip": zip_path.name,
        "categories": {},
        "total_files": 0,
    }

    # 收集根目录文件和子文件夹
    root_files = []
    subfolders = {}

    # 自动检测并跳过中间层包裹目录
    # 如果解压后只有一个子文件夹且没有散落文件，则进入该子文件夹
    scan_dir = extracted_dir
    items = list(scan_dir.iterdir())
    if len(items) == 1 and items[0].is_dir():
        scan_dir = items[0]
        print(f"  自动跳过包裹目录: {extracted_dir.name} → {scan_dir.name}")
        items = list(scan_dir.iterdir())

    for item in items:
        if item.is_file():
            root_files.append(item)
        elif item.is_dir():
            subfolders[item.name] = item

    # 处理子文件夹
    for folder_name, folder_path in subfolders.items():
        category = classify_folder_name(folder_name)
        if category is None:
            category = "unknown"
            label = folder_name
        else:
            label = folder_name

        files_list = []
        for f in sorted(folder_path.iterdir()):
            if f.is_file():
                rel_path = f.relative_to(output_dir)
                files_list.append({
                    "filename": f.name,
                    "type": classify_file_ext(f.name),
                    "path": str(rel_path).replace("\\", "/"),
                })

        if category not in result["categories"]:
            result["categories"][category] = {
                "folder_name": label,
                "files": [],
            }
        result["categories"][category]["files"].extend(files_list)

    # 根目录文件归入 other
    if root_files:
        if "other" not in result["categories"]:
            result["categories"]["other"] = {
                "folder_name": "其它",
                "files": [],
            }
        for f in sorted(root_files):
            rel_path = f.relative_to(output_dir)
            result["categories"]["other"]["files"].append({
                "filename": f.name,
                "type": classify_file_ext(f.name),
                "path": str(rel_path).replace("\\", "/"),
            })

    # 统计总文件数
    total = sum(len(cat["files"]) for cat in result["categories"].values())
    result["total_files"] = total

    # 写出 JSON
    json_path = output_dir / "classified.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # 打印摘要
    print(f"\n{'='*50}")
    print(f"解压目录: {extracted_dir}")
    print(f"分类结果: {json_path}")
    print(f"总文件数: {total}")
    print(f"\n分类明细:")
    for cat_key, cat_val in result["categories"].items():
        folder = cat_val["folder_name"]
        count = len(cat_val["files"])
        types = {}
        for file_info in cat_val["files"]:
            t = file_info["type"]
            types[t] = types.get(t, 0) + 1
        type_summary = ", ".join(f"{t}: {c}" for t, c in sorted(types.items()))
        print(f"  [{cat_key}] {folder} - {count} 个文件 ({type_summary})")
    print(f"{'='*50}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"用法: python {sys.argv[0]} <zip_path> <output_dir>")
        sys.exit(1)

    extract_and_classify(sys.argv[1], sys.argv[2])
