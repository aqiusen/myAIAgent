"""Skill 模糊匹配（对照 Codex SkillPopup + fuzzy_match）。

查询是目标串的子序列即可命中；分数越小越靠前。
先匹配 name，再匹配 description 等 search terms。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


def fuzzy_match(text: str, query: str) -> Optional[Tuple[List[int], int]]:
    """若 query 是 text 的子序列，返回 (命中下标, 分数)。分数越小越好。"""
    query = (query or "").strip()
    if not query:
        return [], 0
    haystack = text or ""
    lower = haystack.lower()
    needle = query.lower()
    indices: List[int] = []
    start = 0
    for char in needle:
        pos = lower.find(char, start)
        if pos < 0:
            return None
        indices.append(pos)
        start = pos + 1
    score = indices[0] * 2
    for i in range(1, len(indices)):
        gap = indices[i] - indices[i - 1] - 1
        score += gap
    return indices, score


def rank_skills(skills: Sequence[dict], query: str) -> List[dict]:
    """按 Codex SkillPopup 的规则过滤并排序：name 命中优先于 description。"""
    query = (query or "").strip()
    if not query:
        return [item for item in skills if item.get("valid", True)]
    scored = []
    for item in skills:
        if not item.get("valid", True):
            continue
        name = item.get("name") or ""
        desc = item.get("description") or ""
        name_hit = fuzzy_match(name, query)
        if name_hit:
            scored.append((0, name_hit[1], name.lower(), item))
            continue
        extra_hit = None
        for term in (desc, item.get("path") or ""):
            extra_hit = fuzzy_match(term, query)
            if extra_hit:
                break
        if extra_hit:
            scored.append((1, extra_hit[1], name.lower(), item))
    scored.sort()
    return [row[-1] for row in scored]


def highlight_fuzzy(text: str, query: str, style: str = "bold #3b82f6") -> str:
    """把模糊命中的字符标成蓝色（Rich markup）。对照 Grok / Codex 补全高亮。"""
    text = text or ""
    hit = fuzzy_match(text, query)
    escaped = text.replace("[", r"\[")
    if not hit or not hit[0]:
        return escaped
    indices = set(hit[0])
    out = []
    for i, char in enumerate(text):
        piece = char.replace("[", r"\[")
        if i in indices:
            out.append(f"[{style}]{piece}[/]")
        else:
            out.append(piece)
    return "".join(out)
