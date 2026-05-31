"""Host-safe tests for app.skills.loader.

``save_fn`` is injected everywhere so the real registry JSON in workspace/ is
never touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills import loader as L

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _recording_save():
    calls = []

    def save(name, task_template, *, description="", force_tier=None,
             extra_tools=None, task_hint=""):
        rec = {
            "name": name, "task_template": task_template,
            "description": description, "force_tier": force_tier,
            "extra_tools": list(extra_tools or []), "task_hint": task_hint,
        }
        calls.append(rec)
        return rec

    return save, calls


# ── parse_front_matter ──────────────────────────────────────────────────────


def test_front_matter_basic_scalars_and_body():
    text = "---\nname: foo\ndescription: a thing\n---\nthe body {x}"
    meta, body = L.parse_front_matter(text)
    assert meta == {"name": "foo", "description": "a thing"}
    assert body == "the body {x}"


def test_front_matter_block_list():
    text = "---\nextra_tools:\n  - web_search\n  - pdf_compose\n---\nbody"
    meta, body = L.parse_front_matter(text)
    assert meta["extra_tools"] == ["web_search", "pdf_compose"]


def test_front_matter_inline_list_and_quotes():
    text = '---\nextra_tools: [web_search, "pdf_compose"]\nname: "q"\n---\nb'
    meta, _ = L.parse_front_matter(text)
    assert meta["extra_tools"] == ["web_search", "pdf_compose"]
    assert meta["name"] == "q"


def test_front_matter_absent_returns_whole_body():
    text = "no fence here\njust text"
    meta, body = L.parse_front_matter(text)
    assert meta == {}
    assert body == "no fence here\njust text"


def test_front_matter_unterminated_fence_is_body():
    text = "---\nname: foo\nno closing fence"
    meta, body = L.parse_front_matter(text)
    assert meta == {}
    assert "no closing fence" in body


# ── parse_skill_md ──────────────────────────────────────────────────────────


def test_parse_skill_md_full():
    text = (
        "---\nname: lit\ndescription: d\nforce_tier: mid\ntask_hint: research\n"
        "extra_tools:\n  - web_search\n---\nReview {topic} in {length} paras"
    )
    kw = L.parse_skill_md(text)
    assert kw == {
        "name": "lit", "task_template": "Review {topic} in {length} paras",
        "description": "d", "force_tier": "mid",
        "extra_tools": ["web_search"], "task_hint": "research",
    }


def test_parse_skill_md_empty_body_raises():
    with pytest.raises(ValueError):
        L.parse_skill_md("---\nname: x\n---\n   ")


def test_parse_skill_md_missing_name_raises():
    with pytest.raises(ValueError):
        L.parse_skill_md("just a body, no front-matter and no default name")


def test_parse_skill_md_default_name_fallback():
    kw = L.parse_skill_md("a plain body", default_name="from-file")
    assert kw["name"] == "from-file"
    assert kw["task_template"] == "a plain body"


def test_parse_skill_md_scalar_tool_coerced_to_list():
    kw = L.parse_skill_md("---\nname: x\nextra_tools: web_search\n---\nbody")
    assert kw["extra_tools"] == ["web_search"]


def test_parse_skill_md_empty_force_tier_is_none():
    kw = L.parse_skill_md("---\nname: x\nforce_tier:\n---\nbody")
    assert kw["force_tier"] is None


# ── load_skill_md ───────────────────────────────────────────────────────────


def test_load_skill_md_persists_via_save_fn(tmp_path):
    f = tmp_path / "my-skill.md"
    f.write_text("---\nname: greet\ntask_hint: chat\n---\nSay hi to {who}", encoding="utf-8")
    save, calls = _recording_save()
    result = L.load_skill_md(f, save_fn=save)
    assert len(calls) == 1
    assert calls[0]["name"] == "greet"
    assert calls[0]["task_template"] == "Say hi to {who}"
    assert calls[0]["task_hint"] == "chat"
    assert result is calls[0]


def test_load_skill_md_name_from_filename(tmp_path):
    f = tmp_path / "morning-briefing.md"
    f.write_text("---\ntask_hint: ops\n---\nBrief me on {date}", encoding="utf-8")
    save, calls = _recording_save()
    L.load_skill_md(f, save_fn=save)
    assert calls[0]["name"] == "morning-briefing"


# ── load_skills_dir ─────────────────────────────────────────────────────────


def test_load_skills_dir_skips_non_skill_and_bad_files(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\n---\nbody a {x}", encoding="utf-8")
    (tmp_path / "b.md").write_text("---\nname: b\n---\nbody b", encoding="utf-8")
    # No front-matter → skipped (e.g. a README).
    (tmp_path / "README.md").write_text("# Skills\nThis folder holds skills.", encoding="utf-8")
    # Front-matter but empty body → parse error → skipped, batch continues.
    (tmp_path / "broken.md").write_text("---\nname: broken\n---\n", encoding="utf-8")

    save, calls = _recording_save()
    loaded = L.load_skills_dir(tmp_path, save_fn=save)
    names = sorted(c["name"] for c in calls)
    assert names == ["a", "b"]
    assert len(loaded) == 2


def test_load_skills_dir_missing_dir_returns_empty(tmp_path):
    save, _ = _recording_save()
    assert L.load_skills_dir(tmp_path / "nope", save_fn=save) == []


# ── real example file round-trips ───────────────────────────────────────────


def test_repo_example_literature_review_parses():
    path = _REPO_ROOT / "skills" / "literature-review.md"
    save, calls = _recording_save()
    L.load_skill_md(path, save_fn=save)
    rec = calls[0]
    assert rec["name"] == "literature-review"
    assert rec["task_hint"] == "research"
    assert rec["force_tier"] == "mid"
    assert set(rec["extra_tools"]) == {"web_search", "pdf_compose"}
    # placeholders survive into the template for registry.save_skill to extract
    assert "{topic}" in rec["task_template"]
    assert "{length}" in rec["task_template"]
