"""工具"执行路由"。

这里的职责是：把所有内置工具收集起来，并维护两份映射：
  - schemas：给模型（它只需要"声明"）
  - funcs  ：我们执行（按名字找到对应函数来调用）

对应 Suna 的 internal/tools + internal/tools/builtin。
"""
from .base import Tool


def _read_file(path: str) -> str:
    """读取一个文本文件并返回其内容。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except IsADirectoryError:
        return f"这是一个目录: {path}"


def _list_dir(path: str) -> str:
    """列出一个目录下的内容，让模型可以"看"项目结构。"""
    import os
    try:
        return "\n".join(sorted(os.listdir(path)))
    except FileNotFoundError:
        return f"目录不存在: {path}"
    except Exception as exc:
        return f"列出目录失败: {exc}"


def _run_command(command: str) -> str:
    """在本地执行一条 shell 命令并返回输出。

    注意：这个工具是"命令执行能力"的入口，也是最危险的权力来源。
    真实工程里（参考 Suna 的 Guard）必须对它做安全审查 / 用户确认。
    这里只做最基础的超时保护，安全话题见 docs/架构与选型.md。
    """
    import subprocess
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=30,          # 防止命令跑死
    )
    output = (result.stdout or "") 
    if result.stderr:
        output += f"\n[stderr]\n{result.stderr}"
    if result.returncode != 0:
        output += f"\n[exit code] {result.returncode}"
    return output


def _write_file(path: str, content: str, mode: str = "overwrite", create_dirs: bool = False) -> str:
    """写文件（参考 Suna 的 WriteFile）。

    mode:
      - overwrite  覆盖（默认）
      - create_new 仅新建（已存在则失败）
      - append     追加
    """
    import os
    if mode not in ("overwrite", "create_new", "append"):
        return f"mode 必须是 overwrite/create_new/append: {mode}"
    if create_dirs:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        if mode == "create_new" and os.path.exists(path):
            return f"文件已存在，create_new 模式拒绝覆盖: {path}"
        flag = "a" if mode == "append" else "w"
        with open(path, flag, encoding="utf-8") as f:
            f.write(content)
        return f"已写入 {len(content)} 字符到 {path}"
    except Exception as exc:
        return f"写入失败: {exc}"


def _edit_file(path: str, edits: list) -> str:
    """编辑文件：一次或多次精确替换（参考 Suna 的 EditFile）。

    edits 是 [{old_string, new_string}] 列表，按顺序应用。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        return f"读取失败: {exc}"

    for i, edit in enumerate(edits):
        old = edit.get("old_string", "")
        new = edit.get("new_string", "")
        if not old:
            return f"第{i+1}个编辑缺少 old_string"
        if old not in content:
            return f"第{i+1}个编辑未找到匹配文本: {old[:50]}"
        content = content.replace(old, new, 1)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"已应用 {len(edits)} 个编辑到 {path}"
    except Exception as exc:
        return f"写入失败: {exc}"


def _filesystem(action: str, path: str, destination: str = "", recursive: bool = False, parents: bool = False, overwrite: bool = False) -> str:
    """文件系统操作（参考 Suna 的 FileSystem）。

    action: stat / mkdir / move / copy / remove
    """
    import os
    import shutil
    try:
        if action == "stat":
            if not os.path.exists(path):
                return f"路径不存在: {path}"
            kind = "dir" if os.path.isdir(path) else "file"
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            return f"{kind} {path} ({size} bytes)"
        if action == "mkdir":
            if parents:
                os.makedirs(path, exist_ok=True)
            else:
                os.mkdir(path)
            return f"已创建目录: {path}"
        if action == "move":
            if not destination:
                return "move 需要 destination"
            shutil.move(path, destination)
            return f"已移动 {path} -> {destination}"
        if action == "copy":
            if not destination:
                return "copy 需要 destination"
            if os.path.isdir(path):
                if not recursive:
                    return "复制目录需要 recursive=true"
                shutil.copytree(path, destination, dirs_exist_ok=overwrite)
            else:
                shutil.copy2(path, destination)
            return f"已复制 {path} -> {destination}"
        if action == "remove":
            if os.path.isdir(path) and not recursive:
                return "删除目录需要 recursive=true"
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return f"已删除: {path}"
        return f"未知 action: {action}"
    except Exception as exc:
        return f"文件系统操作失败: {exc}"


