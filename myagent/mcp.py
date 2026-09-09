"""MCP 客户端（参考 Suna internal/mcp/client.go）。

MCP（Model Context Protocol）是连接 AI agent 与外部工具的标准协议。
MCP 服务器通过 stdio 暴露工具，客户端用 JSON-RPC 2.0 通信。

对应 Suna 的 mcp.Client：
  - start()      启动服务器进程 + initialize 握手
  - list_tools() 获取可用工具（tools/list）
  - call_tool()  调用工具（tools/call）

协议流程：
  1. 启动服务器进程（stdio 传输）
  2. initialize 握手（协商协议版本）
  3. notifications/initialized 通知
  4. tools/list 获取工具列表
  5. tools/call 调用工具
"""
import json
import subprocess
import threading
from typing import Any, Dict, List, Optional


class MCPError(Exception):
    """MCP 协议错误。"""


class MCPClient:
    """一个 MCP 服务器连接（对应 Suna 的 mcp.Client）。"""

    def __init__(
        self,
        server_id: str,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        timeout: float = 30,
    ):
        self.server_id = server_id
        self.command = command
        self.args = args or []
        self.cwd = cwd
        self.env = env
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()
        self._pending: Dict[int, threading.Event] = {}  # id -> 等待事件
        self._responses: Dict[int, Dict] = {}           # id -> 响应
        self._reader: Optional[threading.Thread] = None
        self._closed = False

    # ---------- 生命周期 ----------
    def start(self) -> None:
        """启动服务器进程并完成 initialize 握手。"""
        self.proc = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            text=True,
        )
        # 后台线程读 stdout，按 id 分发响应
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # initialize 握手
        self._call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "myagent", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", None)

    def close(self) -> None:
        """关闭服务器进程。"""
        self._closed = True
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass

    # ---------- MCP 方法 ----------
    def list_tools(self) -> List[Dict]:
        """获取服务器提供的工具列表（tools/list）。"""
        res = self._call("tools/list", None)
        return res.get("tools", [])

    def call_tool(self, name: str, args: Dict[str, Any]) -> Dict:
        """调用工具（tools/call）。"""
        res = self._call("tools/call", {"name": name, "arguments": args})
        return res

    # ---------- JSON-RPC 底层 ----------
    def _call(self, method: str, params: Optional[Dict]) -> Dict:
        """发送请求并等待响应。"""
        with self._lock:
            self._id += 1
            msg_id = self._id
            msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
            if params is not None:
                msg["params"] = params
            event = threading.Event()
            self._pending[msg_id] = event
            self._write(msg)
        # 等待 reader 线程设置事件
        if not event.wait(self.timeout):
            with self._lock:
                self._pending.pop(msg_id, None)
            raise MCPError(f"MCP 请求超时: {method}")
        with self._lock:
            resp = self._responses.pop(msg_id, None)
        if resp is None:
            raise MCPError(f"MCP 无响应: {method}")
        if "error" in resp:
            raise MCPError(f"MCP 错误: {resp['error']}")
        return resp.get("result", {})

    def _notify(self, method: str, params: Optional[Dict]) -> None:
        """发送通知（不需要响应）。"""
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._write(msg)

    def _write(self, msg: Dict) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise MCPError("MCP 服务器未启动")
        data = json.dumps(msg) + "\n"
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _read_loop(self) -> None:
        """后台线程：读 stdout 行，按 id 分发响应。"""
        if self.proc is None or self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            if self._closed:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                with self._lock:
                    event = self._pending.get(msg["id"])
                    if event is not None:
                        self._responses[msg["id"]] = msg
                        event.set()
