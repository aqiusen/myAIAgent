"""Skill LLM 审查的文件收集（对应 Suna internal/skill/review.go）。

把 Skill 目录里的文本文件挑一批送给审查器：SKILL.md 优先，然后 scripts/、references/。
有上限，避免把整个仓库塞进审查 prompt。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from .manager import MAX_CHECK_FILE_BYTES, MAX_CHECK_FILES, SKIPPED_SKILL_DIRS, _looks_binary

MAX_REVIEW_FILES = 12
MAX_REVIEW_FILE_CHARS = 6000
MAX_REVIEW_TOTAL = 24000


@dataclass
class ReviewFile:
    """送给 LLM 审查器的一个文件切片。"""

    path: str
    content: str
    truncated: bool = False


@dataclass
class LLMReviewRequest:
    """一次 Skill 审查请求。"""

    name: str
    description: str = ""
    reasons: List[str] = field(default_factory=list)
    files: List[ReviewFile] = field(default_factory=list)


@dataclass
class LLMReviewResult:
    """审查结果。needs_attention 表示静态检查已经标了 reasons。"""

    name: str
    valid: bool = False
    static_reasons: List[str] = field(default_factory=list)
    review: str = ""
    needs_attention: bool = False
    error: str = ""


def collect_review_files(directory: str) -> List[ReviewFile]:
    """按优先级收集审查文件，截断超长内容。"""
    paths: List[str] = []
    visited = 0
    limited = False
    for dirpath, dirnames, filenames in os.walk(directory):
        keep = []
        for dirname in dirnames:
            if dirpath != directory and dirname.lower() in SKIPPED_SKILL_DIRS:
                continue
            keep.append(dirname)
        dirnames[:] = keep
        for filename in filenames:
            if visited >= MAX_CHECK_FILES:
                limited = True
                continue
            path = os.path.join(dirpath, filename)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            visited += 1
            if size > MAX_CHECK_FILE_BYTES:
                continue
            paths.append(path)

    paths.sort(key=lambda p: (_review_file_rank(p, directory), p))
    files: List[ReviewFile] = []
    total = 0
    for path in paths:
        if len(files) >= MAX_REVIEW_FILES or total >= MAX_REVIEW_TOTAL:
            limited = True
            break
        try:
            with open(path, "rb") as file:
                data = file.read()
        except OSError:
            continue
        if _looks_binary(data):
            continue
        rel = os.path.relpath(path, directory).replace("\\", "/")
        text = data.decode("utf-8", errors="replace")
        truncated = False
        if len(text) > MAX_REVIEW_FILE_CHARS:
            text = text[:MAX_REVIEW_FILE_CHARS]
            truncated = True
        remaining = MAX_REVIEW_TOTAL - total
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        total += len(text)
        files.append(ReviewFile(path=rel, content=text, truncated=truncated))
    if limited:
        files.append(ReviewFile(
            path="[scan-limit]",
            content="Some Skill files were skipped because the Skill is large.",
            truncated=True,
        ))
    return files


def _review_file_rank(path: str, root: str) -> int:
    rel = os.path.relpath(path, root).replace("\\", "/").lower()
    if rel == "skill.md" or rel.endswith("/skill.md"):
        return 0
    if "/scripts/" in f"/{rel}" or rel.startswith("scripts/"):
        return 1
    if "/references/" in f"/{rel}" or rel.startswith("references/"):
        return 2
    return 3
