"""全局 Skill 扫描与加载（对应 Suna internal/skill/skill.go 的 Manager）。

职责：
  - 扫描 skills 根目录下每个子目录的 SKILL.md
  - 索引阶段只读 name/description
  - Load 才读全文，且必须 enabled + valid
  - Check 做静态风险扫描（给 skill_start 用，不在启动时跑）
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .catalog import (
    SCOPE_GLOBAL,
    Descriptor,
    read_descriptor,
)

MAX_CHECK_FILES = 256
MAX_CHECK_FILE_BYTES = 512 * 1024
MAX_CHECK_TOTAL = 8 * 1024 * 1024

SKIPPED_SKILL_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".cache"}

# 静态检查命中这些片段就记一条 reason；不直接判 invalid。
_CHECK_PATTERNS = [
    ("rm -rf", "contains destructive delete commands"),
    ("sudo ", "contains privilege escalation commands"),
    ("curl ", "contains network access commands"),
    ("wget ", "contains network access commands"),
    ("github_token", "mentions sensitive environment variables or tokens"),
    ("api_key", "mentions sensitive environment variables or tokens"),
    ("/etc/", "mentions sensitive system paths"),
    ("ignore previous", "possible prompt injection instruction"),
    ("忽略之前", "possible prompt injection instruction"),
]


@dataclass
class Record:
    """config/records 里保存的轻量管理信息。enabled 是唯一生命周期状态。"""

    enabled: bool = False
    reasons: List[str] = field(default_factory=list)


@dataclass
class Info:
    """给 List / TUI 看的 Skill 状态。"""

    name: str
    description: str = ""
    scope: str = SCOPE_GLOBAL
    can_toggle: bool = False
    enabled: bool = False
    valid: bool = False
    reasons: List[str] = field(default_factory=list)
    path: str = ""
    error: str = ""


@dataclass
class CheckResult:
    """静态检查结果。Valid 表示 Skill 能被解析，不表示没有风险。"""

    name: str
    valid: bool = False
    reasons: List[str] = field(default_factory=list)
    description: str = ""
    error: str = ""


@dataclass
class Skill:
    """磁盘上一个 Skill 目录的索引条目。"""

    name: str
    description: str = ""
    directory: str = ""
    path: str = ""
    valid: bool = True
    error: str = ""
    reasons: List[str] = field(default_factory=list)


class Manager:
    """扫描 root 下的 Skill 目录，按 records 决定谁 enabled。"""

    def __init__(self, root: str, records: Optional[Dict[str, Record]] = None):
        self.root = root
        self.records: Dict[str, Record] = _clone_records(records)
        self.skills: Dict[str, Skill] = {}
        self.infos: List[Info] = []

    def set_records(self, records: Optional[Dict[str, Record]]) -> None:
        """只换记录，不重新扫盘（对应 Suna SetRecords）。"""
        self.records = _clone_records(records)

    def apply_records(self, records: Optional[Dict[str, Record]]) -> None:
        """换记录并重建 Info 列表，仍不重新读 SKILL.md。"""
        self.records = _clone_records(records)
        self.infos = []
        self._rebuild_infos()

    def records_copy(self) -> Dict[str, Record]:
        return _clone_records(self.records)

    def reload(self) -> None:
        """重新扫 root。root 为空则报错；不存在则创建。"""
        self.skills = {}
        self.infos = []
        if not (self.root or "").strip():
            raise ValueError("skill root is empty")
        os.makedirs(self.root, exist_ok=True)
        try:
            entries = os.listdir(self.root)
        except OSError as exc:
            raise ValueError(str(exc)) from exc
        for name in entries:
            directory = os.path.join(self.root, name)
            if not os.path.isdir(directory):
                continue
            skill = _read_skill_dir(directory, name)
            existing = self.skills.get(skill.name)
            if existing is not None:
                msg = f'duplicate skill name "{skill.name}"'
                existing.valid = False
                existing.error = msg
                skill.valid = False
                skill.error = msg
            self.skills[skill.name] = skill
        self._rebuild_infos()

    def list(self) -> List[Info]:
        out = list(self.infos)
        out.sort(key=lambda i: i.name)
        return out

    def summary(self) -> str:
        """只列出已启用且 valid 的短描述，供早期单测对照。"""
        lines = []
        for item in self.enabled_descriptors():
            desc = item.description.strip() or "No description provided."
            lines.append(f"- {item.name}: {desc}")
        return "\n".join(lines)

    def enabled_descriptors(self) -> List[Descriptor]:
        items = []
        for info in self.list():
            if not info.enabled or not info.valid:
                continue
            items.append(Descriptor(
                name=info.name, description=info.description,
                scope=SCOPE_GLOBAL, path=info.path, valid=True,
            ))
        return items

    def load(self, name: str) -> Tuple[str, bool, str]:
        """读 SKILL.md 全文。未找到 / invalid / 未启用都失败。"""
        name = (name or "").strip()
        skill = self.skills.get(name)
        if skill is None:
            return "", False, "skill not found"
        if not skill.valid:
            return "", False, "skill is invalid: " + skill.error
        record = self.records.get(name)
        if record is None or not record.enabled:
            return "", False, "skill is not enabled"
        try:
            with open(skill.path, "r", encoding="utf-8") as file:
                return file.read(), True, ""
        except OSError as exc:
            return "", False, "read SKILL.md: " + str(exc)

    def check(self, name: str) -> CheckResult:
        """静态扫描 Skill 目录，返回 reasons；不改变 enabled。"""
        name = (name or "").strip()
        skill = self.skills.get(name)
        if skill is None:
            return CheckResult(name=name, valid=False, error="skill not found")
        if not skill.valid:
            return CheckResult(name=skill.name, valid=False, error=skill.error)
        reasons = check_reasons(skill.directory)
        skill.reasons = list(reasons)
        return CheckResult(
            name=skill.name, valid=True, reasons=reasons, description=skill.description,
        )

    def info(self, name: str) -> Optional[Info]:
        name = (name or "").strip()
        for item in self.infos:
            if item.name == name:
                return item
        return None

    def _rebuild_infos(self) -> None:
        seen = set()
        for name, skill in self.skills.items():
            seen.add(name)
            record = self.records.get(name, Record())
            self.infos.append(Info(
                name=name,
                description=skill.description,
                scope=SCOPE_GLOBAL,
                can_toggle=skill.valid,
                enabled=record.enabled,
                valid=skill.valid,
                reasons=list(skill.reasons),
                path=skill.directory,
                error=skill.error,
            ))
        for name, record in self.records.items():
            if name in seen:
                continue
            self.infos.append(Info(
                name=name,
                scope=SCOPE_GLOBAL,
                can_toggle=False,
                enabled=record.enabled,
                valid=False,
                reasons=list(record.reasons),
                error="skill not found",
            ))


def valid_name(name: str) -> bool:
    """Skill 名：字母数字和 - _ . ，最长 80。"""
    name = (name or "").strip()
    if not name or len(name) > 80:
        return False
    return re.fullmatch(r"[A-Za-z0-9._-]+", name) is not None


def check_reasons(directory: str) -> List[str]:
    """走一遍 Skill 目录，收集风险提示。目录过大/不可读也记 reason。"""
    reasons = set()
    files = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        keep = []
        for dirname in dirnames:
            if dirpath != directory and dirname.lower() in SKIPPED_SKILL_DIRS:
                reasons.add("skipped generated or dependency directories during check")
                continue
            keep.append(dirname)
        dirnames[:] = keep
        for filename in filenames:
            if files >= MAX_CHECK_FILES or total >= MAX_CHECK_TOTAL:
                reasons.add("skipped some files because the Skill is large")
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, directory)
            if rel.replace("\\", "/").lower().startswith("scripts/"):
                reasons.add("includes scripts/ helper files")
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            files += 1
            if size > MAX_CHECK_FILE_BYTES:
                reasons.add("contains large files skipped during check")
                continue
            if total + size > MAX_CHECK_TOTAL:
                reasons.add("skipped some files because the Skill is large")
                continue
            try:
                with open(path, "rb") as file:
                    data = file.read()
            except OSError:
                reasons.add("contains unreadable files")
                continue
            total += len(data)
            if _looks_binary(data):
                reasons.add("contains binary or obfuscated content")
                continue
            text = data.decode("utf-8", errors="replace").lower()
            for needle, reason in _CHECK_PATTERNS:
                if needle in text:
                    reasons.add(reason)
    return sorted(reasons)


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    return b"\x00" in data[:4096]


def _read_skill_dir(directory: str, fallback: str) -> Skill:
    desc = read_descriptor(directory, fallback, SCOPE_GLOBAL)
    return Skill(
        name=desc.name,
        description=desc.description,
        directory=directory,
        path=os.path.join(directory, "SKILL.md"),
        valid=desc.valid,
        error=desc.error,
    )


def _clone_records(records: Optional[Dict[str, Record]]) -> Dict[str, Record]:
    out: Dict[str, Record] = {}
    for key, value in (records or {}).items():
        out[key] = Record(enabled=value.enabled, reasons=list(value.reasons))
    return out
