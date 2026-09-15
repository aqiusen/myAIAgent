"""Skill 子系统（对应 Suna internal/skill）。

Skill 是「可复用工作方法」：一个目录 + SKILL.md。
模型平时只看到短描述；需要完整指令时调用 skill_load。
"""
from .fuzzy import fuzzy_match, highlight_fuzzy, rank_skills
from .catalog import (
    SCOPE_GLOBAL,
    SCOPE_PROJECT,
    SCOPE_USER,
    Catalog,
    Descriptor,
    discover_project,
    discover_user,
    render_summary,
    sync_user_skills,
    user_skills_synced,
)
from .manager import CheckResult, Info, Manager, Record
from .runtime import CallbackPrompter, EnableDecision, Runtime
from .store import JsonSkillStore, MemorySkillStore
from .workflow import START_CHECK, START_IMPORT, start_json_result

TOOL_LOAD = "skill_load"
TOOL_START = "skill_start"

__all__ = [
    "SCOPE_GLOBAL",
    "SCOPE_PROJECT",
    "SCOPE_USER",
    "Catalog",
    "CallbackPrompter",
    "CheckResult",
    "Descriptor",
    "EnableDecision",
    "Info",
    "JsonSkillStore",
    "Manager",
    "MemorySkillStore",
    "Record",
    "Runtime",
    "START_CHECK",
    "START_IMPORT",
    "TOOL_LOAD",
    "TOOL_START",
    "discover_project",
    "discover_user",
    "fuzzy_match",
    "highlight_fuzzy",
    "rank_skills",
    "render_summary",
    "sync_user_skills",
    "user_skills_synced",
    "start_json_result",
]
