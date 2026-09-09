"""内置工具单元测试。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.tools import TOOLS, tool_for


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
    assert "HTTP 200" in result
