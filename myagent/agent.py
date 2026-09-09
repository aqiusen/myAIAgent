"""编排层（Agent）。

对应 Suna 的 internal/agent。它自己不调用模型、不执行工具，
而是把 memory（历史）、runner（调用）、tools（工具）这几个模块串起来，
对外提供最简单的接口：run(user_input) -> 最终回答。

这正是 Suna 分层的价值：
  - 调用方（CLI）只需要知道 agent.run()，不需要懂模型循环细节。
  - Agent 内部各模块只做自己的事，谁都不越界。
"""
from .config import Config
from .memory import Memory
from .runner import Runner
from .guard import Guard, build_guard
from .store import Store
from .model_registry import ModelRegistry
from .tools.mcp_provider import MCPToolProvider
from . import tools


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

        # 工具：内置工具 + MCP 工具（对应 Suna 的 tools 目录）
        self.mcp_provider = None
        if config.mcp_servers:
            self.mcp_provider = MCPToolProvider(config.mcp_servers)
            mcp_tools = self.mcp_provider.load()
            self.tools_list = tools.TOOLS + mcp_tools
        else:
            self.tools_list = tools.TOOLS
        self.schemas = [t.schema for t in self.tools_list]  # 工具声明（只给模型看的那份）

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

        # 每段会话开始时，先把系统提示词准备好
        self.memory.add_system(config.system_prompt)

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
        # smart 模式：LLM 审查器跟着换
        if self.guard.mode == "smart":
            self.guard.llm_reviewer = _default_llm_reviewer(provider, self.registry.get_config(ref).model)
        self.model_ref = ref
        return ref

    def run(self, user_input: str, on_delta=None, on_tool_call=None) -> str:
        """接收用户一句话，返回 agent 的最终文字回答。

        on_delta：可选回调，流式输出时每生成一段文字就调用一次
        （由 CLI 传入，用来边生成边打印）。
        on_tool_call：可选回调，工具调用时调用 (name, args_json)。
        """
        # 1) 把用户输入写入历史
        self.memory.add_user(user_input)
        self.session_id = self.memory.session_id

        # 2) 取裁剪后的消息列表跑核心循环
        messages = self.memory.snapshot()
        self.runner.tool_callback = on_tool_call
        answer = self.runner.run(messages, self.schemas, on_delta=on_delta)

        # 3) 把最终答案写回历史，供下一轮对话引用
        self.memory.add_assistant(answer)
        self.session_id = self.memory.session_id
        return answer
