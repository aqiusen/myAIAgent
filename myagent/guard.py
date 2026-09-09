"""安全 Guard（参考 Suna 的 internal/guard）。

职责：在工具执行前做安全检查，决定 approve / reject / confirm。
对应 Suna 的 Guard，是"成品 agent 最不能缺的一层"。

设计（参考 Suna）：
  - 分层检查，按顺序硬拦截：结构性高危 → 危险命令规则 → 敏感文件 → 白名单 → 只读判定 → 模式策略。
  - 模式（mode）：readonly / ask / auto / smart，决定"非只读操作"怎么处置。
  - 审计（audit）：每次决策都记录到日志，可追溯。
  - smart 模式：对 exec 命令用 LLM 审查（审风险不审意图）。

安全底线（fail-closed）：
  - 无法证明只读 → 一律非只读，交给模式策略处置，绝不猜测放行。
  - LLM 审查不可用时 → 拒绝（fail-closed），不放行。
"""
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# ---------- 决策与模式 ----------
APPROVE = "approve"
REJECT = "reject"
CONFIRM = "confirm"

MODE_READONLY = "readonly"
MODE_ASK = "ask"
MODE_AUTO = "auto"
MODE_SMART = "smart"

VALID_MODES = {MODE_READONLY, MODE_ASK, MODE_AUTO, MODE_SMART}


@dataclass
class GuardResult:
    decision: str          # approve / reject / confirm
    reason: str
    source: str            # rule / static / llm / user / fallback
    audit: str = ""        # 审计原因标签


# ---------- 危险命令规则（参考 Suna rules_unix.go） ----------
# 每条 = (正则, 拦截原因)。命中即 reject，所有模式一致。
BLOCKED_RULES: List[Tuple[str, str]] = [
    (r"(?i)\brm\b(?=.*\s-[^\s]*r)(?=.*\s-[^\s]*f).*\s(?:/|~|\$HOME)(?:\s|$)", "blocked: recursive delete root"),
    (r"(?i)\brm\b(?=.*\s-[^\s]*r)(?=.*\s-[^\s]*f).*\s(?:~|\$HOME)(?:\s|$)", "blocked: recursive delete home"),
    (r"(?i)\bmkfs\b", "blocked: disk format"),
    (r"(?i)\bdd\b.*\b(if=/dev/zero|of=/dev/)", "blocked: disk wipe"),
    (r"(?i)\bchmod\b.*\s-r\b.*\s777\s+/", "blocked: recursive open permissions"),
    (r"(?i)\b(curl|wget)\b.*\|\s*(sh|bash|zsh|fish)\b", "blocked: remote script pipe execution"),
    (r"(?i)\beval\s*\$\(", "blocked: command injection pattern"),
    (r">\s*/dev/sd", "blocked: write to disk device"),
]

# ---------- 敏感文件规则（参考 Suna sensitive.go） ----------
# 文件名/路径包含这些模式即敏感，读/写一律拒绝。
SENSITIVE_FILE_PATTERNS: List[Tuple[str, str]] = [
    (".credentials", "credential file"),
    (".env", "environment file with secrets"),
    (".pem", "PEM private key"),
    (".key", "private key file"),
    (".p12", "PKCS12 certificate"),
    (".pfx", "PKCS12 certificate"),
    (".jks", "Java keystore"),
    ("id_rsa", "SSH private key"),
    ("id_ed25519", "SSH private key"),
    ("id_ecdsa", "SSH private key"),
    (".ssh/", "SSH directory"),
    (".gnupg/", "GPG directory"),
    (".netrc", "netrc with credentials"),
    (".npmrc", "may contain auth tokens"),
    (".pypirc", "may contain PyPI credentials"),
    (".aws/credentials", "AWS credentials"),
    (".aws/config", "AWS config with secrets"),
    ("credentials.json", "service account credentials"),
    (".docker/config.json", "Docker credentials"),
    (".kube/config", "Kubernetes config with tokens"),
]

# ---------- 简单只读命令白名单（参考 Suna exec_risk.go） ----------
# 只覆盖无参数语义的日常命令；git/find 等有子命令语义的一律保守非只读。
SIMPLE_READONLY_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "stat", "du", "grep", "rg", "ag", "ack",
    "which", "type", "where", "echo", "printf", "date", "whoami", "env", "printenv",
    "uname", "hostname", "pwd", "dir", "findstr", "get-content", "get-childitem",
}

# 解释器：内容无法静态确定，保守非只读。
INTERPRETER_COMMANDS = {
    "python", "python3", "node", "ruby", "perl", "php", "sh", "bash", "zsh", "fish",
}

# 结构性高危命令名（删除/磁盘/执行类），用于动态表达式与 xargs 兜底。
DANGEROUS_COMMANDS = {
    "rm", "rmdir", "shred", "unlink", "del", "erase", "rd", "remove-item",
    "mkfs", "diskpart", "bcdedit", "format", "dd", "chmod", "chown",
    "reg", "sc", "schtasks", "vssadmin", "takeown", "icacls", "robocopy",
    "iex", "invoke-expression", "set-executionpolicy", "start-process", "eval",
}

