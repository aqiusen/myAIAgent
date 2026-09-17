"""孤立子任务（严格对照 Suna internal/subtask/subtask.go）。

不继承主会话历史、记忆、完整工具箱；不能再 spawn、不能问用户。
最终必须交一份 JSON：result + side_effects。
"""
from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Config
from .memory import Memory
from .runner import Runner
from .tools.base import Tool

STATUS_COMPLETED = "completed"
STATUS_COMPLETED_UNSTRUCTURED = "completed_unstructured"
STATUS_FAILED = "failed"

SIDE_NONE = "none"
SIDE_CLEANED = "cleaned"
SIDE_REMAINING = "remaining"
SIDE_UNKNOWN = "unknown"


@dataclass
class SideEffects:
    status: str = SIDE_UNKNOWN
    summary: str = ""
    paths: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = {"status": self.status}
        if self.summary:
            payload["summary"] = self.summary
        if self.paths:
            payload["paths"] = list(self.paths)
        return payload


@dataclass
class Result:
    status: str
    text: str = ""
    error: str = ""
    side_effects: SideEffects = field(default_factory=SideEffects)


@dataclass
class Request:
    task: str
    system: str
    provider: object
    config: Config
    tools: List[Tool]
    guard: object = None
    confirm_callback: object = None


def render_subtask_system(task: str, tools_summary: str, context: str = "", workspace: str = "") -> str:
    """对照 Suna subtask_system.md。"""
    uname = platform.uname()
    lines = [
        "You are a myAIAgent subtask. Complete the assigned task for the main agent.",
        "",
        "Work only within the assigned scope and use only the available tools. "
        "If blocked, report the blocker instead of asking the user or delegating further.",
        "",
        'Return exactly one JSON object: `{"result":"...","side_effects":{"status":"none|cleaned|remaining|unknown","summary":"...","paths":["..."]}}`. '
        "Keep `result` concise and self-contained. Report any local or external side effects caused by tool use.",
        "",
        "Task:",
        task,
        "",
        f"Environment: {uname.system}/{uname.machine}, cwd `{os_getcwd()}`.",
    ]
    if workspace:
        lines.append(
            f"Workspace boundary: `{workspace}`. File operations, command paths, working directories, and redirection targets must stay inside it."
        )
    lines.append(f"Available tools: {tools_summary or 'none'}.")
    if context.strip():
        lines.append("")
        lines.append("Context:")
        lines.append(context.strip())
    return "\n".join(lines)


def os_getcwd() -> str:
    import os
    return os.getcwd()


def run_subtask(req: Request) -> Result:
    """独立 Memory + Runner 跑完子任务。失败也返回 Result，不把异常丢给主循环。"""
    if req.provider is None:
        return failed_result("subtask model binding is required", False)

    working = Memory(max_tokens=req.config.max_tokens)
    working.add_system(req.system)
    working.add_user(req.task)
    working.complete_fn = _make_compress_fn(req.provider)

    sub_runner = Runner(req.config, guard=req.guard, confirm_callback=req.confirm_callback or (lambda p: False), provider=req.provider)
    sub_runner.tools_list = list(req.tools)
    sub_runner.memory = working

    schemas = [t.schema for t in req.tools]
    try:
        text = sub_runner.run(working.snapshot(), schemas, on_delta=None)
    except Exception as exc:
        had = _had_tool_call(working)
        return failed_result(str(exc), had)
    if not (text or "").strip():
        return failed_result("subtask returned no answer", _had_tool_call(working))
    return parse_final_result(text)


def failed_result(message: str, had_tool_call: bool) -> Result:
    return Result(
        status=STATUS_FAILED,
        error=message,
        side_effects=_fallback_side_effects(had_tool_call, "Subtask failed before reporting side effects."),
    )


def parse_final_result(raw: str) -> Result:
    text = (raw or "").strip()
    payload = _json_payload(text)
    try:
        out = json.loads(payload)
    except json.JSONDecodeError:
        return Result(
            status=STATUS_COMPLETED_UNSTRUCTURED,
            text=text,
            side_effects=SideEffects(
                status=SIDE_UNKNOWN,
                summary="Subtask completed but did not return a valid side_effects report.",
            ),
        )
    if not isinstance(out, dict):
        return Result(
            status=STATUS_COMPLETED_UNSTRUCTURED,
            text=text,
            side_effects=SideEffects(
                status=SIDE_UNKNOWN,
                summary="Subtask completed but did not return a valid side_effects report.",
            ),
        )
    result_text = str(out.get("result") or "").strip()
    if not result_text:
        return Result(
            status=STATUS_COMPLETED_UNSTRUCTURED,
            text=text,
            side_effects=SideEffects(
                status=SIDE_UNKNOWN,
                summary="Subtask returned JSON without a non-empty result.",
            ),
        )
    se = normalize_side_effects(_parse_side_effects(out.get("side_effects")))
    return Result(status=STATUS_COMPLETED, text=result_text, side_effects=se)


def normalize_side_effects(se: SideEffects) -> SideEffects:
    if se.status in {SIDE_NONE, SIDE_CLEANED, SIDE_REMAINING, SIDE_UNKNOWN}:
        return se
    if not se.status:
        se.status = SIDE_UNKNOWN
        se.summary = _merge_summary("Subtask omitted side_effects.status.", se.summary)
        return se
    se.summary = _merge_summary(
        f'Subtask reported unsupported side_effects.status "{se.status}".', se.summary,
    )
    se.status = SIDE_UNKNOWN
    return se


def result_payload(res: Result) -> dict:
    payload = {
        "status": res.status,
        "result": res.text,
        "side_effects": res.side_effects.to_dict(),
    }
    if (res.error or "").strip():
        payload["error"] = res.error
    return payload


def _parse_side_effects(raw) -> SideEffects:
    if not isinstance(raw, dict):
        return SideEffects(status="")
    paths = raw.get("paths") or []
    if not isinstance(paths, list):
        paths = []
    return SideEffects(
        status=str(raw.get("status") or ""),
        summary=str(raw.get("summary") or ""),
        paths=[str(p) for p in paths],
    )


def _json_payload(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1].strip()
    return text


def _fallback_side_effects(had_tool_call: bool, summary: str) -> SideEffects:
    if had_tool_call:
        return SideEffects(status=SIDE_UNKNOWN, summary=summary)
    return SideEffects(status=SIDE_NONE)


def _merge_summary(prefix: str, summary: str) -> str:
    prefix = (prefix or "").strip()
    summary = (summary or "").strip()
    if not prefix:
        return summary
    if not summary:
        return prefix
    return prefix + " " + summary


def _had_tool_call(working: Memory) -> bool:
    return any(m.get("role") == "tool" for m in working._messages)


def _make_compress_fn(provider):
    def complete_fn(prompt: str, max_tokens: int) -> str:
        resp = provider.complete(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            temperature=0,
            max_tokens=max_tokens,
            stream=False,
        )
        return (resp.get("content") or "").strip()
    return complete_fn
