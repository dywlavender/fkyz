from __future__ import annotations

import re


def classify_task_error(message: str) -> dict[str, str]:
    """Map a raw execution error to the platform's stable error categories."""
    text = str(message or "").strip()
    rules = (
        (r"缺少要素|不能为空|请填写|至少选择|未找到|无法映射|输入冲突", "INPUT_INVALID", "输入缺少必要要素或存在冲突", "检查需求内容、接口文档和待评审内容后重新提交。"),
        (r"schema_version|元数据.*(?:格式无效|无法解析|版本不兼容)|JSONDecodeError", "SCHEMA_INVALID", "标准元数据无效", "重新执行 Phase 1 生成 v5 标准加工规范。"),
        (r"缺少必要章节|stdout 必须以|未输出完整", "OUTPUT_STRUCTURE_INVALID", "模型输出结构无效", "检查模型输出是否包含当前阶段要求的最小 Markdown 结构。"),
        (r"必须以「?#|缺少必要章节|缺少可识别|未输出.*正文|输出格式", "OUTPUT_FORMAT_INVALID", "模型产物格式无效", "重新执行当前阶段；如重复出现，请检查对应 Prompt 和 Skill。"),
        (r"Claude Code|API Error|调用超时|未找到 claude|退出码", "MODEL_CALL_FAILED", "模型调用失败", "检查 Claude 可用性、网络和超时配置后重试。"),
        (r"取消|终止", "TASK_CANCELLED", "任务已终止", "如需继续，请重新提交任务。"),
    )
    for pattern, code, category, action in rules:
        if re.search(pattern, text, re.I):
            return {"errorCode": code, "errorCategory": category, "suggestedAction": action}
    return {
        "errorCode": "SYSTEM_ERROR",
        "errorCategory": "系统内部错误",
        "suggestedAction": "查看执行详情并根据错误信息处理；如重复出现，请检查后端日志。",
    }


def attach_error_metadata(result: dict, fallback: str = "") -> dict:
    """Attach normalized error metadata to a task result in place."""
    message = str(result.get("stderr") or result.get("error") or fallback or "").strip()
    result.update(classify_task_error(message))
    return result
