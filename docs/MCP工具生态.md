# MCP 工具生态

> 参考 Suna 的 `internal/mcp`（client.go + protocol.go）和 `internal/tools/mcptools`（provider.go），
> 为 agent 接入 MCP（Model Context Protocol）外部工具。

---

## 一、什么是 MCP

MCP（Model Context Protocol）是连接 AI agent 与外部工具的标准协议。
MCP 服务器通过 stdio 暴露工具，客户端用 JSON-RPC 2.0 通信。

**价值**：接入 MCP 后，你的 agent 能用上百种现成工具（文件系统、数据库、浏览器、GitHub 等），
不用自己实现。

---

## 二、参考 Suna 的架构

| Suna 模块 | 职责 | 我们的实现 |
|-----------|------|-----------|
| `internal/mcp/client.go` | MCP 客户端 | `myagent/mcp.py` |
| `internal/mcp/protocol.go` | JSON-RPC 协议 | `myagent/mcp.py` |
| `internal/tools/mcptools/provider.go` | MCP 工具集成 | `myagent/tools/mcp_provider.py` |

---

## 三、MCP 客户端（mcp.py）

对应 Suna 的 mcp.Client。通过 stdio 启动服务器进程，用 JSON-RPC 2.0 通信。

### 协议流程
```
1. 启动服务器进程（stdio 传输）
2. initialize 握手（协商协议版本）
3. notifications/initialized 通知
4. tools/list 获取工具列表
5. tools/call 调用工具
```

### 核心方法
```python
client = MCPClient(server_id="fs", command="npx", args=[...])
client.start()          # 启动 + 握手
client.list_tools()     # 获取工具列表
client.call_tool(name, args)  # 调用工具
client.close()          # 关闭
```

---

## 四、MCP 工具集成（mcp_provider.py）

对应 Suna 的 mcptools.Provider。把 MCP 工具包装成我们系统的 Tool 对象。

### 命名约定（参考 Suna）
```
mcp__server__tool
例如 mcp__fs__read_file
```
避免不同服务器的同名工具冲突。

### 集成流程
```python
provider = MCPToolProvider([client1, client2])
tools = provider.load()   # 连接所有服务器，加载工具
# 工具自动合并进 agent 的工具列表
```

---

## 五、配置 MCP 服务器

```bash
MY_AGENT_MCP_SERVERS=[{"id":"fs","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]}]
```

---

## 六、代码结构

```
myagent/
├── mcp.py                    # MCP 客户端（JSON-RPC over stdio）
├── tools/mcp_provider.py     # MCP 工具提供器
├── agent.py                  # 加载 MCP 工具，合并进工具列表
├── runner.py                 # _dispatch 查找完整工具列表（内置 + MCP）
└── config.py                 # mcp_servers 配置
```

---

## 七、验证结果

| 测试 | 结果 |
|------|------|
| MCP 客户端 list_tools | ✅ |
| MCP 客户端 call_tool | ✅ |
| MCP 工具命名（mcp__server__tool） | ✅ |
| MCP 工具加载 + 执行 | ✅ |
| Agent 集成（工具列表含 MCP 工具） | ✅ |
| 60 个测试全部通过 | ✅ |

---

## 八、一句话总结

**MCP 工具生态 = MCP 客户端（JSON-RPC over stdio）+ 工具提供器（包装成 Tool 对象），
参考 Suna 的 mcp/client.go 和 mcptools/provider.go，让 agent 接入上百种外部工具。**
