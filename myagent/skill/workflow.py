"""skill_start 工作流的结果结构与文案（对应 Suna internal/skill/workflow.go）。

交互步骤在 Runtime.start 里：静态检查 → 问是否 LLM 审查 → 问是否启用。
这里只放结果类型、选项常量和给模型看的 JSON 摘要。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .manager import CheckResult
from .review import LLMReviewResult

START_IMPORT = "import"
START_CHECK = "check"

OPTION_REVIEW_YES = "Run LLM review"
OPTION_REVIEW_NO = "Skip review"
OPTION_ENABLE_YES = "Enable"
OPTION_ENABLE_NO = "Keep disabled"


@dataclass
class StartResult:
    """一次 skill_start 的完整结果（含用户选择）。"""

    name: str
    action: str
    valid: bool = False
    reasons: List[str] = field(default_factory=list)
    review: Optional[LLMReviewResult] = None
    enabled: bool = False
    review_ask: str = ""
    enable_ask: str = ""
    error: str = ""
    description: str = ""


def start_result_from_check(name: str, action: str, check: CheckResult) -> StartResult:
    """用静态检查结果填 StartResult，尚未问用户。"""
    return StartResult(
        name=name,
        action=action,
        valid=check.valid,
        reasons=list(check.reasons),
        error=check.error,
        description=check.description,
    )


def format_check_question(check: CheckResult) -> str:
    """静态检查完成后，问用户要不要再跑 LLM 审查。"""
    lines = [f"**Skill static check completed:** `{check.name}`", ""]
    if not check.reasons:
        lines.append("No obvious issues found.")
        lines.append("")
    else:
        lines.append("**Potential issues:**")
        lines.append("")
        for reason in check.reasons:
            lines.append(f"- {reason}")
        lines.append("")
    lines.append("Run an additional **LLM review** for this Skill?")
    return "\n".join(lines)


def format_enable_question(result: StartResult) -> str:
    """问用户是否启用该 Skill。"""
    prefix = "LLM review completed. " if result.review is not None else ""
    return f"{prefix}Enable Skill {result.name}?"


def start_json_result(result: StartResult) -> str:
    """返回给模型的摘要 JSON，不把完整审查长文塞进工具结果。"""
    payload: dict[str, Any] = {
        "name": result.name,
        "action": result.action,
        "valid": result.valid,
        "enabled": result.enabled,
    }
    if result.reasons:
        payload["reasons"] = list(result.reasons)
    if result.review is not None:
        payload["review_ran"] = True
        payload["needs_attention"] = result.review.needs_attention
        payload["review_summary"] = _first_review_line(result.review.review)
        if result.review.error and not result.error:
            payload["error"] = result.review.error
    if result.review_ask:
        payload["review_ask"] = result.review_ask
    if result.enable_ask:
        payload["enable_ask"] = result.enable_ask
    if result.error:
        payload["error"] = result.error
    if result.description:
        payload["description"] = result.description
    return json.dumps(payload, ensure_ascii=False)


def _first_review_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().strip("#*- `")
        if line:
            return line[:240] + "..." if len(line) > 240 else line
    return ""
