"""The local .env credentials file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from intentbench.envfile import load_env_file, parse_env, read_env_file

REPO = Path(__file__).resolve().parents[1]


def test_parse_env_handles_what_people_paste() -> None:
    text = """
    # a comment
    ANTHROPIC_API_KEY=sk-ant-plain
    export OPENAI_API_KEY="sk-quoted"
    ANTHROPIC_WORKSPACE_ID='wrkspc_single'   
    EMPTY=
    TRAILING=value # note
    not a line
    """
    assert parse_env(text) == {
        "ANTHROPIC_API_KEY": "sk-ant-plain",
        "OPENAI_API_KEY": "sk-quoted",
        "ANTHROPIC_WORKSPACE_ID": "wrkspc_single",
        "EMPTY": "",
        "TRAILING": "value",
    }


def test_only_known_non_empty_keys_are_read(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-x\nOPENAI_API_KEY=\nPATH=/evil\n", encoding="utf-8")
    assert read_env_file(env) == {"ANTHROPIC_API_KEY": "sk-ant-x"}


def test_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope") == []


def test_loads_from_the_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Registered with monkeypatch so the value load_env_file sets is undone.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-cwd\n", encoding="utf-8")
    assert load_env_file() == ["ANTHROPIC_API_KEY"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-cwd"


def test_exported_variables_win(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-exported")
    env = tmp_path / ".env"
    env.write_text("ANTHROPIC_API_KEY=sk-ant-file\n", encoding="utf-8")
    assert load_env_file(env) == []
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-exported"


def test_the_template_holds_no_values_and_covers_every_key() -> None:
    from intentbench.envfile import KNOWN_KEYS

    template = parse_env((REPO / ".env.example").read_text(encoding="utf-8"))
    assert set(template) == KNOWN_KEYS
    assert all(value == "" for value in template.values())


def test_env_is_gitignored_and_the_template_is_not() -> None:
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines
    assert "!.env.example" in lines