def _search(query: str, path: str = ".", max_results: int = 50) -> str:
    """在目录里搜索文件内容（参考 Suna 的 Search）。"""
    import os
    import re
    results = []
    # 跳过常见无关目录
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in skip]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if re.search(query, line, re.I):
                                results.append(f"{fpath}:{lineno}: {line.strip()[:100]}")
                                if len(results) >= max_results:
                                    return "\n".join(results) + f"\n... (达到 {max_results} 条上限)"
                except (OSError, UnicodeDecodeError):
                    continue
    except Exception as exc:
        return f"搜索失败: {exc}"
    return "\n".join(results) if results else f"未找到匹配: {query}"


def _read_image(source: str) -> str:
    """读取图片（参考 Suna 的 ReadImage）。

    返回图片路径/URL 的说明，供多模态模型使用。
    """
    import os
    if source.startswith(("http://", "https://")):
        return f"图片 URL: {source}"
    if os.path.exists(source):
        return f"图片路径: {source} ({os.path.getsize(source)} bytes)"
    return f"图片不存在: {source}"


def _http(url: str, method: str = "GET", headers: dict = None, body: str = "", timeout: int = 30) -> str:
    """发送 HTTP 请求（参考 Suna 的 HTTP）。"""
    import urllib.request
    import urllib.error
    method = method.upper()
    try:
        req = urllib.request.Request(url, method=method, headers=headers or {})
        if body and method in ("POST", "PUT", "PATCH"):
            req.data = body.encode("utf-8")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            resp_headers = dict(resp.headers)
            content = resp.read(100 * 1024).decode("utf-8", errors="ignore")
        return f"HTTP {status}\n{content}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        return f"HTTP 请求失败: {exc}"


# 内置工具清单：每项 = (名字, 描述, 参数名列表, 必填参数, 执行函数)
# 注意：下面 _REGISTRY 里放的是一个个 Tool 对象（不是模块）。
_REGISTRY = [
    Tool.make(
        name="read_file",
        description="读取指定路径的文本文件内容。当需要查看某个文件的代码或配置时使用。",
        param_names=["path"],
        required=["path"],
    ).bind(_read_file),
    Tool.make(
        name="list_dir",
        description="列出某个目录下的条目的名字。当需要了解项目结构时使用。",
        param_names=["path"],
        required=["path"],
    ).bind(_list_dir),
    Tool.make(
        name="run_command",
        description="在本地执行一条 shell 命令并返回其输出。当需要运行程序或查看系统信息时使用。",
        param_names=["command"],
        required=["command"],
    ).bind(_run_command),
    Tool.make(
        name="write_file",
        description="写文件：创建、覆盖或追加内容。mode 可选 overwrite/create_new/append，create_dirs 自动建父目录。",
        param_names=["path", "content", "mode", "create_dirs"],
        required=["path", "content"],
    ).bind(_write_file),
    Tool.make(
        name="edit_file",
        description="编辑文件：一次或多次精确替换。edits 是 [{old_string, new_string}] 列表，按顺序应用。",
        param_names=["path", "edits"],
        required=["path", "edits"],
    ).bind(_edit_file),
    Tool.make(
        name="filesystem",
        description="文件系统操作：stat/mkdir/move/copy/remove。move/copy 需要 destination，删除目录需要 recursive。",
        param_names=["action", "path", "destination", "recursive", "parents", "overwrite"],
        required=["action", "path"],
    ).bind(_filesystem),
    Tool.make(
        name="search",
        description="在目录里搜索文件内容，返回匹配的行。query 是正则，path 是搜索目录。",
        param_names=["query", "path", "max_results"],
        required=["query"],
    ).bind(_search),
    Tool.make(
        name="read_image",
        description="读取图片，供多模态模型使用。source 是本地路径或 http(s) URL。",
        param_names=["source"],
        required=["source"],
    ).bind(_read_image),
    Tool.make(
        name="http",
        description="发送 HTTP 请求并返回状态码和响应体。method 默认 GET，可传 headers/body。",
        param_names=["url", "method", "headers", "body", "timeout"],
        required=["url"],
    ).bind(_http),
]

# 对外只暴露这两个列表，别的模块不需要知道"内置工具"这个细节
# TOOLS 是 Tool 对象的列表；SCHEMAS 是把每个 Tool 的 .schema（一个 dict）提出来组成新列表。
# 这里的 tool 变量 = TOOLS 里的元素（一个 Tool 对象），不是 tools 包。
TOOLS: list = _REGISTRY
SCHEMAS: list = [tool.schema for tool in TOOLS]


def tool_for(name: str):
    """按名字找到可执行工具，找不到返回 None（让 runner 优雅处理）。"""
    for tool in TOOLS:
        if tool.name == name:
            return tool
    return None
