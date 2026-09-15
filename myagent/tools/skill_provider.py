"""Skill 工具适配（对应 Suna internal/tools/skilltools/provider.go）。

只把 Runtime 暴露成两个工具：
  - skill_load：加载已启用 Skill 的完整 SKILL.md
  - skill_start：导入/检查 + 用户确认启用

Skill 领域逻辑仍在 myagent.skill，这里不做扫描、不写 records。
这两个工具在 Suna 里标记 GuardNever，执行时跳过 Guard。
"""
from __future__ import annotations

from typing import Callable, List, Optional

from .base import Tool
from ..skill import (
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
    SCOPE_USER,
    Catalog,
    Runtime,
    TOOL_LOAD,
    TOOL_START,
    start_json_result,
)


def load_parameters() -> dict:
    """skill_load 的 JSON Schema。"""
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact Skill name from Available Skills",
            },
            "scope": {
                "type": "string",
                "enum": [SCOPE_GLOBAL, SCOPE_PROJECT, SCOPE_USER],
                "description": "Skill scope shown in Available Skills",
            },
            "path": {
                "type": "string",
                "description": "Exact discovered Skill root. Required for project/user Skills and omitted for global Skills.",
            },
        },
        "required": ["name", "scope"],
    }


def start_parameters() -> dict:
    """skill_start 的 JSON Schema。"""
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["import", "check"],
                "description": "Skill workflow action. Use import for a source path/URL, check after you prepared files under the skills directory.",
            },
            "name": {
                "type": "string",
                "description": "Skill name. Required for check; optional for import.",
            },
            "source": {
                "type": "string",
                "description": "Local directory path, zip path, or git/http/ssh URL for import.",
            },
        },
        "required": ["action"],
    }


class SkillToolProvider:
    """把 Skill Runtime 适配成 Tool 列表。"""

    def __init__(
        self,
        runtime: Runtime,
        catalog_getter: Optional[Callable[[], Optional[Catalog]]] = None,
        user_catalog_getter: Optional[Callable[[], Optional[Catalog]]] = None,
    ):
        self.runtime = runtime
        self.catalog_getter = catalog_getter or (lambda: None)
        self.user_catalog_getter = user_catalog_getter or (lambda: None)

    def tools(self) -> List[Tool]:
        """返回 skill_load / skill_start 两个工具。"""
        return [
            Tool(
                name=TOOL_LOAD,
                description=(
                    "Load full details for an available Skill. Global Skills must be enabled; "
                    "project and user Skills must be selected by their exact discovered path. "
                    "Use only when you need the Skill's full instructions."
                ),
                parameters=load_parameters(),
                fn=self.execute_load,
            ),
            Tool(
                name=TOOL_START,
                description=(
                    "Start the built-in Skill verification workflow. Use import to import a Skill source, "
                    "or check after you prepared files under the skills directory. The workflow runs static check, "
                    "asks the user whether to run LLM review, and asks whether to enable."
                ),
                parameters=start_parameters(),
                fn=self.execute_start,
            ),
        ]

    def execute_load(self, name: str = "", scope: str = "", path: str = "") -> str:
        """加载 Skill 全文。失败返回错误文本，不抛给循环。"""
        name = (name or "").strip()
        scope = (scope or "").strip()
        path = (path or "").strip()
        if not name:
            return "name is required"
        try:
            if scope == SCOPE_GLOBAL:
                if path:
                    return "path must be omitted for global skills"
                desc, content = self.runtime.load_global(name)
            elif scope == SCOPE_PROJECT:
                catalog = self.catalog_getter()
                if catalog is None:
                    return "project skill catalog is unavailable for this session"
                desc, content = catalog.load_project(name, path)
            elif scope == SCOPE_USER:
                catalog = self.user_catalog_getter()
                if catalog is None:
                    return "user skill catalog is unavailable for this session"
                desc, content = catalog.load_user(name, path)
            else:
                return "scope must be global, project, or user"
        except (ValueError, OSError) as exc:
            return str(exc)
        return f"[Skill: {desc.name}]\nScope: {desc.scope}\nSkill root: {desc.path}\n\n{content}"

    def execute_start(self, action: str = "", name: str = "", source: str = "") -> str:
        """跑 import/check 工作流，把摘要 JSON 回给模型。"""
        try:
            result = self.runtime.start({"action": action, "name": name, "source": source})
        except (ValueError, OSError) as exc:
            return str(exc)
        return start_json_result(result)
