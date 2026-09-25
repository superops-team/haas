from pathlib import Path

import pytest

from scripts.quality.check_agent_skills import main, validate_skill_dir, validate_skills_root


def write_skill(
    root: Path,
    *,
    directory: str = "sample-skill",
    name: str = "sample-skill",
    frontmatter_extra: str = "",
    body: str = "# Sample Skill\n\nFollow the workflow.\n",
    with_openai_yaml: bool = True,
) -> Path:
    skill_dir = root / directory
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Validate sample skills. Use when testing the skill validator.\n"
        f"{frontmatter_extra}"
        "---\n\n"
        f"{body}",
        encoding="utf-8",
    )
    if with_openai_yaml:
        agents_dir = skill_dir / "agents"
        agents_dir.mkdir()
        (agents_dir / "openai.yaml").write_text(
            "interface:\n"
            '  display_name: "Sample Skill"\n'
            '  short_description: "Validate repository skill structure"\n'
            '  default_prompt: "Use $sample-skill to validate this skill folder."\n',
            encoding="utf-8",
        )
    return skill_dir


def test_accepts_a_compliant_skill(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)

    assert validate_skill_dir(skill_dir) == []


def test_rejects_extra_frontmatter_keys(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, frontmatter_extra="version: 1.0.0\n")

    assert any(
        "frontmatter keys must be exactly" in error for error in validate_skill_dir(skill_dir)
    )


def test_rejects_duplicate_frontmatter_keys(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, frontmatter_extra="name: sample-skill\n")

    assert any(
        "duplicate frontmatter keys: name" in error for error in validate_skill_dir(skill_dir)
    )


def test_rejects_invalid_yaml_frontmatter(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: sample-skill\n"
        "description: Invalid YAML: an unquoted colon breaks the value.\n"
        "---\n\n"
        "# Sample Skill\n",
        encoding="utf-8",
    )

    assert any("invalid YAML frontmatter" in error for error in validate_skill_dir(skill_dir))


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (None, "missing SKILL.md"),
        ("# Missing frontmatter\n", "must start with YAML frontmatter"),
        ("---\nname: sample-skill\n", "frontmatter is not closed"),
        ("---\n[]\n---\n", "frontmatter must be a mapping"),
    ],
)
def test_rejects_missing_or_malformed_skill_file(
    tmp_path: Path, content: str | None, message: str
) -> None:
    skill_dir = tmp_path / "sample-skill"
    skill_dir.mkdir()
    if content is not None:
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    assert any(message in error for error in validate_skill_dir(skill_dir))


def test_rejects_directory_name_mismatch(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, name="different-name")

    assert any("must match directory name" in error for error in validate_skill_dir(skill_dir))


def test_rejects_description_without_usage_trigger(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: sample-skill\n"
        "description: Validate sample skill structure.\n"
        "---\n\n"
        "# Sample Skill\n",
        encoding="utf-8",
    )

    assert any(
        "description must say when to use the skill" in error
        for error in validate_skill_dir(skill_dir)
    )


def test_rejects_description_with_angle_brackets(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: sample-skill\n"
        "description: Validate <input>. Use when testing the skill validator.\n"
        "---\n\n"
        "# Sample Skill\n",
        encoding="utf-8",
    )

    assert any(
        "description cannot contain angle brackets" in error
        for error in validate_skill_dir(skill_dir)
    )


def test_rejects_invalid_name_and_description_limits(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: Invalid_Name\n"
        f"description: {'x' * 1025} Use when validation is required.\n"
        "---\n\n"
        "# Sample Skill\n",
        encoding="utf-8",
    )

    errors = validate_skill_dir(skill_dir)

    assert any("name must use lowercase kebab-case" in error for error in errors)
    assert any("description must not exceed 1024 characters" in error for error in errors)


def test_rejects_name_over_64_characters(tmp_path: Path) -> None:
    name = "a" * 65
    skill_dir = write_skill(tmp_path, directory=name, name=name)

    assert any(
        "name must not exceed 64 characters" in error for error in validate_skill_dir(skill_dir)
    )


def test_rejects_missing_openai_metadata(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, with_openai_yaml=False)

    assert any("missing agents/openai.yaml" in error for error in validate_skill_dir(skill_dir))


def test_rejects_oversized_skill_body(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path, body="\n".join(["Do the work."] * 501))

    assert any("body exceeds 500 lines" in error for error in validate_skill_dir(skill_dir))


def test_rejects_broken_relative_markdown_link(tmp_path: Path) -> None:
    skill_dir = write_skill(
        tmp_path, body="# Sample Skill\n\nRead [details](references/missing.md).\n"
    )

    assert any("broken relative link" in error for error in validate_skill_dir(skill_dir))


