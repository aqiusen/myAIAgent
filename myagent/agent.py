"""编排层（Agent）。

对应 Suna 的 internal/agent。它自己不调用模型、不执行工具，
而是把 memory（历史）、runner（调用）、tools（工具）这几个模块串起来，
对外提供最简单的接口：run(user_input) -> 最终回答。

这正是 Suna 分层的价值：
  - 调用方（CLI）只需要知道 agent.run()，不需要懂模型循环细节。
  - Agent 内部各模块只做自己的事，谁都不越界。
"""
import os
import re

from .config import Config, PROJECT_ROOT
from .memory import Memory
from .runner import Runner
from .guard import build_guard
from .store import Store
from .model_registry import ModelRegistry
from .tools.mcp_provider import MCPToolProvider
from .tools.skill_provider import SkillToolProvider
from .tools.spawn_provider import make_spawn_tool
from .tools.base import can_grant_to_subtask
from .subtask import Request as SubtaskRequest, render_subtask_system, result_payload, run_subtask
from .skill import (
    SCOPE_PROJECT,
    CallbackPrompter,
    EnableDecision,
    JsonSkillStore,
    MemorySkillStore,
    Runtime,
    discover_project,
    rank_skills,
    render_summary,
    sync_user_skills,
    user_skills_synced,
)
from . import tools

# 行首 / 这些是 TUI 命令，不当成 Skill 激活。
_RESERVED_SLASH = {"q", "quit", "setting", "model", "skills"}


# smart 模式默认的 LLM 审查器：用同一个模型判断命令是否危险。
# 审风险不审意图：只判断操作本身是否危险，不判断是否符合用户意图。
def _default_llm_reviewer(provider, model: str):
    def reviewer(command: str, params_json: str) -> str:
        prompt = (
            "你是命令安全审查器。判断下面这条 shell 命令是否危险。\n"
            "只输出 JSON：{\"decision\": \"approve\" 或 \"reject\", \"reason\": \"简短原因\"}\n\n"
            f"命令: {command}\n"
            f"参数: {params_json}\n"
        )
        resp = provider.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
        )
        return resp.choices[0].message.content or ""
    return reviewer


def _default_skill_reviewer(provider, model: str):
    """Skill LLM 审查器（对应 Suna agentSkillReviewer）。

    审安全、清晰度和是否名实相符，不替用户做启用决定。
    """
    def reviewer(req) -> str:
        reason_lines = "\n".join(f"- {r}" for r in (req.reasons or [])) or "none"
        file_blocks = []
        for item in req.files or []:
            tag = f"{item.path} (truncated)" if item.truncated else item.path
            file_blocks.append(f"--- {tag} ---\n{item.content}")
        prompt = (
            "Review this Skill for safety, clarity, and whether it matches its stated purpose.\n"
            "Include: 1. Summary 2. Risks or issues 3. Recommendation\n"
            "Do not invent files not shown.\n\n"
            f"Skill: {req.name}\n"
            f"Description: {req.description}\n\n"
            f"Static check reasons:\n{reason_lines}\n\n"
            "Files:\n" + "\n".join(file_blocks)
        )
        resp = provider.client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "You are reviewing an Agent Skill. Be concise, practical, and safety-focused.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=600,
        )
        return resp.choices[0].message.content or ""
    return reviewer


