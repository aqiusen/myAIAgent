"""内置工具单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.tools import TOOLS, tool_for


def test_runner_forces_wrapup_after_max_iterations():
    from myagent.config import Config
    from myagent.model_registry import ModelConfig
    from myagent.runner import MAX_ITERATIONS, Runner
    from myagent.tools.base import Tool

    class FakeProvider:
        def __init__(self):
            self.with_tools = 0
            self.without_tools = 0

        def complete(self, messages, tools, temperature, max_tokens, stream, on_delta=None):
            if tools:
                self.with_tools += 1
                return {
                    "content": "",
                    "tool_calls": [{
                        "id": f"c{self.with_tools}",
                        "type": "function",
                        "function": {"name": "noop", "arguments": "{}"},
                    }],
                }
            self.without_tools += 1
            return {"content": "wrap-up answer", "tool_calls": []}

    config = Config(
        model="m", base_url="http://x", api_key="k",
        models=[ModelConfig(ref="default", model="m", base_url="http://x", api_key="k")],
    )
    fake = FakeProvider()
    runner = Runner(config, provider=fake)
    runner.tools_list = [
        Tool(name="noop", description="n", parameters={"type": "object", "properties": {}}, fn=lambda: "ok"),
    ]
    out = runner.run([{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "noop"}}])
    assert out == "wrap-up answer"
    assert fake.with_tools == MAX_ITERATIONS
    assert fake.without_tools == 1


def test_tool_count():
    assert len(TOOLS) == 9


def test_all_tools_registered():
    names = {t.name for t in TOOLS}
    expected = {
        "read_file", "list_dir", "run_command",
        "write_file", "edit_file", "filesystem",
        "search", "read_image", "http",
    }
    assert names == expected


def test_write_and_read_file(tmp_path):
    f = str(tmp_path / "test.txt")
    assert "已写入" in tool_for("write_file").run(path=f, content="hello")
    assert tool_for("read_file").run(path=f) == "hello"


def test_write_append(tmp_path):
    f = str(tmp_path / "test.txt")
    tool_for("write_file").run(path=f, content="line1")
    tool_for("write_file").run(path=f, content="\nline2", mode="append")
    assert tool_for("read_file").run(path=f) == "line1\nline2"


def test_write_create_new_rejects_existing(tmp_path):
    f = str(tmp_path / "test.txt")
    tool_for("write_file").run(path=f, content="x")
    result = tool_for("write_file").run(path=f, content="y", mode="create_new")
    assert "已存在" in result


def test_edit_file(tmp_path):
    f = str(tmp_path / "test.txt")
    tool_for("write_file").run(path=f, content="hello world")
    result = tool_for("edit_file").run(
        path=f, edits=[{"old_string": "hello", "new_string": "HELLO"}]
    )
    assert "已应用" in result
    assert tool_for("read_file").run(path=f) == "HELLO world"


def test_edit_file_not_found(tmp_path):
    f = str(tmp_path / "test.txt")
    tool_for("write_file").run(path=f, content="hello")
    result = tool_for("edit_file").run(
        path=f, edits=[{"old_string": "nonexistent", "new_string": "x"}]
    )
    assert "未找到" in result


def test_filesystem_stat(tmp_path):
    f = str(tmp_path / "test.txt")
    tool_for("write_file").run(path=f, content="hello")
    result = tool_for("filesystem").run(action="stat", path=f)
    assert "file" in result


def test_filesystem_mkdir_move_remove(tmp_path):
    src = str(tmp_path / "src.txt")
    dst = str(tmp_path / "dst.txt")
    tool_for("write_file").run(path=src, content="x")
    assert "已移动" in tool_for("filesystem").run(action="move", path=src, destination=dst)
    assert "已删除" in tool_for("filesystem").run(action="remove", path=dst)


def test_search(tmp_path):
    f = str(tmp_path / "test.txt")
    tool_for("write_file").run(path=f, content="hello world\nfoo bar")
    result = tool_for("search").run(query="hello", path=str(tmp_path))
    assert "hello" in result


def test_read_image(tmp_path):
    f = str(tmp_path / "img.png")
    tool_for("write_file").run(path=f, content="fake")
    result = tool_for("read_image").run(source=f)
    assert "图片路径" in result


def test_http():
    result = tool_for("http").run(url="https://httpbin.org/get")
    assert "Status: 200" in result


def test_timeout_schema_is_integer():
    http_schema = tool_for("http").schema["function"]["parameters"]["properties"]
    cmd_schema = tool_for("run_command").schema["function"]["parameters"]["properties"]
    assert http_schema["timeout"]["type"] == "integer"
    assert cmd_schema["timeout"]["type"] == "integer"


def test_parse_timeout_seconds_and_milliseconds():
    from myagent.tools.builtin import _parse_timeout
    seconds, err = _parse_timeout("8000", default=30, max_s=60)
    assert err == "" and seconds == 8
    seconds, err = _parse_timeout(20, default=30, max_s=60)
    assert err == "" and seconds == 20
    _, err = _parse_timeout("bad", default=30, max_s=60)
    assert "positive integer" in err


def test_run_command_prefers_dedicated_tools():
    desc = tool_for("run_command").schema["function"]["description"]
    assert "Prefer dedicated file, search, and HTTP tools" in desc


def test_run_command_timeout_returns_message():
    result = tool_for("run_command").run(command="sleep 5", timeout=1)
    assert "timed out" in result
