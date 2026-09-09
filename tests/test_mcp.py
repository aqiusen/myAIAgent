"""MCP 客户端 + 工具提供器单元测试（用假 MCP 服务器）。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.mcp import MCPClient
from myagent.tools.mcp_provider import MCPToolProvider, public_name


def make_client():
    return MCPClient(
        server_id="fake",
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "fake_mcp_server.py")],
    )


def test_public_name():
    assert public_name("my server", "read file") == "mcp__my_server__read_file"
    assert public_name("fs", "list") == "mcp__fs__list"


def test_mcp_client_list_and_call():
    client = make_client()
    client.start()
    try:
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"

        res = client.call_tool("echo", {"text": "你好"})
        assert res["content"][0]["text"] == "echo: 你好"
    finally:
        client.close()


def test_mcp_provider_loads_tools():
    client = make_client()
    provider = MCPToolProvider([client])
    tools = provider.load()
    try:
        assert len(tools) == 1
        assert tools[0].name == "mcp__fake__echo"
        # 执行
        result = tools[0].run(text="测试")
        assert result == "echo: 测试"
    finally:
        provider.close()


def test_mcp_provider_schema():
    client = make_client()
    provider = MCPToolProvider([client])
    tools = provider.load()
    try:
        schema = tools[0].schema
        assert schema["function"]["name"] == "mcp__fake__echo"
        assert "text" in schema["function"]["parameters"]["properties"]
    finally:
        provider.close()
