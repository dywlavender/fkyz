from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid

from .paths import BUSINESS_RULES_PATH, DATA_DIR
from .utils import read_text


BUSINESS_RULES_LOCK = threading.Lock()

BUSINESS_RULE_TYPES = {
    "business_mandatory": {
        "label": "业务口径强制规则",
        "priority": 1,
        "phase1": "必须用于标准加工规范生成；影响加工结果的字段、码值、枚举、过滤或计算口径应写入标准。",
        "phase2": "不直接输入 Phase 2；其结果仅通过冻结的标准加工规范参与评审。",
    },
    "exception_fallback": {
        "label": "异常兜底语义规则",
        "priority": 2,
        "phase1": "用于异常与默认值兜底口径；不绑定某个接口固定成功码字段名或成功值。",
        "phase2": "不直接输入 Phase 2；标准加工规范只保留异常返回配置默认值的统一语义。",
    },
    "default_assumption": {
        "label": "默认假设规则",
        "priority": 3,
        "phase1": "作为分析前提使用，可写入标准的前置假设或边界说明。",
        "phase2": "不直接输入 Phase 2；采用后的结果必须已体现在冻结的标准加工规范中。",
    },
}


def rule_version() -> str:
    return uuid.uuid4().hex[:12]


def parse_business_rules():
    if not BUSINESS_RULES_PATH.exists():
        return []
    try:
        data = json.loads(read_text(BUSINESS_RULES_PATH))
        if not isinstance(data, list):
            return []
        changed = False
        rules = []
        for item in data:
            if not isinstance(item, dict):
                continue
            rule = normalize_business_rule(item, existing_id=item.get("id"))
            rule["version"] = item.get("version") or rule["version"]
            rule["updatedAt"] = item.get("updatedAt") or rule["updatedAt"]
            changed = changed or rule != item
            rules.append(rule)
        if changed:
            save_business_rules(rules)
        return rules
    except Exception:
        return []


def save_business_rules(rules: list[dict]):
    DATA_DIR.mkdir(exist_ok=True)
    BUSINESS_RULES_PATH.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_business_rule(payload: dict, existing_id: str | None = None) -> dict:
    rule_id = existing_id or (payload.get("id") or f"rule-{uuid.uuid4().hex[:8]}")
    rule_type = (payload.get("type") or payload.get("ruleType") or "business_mandatory").strip()
    if rule_type not in BUSINESS_RULE_TYPES:
        rule_type = "business_mandatory"
    return {
        "id": str(rule_id),
        "type": rule_type,
        "typeLabel": BUSINESS_RULE_TYPES[rule_type]["label"],
        "priority": BUSINESS_RULE_TYPES[rule_type]["priority"],
        "text": (payload.get("text") or payload.get("rule") or "").strip(),
        "version": rule_version(),
        "updatedAt": int(time.time()),
    }


def validate_business_rule(rule: dict) -> list[str]:
    errors = []
    if rule.get("type") not in BUSINESS_RULE_TYPES:
        errors.append("规则类型无效")
    if not (rule.get("text") or "").strip():
        errors.append("规则说明不能为空")
    return errors


def business_rules_fingerprint() -> list[dict]:
    return [
        {
            "id": rule.get("id", ""),
            "type": rule.get("type") or "business_mandatory",
            "text_sha256": hashlib.sha256((rule.get("text") or "").encode("utf-8")).hexdigest(),
        }
        for rule in parse_business_rules()
        if (rule.get("text") or "").strip()
    ]
