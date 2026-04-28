"""Unit tests for skillctl.eval.init — eval scaffold generation."""

from __future__ import annotations


from skillctl.eval.init import generate_eval_scaffold


def test_eval_init_generates_skilleval_yaml(tmp_path):
    """generate_eval_scaffold should create a .skilleval.yaml file."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: Test\n---\n\nBody")

    generate_eval_scaffold(str(skill_dir))

    assert (skill_dir / ".skilleval.yaml").exists()
    content = (skill_dir / ".skilleval.yaml").read_text()
    assert "min_score: 70" in content
    assert "ignore:" in content
    assert "safe_domains:" in content


def test_eval_init_generates_all_files(tmp_path):
    """generate_eval_scaffold should create evals.json, eval_queries.json, and .skilleval.yaml."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\ndescription: My skill\n---\n\nContent")

    result = generate_eval_scaffold(str(skill_dir))

    assert result == 0
    assert (skill_dir / "evals" / "evals.json").exists()
    assert (skill_dir / "evals" / "eval_queries.json").exists()
    assert (skill_dir / ".skilleval.yaml").exists()


def test_eval_init_skips_existing_skilleval_yaml(tmp_path):
    """generate_eval_scaffold should skip .skilleval.yaml if it already exists."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: Test\n---\n\nBody")

    # Pre-create the file
    existing_content = "# existing config\nmin_score: 90\n"
    (skill_dir / ".skilleval.yaml").write_text(existing_content)

    generate_eval_scaffold(str(skill_dir))

    # Should not be overwritten
    assert (skill_dir / ".skilleval.yaml").read_text() == existing_content


def test_eval_init_skilleval_yaml_contains_skill_name(tmp_path):
    """The generated .skilleval.yaml should reference the skill name."""
    skill_dir = tmp_path / "cool-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: cool-skill\ndescription: A cool skill\n---\n\nBody")

    generate_eval_scaffold(str(skill_dir))

    content = (skill_dir / ".skilleval.yaml").read_text()
    assert "cool-skill" in content
    assert "overrides:" in content
