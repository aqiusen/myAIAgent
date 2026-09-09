"""配置模块。

对应 Suna 的 internal/config。它只有一个职责：集中管理"配置从哪来、默认值是什么"，
不让其它模块各自去猜超时、路径、凭据之类的魔法值。

设计取舍（对应架构文档"默认值、超时、凭据放在所属层集中维护"）：
  - 凭据（API key）绝不硬编码进代码，全部走环境变量。这是安全底线。
  - 不引入像 pydantic 这样的大依赖，用 dataclass 就够，保持依赖极简（轻量、克制）。
  - Suna 用 TOML 文件存配置；这里是第一步原型，先用环境变量 + 默认值，
    等到需要多端配置时再升级成文件读取，思路完全一样。
"""
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .model_registry import ModelConfig
from .mcp import MCPClient


# 项目根目录（config.py 位于 myagent/ 下，上一级即根目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 本地配置文件路径
ENV_FILE = PROJECT_ROOT / ".env"


def _load_dotenv(path: Path = ENV_FILE) -> None:
    """极简 .env 加载器：把文件里的 KEY=VALUE 读进环境变量。

    规则：
      - 已存在的环境变量优先，不覆盖（这样 shell 里 export 的仍生效）。
      - 忽略空行和以 # 开头的注释行。
      - 值两端的引号会被去掉。
      - 支持多行值（以 [ 或 { 开头、跨多行的 JSON）。
    不引入 python-dotenv，保持依赖极简（与项目选型一致）。
    """
    if not path.exists():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 多行值：以 [ 或 { 开头且未闭合，继续拼接后续行
        if value.startswith(("[", "{")) and not _is_balanced(value):
            while i < len(lines):
                value += "\n" + lines[i].strip()
                i += 1
                if _is_balanced(value):
                    break
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _is_balanced(s: str) -> bool:
    """粗略判断方括号/花括号是否闭合（用于多行值拼接）。"""
    depth = 0
    for ch in s:
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth <= 0


def _db_path_from_env() -> str:
    db_path = os.environ.get("MY_AGENT_DB_PATH", str(PROJECT_ROOT / "db" / "myagent.db"))
    if not db_path:
        return ""
    path = Path(db_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


@dataclass
class Config:
    """运行所需的一小撮配置。字段都有默认值，避免某处突然 KeyError。"""

    # ---------- 模型连接 ----------
    model: str          # 模型名，例如 gpt-4o-mini / deepseek-chat
    base_url: str       # OpenAI 兼容端点，OpenAI 官方就是 https://api.openai.com/v1
    api_key: str        # 凭据，从环境变量读取

    # 多模型配置（对应 Suna 的 Router）。第一个是主模型，其余来自 MY_AGENT_MODELS。
    models: List[ModelConfig] = field(default_factory=list)

    # MCP 服务器配置（对应 Suna 的 MCP）。来自 MY_AGENT_MCP_SERVERS。
    mcp_servers: List[MCPClient] = field(default_factory=list)

    temperature: float = 0.7   # 温度：越低越确定，越高越有创造力
    max_tokens: int = 1024     # 单次生成上限，防止模型话痨烧钱

    # ---------- 记忆 / 上下文 ----------
    max_history: int = 20  # 裁剪前最多保留多少条用户/助手消息（见 memory.py）
    max_tokens: int = 8000  # 上下文 token 预算，超过则裁剪旧消息
    # 会话持久化 DB 路径（默认项目 db/ 文件夹，空则关闭持久化）
    db_path: str = ""

    # ---------- 安全 Guard ----------
    # 模式：readonly / ask / auto / smart（见 guard.py）
    guard_mode: str = "smart"
    # 审计日志路径（None 则不记录）
    guard_audit_path: str = ""

    # 系统提示词：定义 Agent 的角色与行为边界
    system_prompt: str = (
        "你是一个在本地运行的代码 Agent。"
        "你会使用用户提供的工具来读取文件或执行命令以解决问题。"
        "回答请简洁、准确。"
    )

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量构造配置。

        需要的变量：
          MY_AGENT_MODEL     模型名
          MY_AGENT_BASE_URL  OpenAI 兼容端点
          MY_AGENT_API_KEY   凭据（必填）

        只读 MY_AGENT_* 前缀，不读整个 os.environ，避免和别的程序变量互相污染。
        所有字段在缺少时给出明确报错，让用户第一眼就知道该设什么。

        优先从环境变量读取；若未设置，则回退到项目根目录的 .env 文件
        （见 _load_dotenv）。这样本地开发只需配一次 .env，不用每次 export。
        """
        _load_dotenv()

        api_key = os.environ.get("MY_AGENT_API_KEY")
        if not api_key:
            raise RuntimeError(
                "缺少环境变量 MY_AGENT_API_KEY。请先设置：\n"
                '  export MY_AGENT_API_KEY=你的key\n'
                '  export MY_AGENT_MODEL=模型名        # 例如 gpt-4o-mini\n'
                '  export MY_AGENT_BASE_URL=兼容端点    # 例如 https://api.openai.com/v1'
            )

        model = os.environ.get("MY_AGENT_MODEL", "gpt-4o-mini")
        base_url = os.environ.get("MY_AGENT_BASE_URL", "https://api.openai.com/v1")

        # 主模型（第一个，ref=default）
        models = [
            ModelConfig(
                ref="default",
                model=model,
                base_url=base_url,
                api_key=api_key,
            )
        ]
        # 额外模型：MY_AGENT_MODELS 是 JSON 数组，如
        #   [{"ref":"gpt4o","model":"gpt-4o","base_url":"...","api_key":"..."}]
        extra = os.environ.get("MY_AGENT_MODELS", "")
        if extra.strip():
            try:
                for item in json.loads(extra):
                    models.append(
                        ModelConfig(
                            ref=item.get("ref", ""),
                            model=item.get("model", ""),
                            base_url=item.get("base_url", ""),
                            api_key=item.get("api_key", ""),
                            provider=item.get("provider", "openai"),
                            temperature=float(item.get("temperature", 0.7)),
                            max_tokens=int(item.get("max_tokens", 1024)),
                        )
                    )
            except (json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"MY_AGENT_MODELS 解析失败: {exc}")

        # MCP 服务器：MY_AGENT_MCP_SERVERS 是 JSON 数组，如
        #   [{"id":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}]
        mcp_servers = []
        mcp_raw = os.environ.get("MY_AGENT_MCP_SERVERS", "")
        if mcp_raw.strip():
            try:
                for item in json.loads(mcp_raw):
                    mcp_servers.append(
                        MCPClient(
                            server_id=item.get("id", ""),
                            command=item.get("command", ""),
                            args=item.get("args", []),
                            cwd=item.get("cwd"),
                            env=item.get("env"),
                        )
                    )
            except (json.JSONDecodeError, ValueError) as exc:
                raise RuntimeError(f"MY_AGENT_MCP_SERVERS 解析失败: {exc}")

        return cls(
            model=model,
            base_url=base_url,
            api_key=api_key,
            models=models,
            mcp_servers=mcp_servers,
            guard_mode=os.environ.get("MY_AGENT_GUARD_MODE", "smart"),
            guard_audit_path=os.environ.get("MY_AGENT_GUARD_AUDIT", ""),
            max_tokens=int(os.environ.get("MY_AGENT_MAX_TOKENS", "8000")),
            db_path=_db_path_from_env(),
        )