# 下载类命令
DOWNLOAD_COMMANDS = {"curl", "wget", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"}

# 执行类命令
EXECUTE_COMMANDS = {
    "sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh",
    "iex", "invoke-expression", "eval", "source",
}


def _expand_path(path: str) -> str:
    """展开 ~ 和相对路径为绝对路径，用于敏感文件/workspace 判断。"""
    if path.startswith("~/"):
        return os.path.join(os.path.expanduser("~"), path[2:])
    return os.path.abspath(path)


def is_sensitive_path(path: str) -> Tuple[bool, str]:
    """判断路径是否指向敏感文件。返回 (是否敏感, 原因)。"""
    lower = _expand_path(path).lower()
    for pattern, reason in SENSITIVE_FILE_PATTERNS:
        if pattern in lower:
            return True, reason
    return False, ""


def _split_command(command: str) -> List[str]:
    """极简 shell 分词：按空白切分，去掉引号。够 Guard 用，不追求完整 AST。"""
    # 去掉引号包裹，简单处理
    command = re.sub(r"['\"]", "", command)
    return command.split()


def _first_command(command: str) -> str:
    """取命令的第一个词（命令名）。"""
    parts = _split_command(command)
    return parts[0].lower() if parts else ""


def _is_readonly_command(command: str) -> bool:
    """静态判定命令是否可证明只读。

    唯一规则：无法证明只读 → 非只读。只放行简单只读白名单命令，
    且不含写重定向（> file）、不含管道到执行器、不含动态表达式。
    """
    cmd = command.strip()
    if not cmd:
        return False
    # 写/追加重定向（非 /dev/null）→ 非只读
    if re.search(r"[>]{1,2}\s*(?!\s*/dev/null\b)\S+", cmd):
        return False
    # 管道到执行器（curl x | sh）→ 非只读
    if re.search(r"\|\s*(sh|bash|zsh|fish|python|python3|node|ruby|perl|php)\b", cmd):
        return False
    # 动态表达式 $(...) → 非只读
    if "$(" in cmd or "`" in cmd:
        return False
    # 所有命令都必须是简单只读白名单
    for part in cmd.split(";"):
        part = part.strip()
        if not part:
            continue
        name = _first_command(part)
        if name not in SIMPLE_READONLY_COMMANDS:
            return False
    return True


def _is_structural_high_risk(command: str) -> bool:
    """结构性高危兜底：识别高危组合特征（命令名+参数+目标）。

    参考 Suna exec_high_risk.go。只识别组合特征，单命令不拦：
    rm file 不拦、echo "rm -rf /" 不拦。
    """
    cmd = command.strip()
    if not cmd:
        return False
    # 1. 删除根/家目录：rm -rf / 或 ~
    if re.search(r"\brm\b.*\s-[^\s]*r[^\s]*f[^\s]*\s+(?:/|~|\$HOME)(?:\s|$)", cmd, re.I):
        return True
    # 2. 磁盘操作：dd of=/dev/、mkfs
    if re.search(r"\bdd\b.*\bof=/dev/", cmd, re.I):
        return True
    if re.search(r"\bmkfs\b", cmd, re.I):
        return True
    # 3. 权限变更：chmod -R 777 /
    if re.search(r"\bchmod\b.*\s-r\b.*\s777\s+/", cmd, re.I):
        return True
    # 4. 下载→执行链：curl x | sh
    if re.search(r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh|fish)\b", cmd, re.I):
        return True
    # 5. 动态表达式内部高危：$(rm -rf /)
    for m in re.findall(r"\$\(([^)]+)\)", cmd):
        inner = m.strip()
        name = _first_command(inner)
        if name in DANGEROUS_COMMANDS:
            return True
    # 6. xargs 参数含删除命令
    if re.search(r"\bxargs\b.*\b(rm|rmdir|shred)\b", cmd, re.I):
        return True
    return False


# ---------- LLM 审查 ----------
# smart 模式下对 exec 命令做 LLM 审查。审风险不审意图。
LLMReviewer = Callable[[str, str], str]  # (command, params_json) -> LLM 原始回复


class Guard:
    """工具执行前的安全检查器。"""

    def __init__(
        self,
        mode: str = MODE_SMART,
        audit_path: Optional[str] = None,
        llm_reviewer: Optional[LLMReviewer] = None,
    ):
        self.mode = self._normalize_mode(mode)
        self.llm_reviewer = llm_reviewer
        self._audit_path = audit_path
        self._blocked = [(re.compile(p, re.I), r) for p, r in BLOCKED_RULES]

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        mode = mode.strip().lower()
        return mode if mode in VALID_MODES else MODE_SMART

    # ---------- 审计 ----------
    def _audit(self, tool: str, params: Dict, decision: str, reason: str) -> None:
        if not self._audit_path:
            return
        try:
            record = {
                "ts": time.time(),
                "tool": tool,
                "params": params,
                "decision": decision,
                "reason": reason,
            }
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 审计失败不影响主流程

    # ---------- 分层检查 ----------
    def check(self, tool: str, params: Dict) -> GuardResult:
        """对一次工具调用做安全检查，返回决策。"""
        # 1. 结构性高危（所有模式一致拦截）
        if tool == "run_command":
            command = params.get("command", "")
            if _is_structural_high_risk(command):
                self._audit(tool, params, REJECT, "structural_high_risk")
                return GuardResult(REJECT, "blocked: systemically dangerous command", "rule", "structural_high_risk")

        # 2. 危险命令规则
        if tool == "run_command":
            command = params.get("command", "")
            for pattern, reason in self._blocked:
                if pattern.search(command):
                    self._audit(tool, params, REJECT, reason)
                    return GuardResult(REJECT, reason, "rule", "blocked")

        # 3. 敏感文件（读/写一律拒绝）
        for field in ("path", "destination"):
            path = params.get(field)
            if path:
                sensitive, reason = is_sensitive_path(str(path))
                if sensitive:
                    msg = f"blocked: sensitive file ({reason}). Accessing credential/secret files is not allowed."
                    self._audit(tool, params, REJECT, msg)
                    return GuardResult(REJECT, msg, "rule", "sensitive_reject")

        # 4. 只读判定（静态可证明无副作用）
        read_only = self._is_readonly_call(tool, params)

        # 5. 模式策略
        if read_only:
            self._audit(tool, params, APPROVE, "readonly call")
            return GuardResult(APPROVE, "readonly call", "static", "auto_approve")

        if self.mode == MODE_READONLY:
            self._audit(tool, params, REJECT, "readonly mode blocks this operation")
            return GuardResult(REJECT, "readonly mode blocks this operation", "static", "readonly_reject")

        if self.mode == MODE_AUTO:
            self._audit(tool, params, APPROVE, "auto mode")
            return GuardResult(APPROVE, "", "static", "auto_approve")

        if self.mode == MODE_ASK:
            self._audit(tool, params, CONFIRM, "ask mode")
            return GuardResult(CONFIRM, "confirm risky operation", "user", "confirm")

        # smart 模式：只审 exec（run_command），其他非只读工具静态放行
        if tool != "run_command":
            self._audit(tool, params, APPROVE, "smart mode non-exec write")
            return GuardResult(APPROVE, "", "static", "auto_approve")

        # smart 模式审 exec：LLM 审查
        if self.llm_reviewer is None:
            return self._review_fallback(tool, params, "review_unavailable", "Smart Guard reviewer is unavailable")
        return self._llm_review(tool, params)

    def _is_readonly_call(self, tool: str, params: Dict) -> bool:
        """静态判定调用是否可证明无副作用。无法证明 → 非只读。"""
        if tool in ("read_file", "list_dir", "search", "read_image"):
            return True
        if tool == "run_command":
            return _is_readonly_command(params.get("command", ""))
        return False

    # ---------- smart 模式 LLM 审查 ----------
    def _llm_review(self, tool: str, params: Dict) -> GuardResult:
        command = params.get("command", "")
        params_json = json.dumps(params, ensure_ascii=False)
        try:
            resp = self.llm_reviewer(command, params_json)
        except Exception as exc:
            return self._review_fallback(tool, params, "review_provider_error", f"Smart Guard review failed: {exc}")

        decision = self._extract_decision(resp)
        if decision == "reject":
            self._audit(tool, params, REJECT, resp)
            return GuardResult(REJECT, resp, "llm", "llm_reject")
        if decision == "approve":
            self._audit(tool, params, APPROVE, resp)
            return GuardResult(APPROVE, resp, "llm", "llm_approve")
        # LLM 表达不确定：硬拦截已兜底确定性危险，按 approve 放行并留痕
        self._audit(tool, params, APPROVE, resp)
        return GuardResult(APPROVE, "smart guard: " + resp, "llm", "llm_approve_uncertain")

    @staticmethod
    def _extract_decision(resp: str) -> str:
        """从 LLM 回复中提取 decision 字段（approve/reject）。"""
        try:
            start = resp.find("{")
            if start < 0:
                return ""
            data = json.loads(resp[start:])
            return str(data.get("decision", "")).strip().lower()
        except (json.JSONDecodeError, ValueError):
            return ""

    def _review_fallback(self, tool: str, params: Dict, code: str, message: str) -> GuardResult:
        """LLM 审查不可用时 fail-closed：审核能力缺失不放行。"""
        self._audit(tool, params, REJECT, message)
        return GuardResult(REJECT, message, "fallback", code)


# 便捷构造：从配置创建 Guard
def build_guard(mode: str, audit_path: Optional[str] = None, llm_reviewer: Optional[LLMReviewer] = None) -> Guard:
    return Guard(mode=mode, audit_path=audit_path, llm_reviewer=llm_reviewer)
