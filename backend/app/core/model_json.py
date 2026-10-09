"""模型 JSON 输出的容错解析。

LLM 返回 JSON 时常见的三种破坏：

1. 被 Markdown 代码围栏包裹（```json ... ```）。
2. 前后带解释性文字。
3. 被 max_tokens 截断，JSON 没有闭合。

这里统一处理，避免各 service 直接调用 model_validate_json
时被这些格式问题打断。
"""

import json
import re

from pydantic import BaseModel, ValidationError


class ModelJSONParseError(RuntimeError):
    """模型输出无法解析为期望的结构。"""


_FENCE_PATTERN = re.compile(
    r"```(?:json|JSON)?\s*(.*?)\s*```",
    re.DOTALL,
)


def strip_code_fence(text: str) -> str:
    """去掉 Markdown 代码围栏，并提取最外层 JSON 对象。"""

    if not text:
        return ""

    candidate = text.strip()

    fenced = _FENCE_PATTERN.search(candidate)
    if fenced:
        candidate = fenced.group(1).strip()

    # 去掉 JSON 前后的解释性文字
    start = candidate.find("{")
    end = candidate.rfind("}")

    if start != -1 and end != -1 and end > start:
        candidate = candidate[start:end + 1]

    return candidate.strip()


def _repair_truncated_json(text: str) -> str:
    """尝试补齐被截断的 JSON。

    策略：只保留完整写出的顶层字段，再按栈补齐缺失的
    } 和 ]。这样即使末尾字段被截断，也能保住前面已经
    写完整的字段（尤其是分数）。

    例如：
        {"a": 1, "b": ["x", "y"
    修复为：
        {"a": 1, "b": ["x", "y"]}
    """

    depth = 0
    in_string = False
    escaped = False

    # 顶层对象内最后一个"字段边界"位置
    last_field_end = 0
    # 当前是否停在"值已完整、正等待 , 或 }"的位置
    at_value_end = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                if depth == 1:
                    at_value_end = True
            continue

        if char == '"':
            in_string = True
            at_value_end = False
        elif char in "{[":
            depth += 1
            at_value_end = False
        elif char in "}]":
            depth -= 1
            if depth == 1:
                at_value_end = True
        elif char == ":":
            at_value_end = False
        elif char == ",":
            if depth == 1:
                # 分界符本身也是合法的截断点
                last_field_end = index
            at_value_end = False
        elif not char.isspace() and depth == 1:
            # 数字、true/false/null 等裸值
            at_value_end = True

    if at_value_end:
        # 末尾是完整的值或闭合括号，无需回退字段
        kept = text
    else:
        kept = text[:last_field_end] if last_field_end else text

    return kept + _closing_suffix(kept)


def _closing_suffix(text: str) -> str:
    """计算文本中尚未闭合的 } 数量。

    只需补 }：截断处理只会回退到顶层字段边界，
    不会停在嵌套的 [ 中间。
    """

    depth = 0
    in_string = False
    escaped = False

    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1

    return "}" * max(depth, 0)


def parse_model_json(
    raw: str,
    model: type[BaseModel],
    allow_repair: bool = True,
) -> BaseModel:
    """把模型原始输出解析为指定的 Pydantic 模型。"""

    if raw is None or not raw.strip():
        raise ModelJSONParseError("模型输出为空")

    candidate = strip_code_fence(raw)

    try:
        return model.model_validate_json(candidate)

    except (ValidationError, ValueError) as first_error:

        if not allow_repair:
            raise ModelJSONParseError(
                f"模型输出无法解析为 {model.__name__}: "
                f"{first_error}; 原始内容前 300 字符: "
                f"{raw[:300]!r}"
            ) from first_error

        try:
            repaired = _repair_truncated_json(candidate)
            data = json.loads(repaired)
        except (ValueError, TypeError) as repair_error:
            raise ModelJSONParseError(
                f"模型输出无法解析为 {model.__name__}: "
                f"{first_error}; 修复后仍失败: {repair_error}; "
                f"原始内容前 300 字符: {raw[:300]!r}"
            ) from repair_error

        # 补齐被截断的列表字段，避免必填字段缺失
        for field_name, field_info in model.model_fields.items():
            if field_name in data:
                continue

            if field_info.is_required():
                annotation = field_info.annotation
                if annotation is str:
                    data[field_name] = ""
                elif getattr(annotation, "__origin__", None) is list:
                    data[field_name] = []

        try:
            return model.model_validate(data)
        except ValidationError as second_error:
            raise ModelJSONParseError(
                f"修复后仍无法解析为 {model.__name__}: "
                f"{second_error}; 原始内容前 300 字符: "
                f"{raw[:300]!r}"
            ) from second_error
