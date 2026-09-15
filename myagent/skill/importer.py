"""Skill 导入（对应 Suna internal/skill/import.go）。

支持三种来源：本地目录、zip、git/http/ssh URL。
导入后默认不启用，必须走 skill_start 的 check + 用户确认。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlparse

from .manager import CheckResult, valid_name
from .metadata import read_skill_metadata

if TYPE_CHECKING:
    from .runtime import Runtime

MAX_IMPORT_ZIP_FILES = 256
MAX_IMPORT_ZIP_TOTAL_BYTES = 32 * 1024 * 1024
MAX_IMPORT_ZIP_FILE_BYTES = 8 * 1024 * 1024


@dataclass
class ImportResult:
    """导入结果：目标目录 + 静态检查。Enabled 此时仍为 false。"""

    name: str
    path: str
    check: CheckResult


def import_source(runtime: "Runtime", source: str, name: str = "") -> ImportResult:
    """把 source 装进 runtime.root/<name>。调用方须已持有 runtime 锁。"""
    source = (source or "").strip()
    name = (name or "").strip()
    if not source:
        raise ValueError("source is required")
    if _is_remote_source(source):
        return _import_git(runtime, source, name)
    if os.path.splitext(source)[1].lower() == ".zip":
        return _import_zip(runtime, source, name)
    return import_local(runtime, source, name)


def import_local(runtime: "Runtime", source: str, name: str) -> ImportResult:
    """从本地 Skill 目录复制到全局 root。"""
    abs_source = os.path.abspath(source)
    if not os.path.isdir(abs_source):
        raise ValueError("source must be a skill directory")
    parsed_name, _ = read_skill_metadata(os.path.join(abs_source, "SKILL.md"))
    if not name:
        name = parsed_name or os.path.basename(abs_source)
    if not valid_name(name):
        raise ValueError("invalid skill name")
    if parsed_name and parsed_name != name:
        raise ValueError(f'SKILL.md name "{parsed_name}" does not match target name "{name}"')
    dest = os.path.join(runtime.root, name)
    _ensure_safe_import_paths(abs_source, dest)
    _replace_dir(abs_source, dest)
    runtime._reload_locked()
    check = runtime.manager.check(name)
    runtime._save_workflow_check_locked(name, False, check)
    return ImportResult(name=name, path=dest, check=check)


def _import_git(runtime: "Runtime", source: str, name: str) -> ImportResult:
    """浅 clone 后按本地目录导入。是否启用仍要用户确认。"""
    tmp = tempfile.mkdtemp(prefix="myagent-skill-")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", source, tmp],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            msg = (proc.stdout or "") + (proc.stderr or "")
            raise ValueError("git clone failed: " + msg.strip())
        return import_local(runtime, tmp, name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _import_zip(runtime: "Runtime", source: str, name: str) -> ImportResult:
    tmp = tempfile.mkdtemp(prefix="myagent-skill-zip-")
    try:
        _unzip(source, tmp)
        root = tmp
        if not os.path.exists(os.path.join(root, "SKILL.md")):
            for entry in os.listdir(tmp):
                candidate = os.path.join(tmp, entry)
                if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "SKILL.md")):
                    root = candidate
                    break
        return import_local(runtime, root, name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _is_remote_source(source: str) -> bool:
    if source.startswith("git@"):
        return True
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https", "ssh")


def _ensure_safe_import_paths(src: str, dst: str) -> None:
    abs_src = os.path.normpath(os.path.abspath(src))
    abs_dst = os.path.normpath(os.path.abspath(dst))
    if abs_src == abs_dst:
        raise ValueError("source is already installed at destination")
    if _path_contains(abs_src, abs_dst) or _path_contains(abs_dst, abs_src):
        raise ValueError("source and destination directories must not contain each other")


def _path_contains(parent: str, child: str) -> bool:
    try:
        rel = os.path.relpath(child, parent)
    except ValueError:
        return False
    if rel == ".":
        return False
    return rel != ".." and not rel.startswith(".." + os.sep)


def _replace_dir(src: str, dst: str) -> None:
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(src):
        if ".git" in dirnames:
            dirnames.remove(".git")
        rel = os.path.relpath(dirpath, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for filename in filenames:
            src_file = os.path.join(dirpath, filename)
            if not os.path.isfile(src_file):
                continue
            shutil.copy2(src_file, os.path.join(target_dir, filename))


def _unzip(src: str, dst: str) -> None:
    files = 0
    total = 0
    with zipfile.ZipFile(src) as zf:
        for info in zf.infolist():
            clean = os.path.normpath(info.filename)
            if clean.startswith("..") or os.path.isabs(clean):
                raise ValueError(f"zip contains unsafe path: {info.filename}")
            target = os.path.join(dst, clean)
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            files += 1
            if files > MAX_IMPORT_ZIP_FILES:
                raise ValueError(f"zip contains too many files (max {MAX_IMPORT_ZIP_FILES})")
            if info.file_size > MAX_IMPORT_ZIP_FILE_BYTES:
                raise ValueError(
                    f"zip file {info.filename} is too large (max {MAX_IMPORT_ZIP_FILE_BYTES} bytes)"
                )
            total += info.file_size
            if total > MAX_IMPORT_ZIP_TOTAL_BYTES:
                raise ValueError(f"zip content is too large (max {MAX_IMPORT_ZIP_TOTAL_BYTES} bytes)")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src_file:
                data = src_file.read(MAX_IMPORT_ZIP_FILE_BYTES + 1)
            if len(data) > MAX_IMPORT_ZIP_FILE_BYTES:
                raise ValueError(f"zip file {info.filename} exceeds read limit")
            with open(target, "wb") as out:
                out.write(data)
