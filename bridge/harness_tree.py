#!/usr/bin/env python3
"""Load and check an editable harness component tree.

Component text lives under bridge/harness/. This module does not import harness2.
The pure-mode phrase list is pure_forbidden.json so this file does not embed it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

_MISSING = object()

REQUIRED_FILES = (
    "harness.json",
    "systemprompt.md",
    "guardrails.md",
    "memory.json",
    "tool_guidance.json",
    "skills.json",
    "plugins.json",
)

COMPONENT_NAMES = (
    "systemprompt",
    "guardrails",
    "memory",
    "tool_guidance",
    "subagents",
    "skills",
    "scripts",
    "plugins",
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DIR = Path(__file__).resolve().parent / "harness" / "base"
_FORBIDDEN_PATH = Path(__file__).resolve().parent / "pure_forbidden.json"


def resolve_harness_dir(directory: str | Path | None = None) -> Path:
    """JEV_HARNESS_DIR wins. Otherwise bridge/harness/base."""
    if directory is not None:
        return Path(directory)
    env = os.environ.get("JEV_HARNESS_DIR", "").strip()
    if not env:
        return _DEFAULT_DIR
    path = Path(env)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path


def load_forbidden(path: str | Path | None = None) -> dict[str, str]:
    src = Path(path) if path is not None else _FORBIDDEN_PATH
    data = json.loads(src.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{src.name} must be a JSON object of pattern to reason")
    out: dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"{src.name} entries must be strings")
        out[key] = value
    return out


def _read_files(directory: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory)
        if any(part.startswith(".") for part in rel.parts):
            continue
        files[rel.as_posix()] = path.read_text(encoding="utf-8")
    return files


class HarnessTree:
    def __init__(self, directory: Path, files: dict[str, str]) -> None:
        self.directory = directory
        self.files = dict(files)
        self.data: dict[str, Any] = {}
        for rel, text in self.files.items():
            path = Path(rel)
            if path.parent != Path("."):
                continue
            if path.suffix == ".json":
                try:
                    self.data[path.stem] = json.loads(text)
                except json.JSONDecodeError:
                    self.data[path.stem] = text
            else:
                self.data[path.stem] = text

    @classmethod
    def load(cls, directory: str | Path | None = None) -> HarnessTree:
        directory = resolve_harness_dir(directory)
        if not directory.is_dir():
            raise FileNotFoundError(f"harness directory not found: {directory}")
        return cls(directory, _read_files(directory))

    def get(self, dotted_path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted_path.split("."):
            if part == "" or not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def text(self, component: str, key: str, /, **fmt: Any) -> str:
        # key is positional-only so a template may contain {key}.
        path = f"{component}.{key}" if key else component
        value = self.get(path, _MISSING)
        if value is _MISSING or not isinstance(value, str):
            raise KeyError(path)
        if not fmt:
            return value
        return value.format(**fmt)

    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for rel in sorted(self.files):
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(self.files[rel].encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()


def validate_tree(tree: dict[str, str]) -> None:
    """Raise ValueError listing every missing component, JSON error, and forbidden hit."""
    problems: list[str] = []
    parsed: dict[str, Any] = {}

    for name in REQUIRED_FILES:
        if name not in tree or not str(tree[name]).strip():
            problems.append(f"missing component: {name}")

    for name in sorted(tree):
        if not name.endswith(".json"):
            continue
        try:
            parsed[name] = json.loads(tree[name])
        except json.JSONDecodeError as exc:
            problems.append(f"json: {name}: {exc.msg} (line {exc.lineno})")

    for name in REQUIRED_FILES:
        if name.endswith(".json") and name in parsed and not isinstance(parsed[name], dict):
            problems.append(f"json: {name}: expected an object")

    harness = parsed.get("harness.json")
    if isinstance(harness, dict):
        if "id" not in harness:
            problems.append("missing component: harness.id")
        if "version" not in harness:
            problems.append("missing component: harness.version")
        components = harness.get("components")
        names = {str(item) for item in components} if isinstance(components, list) else set()
        if not isinstance(components, list):
            problems.append("missing component: harness.components")
        else:
            for component in COMPONENT_NAMES:
                if component not in names:
                    problems.append(f"missing component: {component}")
        unsupported = harness.get("unsupported")
        if not isinstance(unsupported, dict):
            unsupported = {}
        for component in ("subagents", "scripts"):
            reason = unsupported.get(component)
            if not isinstance(reason, str) or not reason.strip():
                problems.append(f"missing component: {component}")

    try:
        forbidden = load_forbidden()
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        problems.append(f"json: pure_forbidden.json: {exc}")
        forbidden = {}

    compiled: list[tuple[re.Pattern[str], str]] = []
    for pattern, why in forbidden.items():
        try:
            compiled.append((re.compile(pattern), why))
        except re.error as exc:
            problems.append(f"json: pure_forbidden.json: {exc}")

    for name in sorted(tree):
        text = tree[name]
        if not isinstance(text, str):
            problems.append(f"json: {name}: expected text")
            continue
        for cre, why in compiled:
            for match in cre.finditer(text):
                line = text[: match.start()].count("\n") + 1
                problems.append(f"forbidden: {name}:{line}: {why}")

    unique: list[str] = []
    seen: set[str] = set()
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            unique.append(problem)
    if unique:
        raise ValueError("harness tree invalid:\n" + "\n".join(f"- {problem}" for problem in unique))
