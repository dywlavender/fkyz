from __future__ import annotations

import csv
import base64
import xml.etree.ElementTree as ET
from io import BytesIO, StringIO
from zipfile import ZipFile


class TabularImportError(ValueError):
    """A user-facing, explicit CSV/XLSX decoding or parsing failure."""


def parse_xlsx(data: bytes) -> list[list[str]]:
    """Parse the first worksheet from an xlsx file using only stdlib."""
    rows: list[list[str]] = []
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    try:
        with ZipFile(BytesIO(data)) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                tree = ET.parse(archive.open("xl/sharedStrings.xml"))
                for item in tree.findall(f"{ns}si"):
                    shared_strings.append("".join(node.text or "" for node in item.findall(f".//{ns}t")))
            sheet = ET.parse(archive.open("xl/worksheets/sheet1.xml")).getroot()
            sheet_data = sheet.find(f"{ns}sheetData")
            if sheet_data is None:
                return rows
            for row_element in sheet_data.findall(f"{ns}row"):
                cells: dict[str, str] = {}
                for cell in row_element.findall(f"{ns}c"):
                    reference = cell.get("r", "")
                    value_element = cell.find(f"{ns}v")
                    value = value_element.text if value_element is not None else ""
                    if cell.get("t", "") == "s" and value:
                        try:
                            value = shared_strings[int(float(value))]
                        except (IndexError, ValueError):
                            pass
                    cells[reference] = value
                if not cells:
                    continue
                max_column = 0
                for reference in cells:
                    column_index = 0
                    for character in "".join(filter(str.isalpha, reference)).upper():
                        column_index = column_index * 26 + (ord(character) - 64)
                    max_column = max(max_column, column_index)
                row_number = "".join(filter(str.isdigit, next(iter(cells))))
                row_data = []
                for column_index in range(1, max_column + 1):
                    reference = ""
                    remaining = column_index
                    while remaining > 0:
                        remaining, offset = divmod(remaining - 1, 26)
                        reference = chr(offset + 65) + reference
                    row_data.append(cells.get(reference + row_number, ""))
                rows.append(row_data)
    except Exception as exc:
        raise TabularImportError(f"XLSX 文件解析失败：{exc}") from exc
    return rows


def parse_csv_rows(text: str) -> list[list[str]]:
    """Parse non-empty CSV records and trim every cell."""
    rows = []
    for row in csv.reader(StringIO(text.lstrip("\ufeff"))):
        if any(str(cell).strip() for cell in row):
            rows.append([str(cell).strip() for cell in row])
    return rows


def decode_import_rows(name: str, data_base64: str) -> list[list[str]]:
    """Decode CSV/XLSX with one shared encoding and error policy."""
    if not data_base64:
        raise TabularImportError("上传文件内容为空。")
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except Exception as exc:
        raise TabularImportError("上传文件 base64 解码失败。") from exc
    if str(name or "").lower().endswith((".xlsx", ".xls")):
        rows = parse_xlsx(raw)
    else:
        last_error: UnicodeDecodeError | None = None
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                rows = parse_csv_rows(raw.decode(encoding))
                break
            except UnicodeDecodeError as exc:
                last_error = exc
        else:
            raise TabularImportError("CSV 文件编码无法识别，请使用 UTF-8 或 GB18030。") from last_error
    if not rows:
        raise TabularImportError("未解析出有效数据行。")
    return rows


def normalize_batch_rows_from_table(rows: list[list[str]]) -> list[dict]:
    """Map accepted spreadsheet header aliases to the canonical batch-review fields."""
    if not rows:
        return []
    aliases = {
        "caseName": ["案例名称", "案例名", "caseName", "case_name", "title"],
        "factorName": ["因子名", "因子中文名", "factorName", "factor_name"],
        "requirement": ["需求内容", "需求", "requirement"],
        "apiDocs": ["接口文档", "接口", "接口文档ID", "apiDocs", "apiDocIds", "api_names"],
        "paramFields": ["因子加工输入字段", "paramFields"],
        "procLogic": ["加工逻辑", "逻辑", "procLogic"],
        "draft": ["待评审内容", "准出内容", "reviewContent", "review_content", "draft"],
        "defaultValues": ["默认值", "defaultVal", "defaultValues"],
        "businessRuleIds": ["业务规则ID", "业务规则 ID", "businessRuleIds", "business_rule_ids"],
        "standardFile": ["标准文件ID", "标准文件", "standardFile", "standard_file"],
        "remark": ["备注", "remark"],
    }
    header = [str(cell).strip() for cell in rows[0]]
    column_map = {}
    for key, names in aliases.items():
        for name in names:
            if name in header:
                column_map[key] = header.index(name)
                break
    normalized = []
    for raw in rows[1:]:
        item = {
            key: str(raw[index]).strip() if (index := column_map.get(key)) is not None and index < len(raw) else ""
            for key in aliases
        }
        if item.get("factorName") or item.get("requirement"):
            normalized.append(item)
    return normalized
