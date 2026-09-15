"""Skill 启用记录的持久化（对应 Suna config.Skills + agentSkillStore）。

skill 包不直接读 .env / sqlite，由宿主注入 Store：
  - MemorySkillStore：测试或不落盘
  - JsonSkillStore：把 records 写到 JSON 文件
"""
from __future__ import annotations

import json
import os
from typing import Dict, Optional, Protocol

from .manager import Record


class Store(Protocol):
    """宿主提供的 records 读写。SaveSkillRecord 必须返回完整快照，避免覆盖并发更新。"""

    def load_skill_records(self) -> Dict[str, Record]:
        ...

    def save_skill_records(self, records: Dict[str, Record]) -> None:
        ...

    def save_skill_record(self, name: str, record: Record) -> Dict[str, Record]:
        ...


class MemorySkillStore:
    """内存 records，测试和未配置持久化路径时使用。"""

    def __init__(self, records: Optional[Dict[str, Record]] = None):
        self.records: Dict[str, Record] = {}
        if records:
            for name, record in records.items():
                self.records[name] = Record(enabled=record.enabled, reasons=list(record.reasons))

    def load_skill_records(self) -> Dict[str, Record]:
        return {
            name: Record(enabled=record.enabled, reasons=list(record.reasons))
            for name, record in self.records.items()
        }

    def save_skill_records(self, records: Dict[str, Record]) -> None:
        self.records = {
            name: Record(enabled=record.enabled, reasons=list(record.reasons))
            for name, record in (records or {}).items()
        }

    def save_skill_record(self, name: str, record: Record) -> Dict[str, Record]:
        self.records[name] = Record(enabled=record.enabled, reasons=list(record.reasons))
        return self.load_skill_records()


class JsonSkillStore:
    """把 records 存成 JSON 文件，对应 Suna 写回 config.toml 的 [skills.<name>]。"""

    def __init__(self, path: str):
        self.path = path

    def load_skill_records(self) -> Dict[str, Record]:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                raw = json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}
        out: Dict[str, Record] = {}
        if not isinstance(raw, dict):
            return out
        for name, item in raw.items():
            if not isinstance(item, dict):
                continue
            out[name] = Record(
                enabled=bool(item.get("enabled", False)),
                reasons=list(item.get("reasons") or []),
            )
        return out

    def save_skill_records(self, records: Dict[str, Record]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            name: {"enabled": record.enabled, "reasons": list(record.reasons)}
            for name, record in (records or {}).items()
        }
        with open(self.path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def save_skill_record(self, name: str, record: Record) -> Dict[str, Record]:
        records = self.load_skill_records()
        records[name] = Record(enabled=record.enabled, reasons=list(record.reasons))
        self.save_skill_records(records)
        return records
