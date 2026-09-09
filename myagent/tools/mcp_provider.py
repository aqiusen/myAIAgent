"""MCP 工具提供器（参考 Suna internal/tools/mcptools/provider.go）。

职责：把 MCP 服务器提供的工具，包装成我们系统的 Tool 对象，
让模型能像调用内置工具一样调用 MCP 工具。

对应 Suna 的 mcptools.Provider：
  - Specs()    列出 MCP 工具（tools/list）
  - Execute()  调用 MCP 工具（tools/call）

命名约定（参考 Suna）：mcp__server__tool
  - 避免不同服务器的同名工具冲突
  - 例如 mcp__filesystem__read_file
"""
import re
from typing import Dict, List, Optional

from .base import Tool
from ..mcp import MCPClient

# MCP 工具名前缀（参考 Suna 的 prefix）
MCP_PREFIX = "mcp__"


def public_name(server: str, tool: str) -> str:
    """生成公开工具名：mcp__server__tool（参考 Suna 的 PublicName）。"""
    return f"{MCP_PREFIX}{_sanitize(server)}__{_sanitize(tool)}"


def _sanitize(name: str) -> str:
    """把名字里的非法字符替换为下划线（参考 Suna 的 sanitizeName）。"""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return name or "unnamed"


class MCPToolProvider:
    """MCP 工具提供器：持有多个 MCP 客户端，暴露它们的工具。"""

    def __init__(self, clients: List[MCPClient]):
        self.clients = clients
        self._tools: List[Tool] = []
        self._loaded = False

    def load(self) -> List[Tool]:
        """连接所有 MCP 服务器，加载它们的工具。"""
        if self._loaded:
            return self._tools
        for client in self.clients:
            try:
                client.start()
                for item in client.list_tools():
                    name = public_name(client.server_id, item.get("name", ""))
                    schema = item.get("inputSchema") or {
                        "type": "object",
                        "properties": {},
                    }
                    tool = Tool(
                        name=name,
                        description=item.get("description", ""),
                        parameters=schema,
                        fn=self._make_executor(client, item.get("name", "")),
                    )
                    self._tools.append(tool)
            except Exception as exc:
                # 单个服务器失败不影响其他服务器
                print(f"[MCP] 服务器 {client.server_id} 加载失败: {exc}")
        self._loaded = True
        return self._tools

    def _make_executor(self, client: MCPClient, tool_name: str):
        """为某个 MCP 工具创建执行函数。"""
        def executor(**kwargs):
            try:
                res = client.call_tool(tool_name, kwargs)
                return _format_result(res)
            except Exception as exc:
                return f"[MCP 工具调用失败] {exc}"
        return executor

    def close(self) -> None:
        for client in self.clients:
            try:
                client.close()
            except Exception:
                pass


def _format_result(res: Dict) -> str:
    """格式化 MCP 工具返回结果（参考 Suna 的 formatResult）。"""
    content = res.get("content", [])
    parts = []
    for item in content:
        if item.get("type") in ("", "text"):
            if item.get("text"):
                parts.append(item["text"])
        else:
            # 非文本内容（图片/二进制）只给提示，不展开
            parts.append(f"[MCP {item.get('type')} content omitted]")
    if not parts:
        return "[MCP tool returned no content]"
    return "\n".join(parts)
