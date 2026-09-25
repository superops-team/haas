#!/usr/bin/env python3
"""Validate repository-local agent skills without reading external state."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

FRONTMATTER_KEYS = {"name", "description"}
ROOT_ENTRIES = {"SKILL.md", "agents", "assets", "references", "scripts"}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
TRIGGER_MARKERS = (
    "use when",
    "triggers when",
    "used when",
    "用于",
    "适用场景",
    "当用户",
    "用户要求",
    "用户需要",
)
QUOTED_INTERFACE_FIELD = re.compile(
    r'^\s{2}(display_name|short_description|default_prompt):\s*(["\']).*\2\s*$'
)


def _load_yaml(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        errors.append(f"{path}: invalid {label}: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path}: {label} must be a mapping")
        return None
    return value


def _load_skill(skill_dir: Path, errors: list[str]) -> tuple[dict[str, Any], str] | None:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        errors.append(f"{skill_dir}: missing SKILL.md")
        return None

    try:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        errors.append(f"{skill_file}: cannot read file: {exc}")
        return None

    if not lines or lines[0] != "---":
        errors.append(f"{skill_file}: SKILL.md must start with YAML frontmatter")
        return None
    try:
        closing_index = lines.index("---", 1)
    except ValueError:
        errors.append(f"{skill_file}: YAML frontmatter is not closed")
        return None

    frontmatter_text = "\n".join(lines[1:closing_index])
    root_keys = re.findall(r"^([A-Za-z0-9_-]+):", frontmatter_text, flags=re.MULTILINE)
    duplicate_keys = sorted({key for key in root_keys if root_keys.count(key) > 1})
    if duplicate_keys:
        errors.append(f"{skill_file}: duplicate frontmatter keys: {', '.join(duplicate_keys)}")
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        errors.append(f"{skill_file}: invalid YAML frontmatter: {exc}")
        return None
    if not isinstance(frontmatter, dict):
        errors.append(f"{skill_file}: YAML frontmatter must be a mapping")
        return None

    body_lines = lines[closing_index + 1 :]
    if len(body_lines) > 500:
        errors.append(f"{skill_file}: body exceeds 500 lines ({len(body_lines)})")
    return frontmatter, "\n".join(body_lines)


def _validate_frontmatter(
    skill_dir: Path, frontmatter: dict[str, Any], errors: list[str]
) -> str | None:
    keys = set(frontmatter)
    if keys != FRONTMATTER_KEYS:
        errors.append(
            f"{skill_dir / 'SKILL.md'}: frontmatter keys must be exactly "
            f"name and description; found {', '.join(sorted(str(key) for key in keys)) or 'none'}"
        )

    name_value = frontmatter.get("name")
    name: str | None = None
    if not isinstance(name_value, str) or not NAME_PATTERN.fullmatch(name_value):
        errors.append(f"{skill_dir / 'SKILL.md'}: name must use lowercase kebab-case")
    else:
        name = name_value
        if len(name) > 64:
            errors.append(f"{skill_dir / 'SKILL.md'}: name must not exceed 64 characters")
        if name != skill_dir.name:
            errors.append(
                f"{skill_dir / 'SKILL.md'}: name {name!r} must match directory name "
                f"{skill_dir.name!r}"
            )

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{skill_dir / 'SKILL.md'}: description must be a non-empty string")
    else:
        if len(description) > 1024:
            errors.append(f"{skill_dir / 'SKILL.md'}: description must not exceed 1024 characters")
        if "<" in description or ">" in description:
            errors.append(f"{skill_dir / 'SKILL.md'}: description cannot contain angle brackets")
        if not any(marker in description.lower() for marker in TRIGGER_MARKERS):
            errors.append(f"{skill_dir / 'SKILL.md'}: description must say when to use the skill")
    return name


def _validate_openai_yaml(skill_dir: Path, name: str | None, errors: list[str]) -> None:
    metadata_file = skill_dir / "agents" / "openai.yaml"
    if not metadata_file.is_file():
        errors.append(f"{skill_dir}: missing agents/openai.yaml")
        return

    metadata = _load_yaml(metadata_file, "agents/openai.yaml", errors)
    if metadata is None:
        return
    interface = metadata.get("interface")
    if not isinstance(interface, dict):
        errors.append(f"{metadata_file}: interface must be a mapping")
        return

    required_fields = {"display_name", "short_description", "default_prompt"}
    missing = required_fields - set(interface)
    if missing:
        errors.append(f"{metadata_file}: missing interface fields: {', '.join(sorted(missing))}")

    for field in sorted(required_fields):
        value = interface.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{metadata_file}: interface.{field} must be a non-empty string")

    short_description = interface.get("short_description")
    if isinstance(short_description, str) and not 25 <= len(short_description) <= 64:
        errors.append(
            f"{metadata_file}: short_description must contain 25-64 characters "
            f"(found {len(short_description)})"
        )

    default_prompt = interface.get("default_prompt")
    if name and isinstance(default_prompt, str) and f"${name}" not in default_prompt:
        errors.append(f"{metadata_file}: default_prompt must mention ${name}")

    quoted_fields = {
        match.group(1)
        for line in metadata_file.read_text(encoding="utf-8").splitlines()
        if (match := QUOTED_INTERFACE_FIELD.fullmatch(line))
    }
    unquoted = required_fields - quoted_fields
    if unquoted:
        errors.append(
            f"{metadata_file}: quote interface string fields: {', '.join(sorted(unquoted))}"
        )

    policy = metadata.get("policy")
    if policy is not None:
        if not isinstance(policy, dict):
            errors.append(f"{metadata_file}: policy must be a mapping")
        elif "allow_implicit_invocation" in policy:
            implicit_invocation = policy["allow_implicit_invocation"]
            if not isinstance(implicit_invocation, bool):
                errors.append(
                    f"{metadata_file}: policy.allow_implicit_invocation must be a boolean"
                )
            elif implicit_invocation:
                errors.append(
                    f"{metadata_file}: allow_implicit_invocation should be omitted unless false"
                )


def _validate_root_layout(skill_dir: Path, errors: list[str]) -> None:
    for entry in sorted(skill_dir.iterdir(), key=lambda item: item.name):
        if entry.name not in ROOT_ENTRIES:
            errors.append(f"{skill_dir}: unsupported root entry: {entry.name}")


def _validate_markdown_links(skill_dir: Path, errors: list[str]) -> None:
    markdown_files = [skill_dir / "SKILL.md"]
    references_dir = skill_dir / "references"
    if references_dir.is_dir():
        markdown_files.extend(sorted(references_dir.rglob("*.md")))

    for markdown_file in markdown_files:
        try:
            content = markdown_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{markdown_file}: cannot read file: {exc}")
            continue
        for raw_target in MARKDOWN_LINK_PATTERN.findall(content):
            target = raw_target.strip()
            if not target or target.startswith(
                ("#", "http://", "https://", "mailto:", "file:", "data:")
            ):
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            else:
                target = target.split(maxsplit=1)[0]
            target = unquote(target.split("#", 1)[0])
            if not target:
                continue
            resolved = (markdown_file.parent / target).resolve()
            if not resolved.exists():
                errors.append(f"{markdown_file}: broken relative link: {raw_target}")


def _validate_shell_scripts(skill_dir: Path, errors: list[str]) -> None:
    for script_file in sorted(skill_dir.rglob("*.sh")):
        result = subprocess.run(
            ["bash", "-n", str(script_file)], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            diagnostic = result.stderr.strip().splitlines()[-1]
            errors.append(f"{script_file}: invalid shell syntax: {diagnostic}")


def validate_skill_dir(skill_dir: Path) -> list[str]:
    errors: list[str] = []
    loaded = _load_skill(skill_dir, errors)
    name: str | None = None
    if loaded is not None:
        frontmatter, _body = loaded
        name = _validate_frontmatter(skill_dir, frontmatter, errors)
    _validate_openai_yaml(skill_dir, name, errors)
    _validate_root_layout(skill_dir, errors)
    _validate_markdown_links(skill_dir, errors)
    _validate_shell_scripts(skill_dir, errors)
    return errors


def validate_skills_root(skills_root: Path) -> tuple[int, list[str]]:
    if not skills_root.is_dir():
        return 0, [f"{skills_root}: skills root does not exist"]

    skill_dirs = sorted(path for path in skills_root.iterdir() if path.is_dir())
    errors: list[str] = []
    for skill_dir in skill_dirs:
        errors.extend(validate_skill_dir(skill_dir))
    return len(skill_dirs), errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "skills_root",
        nargs="?",
        type=Path,
        default=Path(".agents/skills"),
        help="Directory containing repository-local skills (default: .agents/skills)",
    )
    args = parser.parse_args()

    count, errors = validate_skills_root(args.skills_root)
    if errors:
        print(f"Agent skill validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"Agent skill validation passed for {count} skill(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
