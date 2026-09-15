"""Skill Runtime（对应 Suna internal/skill/runtime.go）。

把 Manager + Store + 审查器 + 询问器绑在一起，是 Agent 真正持有的对象。
领域逻辑留在 skill 包；tools/skill_provider 只做工具适配。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol

from .catalog import Descriptor, render_summary
from .importer import ImportResult, import_source
from .manager import CheckResult, Info, Manager, Record
from .review import LLMReviewRequest, LLMReviewResult, collect_review_files
from .store import MemorySkillStore, Store
from .workflow import (
    OPTION_ENABLE_NO,
    OPTION_ENABLE_YES,
    OPTION_REVIEW_NO,
    OPTION_REVIEW_YES,
    START_CHECK,
    START_IMPORT,
    StartResult,
    format_check_question,
    format_enable_question,
    start_result_from_check,
)


class LLMReviewer(Protocol):
    """Skill LLM 审查器。由 Agent 注入，复用当前模型。"""

    def review_skill(self, req: LLMReviewRequest) -> str:
        ...


class UserPrompter(Protocol):
    """skill_start 向用户提问。TUI 弹窗 / 测试假对象都实现这个。"""

    def ask_choice(self, question: str, options: List[str]) -> str:
        ...


@dataclass
class EnableDecision:
    """启用/停用一次 Skill。"""

    name: str
    enabled: bool
    reasons: Optional[List[str]] = field(default=None)


class Runtime:
    """全局 Skill 的运行时入口。"""

    def __init__(self, root: str, store: Optional[Store] = None):
        self.root = root
        self.store: Store = store or MemorySkillStore()
        self.manager = Manager(root, self._load_records())
        self.reviewer: Optional[LLMReviewer] = None
        self.prompter: Optional[UserPrompter] = None
        self._lock = threading.Lock()

    def set_root(self, root: str) -> None:
        with self._lock:
            self.root = root
            self.manager.root = root

    def set_store(self, store: Store) -> None:
        with self._lock:
            self.store = store

    def set_reviewer(self, reviewer: Optional[LLMReviewer]) -> None:
        with self._lock:
            self.reviewer = reviewer

    def set_prompter(self, prompter: Optional[UserPrompter]) -> None:
        with self._lock:
            self.prompter = prompter

    def reload(self) -> None:
        with self._lock:
            self._reload_locked()

    def list(self) -> List[Info]:
        with self._lock:
            self._reload_locked()
            return self.manager.list()

    def current_list(self) -> List[Info]:
        with self._lock:
            return self.manager.list()

    def summary(self) -> str:
        return render_summary(self.enabled_descriptors(), [], self.root)

    def enabled_descriptors(self) -> List[Descriptor]:
        with self._lock:
            return self.manager.enabled_descriptors()

    def check(self, name: str) -> CheckResult:
        with self._lock:
            self._reload_locked()
            return self.manager.check(name.strip())

    def review(self, name: str) -> LLMReviewResult:
        """静态检查 + LLM 审查。审查器不可用时直接报错，不静默放行。"""
        with self._lock:
            if self.reviewer is None:
                raise ValueError("skill LLM reviewer is not configured")
            self._reload_locked()
            check = self.manager.check(name.strip())
            if not check.valid:
                return LLMReviewResult(
                    name=check.name, valid=False,
                    static_reasons=list(check.reasons), error=check.error,
                )
            req = self._review_request_locked(check)
            reviewer = self.reviewer
        text = reviewer.review_skill(req)
        return LLMReviewResult(
            name=check.name, valid=True,
            static_reasons=list(check.reasons),
            review=(text or "").strip(),
            needs_attention=len(check.reasons) > 0,
        )

    def set_enabled(self, decision: EnableDecision) -> None:
        with self._lock:
            self._set_enabled_locked(decision.name, decision.enabled, decision.reasons, True)

    def disable(self, name: str) -> None:
        with self._lock:
            self._set_enabled_locked(name, False, None, False)

    def load_global(self, name: str) -> tuple[Descriptor, str]:
        """加载已启用的全局 Skill 全文。"""
        with self._lock:
            name = (name or "").strip()
            if not name:
                raise ValueError("name is required")
            content, ok, reason = self.manager.load(name)
            if not ok:
                raise ValueError(reason)
            info = self.manager.info(name)
            if info is None:
                raise ValueError("skill not found")
            desc = Descriptor(
                name=info.name, description=info.description,
                scope=info.scope, path=info.path, valid=info.valid,
            )
            return desc, content

    def load_content(self, name: str) -> str:
        """只返回全文，测试和内部用。"""
        with self._lock:
            name = (name or "").strip()
            if not name:
                raise ValueError("name is required")
            content, ok, reason = self.manager.load(name)
            if not ok:
                raise ValueError(reason)
            return content

    def import_skill(self, source: str, name: str = "") -> ImportResult:
        with self._lock:
            return import_source(self, source, name)

    def start(self, params: dict) -> StartResult:
        """skill_start 入口：import 或 check，然后走确认工作流。"""
        action = str(params.get("action") or "").strip()
        if action == START_IMPORT:
            source = str(params.get("source") or "")
            name = str(params.get("name") or "")
            imported = self.import_skill(source, name)
            return self._finish_start(start_result_from_check(imported.name, action, imported.check))
        if action == START_CHECK:
            name = str(params.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            check = self.check(name)
            if not check.valid:
                return start_result_from_check(name, action, check)
            self.disable(name)
            return self._finish_start(start_result_from_check(name, action, check))
        raise ValueError("invalid skill_start action")

    def _finish_start(self, result: StartResult) -> StartResult:
        if not result.valid:
            return result
        review_choice = self._ask_choice(
            format_check_question(CheckResult(
                name=result.name, valid=result.valid,
                reasons=list(result.reasons), description=result.description, error=result.error,
            )),
            [OPTION_REVIEW_YES, OPTION_REVIEW_NO],
        )
        result.review_ask = review_choice
        if review_choice == OPTION_REVIEW_YES:
            result.review = self.review(result.name)
        enable_choice = self._ask_choice(
            format_enable_question(result),
            [OPTION_ENABLE_YES, OPTION_ENABLE_NO],
        )
        result.enable_ask = enable_choice
        result.enabled = enable_choice == OPTION_ENABLE_YES
        self._save_workflow_decision(result)
        return result

    def _ask_choice(self, question: str, options: List[str]) -> str:
        with self._lock:
            prompter = self.prompter
        if prompter is None:
            raise ValueError("skill workflow prompter is not configured")
        for attempt in range(2):
            q = question
            if attempt > 0:
                q = "Please choose one of the provided options to continue the Skill workflow.\n" + question
            answer = prompter.ask_choice(q, options)
            for opt in options:
                if (answer or "").strip() == opt:
                    return opt
        raise ValueError("invalid choice")

    def _reload_locked(self) -> None:
        self.manager.set_records(self._load_records())
        self.manager.reload()
        self._sync_store_locked()

    def _sync_store_locked(self) -> None:
        """目录里新出现、records 里还没有的 Skill，视为用户已放入，默认启用。"""
        records = self.manager.records_copy()
        changed = False
        for info in self.manager.list():
            if not info.valid:
                continue
            if info.name not in records:
                records[info.name] = Record(enabled=True)
                changed = True
        if not changed:
            return
        self.store.save_skill_records(records)
        self.manager.apply_records(records)

    def _set_enabled_locked(
        self, name: str, enabled: bool, reasons: Optional[List[str]], require_valid: bool,
    ) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")
        if require_valid:
            info = self.manager.info(name)
            if info is None or not info.valid:
                raise ValueError(f'skill "{name}" is missing or invalid')
        records = self.manager.records_copy()
        current = records.get(name, Record())
        current.enabled = enabled
        if reasons is not None:
            current.reasons = list(reasons)
        persisted = self.store.save_skill_record(name, current)
        self.manager.apply_records(persisted)

    def _save_workflow_check_locked(self, name: str, enabled: bool, check: CheckResult) -> None:
        self._set_enabled_locked(name, enabled, list(check.reasons), True)

    def _save_workflow_decision(self, result: StartResult) -> None:
        with self._lock:
            self._set_enabled_locked(result.name, result.enabled, list(result.reasons), True)

    def _review_request_locked(self, check: CheckResult) -> LLMReviewRequest:
        skill = self.manager.skills.get(check.name)
        if skill is None or not skill.valid:
            raise ValueError(f'skill "{check.name}" is missing or invalid')
        files = collect_review_files(skill.directory)
        return LLMReviewRequest(
            name=check.name, description=check.description,
            reasons=list(check.reasons), files=files,
        )

    def _load_records(self) -> Dict[str, Record]:
        return self.store.load_skill_records()


class CallbackPrompter:
    """把 (question, options) -> str 回调包成 UserPrompter。"""

    def __init__(self, fn: Callable[[str, List[str]], str]):
        self.fn = fn

    def ask_choice(self, question: str, options: List[str]) -> str:
        return self.fn(question, options)