class Agent:
    def __init__(self, config: Config, confirm_callback=None, session_id=None, model_ref="default"):
        self.config = config

        # 模型注册表（对应 Suna 的 Router）：持有多个模型，按 ref 路由。
        self.registry = ModelRegistry(config.models)
        self.model_ref = model_ref

        # 可选持久化：建 Store；新会话等第一条消息再落库，避免空会话。
        self.store = None
        if config.db_path:
            self.store = Store(config.db_path)
        self.session_id = session_id

        self.memory = Memory(
            max_history=config.max_history,
            max_tokens=config.max_tokens,
            store=self.store,
            session_id=session_id,
        )
        # 若指定了已有会话，加载其历史（不含系统提示词）
        if session_id is not None and self.store is not None:
            self.memory.load_from_store(session_id)

        # Skill Runtime（对应 Suna Agent.skills + projectSkills）
        self.cwd = os.getcwd()
        skills_dir = config.skills_dir or str(PROJECT_ROOT / "skills")
        if config.skills_records_path:
            skill_store = JsonSkillStore(config.skills_records_path)
        else:
            skill_store = MemorySkillStore()
        self.skills = Runtime(skills_dir, skill_store)
        try:
            self.skills.reload()
        except ValueError:
            pass
        self.project_skills = discover_project(self.cwd)
        user_home = (config.skills_user_home or "").strip()
        if user_home != "-" and not user_skills_synced(skills_dir):
            sync_user_skills(skills_dir, user_home)
            try:
                self.skills.reload()
            except ValueError:
                pass
        self.skill_provider = SkillToolProvider(
            self.skills,
            catalog_getter=lambda: self.project_skills,
        )

        # 工具：内置 + MCP + Skill（对应 Suna 的 tools 目录）
        self.mcp_provider = None
        self.tools_list = list(tools.TOOLS)
        if config.mcp_servers:
            self.mcp_provider = MCPToolProvider(config.mcp_servers)
            self.tools_list.extend(self.mcp_provider.load())
        self.tools_list.extend(self.skill_provider.tools())
        self.tools_list.append(make_spawn_tool(self.tools_list, self.execute_spawn))
        self.schemas = [t.schema for t in self.tools_list]  # 工具声明（只给模型看的那份）
        self._in_subtask = False

        # 先建 Runner（它持有模型 Provider），再建 Guard 复用其客户端。
        provider = self.registry.get_provider(model_ref)
        self.runner = Runner(config, provider=provider)
        self.runner.tools_list = self.tools_list

        # 创建 Guard（参考 Suna internal/guard）
        audit_path = config.guard_audit_path or None
        guard = build_guard(mode=config.guard_mode, audit_path=audit_path)
        # smart 模式：注入 LLM 审查器（用同一个模型 Provider）
        if guard.mode == "smart":
            guard.llm_reviewer = _default_llm_reviewer(provider, config.model)
        self.guard = guard
        self.runner.guard = guard
        self.runner.confirm_callback = confirm_callback
        self.runner.memory = self.memory
        self.memory.complete_fn = self._compress_complete
        self.memory.context_window = config.max_tokens

        # Skill 审查器复用当前模型；prompter 由 TUI 稍后注入。
        self.skills.set_reviewer(_FnSkillReviewer(_default_skill_reviewer(provider, config.model)))
        self.last_activated_skills = []
        self.last_skill_errors = []

        # 每段会话开始时，先把系统提示词准备好（含 Available Skills 摘要）
        self.memory.add_system(self._build_system_prompt())

    # ---------- 模型切换 ----------
    def list_models(self) -> list:
        """列出所有可用模型 ref。"""
        return self.registry.list_models()

    def current_model(self) -> str:
        """返回当前模型 ref。"""
        return self.model_ref

    def switch_model(self, ref: str) -> str:
        """运行时切换模型（对应 Suna 的 Router.Bind）。

        切换后：
          - Runner 的 Provider 换成新模型的
          - smart 模式的 LLM 审查器也换成新模型的
        """
        if not self.registry.has(ref):
            raise KeyError(f"模型不存在: {ref}，可用: {self.list_models()}")
        provider = self.registry.get_provider(ref)
        self.runner.provider = provider
        model_name = self.registry.get_config(ref).model
        # smart 模式：LLM 审查器跟着换
        if self.guard.mode == "smart":
            self.guard.llm_reviewer = _default_llm_reviewer(provider, model_name)
        self.skills.set_reviewer(_FnSkillReviewer(_default_skill_reviewer(provider, model_name)))
        self.model_ref = ref
        return ref

    def set_skill_prompter(self, fn) -> None:
        """注入 skill_start 的用户选择回调（TUI 弹窗）。"""
        self.skills.set_prompter(CallbackPrompter(fn) if fn else None)

    def list_skill_infos(self) -> list:
        """列出全局 + project Skill。同名全局 Skill 只会出现一次。"""
        items = []
        seen = set()
        for info in self.skills.list():
            key = (info.scope, info.path, info.name)
            seen.add(key)
            items.append({
                "name": info.name,
                "description": info.description,
                "scope": info.scope,
                "enabled": info.enabled,
                "valid": info.valid,
                "can_toggle": info.can_toggle,
                "path": info.path,
                "error": info.error,
            })
        for desc in self.project_skills.descriptors():
            self._append_external_skill(items, seen, desc, SCOPE_PROJECT)
        items.sort(key=lambda item: (item["scope"], item["name"], item["path"]))
        return items

    def resolve_skill_name(self, token: str) -> str:
        """把 $token 解析成精确 Skill 名：先精确匹配，再模糊搜索。"""
        token = (token or "").strip()
        infos = [item for item in self.list_skill_infos() if item.get("valid")]
        for item in infos:
            if item.get("name") == token:
                return item["name"]
        ranked = rank_skills(infos, token)
        return ranked[0]["name"] if ranked else ""

    def load_mentioned_skills(self, text: str) -> tuple:
        """解析 `$name` 或行首 `/name`（保留命令除外），加载正文。"""
        mentions = re.findall(r"\$([A-Za-z0-9._-]+)", text or "")
        slash = re.match(r"^/([A-Za-z0-9._-]+)(?:\s|$)", text or "")
        if slash and slash.group(1) not in _RESERVED_SLASH:
            mentions.insert(0, slash.group(1))
        loaded = []
        errors = []
        seen = set()
        for token in mentions:
            name = self.resolve_skill_name(token)
            if not name:
                errors.append(f"${token} 没有匹配到 Skill")
                continue
            if name in seen:
                continue
            try:
                desc, content = self.skills.load_global(name)
            except ValueError as exc:
                errors.append(f"${token} ({name}) 加载失败: {exc}")
                continue
            seen.add(name)
            loaded.append((desc.name, content, desc.path))
        return loaded, errors

    @staticmethod
    def strip_skill_mentions(text: str) -> str:
        """从用户可见请求里去掉 $skill 和行首 /skill，只留下真正要做的事。"""
        text = re.sub(r"\$[A-Za-z0-9._-]+", "", text or "")
        slash = re.match(r"^/([A-Za-z0-9._-]+)(\s+|$)", text)
        if slash and slash.group(1) not in _RESERVED_SLASH:
            text = text[slash.end():]
        return text.strip()

    def import_user_skills(self) -> list:
        """从用户主目录再扫一遍，把还没有的 Skill 拷进本 agent。

        给 /skills sync 用。已有同名目录不覆盖。
        """
        home = (self.config.skills_user_home or "").strip()
        if home == "-":
            home = os.path.expanduser("~")
        copied = sync_user_skills(self.skills.root, home)
        try:
            self.skills.reload()
        except ValueError:
            pass
        self._refresh_system_prompt()
        return copied

    @staticmethod
    def _append_external_skill(items, seen, desc, scope: str) -> None:
        """把 project/user Skill 并进 /skills 列表，按路径去重。"""
        key = (scope, desc.path, desc.name)
        if key in seen:
            return
        seen.add(key)
        items.append({
            "name": desc.name,
            "description": desc.description,
            "scope": scope,
            "enabled": desc.valid,
            "valid": desc.valid,
            "can_toggle": False,
            "path": desc.path,
            "error": desc.error,
        })

    def toggle_skill(self, name: str) -> bool:
        """切换全局 Skill 的 enabled（对应 Suna skill.set）。

        返回切换后是否启用。project / invalid 的 Skill 不能切。
        """
        name = (name or "").strip()
        infos = {info.name: info for info in self.skills.list()}
        info = infos.get(name)
        if info is None:
            raise KeyError(f"Skill 不存在: {name}")
        if info.scope == SCOPE_PROJECT or not info.can_toggle:
            raise ValueError(f'该 Skill 暂不能切换: {name}')
        enabled = not (info.enabled and info.valid)
        self.skills.set_enabled(EnableDecision(name=name, enabled=enabled))
        self._refresh_system_prompt()
        return enabled

    def _build_system_prompt(self, active_skills=None) -> str:
        """拼系统提示词：角色说明 + Available Skills + 本轮用户激活的 Skill 正文。"""
        summary = render_summary(
            self.skills.enabled_descriptors(),
            self.project_skills.descriptors(),
            self.skills.root,
        )
        extra = (
            "Complete the user's task. If an operation fails, inspect the cause and adjust. "
            "If the repo has no match, look it up with `http` before concluding. "
            "Analyze commands against evidence (docs or source), not generic tips.\n"
            "To load a Skill's full instructions, call `skill_load` with "
            "name and scope=global. Do not list_dir or read_file the skills/ "
            "directory to activate a Skill.\n"
            "After creating or importing a global Skill, use `skill_start`; "
            "do not bypass its verification and enable decisions.\n"
            "Available subtask models:\n" + self._spawn_models_summary()
        )
        parts = [self.config.system_prompt]
        if summary:
            parts.append(summary)
        parts.append(extra.strip())
        if active_skills:
            blocks = [
                "The user explicitly activated the following Skill(s). "
                "Treat them as operating instructions for this turn, not as the user's request. "
                "The user's actual request is the last user message."
            ]
            for name, content, path in active_skills:
                blocks.append(f"### Active Skill: {name}\nSkill root: {path}\n\n{content}")
            parts.append("## Active Skills\n" + "\n\n".join(blocks))
        return "\n\n".join(parts)

    def _spawn_models_summary(self) -> str:
        refs = self.registry.list_models()
        if not refs:
            return "- No models configured. Configure a model before using spawn."
        return "\n".join(f"- {ref}" for ref in refs)

    def execute_spawn(self, task: str = "", model: str = "", tools=None, context: str = "") -> str:
        """对照 Suna ExecuteSpawnTool：独立模型、独立上下文、缩小工具箱。"""
        import json
        if self._in_subtask:
            return "tool \"spawn\" is not available to subtasks"
        task = (task or "").strip()
        if not task:
            return "task is required"
        model_ref = (model or "").strip()
        if not model_ref:
            return "spawn requires explicit model. Choose one of: " + ", ".join(self.registry.list_models())
        if not self.registry.has(model_ref):
            return f'invalid spawn model "{model_ref}". Choose one of: ' + ", ".join(self.registry.list_models())
        allowed, err = self._build_subtask_tools(tools)
        if err:
            return err
        names = [t.name for t in allowed]
        prompt = render_subtask_system(task, ", ".join(names) or "none", context or "")
        req = SubtaskRequest(
            task=task,
            system=prompt,
            provider=self.registry.get_provider(model_ref),
            config=self.config,
            tools=allowed,
            guard=self.guard,
            confirm_callback=lambda p: False,
        )
        self._in_subtask = True
        try:
            res = run_subtask(req)
        finally:
            self._in_subtask = False
        payload = result_payload(res)
        text = json.dumps(payload, ensure_ascii=False)
        if res.status == "failed":
            err_text = (res.error or res.text or "subtask failed").strip()
            return f"{text}\n[spawn error] {err_text}"
        return text

    def _build_subtask_tools(self, value) -> tuple:
        grantable = {t.name: t for t in self.tools_list if can_grant_to_subtask(t)}
        names = value if isinstance(value, list) else []
        allowed = []
        seen = set()
        for name in names:
            name = str(name or "").strip()
            if not name or name in seen:
                continue
            tool = grantable.get(name)
            if tool is None:
                return [], (
                    f'invalid spawn tool "{name}". Choose from: '
                    + ", ".join(sorted(grantable))
                )
            seen.add(name)
            allowed.append(tool)
        return allowed, ""

    def _compress_complete(self, prompt: str, max_tokens: int) -> str:
        """压缩专用 LLM 调用：无 tools、temperature=0（对照 Suna purpose=compress）。"""
        resp = self.runner.provider.complete(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            temperature=0,
            max_tokens=max_tokens,
            stream=False,
        )
        return (resp.get("content") or "").strip()

    def _refresh_system_prompt(self, active_skills=None) -> None:
        """每轮对话前刷新 Skills 摘要；若用户 $ 激活了 Skill，把正文放进系统提示词。"""
        self.memory.set_system(self._build_system_prompt(active_skills=active_skills))

    def run(self, user_input: str, on_delta=None, on_tool_call=None) -> str:
        """接收用户一句话，返回 agent 的最终文字回答。

        on_delta：可选回调，流式输出时每生成一段文字就调用一次
        （由 CLI 传入，用来边生成边打印）。
        on_tool_call：可选回调，工具调用时调用 (name, args_json)。
        """
        # 0) $skill 激活：正文进系统提示词（对照 Codex 的 skill input item），用户消息只留请求
        active, skill_errors = self.load_mentioned_skills(user_input)
        self.last_activated_skills = [name for name, _, _ in active]
        self.last_skill_errors = skill_errors
        self._refresh_system_prompt(active_skills=active)

        # 1) 把用户输入写入历史
        request = self.strip_skill_mentions(user_input) or user_input
        if skill_errors:
            request = request + "\n\n[Skill 加载失败: " + "; ".join(skill_errors) + "]"
        self.memory.add_user(request)
        self.session_id = self.memory.session_id

        # 2) 取裁剪后的消息列表跑核心循环
        messages = self.memory.snapshot()
        self.runner.tool_callback = on_tool_call
        answer = self.runner.run(messages, self.schemas, on_delta=on_delta)

        # 3) 把最终答案写回历史，供下一轮对话引用
        self.memory.add_assistant(answer)
        self.session_id = self.memory.session_id
        return answer


class _FnSkillReviewer:
    """把函数适配成 Runtime 需要的 review_skill 接口。"""

    def __init__(self, fn):
        self.fn = fn

    def review_skill(self, req) -> str:
        return self.fn(req)
