"""Ready-made wordings for secrets in records (TASK-085, operator decision 2026-11-27).

The gate now refuses length, hash and prefix and allows only the fact of setup. That leaves the
agent with a problem a rule alone does not solve: what exactly may it write? Left to invent, it
writes something plausible that the scanner refuses on the next read — and then "the check is
broken" is the reasonable conclusion.

So the wordings live in the plugin, as a template the agent copies. This file pins three things:

* the template exists and is pointed at from ``SKILL.md`` (a template nobody is sent to is a file
  nobody opens);
* **every** phrase it teaches passes the scanner — checked by running the scanner over the
  template itself, not over a hand-picked sample;
* the forbidden forms are named next to the allowed ones, so an agent that adapts a phrase knows
  where the line is.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_SKILL = jrnl.SKILL_DIR
_HOOKS = jrnl.HOOKS
_SECRETS = _HOOKS / "bugfixer_secrets.py"
_TEMPLATE = _SKILL / "templates" / "secrets-in-records.md"
_SKILL_MD = _SKILL / "SKILL.md"


def _load(name: str, path: Path):
    """Import a module from a file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def scanner():
    """The secret scanner."""
    return _load("bugfixer_secrets_wordings", _SECRETS)


# --- the template and its reach ---------------------------------------------


def test_the_template_exists() -> None:
    """A file the skill cannot point at is a file nobody copies."""
    assert _TEMPLATE.is_file(), f"{_TEMPLATE} is missing"


def test_the_skill_points_at_the_template() -> None:
    """`SKILL.md` is what the agent reads; a template it never mentions is dead weight."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    assert "templates/secrets-in-records.md" in text, (
        "the skill does not send the agent to the wordings, so it will invent its own"
    )


def test_every_file_the_skill_points_at_still_exists() -> None:
    """The reference must resolve in an installed tree, not only in this checkout."""
    text = _SKILL_MD.read_text(encoding="utf-8")
    referenced = set(re.findall(r"`((?:references|templates)/[^`]+)`", text))
    missing = sorted(rel for rel in referenced if not (_SKILL / rel).is_file())
    assert not missing, f"SKILL.md references missing files: {missing}"


# --- every taught phrase must pass the gate ----------------------------------


def _taught_phrases() -> list[str]:
    """Lines the template presents as allowed.

    The template marks them in a fenced block labelled ``allowed``; anything else in the file
    (prose, the forbidden table) is not a phrase the agent is meant to copy.

    Returns:
        Non-empty stripped lines from the allowed blocks.
    """
    text = _TEMPLATE.read_text(encoding="utf-8")
    blocks = re.findall(r"```(?:text|markdown) allowed\n(.*?)```", text, re.DOTALL)
    assert blocks, "the template has no block labelled `allowed` to copy from"
    lines: list[str] = []
    for block in blocks:
        lines.extend(line.rstrip() for line in block.splitlines() if line.strip())
    return lines


def test_the_template_teaches_something(scanner) -> None:
    """A guard over an empty list passes vacuously; this proves there is material."""
    phrases = _taught_phrases()
    assert len(phrases) >= 8, f"only {len(phrases)} taught phrases; the list looks truncated"


def test_no_taught_phrase_is_refused(scanner) -> None:
    """The test that matters: the scanner runs over the template, not over a sample of it.

    An agent that copies a phrase and is then refused learns the opposite of the lesson: it
    concludes the check is unreliable and works around it.
    """
    refused = [
        (line, [finding.rule for finding in scanner.scan(line)])
        for line in _taught_phrases()
        if scanner.scan(line)
    ]
    assert not refused, f"the template teaches phrases the gate refuses: {refused}"


def test_the_forbidden_forms_are_named_beside_the_allowed_ones() -> None:
    """An agent adapting a phrase has to see where the line is, not infer it from a refusal."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    for form in ("len=", "sha256", "prefix", "sk-"):
        assert form in text, f"the template never shows the forbidden form {form!r}"


def test_the_forbidden_examples_are_marked_as_such(scanner) -> None:
    """And the guard is not symmetric by accident: the forbidden ones really are refused.

    Without this, a template that showed `api_key: len=51` as allowed would pass the test above
    only if the scanner were broken — the two tests together pin both directions.
    """
    text = _TEMPLATE.read_text(encoding="utf-8")
    blocks = re.findall(r"```(?:text|markdown) forbidden\n(.*?)```", text, re.DOTALL)
    assert blocks, "the template has no block labelled `forbidden`"
    lines = [line.strip() for block in blocks for line in block.splitlines() if line.strip()]
    assert lines, "the forbidden block is empty"
    allowed_through = [line for line in lines if not scanner.scan(line)]
    assert not allowed_through, f"these are shown as forbidden but pass: {allowed_through}"


def test_the_record_markers_stay_copyable() -> None:
    """`Secrets scan: clean` is what step 4.9 records; the wordings must not bury it."""
    text = _TEMPLATE.read_text(encoding="utf-8")
    assert "Secrets scan: clean" in text
