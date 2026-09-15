"""Skill 工具 + Agent 接入测试。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.agent import Agent
from myagent.config import PROJECT_ROOT, Config
from myagent.model_registry import ModelConfig
from myagent.skill import TOOL_LOAD, TOOL_START, SCOPE_GLOBAL
from myagent.skill.manager import Record
from myagent.skill.runtime import Runtime
from myagent.skill.store import MemorySkillStore
from myagent.tools.skill_provider import SkillToolProvider
from myagent.guard import Guard, MODE_READONLY


def write_skill(root, directory, name, desc) -> str:
    path = os.path.join(root, directory, "SKILL.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\nbody here\n")
    return path


def make_config(tmp_path, **kwargs):
    return Config(
        model="m",
        base_url="http://x",
        api_key="k",
        models=[ModelConfig(ref="default", model="m", base_url="http://x", api_key="k")],
        skills_dir=str(tmp_path / "skills"),
        skills_records_path=str(tmp_path / "skills.json"),
        **kwargs,
    )


def test_skill_load_returns_full_body(tmp_path):
    write_skill(tmp_path, "writer", "writer", "Writing.")
    rt = Runtime(str(tmp_path), MemorySkillStore())
    rt.reload()
    provider = SkillToolProvider(rt)
    result = provider.execute_load(name="writer", scope=SCOPE_GLOBAL)
    assert result.startswith("[Skill: writer]")
    assert "Scope: global" in result
    assert "body here" in result


def test_skill_load_blocks_disabled(tmp_path):
    write_skill(tmp_path, "writer", "writer", "Writing.")
    rt = Runtime(str(tmp_path), MemorySkillStore({"writer": Record(enabled=False)}))
    rt.reload()
    provider = SkillToolProvider(rt)
    result = provider.execute_load(name="writer", scope=SCOPE_GLOBAL)
    assert "not enabled" in result


def test_system_prompt_requires_web_fallback_and_analysis(tmp_path):
    agent = Agent(make_config(tmp_path))
    prompt = agent._build_system_prompt()
    assert "http" in prompt
    assert "generic tips" in prompt or "空泛提示" in prompt
    assert "完成用户的任务" in prompt
    assert "失败" in prompt and "换方法" in prompt


def test_agent_injects_available_skills_into_system_prompt(tmp_path):
    skills_dir = tmp_path / "skills"
    write_skill(skills_dir, "code-review", "code-review", "Review source code.")
    agent = Agent(make_config(tmp_path))
    system = [m["content"] for m in agent.memory._messages if m["role"] == "system"][0]
    assert "Available Skills" in system
    assert "code-review: Review source code." in system
    assert "skill_start" in system
    names = {t.name for t in agent.tools_list}
    assert TOOL_LOAD in names
    assert TOOL_START in names


def test_agent_skill_load_via_tool(tmp_path):
    skills_dir = tmp_path / "skills"
    write_skill(skills_dir, "code-review", "code-review", "Review source code.")
    agent = Agent(make_config(tmp_path))
    tool = next(t for t in agent.tools_list if t.name == TOOL_LOAD)
    result = tool.run(name="code-review", scope="global")
    assert "[Skill: code-review]" in result
    assert "body here" in result


def test_agent_imports_user_home_skills_once_as_global(tmp_path):
    write_skill(tmp_path / "home" / ".codex" / "skills", "ponytail", "ponytail", "Lazy mode.")
    write_skill(tmp_path / "home" / ".grok" / "skills", "ponytail", "ponytail", "Duplicate.")
    config = make_config(tmp_path)
    config.skills_user_home = str(tmp_path / "home")
    agent = Agent(config)
    ponytails = [item for item in agent.list_skill_infos() if item["name"] == "ponytail"]
    assert len(ponytails) == 1
    assert ponytails[0]["scope"] == "global"
    system = [m["content"] for m in agent.memory._messages if m["role"] == "system"][0]
    assert "ponytail:" in system
    assert "scope=user" not in system
    tool = next(t for t in agent.tools_list if t.name == TOOL_LOAD)
    loaded = tool.run(name="ponytail", scope="global")
    assert loaded.startswith("[Skill: ponytail]")
    assert "body here" in loaded

    write_skill(tmp_path / "home" / ".codex" / "skills", "new-skill", "new-skill", "Added later.")
    agent2 = Agent(config)
    names = {item["name"] for item in agent2.list_skill_infos()}
    assert "new-skill" not in names
    copied = agent2.import_user_skills()
    assert "new-skill" in copied


def test_agent_activates_fuzzy_skill_mention_in_system_prompt(tmp_path):
    write_skill(tmp_path / "skills", "ponytail", "ponytail", "Lazy mode.")
    config = make_config(tmp_path)
    agent = Agent(config)
    assert agent.resolve_skill_name("pony") == "ponytail"
    slash_active, slash_errors = agent.load_mentioned_skills("/ponytail 帮我改这段")
    assert slash_errors == []
    assert slash_active[0][0] == "ponytail"
    assert agent.strip_skill_mentions("/ponytail 帮我改这段") == "帮我改这段"
    active, errors = agent.load_mentioned_skills("$pony 帮我改这段")
    assert errors == []
    assert active[0][0] == "ponytail"
    assert "Lazy mode." in active[0][1]
    prompt = agent._build_system_prompt(active_skills=active)
    assert "## Active Skills" in prompt
    assert "Active Skill: ponytail" in prompt
    assert "operating instructions" in prompt
    assert agent.strip_skill_mentions("$pony 帮我改这段") == "帮我改这段"
    agent.runner.run = lambda messages, schemas, on_delta=None: "ok"
    agent.run("$pony 帮我改这段")
    user = [m["content"] for m in agent.memory._messages if m["role"] == "user"][-1]
    system = [m["content"] for m in agent.memory._messages if m["role"] == "system"][0]
    assert user == "帮我改这段"
    assert "Active Skill: ponytail" in system
    assert agent.last_activated_skills == ["ponytail"]


def test_bundled_skills_load_and_toggle(tmp_path):
    """用仓库里预置的 Skill 做一次真实 Load + /skills 那种 toggle。"""
    config = make_config(tmp_path)
    config.skills_dir = str(PROJECT_ROOT / "skills")
    config.skills_records_path = str(tmp_path / "records.json")
    agent = Agent(config)

    names = {item["name"] for item in agent.list_skill_infos()}
    assert "code-review" in names
    assert "write-tests" in names
    system = [m["content"] for m in agent.memory._messages if m["role"] == "system"][0]
    assert "code-review:" in system

    tool = next(t for t in agent.tools_list if t.name == TOOL_LOAD)
    loaded = tool.run(name="code-review", scope="global")
    assert loaded.startswith("[Skill: code-review]")
    assert "Review the code the user pointed to" in loaded

    enabled = agent.toggle_skill("code-review")
    assert enabled is False
    blocked = tool.run(name="code-review", scope="global")
    assert "not enabled" in blocked
    assert agent.toggle_skill("code-review") is True


def test_runner_skips_guard_for_skill_tools(tmp_path):
    """readonly 模式也不能挡住 skill_load（对应 Suna GuardNever）。"""
    skills_dir = tmp_path / "skills"
    write_skill(skills_dir, "code-review", "code-review", "Review source code.")
    config = make_config(tmp_path)
    agent = Agent(config)
    agent.runner.guard = Guard(mode=MODE_READONLY)
    tc = {
        "function": {
            "name": TOOL_LOAD,
            "arguments": '{"name":"code-review","scope":"global"}',
        }
    }
    result = agent.runner._dispatch(tc)
    assert "[Skill: code-review]" in result
    assert "[Guard 拒绝]" not in result
