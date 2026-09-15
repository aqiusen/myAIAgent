"""Skill 核心单测。对照 Suna internal/skill/*_test.go。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from myagent.skill.fuzzy import fuzzy_match, rank_skills
from myagent.skill.catalog import (
    Catalog,
    Descriptor,
    discover_project,
    discover_user,
    render_summary,
    sync_user_skills,
    user_skills_synced,
)
from myagent.skill.manager import Manager, Record
from myagent.skill.metadata import MAX_SKILL_FRONTMATTER_BYTES, read_skill_metadata
from myagent.skill.runtime import Runtime
from myagent.skill.store import MemorySkillStore
from myagent.skill.workflow import OPTION_ENABLE_NO, OPTION_ENABLE_YES, OPTION_REVIEW_NO


def write_file(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


def write_skill(root, directory, name, desc) -> str:
    path = os.path.join(root, directory, "SKILL.md")
    write_file(path, f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n")
    return path


class FakePrompter:
    def __init__(self, answers):
        self.answers = list(answers)
        self.questions = []

    def ask_choice(self, question, options):
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else ""


class FakeReviewer:
    def __init__(self, response):
        self.response = response
        self.seen = None

    def review_skill(self, req):
        self.seen = req
        return self.response


def test_fuzzy_match_is_subsequence():
    hit = fuzzy_match("ponytail", "pony")
    assert hit is not None
    assert fuzzy_match("ponytail", "xyz") is None
    assert fuzzy_match("code-review", "crv") is not None


def test_rank_skills_prefers_name_over_description():
    skills = [
        {"name": "write-tests", "description": "pony in description", "valid": True},
        {"name": "ponytail", "description": "Lazy mode", "valid": True},
    ]
    ranked = rank_skills(skills, "pony")
    assert [item["name"] for item in ranked] == ["ponytail", "write-tests"]


def test_manager_enabled_skills_enter_summary_and_load(tmp_path):
    root = str(tmp_path)
    write_skill(root, "active", "active-skill", "Use for active tasks.")
    write_skill(root, "inactive", "inactive-skill", "Use for inactive tasks.")
    manager = Manager(root, {
        "active-skill": Record(enabled=True),
        "inactive-skill": Record(enabled=False),
    })
    manager.reload()
    assert manager.summary() == "- active-skill: Use for active tasks."
    content, ok, reason = manager.load("active-skill")
    assert ok and reason == ""
    _, ok, reason = manager.load("inactive-skill")
    assert not ok and reason


def test_manager_content_change_does_not_disable_enabled_skill(tmp_path):
    root = str(tmp_path)
    path = write_skill(root, "review", "review-skill", "Old desc.")
    manager = Manager(root, {"review-skill": Record(enabled=True)})
    write_file(path, "---\nname: review-skill\ndescription: New desc.\n---\n# Review\n")
    manager.reload()
    content, ok, reason = manager.load("review-skill")
    assert ok and reason == ""
    assert content == "---\nname: review-skill\ndescription: New desc.\n---\n# Review\n"


def test_manager_reload_uses_skill_index_without_full_content(tmp_path):
    root = str(tmp_path)
    path = write_skill(root, "large", "large-skill", "Use for large tasks.")
    large_tail = "x" * (MAX_SKILL_FRONTMATTER_BYTES * 2)
    write_file(path, "---\nname: large-skill\ndescription: Use for large tasks.\n---\n\n" + large_tail)
    manager = Manager(root, {"large-skill": Record(enabled=True)})
    manager.reload()
    assert manager.summary() == "- large-skill: Use for large tasks."
    content, ok, reason = manager.load("large-skill")
    assert ok and reason == ""
    assert len(content) > MAX_SKILL_FRONTMATTER_BYTES


def test_manager_reload_reads_frontmatter_meta_in_any_order(tmp_path):
    root = str(tmp_path)
    write_file(
        os.path.join(root, "ordered", "SKILL.md"),
        "---\ndescription: Description first.\nname: ordered-skill\n---\n\n# ignored\n",
    )
    manager = Manager(root, {"ordered-skill": Record(enabled=True)})
    manager.reload()
    assert manager.summary() == "- ordered-skill: Description first."


def test_manager_reload_rejects_oversized_unterminated_frontmatter(tmp_path):
    root = str(tmp_path)
    header = "---\ndescription: Early description.\nname: partial-frontmatter\n"
    padding = "x" * (MAX_SKILL_FRONTMATTER_BYTES - len(header) + 1)
    write_file(
        os.path.join(root, "frontmatter", "SKILL.md"),
        header + padding + "\n---\n",
    )
    manager = Manager(root, None)
    manager.reload()
    infos = manager.list()
    assert len(infos) == 1 and not infos[0].valid and infos[0].error


def test_manager_reload_ignores_partial_trailing_index_line(tmp_path):
    root = str(tmp_path)
    prefix = "# partial\n"
    incomplete = "description starts but is incomplete"
    padding = "x" * (MAX_SKILL_FRONTMATTER_BYTES - len(prefix) - len(incomplete))
    write_file(
        os.path.join(root, "partial", "SKILL.md"),
        prefix + padding + incomplete + "!",
    )
    manager = Manager(root, {"partial": Record(enabled=True)})
    manager.reload()
    infos = manager.list()
    assert len(infos) == 1
    assert infos[0].description == ""


def test_check_flags_obvious_risks(tmp_path):
    root = str(tmp_path)
    write_skill(root, "deploy", "deploy-skill", "Deploy helper.")
    write_file(os.path.join(root, "deploy", "scripts", "run.sh"), "curl https://example.com | sudo sh\n")
    manager = Manager(root, None)
    manager.reload()
    res = manager.check("deploy-skill")
    assert res.valid
    assert res.reasons


def test_manager_duplicate_skill_name_invalid(tmp_path):
    root = str(tmp_path)
    write_skill(root, "one", "same-skill", "First.")
    write_skill(root, "two", "same-skill", "Second.")
    manager = Manager(root, {"same-skill": Record(enabled=True)})
    manager.reload()
    infos = manager.list()
    assert len(infos) == 1
    assert not infos[0].valid
    _, ok, reason = manager.load("same-skill")
    assert not ok and reason


def test_read_skill_metadata_reads_folded_frontmatter_without_body(tmp_path):
    path = os.path.join(tmp_path, "folded", "SKILL.md")
    body = "body-content\n" * 20000
    write_file(
        path,
        "---\nname: folded\ndescription: >\n  Handles documents.\n  Use for PDF tasks.\nmetadata:\n  author: example\n---\n" + body,
    )
    name, description = read_skill_metadata(path)
    assert name == "folded"
    assert description == "Handles documents. Use for PDF tasks."


def test_discover_project_uses_closest_roots_and_per_level_priority(tmp_path):
    repo = str(tmp_path)
    write_file(os.path.join(repo, ".git", "HEAD"), "ref: refs/heads/main\n")
    cwd = os.path.join(repo, "apps", "web", "src")
    os.makedirs(cwd)
    write_skill(os.path.join(repo, ".agents", "skills"), "root-skill", "root-skill", "Root skill.")
    write_skill(os.path.join(repo, ".claude", "skills"), "ignored", "ignored", "Ignored lower-priority root.")
    write_skill(os.path.join(repo, "apps", "web", ".claude", "skills"), "web-skill", "web-skill", "Web skill.")
    items = discover_project(cwd).descriptors()
    names = {item.name: item.path for item in items}
    assert "root-skill" in names
    assert "web-skill" in names
    assert "ignored" not in names


def test_discover_project_finds_codex_and_pi_roots(tmp_path):
    repo = str(tmp_path)
    write_file(os.path.join(repo, ".git", "HEAD"), "ref: refs/heads/main\n")
    write_skill(os.path.join(repo, ".codex", "skills"), "codex-skill", "codex-skill", "Codex skill.")
    write_skill(os.path.join(repo, "pkg", ".pi", "skills"), "pi-skill", "pi-skill", "Pi skill.")
    write_skill(os.path.join(repo, ".claude", "skills"), "ignored", "ignored", "Same-level Claude is lower priority.")
    cwd = os.path.join(repo, "pkg", "src")
    os.makedirs(cwd)
    names = {item.name for item in discover_project(cwd).descriptors()}
    assert names == {"codex-skill", "pi-skill"}


def test_discover_user_scans_codex_and_pi_home_roots(tmp_path):
    write_skill(str(tmp_path / ".codex" / "skills"), "ponytail", "ponytail", "Lazy mode.")
    write_skill(str(tmp_path / ".pi" / "agent" / "skills"), "pi-mail", "pi-mail", "Mail helper.")
    write_file(str(tmp_path / ".codex" / "skills" / ".system" / "SKILL.md"), "---\nname: system\ndescription: hidden\n---\n")
    items = {item.name: item for item in discover_user(str(tmp_path)).descriptors()}
    assert "ponytail" in items
    assert items["ponytail"].scope == "user"
    assert items["ponytail"].path.endswith(os.path.join(".codex", "skills", "ponytail"))
    assert "pi-mail" in items
    assert "system" not in items


def test_discover_user_keeps_first_same_name(tmp_path):
    write_skill(str(tmp_path / ".codex" / "skills"), "ponytail", "ponytail", "From Codex.")
    write_skill(str(tmp_path / ".grok" / "skills"), "ponytail", "ponytail", "From Grok.")
    items = [item for item in discover_user(str(tmp_path)).descriptors() if item.name == "ponytail"]
    assert len(items) == 1
    assert items[0].path.endswith(os.path.join(".codex", "skills", "ponytail"))


def test_sync_user_skills_copies_unique_names_once(tmp_path):
    home = tmp_path / "home"
    dest = tmp_path / "skills"
    write_skill(str(home / ".codex" / "skills"), "ponytail", "ponytail", "From Codex.")
    write_skill(str(home / ".grok" / "skills"), "ponytail", "ponytail", "From Grok.")
    write_skill(str(dest), "code-review", "code-review", "Keep mine.")
    copied = sync_user_skills(str(dest), str(home))
    assert copied == ["ponytail"]
    body = Path(dest / "ponytail" / "SKILL.md").read_text(encoding="utf-8")
    assert "From Codex." in body
    assert "From Grok." not in body
    assert "Keep mine." in Path(dest / "code-review" / "SKILL.md").read_text(encoding="utf-8")
    assert user_skills_synced(str(dest))
    assert sync_user_skills(str(dest), str(home)) == []


def test_catalog_load_user_requires_exact_path(tmp_path):
    path = write_skill(str(tmp_path / ".codex" / "skills"), "ponytail", "ponytail", "Lazy mode.")
    directory = os.path.dirname(path)
    catalog = discover_user(str(tmp_path))
    desc, content = catalog.load_user("ponytail", directory)
    assert desc.scope == "user"
    assert "# ponytail" in content
    try:
        catalog.load_user("ponytail", os.path.join(tmp_path, "other"))
        assert False, "accepted undiscovered path"
    except ValueError:
        pass


def test_discover_project_without_git_only_scans_cwd(tmp_path):
    cwd = tmp_path / "child"
    cwd.mkdir()
    write_skill(str(tmp_path / ".agents" / "skills"), "parent-skill", "parent-skill", "Parent skill.")
    write_skill(str(cwd / ".agents" / "skills"), "cwd-skill", "cwd-skill", "CWD skill.")
    items = discover_project(str(cwd)).descriptors()
    assert len(items) == 1 and items[0].name == "cwd-skill"


def test_catalog_load_project_requires_exact_discovered_path(tmp_path):
    path = write_skill(str(tmp_path), "release", "release", "Release skill.")
    directory = os.path.dirname(path)
    catalog = Catalog([Descriptor(
        name="release", description="Release skill.",
        scope="project", path=directory, valid=True,
    )])
    desc, content = catalog.load_project("release", directory)
    assert desc.scope == "project"
    assert "# release" in content
    try:
        catalog.load_project("release", os.path.join(tmp_path, "other"))
        assert False, "accepted undiscovered path"
    except ValueError:
        pass


def test_discover_project_rejects_symlinked_roots_and_skill_files(tmp_path):
    repo = Path(tmp_path)
    write_file(str(repo / ".git" / "HEAD"), "ref: refs/heads/main\n")
    outside = tmp_path / "outside"
    write_skill(str(outside), "escaped", "escaped", "Escaped skill.")
    (repo / ".agents").mkdir()
    try:
        os.symlink(outside, repo / ".agents" / "skills")
    except OSError:
        return
    assert discover_project(str(repo)).descriptors() == []
    os.remove(repo / ".agents" / "skills")
    directory = repo / ".agents" / "skills" / "escaped"
    directory.mkdir(parents=True)
    try:
        os.symlink(outside / "escaped" / "SKILL.md", directory / "SKILL.md")
    except OSError:
        return
    assert discover_project(str(repo)).descriptors() == []


def test_catalog_load_project_rejects_changed_to_symlink(tmp_path):
    path = write_skill(str(tmp_path), "release", "release", "Release skill.")
    directory = os.path.dirname(path)
    catalog = Catalog([Descriptor(
        name="release", description="Release skill.",
        scope="project", path=directory, valid=True,
    )])
    secret = tmp_path / "secret"
    secret.write_text("secret", encoding="utf-8")
    os.remove(path)
    try:
        os.symlink(secret, path)
    except OSError:
        return
    try:
        catalog.load_project("release", directory)
        assert False, "accepted SKILL.md changed to symlink"
    except ValueError:
        pass


def test_render_summary_keeps_global_root_compact_and_project_paths_explicit():
    got = render_summary(
        [
            Descriptor(name="img", description="Images.", scope="global", valid=True),
            Descriptor(name="notes", description="Notes.", scope="global", valid=True),
        ],
        [Descriptor(
            name="release", description="Release.", scope="project",
            path="/repo/.agents/skills/release", valid=True,
        )],
        "/home/test/.suna/skills",
    )
    assert got.count("/home/test/.suna/skills") == 1
    for want in ("Global Skills", "- img: Images.", "- notes: Notes.", "Project Skills", "/repo/.agents/skills/release"):
        assert want in got


def test_runtime_manual_skill_defaults_enabled(tmp_path):
    write_skill(str(tmp_path), "writer", "writer", "Writing.")
    rt = Runtime(str(tmp_path), MemorySkillStore())
    infos = rt.list()
    assert len(infos) == 1
    assert infos[0].enabled and infos[0].valid
    assert rt.load_content("writer")


def test_runtime_start_check_existing_skill_requires_explicit_enable(tmp_path):
    write_skill(str(tmp_path), "report", "report", "Write reports.")
    store = MemorySkillStore({"report": Record(enabled=False)})
    prompter = FakePrompter([OPTION_REVIEW_NO, OPTION_ENABLE_YES])
    rt = Runtime(str(tmp_path), store)
    rt.set_prompter(prompter)
    rt.start({"action": "check", "name": "report"})
    assert rt.load_content("report")
    assert store.records["report"].enabled
    assert len(prompter.questions) == 2


def test_runtime_import_local_skill_requires_explicit_enable(tmp_path):
    source = os.path.dirname(write_skill(str(tmp_path / "src"), "imported", "imported", "Imported."))
    dest = tmp_path / "skills"
    dest.mkdir()
    rt = Runtime(str(dest), MemorySkillStore())
    res = rt.import_skill(source, "")
    assert res.check.valid
    assert os.path.exists(os.path.join(dest, "imported", "SKILL.md"))
    try:
        rt.load_content("imported")
        assert False, "loaded before explicit enable"
    except ValueError:
        pass


def test_runtime_start_import_runs_workflow(tmp_path):
    source = os.path.dirname(write_skill(str(tmp_path / "src"), "imported", "imported", "Imported."))
    dest = tmp_path / "skills"
    dest.mkdir()
    store = MemorySkillStore()
    prompter = FakePrompter([OPTION_REVIEW_NO, OPTION_ENABLE_YES])
    rt = Runtime(str(dest), store)
    rt.set_prompter(prompter)
    rt.start({"action": "import", "source": source})
    assert store.records["imported"].enabled
    assert len(prompter.questions) == 2


def test_runtime_start_choice_retry(tmp_path):
    write_skill(str(tmp_path), "retry", "retry", "Retry choices.")
    prompter = FakePrompter(["anything", OPTION_REVIEW_NO, "not sure", OPTION_ENABLE_NO])
    rt = Runtime(str(tmp_path), MemorySkillStore({"retry": Record(enabled=False)}))
    rt.set_prompter(prompter)
    rt.start({"action": "check", "name": "retry"})
    assert len(prompter.questions) == 4


def test_runtime_toggle_uses_loaded_catalog_without_rescanning_files(tmp_path):
    path = write_skill(str(tmp_path), "toggle", "toggle", "Original description.")
    store = MemorySkillStore({"toggle": Record(enabled=False)})
    rt = Runtime(str(tmp_path), store)
    rt.reload()
    write_file(path, "---\nname: toggle\ndescription: Changed on disk.\n---\n")
    from myagent.skill.runtime import EnableDecision
    rt.set_enabled(EnableDecision(name="toggle", enabled=True))
    items = rt.enabled_descriptors()
    assert len(items) == 1 and items[0].description == "Original description."
    assert "Changed on disk." in rt.load_content("toggle")


def test_runtime_set_enabled_does_not_run_check(tmp_path):
    write_skill(str(tmp_path), "toggle", "toggle-skill", "Toggle skill.")
    write_file(os.path.join(tmp_path, "toggle", "scripts", "run.sh"), "curl https://example.com\n")
    store = MemorySkillStore({"toggle-skill": Record(enabled=False, reasons=["old reason"])})
    rt = Runtime(str(tmp_path), store)
    rt.reload()
    from myagent.skill.runtime import EnableDecision
    rt.set_enabled(EnableDecision(name="toggle-skill", enabled=True))
    assert store.records["toggle-skill"].enabled
    assert store.records["toggle-skill"].reasons == ["old reason"]


def test_runtime_optional_llm_review(tmp_path):
    write_skill(str(tmp_path), "review", "review", "Review me.")
    reviewer = FakeReviewer("看起来可用，未发现明显风险。")
    rt = Runtime(str(tmp_path), MemorySkillStore())
    rt.set_reviewer(reviewer)
    res = rt.review("review")
    assert res.valid and res.review
    assert reviewer.seen.name == "review"


def test_runtime_review_requires_reviewer(tmp_path):
    rt = Runtime(str(tmp_path), MemorySkillStore())
    try:
        rt.review("missing")
        assert False
    except ValueError:
        pass


def test_runtime_disable_missing_recorded_skill(tmp_path):
    store = MemorySkillStore({"gone": Record(enabled=True, reasons=["old"])})
    rt = Runtime(str(tmp_path), store)
    rt.disable("gone")
    assert not store.records["gone"].enabled


def test_runtime_import_rejects_installed_source(tmp_path):
    installed = os.path.dirname(write_skill(str(tmp_path), "same", "same", "Same."))
    rt = Runtime(str(tmp_path), MemorySkillStore())
    try:
        rt.import_skill(installed, "same")
        assert False
    except ValueError:
        pass
    assert os.path.exists(os.path.join(installed, "SKILL.md"))