def test_ignores_output_links_in_assets(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    assets_dir = skill_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "template.md").write_text(
        "![Generated output](screenshots/result.png)\n", encoding="utf-8"
    )

    assert validate_skill_dir(skill_dir) == []


def test_accepts_supported_relative_and_external_links(tmp_path: Path) -> None:
    skill_dir = write_skill(
        tmp_path,
        body=(
            "# Sample Skill\n\n"
            "[angle](<references/existing.md>) "
            '[title](references/existing.md "Details") '
            "[anchor](#section) [external](https://example.com).\n"
        ),
    )
    references = skill_dir / "references"
    references.mkdir()
    (references / "existing.md").write_text("# Existing\n", encoding="utf-8")

    assert validate_skill_dir(skill_dir) == []


def test_rejects_extraneous_root_files_and_directories(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "README.md").write_text("extra\n", encoding="utf-8")
    (skill_dir / "templates").mkdir()

    errors = validate_skill_dir(skill_dir)

    assert any("unsupported root entry: README.md" in error for error in errors)
    assert any("unsupported root entry: templates" in error for error in errors)


def test_rejects_invalid_bundled_shell_script(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "broken.sh").write_text("if then\n", encoding="utf-8")

    assert any("invalid shell syntax" in error for error in validate_skill_dir(skill_dir))


def test_rejects_invalid_openai_metadata(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "agents" / "openai.yaml").write_text(
        "interface:\n"
        '  display_name: "Sample Skill"\n'
        '  short_description: "Too short"\n'
        '  default_prompt: "Validate this skill folder."\n',
        encoding="utf-8",
    )

    errors = validate_skill_dir(skill_dir)

    assert any("short_description must contain 25-64 characters" in error for error in errors)
    assert any("default_prompt must mention $sample-skill" in error for error in errors)


def test_rejects_redundant_true_implicit_invocation_policy(tmp_path: Path) -> None:
    skill_dir = write_skill(tmp_path)
    with (skill_dir / "agents" / "openai.yaml").open("a", encoding="utf-8") as stream:
        stream.write("policy:\n  allow_implicit_invocation: true\n")

    assert any(
        "allow_implicit_invocation should be omitted unless false" in error
        for error in validate_skill_dir(skill_dir)
    )


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ("interface: [\n", "invalid agents/openai.yaml"),
        ("[]\n", "agents/openai.yaml must be a mapping"),
        ('interface: "invalid"\n', "interface must be a mapping"),
        (
            'interface:\n  display_name: "Sample Skill"\n  short_description: 42\n',
            "missing interface fields: default_prompt",
        ),
        (
            "interface:\n"
            "  display_name: Sample Skill\n"
            "  short_description: Validate repository skill structure\n"
            "  default_prompt: Use $sample-skill to validate this skill folder.\n",
            "quote interface string fields",
        ),
        (
            "interface:\n"
            '  display_name: "Sample Skill"\n'
            '  short_description: "Validate repository skill structure"\n'
            '  default_prompt: "Use $sample-skill to validate this skill folder."\n'
            'policy: "invalid"\n',
            "policy must be a mapping",
        ),
        (
            "interface:\n"
            '  display_name: "Sample Skill"\n'
            '  short_description: "Validate repository skill structure"\n'
            '  default_prompt: "Use $sample-skill to validate this skill folder."\n'
            "policy:\n"
            '  allow_implicit_invocation: "false"\n',
            "policy.allow_implicit_invocation must be a boolean",
        ),
    ],
)
def test_rejects_malformed_openai_metadata(tmp_path: Path, metadata: str, message: str) -> None:
    skill_dir = write_skill(tmp_path)
    (skill_dir / "agents" / "openai.yaml").write_text(metadata, encoding="utf-8")

    assert any(message in error for error in validate_skill_dir(skill_dir))


def test_validates_a_skills_root_and_cli_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_skill(tmp_path)

    count, errors = validate_skills_root(tmp_path)
    assert count == 1
    assert errors == []

    monkeypatch.setattr("sys.argv", ["check_agent_skills.py", str(tmp_path)])
    assert main() == 0
    assert "passed for 1 skill(s)" in capsys.readouterr().out


def test_rejects_a_missing_skills_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"

    count, errors = validate_skills_root(missing)
    assert count == 0
    assert any("skills root does not exist" in error for error in errors)

    monkeypatch.setattr("sys.argv", ["check_agent_skills.py", str(missing)])
    assert main() == 1
    assert "validation failed" in capsys.readouterr().err
