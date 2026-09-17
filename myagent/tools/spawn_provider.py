"""spawn 工具（对照 Suna internal/tools/agenttools）。

只负责 schema 和路由，真正执行由 Agent.execute_spawn。
"""
from __future__ import annotations

from typing import Callable, List

from .base import Tool, can_grant_to_subtask

TOOL_SPAWN = "spawn"


def spawn_tool_names(catalog: List[Tool]) -> List[str]:
    return [t.name for t in catalog if can_grant_to_subtask(t)]


def spawn_parameters(tool_names: List[str]) -> dict:
    items = {"type": "string"}
    if tool_names:
        items["enum"] = list(tool_names)
    return {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Self-contained task for the subtask"},
            "model": {"type": "string", "description": "Exact model ref from Available subtask models"},
            "tools": {
                "type": "array",
                "items": items,
                "description": "Allowed tools for the isolated subtask; use [] for model-only tasks",
            },
            "context": {"type": "string", "description": "Extra context"},
        },
        "required": ["task", "model", "tools"],
    }


def make_spawn_tool(catalog: List[Tool], execute: Callable[..., str]) -> Tool:
    names = spawn_tool_names(catalog)
    return Tool(
        name=TOOL_SPAWN,
        description=(
            "Delegate an isolated subtask to a selected model. It sees only task/context and allowed tools; "
            "it does not inherit main history or images."
        ),
        parameters=spawn_parameters(names),
        fn=execute,
        source="agent",
    )
