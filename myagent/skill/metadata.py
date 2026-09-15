"""SKILL.md 元数据读取（对应 Suna internal/skill/metadata.go）。

只解析受限 frontmatter（name / description），正文留到 skill_load 时再读。
这样 Manager.Reload 扫描目录时不会把大文件整篇塞进内存。
"""
from __future__ import annotations

import os
import stat
from typing import Tuple

# 索引阶段最多读这么多字节；超出则视为 frontmatter 非法或丢掉半截行。
MAX_SKILL_FRONTMATTER_BYTES = 64 * 1024


def read_skill_metadata(path: str) -> Tuple[str, str]:
    """读取 SKILL.md 的 name 和 description，不读完整正文。

    优先 YAML frontmatter（首行 `---`）；没有 frontmatter 时回退到 H1 + 首段。
    失败时抛 ValueError，由上层把该 Skill 标为 invalid。
    """
    try:
        info = os.stat(path)
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("SKILL.md must be a regular file")

    with open(path, "rb") as file:
        raw = file.read(MAX_SKILL_FRONTMATTER_BYTES + 1)
    text = raw.decode("utf-8", errors="replace")
    first, sep, rest = text.partition("\n")
    if not sep and not rest:
        # 单行文件，没有换行。
        rest = ""
    if _trim_line_ending(first) != "---":
        content = first + (("\n" + rest) if sep else rest)
        if len(content) > MAX_SKILL_FRONTMATTER_BYTES:
            content = content[:MAX_SKILL_FRONTMATTER_BYTES]
            idx = content.rfind("\n")
            content = content[: idx + 1] if idx >= 0 else ""
        return extract_h1(content), extract_description(content)

    frontmatter_lines = []
    read_bytes = len(first) + (1 if sep else 0)
    for line in rest.splitlines(keepends=True):
        read_bytes += len(line)
        if read_bytes > MAX_SKILL_FRONTMATTER_BYTES:
            raise ValueError(f"SKILL.md frontmatter exceeds {MAX_SKILL_FRONTMATTER_BYTES} bytes")
        if _trim_line_ending(line) == "---":
            break
        frontmatter_lines.append(line)
    else:
        raise ValueError("SKILL.md frontmatter is not terminated")

    meta = _parse_yaml_mapping("".join(frontmatter_lines))
    return meta.get("name", "").strip(), meta.get("description", "").strip()


def extract_h1(body: str) -> str:
    """从正文里取第一个 `# 标题`，作为无 frontmatter 时的 Skill 名。"""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def extract_description(body: str) -> str:
    """H1 之后第一段非空、非标题行，最长 240 字符。"""
    seen_h1 = False
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("# "):
            seen_h1 = True
            continue
        if seen_h1 and line and not line.startswith("#"):
            return line[:240]
    return ""


def _trim_line_ending(line: str) -> str:
    return line.rstrip("\r\n")


def _parse_yaml_mapping(text: str) -> dict:
    """解析 SKILL.md frontmatter 用的受限 YAML 映射。

    只需要 name / description；其它键（如 metadata）忽略。
    支持 `key: value`、`key: >` 折叠标量和 `key: |` 字面标量。
    不引入 PyYAML，保持项目依赖极简。
    """
    result = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if line[:1] in " \t":
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest in (">", "|"):
            folded = rest == ">"
            block = []
            i += 1
            while i < len(lines) and (lines[i][:1] in " \t" or lines[i].strip() == ""):
                if lines[i][:1] in " \t":
                    block.append(lines[i].lstrip())
                    i += 1
                    continue
                if lines[i].strip() == "":
                    i += 1
                    continue
                break
            result[key] = " ".join(block) if folded else "\n".join(block)
            continue
        if rest in ("", "{}", "[]"):
            i += 1
            while i < len(lines) and lines[i][:1] in " \t":
                i += 1
            continue
        if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in "\"'":
            rest = rest[1:-1]
        result[key] = rest
        i += 1
    return result
