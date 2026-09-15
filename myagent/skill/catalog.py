"""Skill 目录发现与摘要（对应 Suna internal/skill/catalog.go）。

三套来源：
  - global：Runtime/Manager 扫描的本 agent skills 根目录
  - project：从 cwd 向上找仓库内 `.agents/skills` 等约定目录
  - user：启动时按 name 去重，把主目录里还没有的 Skill 拷进本 agent skills/

拷贝后按全局 Skill 使用；有同步标记则下次启动不再扫其它 agent。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .metadata import read_skill_metadata

SCOPE_GLOBAL = "global"
SCOPE_PROJECT = "project"
SCOPE_USER = "user"

# 项目 Skill 根目录的优先级：同层只取第一个命中的。
# .agents 是跨工具共享约定；.codex / .pi 是 Codex、Pi 的仓库级目录。
PROJECT_SKILL_ROOTS = [
    os.path.join(".agents", "skills"),
    os.path.join(".codex", "skills"),
    os.path.join(".pi", "skills"),
    os.path.join(".claude", "skills"),
    os.path.join(".github", "skills"),
    os.path.join(".gemini", "skills"),
    os.path.join(".cursor", "skills"),
    os.path.join(".opencode", "skills"),
]

# 用户主目录下其它 agent 的技能根。全部扫描，不做同层互斥，方便复用。
USER_SKILL_ROOTS = [
    os.path.join(".codex", "skills"),
    os.path.join(".pi", "agent", "skills"),
    os.path.join(".pi", "skills"),
    os.path.join(".claude", "skills"),
    os.path.join(".agents", "skills"),
    os.path.join(".cursor", "skills"),
    os.path.join(".gemini", "skills"),
    os.path.join(".opencode", "skills"),
    os.path.join(".grok", "skills"),
]

# 拷进本 agent skills/ 后留下的标记，有它就不再每次扫描主目录。
USER_SYNC_STAMP = ".imported-user-skills.json"


@dataclass
class Descriptor:
    """给模型看的短描述 + 加载时需要的定位信息。"""

    name: str
    description: str = ""
    scope: str = SCOPE_GLOBAL
    path: str = ""
    valid: bool = True
    error: str = ""


class Catalog:
    """一次会话里冻结的 project Skill 清单（对应 Suna 的 session catalog）。

    发现一次后不再扫盘，避免同一轮对话里目录变化让模型看到漂移的列表。
    """

    def __init__(self, items: Optional[List[Descriptor]] = None):
        self.items: List[Descriptor] = list(items or [])
        self.items.sort(key=lambda d: (d.name, d.path))

    def descriptors(self) -> List[Descriptor]:
        return list(self.items)

    def load_project(self, name: str, exact_path: str) -> Tuple[Descriptor, str]:
        """按 name + 发现时的精确路径加载 project Skill 全文。"""
        return self.load_named(name, exact_path, SCOPE_PROJECT)

    def load_user(self, name: str, exact_path: str) -> Tuple[Descriptor, str]:
        """按 name + 发现时的精确路径加载用户主目录里的外部 Skill 全文。"""
        return self.load_named(name, exact_path, SCOPE_USER)

    def load_named(self, name: str, exact_path: str, scope: str) -> Tuple[Descriptor, str]:
        """路径对不上 → 拒绝。加载前再次校验目录/SKILL.md 不是符号链接。"""
        name = (name or "").strip()
        exact_path = (exact_path or "").strip()
        if not name or not exact_path:
            raise ValueError(f"{scope} skill name and exact path are required")
        for item in self.items:
            if item.scope != scope or item.name != name or item.path != exact_path:
                continue
            if not item.valid:
                raise ValueError(f'{scope} skill "{name}" is invalid: {item.error}')
            skill_path = os.path.join(item.path, "SKILL.md")
            err = validate_project_skill_path(item.path, skill_path)
            if err:
                raise ValueError(f'{scope} skill "{name}" is no longer safe to load: {err}')
            data = _read_project_skill_file(item.path)
            return item, data
        raise ValueError(
            f'{scope} skill "{name}" with path "{exact_path}" is not in this session catalog'
        )


def discover_project(cwd: str) -> Catalog:
    """从 cwd 发现 project Skill。

    有 git 根：从 cwd 走到 git 根，每一层找约定目录。
    无 git 根：只扫 cwd，避免把父目录的 Skill 泄漏进无关项目。
    """
    cwd = _canonical_existing_dir(cwd)
    if not cwd:
        return Catalog([])
    git_root = _nearest_git_root(cwd)
    levels = _ancestors_to(cwd, git_root) if git_root else [cwd]
    items: List[Descriptor] = []
    for level in levels:
        for rel in PROJECT_SKILL_ROOTS:
            root = os.path.join(level, rel)
            if not _has_direct_skill(root):
                continue
            items.extend(_scan_skill_root(root, SCOPE_PROJECT))
            break
    return Catalog(items)


def discover_user(home: str = "") -> Catalog:
    """扫描用户主目录下其它 agent 的技能。同名按 USER_SKILL_ROOTS 顺序只保留先看到的。"""
    home = _canonical_existing_dir(home or os.path.expanduser("~"))
    if not home:
        return Catalog([])
    items: List[Descriptor] = []
    for rel in USER_SKILL_ROOTS:
        root = os.path.join(home, rel)
        if not _has_direct_skill(root):
            continue
        items.extend(_scan_skill_root(root, SCOPE_USER))
    return Catalog(_unique_by_name(items))


def user_skills_synced(dest_root: str) -> bool:
    """本 agent skills 根目录是否已经做过主目录导入。"""
    return os.path.isfile(os.path.join(dest_root, USER_SYNC_STAMP))


def sync_user_skills(dest_root: str, home: str = "") -> List[str]:
    """把主目录里尚未拥有的 Skill 按 name 拷进 dest_root。

    同名只拷一份（Codex 优先于 Pi / Grok）。已存在的目录不覆盖。
    返回本次新拷贝的 name 列表。
    """
    dest_root = (dest_root or "").strip()
    if not dest_root:
        raise ValueError("skill dest root is empty")
    os.makedirs(dest_root, exist_ok=True)
    have = _existing_skill_names(dest_root)
    copied: List[str] = []
    for item in discover_user(home).descriptors():
        if not item.valid or item.name in have:
            continue
        dest = os.path.join(dest_root, item.name)
        if os.path.exists(dest):
            have.add(item.name)
            continue
        try:
            shutil.copytree(
                item.path,
                dest,
                symlinks=False,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
        except OSError:
            continue
        have.add(item.name)
        copied.append(item.name)
    _write_sync_stamp(dest_root, sorted(have))
    return copied


def _unique_by_name(items: List[Descriptor]) -> List[Descriptor]:
    """同名只留第一条，配合 USER_SKILL_ROOTS 的优先级。"""
    seen = set()
    out = []
    for item in items:
        if item.name in seen:
            continue
        seen.add(item.name)
        out.append(item)
    return out


def _existing_skill_names(dest_root: str) -> set:
    names = set()
    try:
        entries = os.listdir(dest_root)
    except OSError:
        return names
    for name in entries:
        if name.startswith("."):
            continue
        directory = os.path.join(dest_root, name)
        if not os.path.isdir(directory):
            continue
        desc = read_descriptor(directory, name, SCOPE_GLOBAL)
        names.add(desc.name if desc.valid else name)
    return names


def _write_sync_stamp(dest_root: str, names: List[str]) -> None:
    path = os.path.join(dest_root, USER_SYNC_STAMP)
    payload = {"names": names}
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def render_summary(
    global_items: List[Descriptor],
    project_items: List[Descriptor],
    global_root: str,
    user_items: Optional[List[Descriptor]] = None,
) -> str:
    """拼进 system prompt 的 Available Skills 摘要。只含短描述，不含正文。"""
    sections = []
    global_lines = []
    for item in global_items:
        if not item.valid:
            continue
        desc = item.description.strip() or "No description provided."
        global_lines.append(f"- {item.name}: {desc}")
    if global_root.strip():
        body = f"Global Skills root: `{global_root}`"
        body += "\n" + "\n".join(global_lines) if global_lines else "\n- No enabled global Skills."
        sections.append(body)
    project_lines = []
    for item in project_items:
        if not item.valid:
            continue
        desc = item.description.strip() or "No description provided."
        project_lines.append(f"- {item.name} (path: `{item.path}`): {desc}")
    if project_lines:
        sections.append(
            "Project Skills (session catalog; use the exact path with scope=project):\n"
            + "\n".join(project_lines)
        )
    user_lines = []
    for item in user_items or []:
        if not item.valid:
            continue
        desc = item.description.strip() or "No description provided."
        user_lines.append(f"- {item.name} (path: `{item.path}`): {desc}")
    if user_lines:
        sections.append(
            "User Skills (from ~/.codex/skills, ~/.pi/agent/skills, etc; "
            "use the exact path with scope=user):\n"
            + "\n".join(user_lines)
        )
    if not sections:
        return ""
    return "## Available Skills\n" + "\n\n".join(sections)


def read_descriptor(directory: str, fallback: str, scope: str) -> Descriptor:
    """读一个 Skill 目录的短描述。name 非法或 SKILL.md 读失败 → valid=False。"""
    from .manager import valid_name

    try:
        name, description = read_skill_metadata(os.path.join(directory, "SKILL.md"))
    except (OSError, ValueError) as exc:
        return Descriptor(name=fallback, scope=scope, path=directory, valid=False, error=str(exc))
    if not name:
        name = fallback
    if not valid_name(name):
        return Descriptor(
            name=name, description=description, scope=scope, path=directory,
            valid=False, error="invalid skill name",
        )
    return Descriptor(name=name, description=description, scope=scope, path=directory, valid=True)


def validate_project_root(root: str) -> Optional[str]:
    """project Skill 根及其父目录都必须是非符号链接目录。"""
    err = _require_plain_dir(os.path.dirname(root))
    if err:
        return err
    return _require_plain_dir(root)


def validate_project_skill_path(directory: str, skill_path: str) -> Optional[str]:
    """加载前再校验：目录树不是 symlink，SKILL.md 是普通文件。"""
    err = validate_project_root(os.path.dirname(directory))
    if err:
        return err
    err = _require_plain_dir(directory)
    if err:
        return err
    try:
        info = os.lstat(skill_path)
    except OSError as exc:
        return str(exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return "SKILL.md must be a regular non-symlink file"
    return None


def _scan_skill_root(root: str, scope: str) -> List[Descriptor]:
    if validate_project_root(root):
        return []
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    items = []
    for name in entries:
        if name.startswith("."):
            continue
        directory = os.path.join(root, name)
        try:
            info = os.lstat(directory)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        skill_path = os.path.join(directory, "SKILL.md")
        if validate_project_skill_path(directory, skill_path):
            continue
        items.append(read_descriptor(directory, name, scope))
    return items


def _has_direct_skill(root: str) -> bool:
    if validate_project_root(root):
        return False
    try:
        entries = os.listdir(root)
    except OSError:
        return False
    for name in entries:
        directory = os.path.join(root, name)
        try:
            info = os.lstat(directory)
        except OSError:
            continue
        if name.startswith("."):
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        if validate_project_skill_path(directory, os.path.join(directory, "SKILL.md")) is None:
            return True
    return False


def _nearest_git_root(cwd: str) -> str:
    directory = cwd
    while True:
        git_path = os.path.join(directory, ".git")
        try:
            info = os.lstat(git_path)
        except OSError:
            info = None
        if info is not None and not stat.S_ISLNK(info.st_mode):
            return directory
        parent = os.path.dirname(directory)
        if parent == directory:
            return ""
        directory = parent


def _ancestors_to(cwd: str, root: str) -> List[str]:
    out = []
    directory = cwd
    while True:
        out.append(directory)
        if directory == root:
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return out


def _require_plain_dir(path: str) -> Optional[str]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        return str(exc)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return f"{path} must be a non-symlink directory"
    return None


def _read_project_skill_file(directory: str) -> str:
    """读已发现目录里当前磁盘上的 SKILL.md 全文。"""
    path = os.path.join(directory, "SKILL.md")
    with open(path, "r", encoding="utf-8") as file:
        return file.read()


def _canonical_existing_dir(path: str) -> str:
    path = (path or "").strip()
    if not path:
        return ""
    try:
        real = os.path.realpath(os.path.abspath(path))
    except OSError:
        return ""
    if not os.path.isdir(real):
        return ""
    return os.path.normpath(real)
