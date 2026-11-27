"""Secrets: a value and anything derived from it are leaks; the fact of setup is not.

Spec §10 was changed on 2026-11-27 by operator decision. It used to allow "length, type and hash"
as a safe way to describe a secret; that advice was wrong — a length narrows a brute-force search
and a hash can be checked offline — and it contradicted the gate, which refused both while the
norm said they were fine.

The boundary now:

* **forbidden** — the value, its length, its size, its hash, its prefix/suffix, a mask;
* **allowed, and required** — whether the secret is configured at all. An operator has to be able
  to read "the key is set" without being handed anything that helps recover it.

Every case below is paired: a forbidden form next to the allowed form that must keep working, so a
scanner that simply refuses more cannot pass.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import bugfixer_helpers as jrnl
import pytest

_HOOKS = jrnl.HOOKS
_SECRETS = _HOOKS / "bugfixer_secrets.py"


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
    return _load("bugfixer_secrets_derived", _SECRETS)


def _refused(scanner, line: str) -> bool:
    """Whether the line is refused.

    Args:
        scanner: The scanner module.
        line: One line of text.

    Returns:
        ``True`` when at least one finding exists — every finding refuses now.
    """
    return bool(scanner.scan(line))


# --- forbidden: anything derived from the value ------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "api_key: len=51",
        "api_key_len=51",
        "api_key_length: 51",
        "token: length=120",
        "password_size=32",
        "token_sha256=9f2c8b1a",
        "api_key: sha256=deadbeef",
        "secret_hash=abc123",
        "api_key_digest: 0f1e2d",
        "token_md5=5d41402abc4b2a76b9719d911017c592",
        "api_key_prefix=sk-proj",
        "token_suffix=9f2c",
        "api_key: bytes=51",
    ],
)
def test_a_derived_characteristic_is_refused(scanner, line: str) -> None:
    """Length, hash, size and prefix all narrow the search space, so all are leaks.

    Measured before the change: `api_key: len=51` was refused by the code and **allowed** by the
    norm. The norm was wrong, and it is the side that moved.
    """
    assert _refused(scanner, line), f"{line!r} is a derived characteristic and must be refused"


def test_the_derived_rule_names_itself(scanner) -> None:
    """A refusal has to say which rule fired, or the agent cannot tell what to remove."""
    findings = scanner.scan("api_key_len=51")
    assert findings
    assert findings[0].rule == "derived"


def test_a_value_is_still_refused(scanner) -> None:
    """The original rule keeps working: a literal key is a leak."""
    findings = scanner.scan("api_key: sk-abcdef1234567890abcdef")
    assert findings
    assert findings[0].rule == "openai"


# --- allowed: the fact of setup, and only that -------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "api_key: configured",
        "api_key: configured=false",
        "api_key_configured=yes",
        "provider configured=yes",
        "password_present=true",
        "password_present=false",
        "token: set",
        "token: unset",
        "credentials_found=no",
        "secret: absent",
        "api_key: enabled",
    ],
)
def test_the_fact_of_setup_is_allowed(scanner, line: str) -> None:
    """The operator has to be told whether a key is configured; that is the point of the rule.

    This is the case the old advisory pattern warned about. Warning here was wrong in both
    directions: it made an allowed report look suspicious, and it missed `api_key: configured`,
    which spells the same fact differently.
    """
    assert not _refused(scanner, line), f"{line!r} reports setup, not a secret"


def test_a_harmless_flag_is_still_silent(scanner) -> None:
    """`cache_present=true` is not about a secret at all and must not even be reported."""
    assert scanner.scan("cache_present=true") == []


def test_an_env_var_reference_is_not_a_secret(scanner) -> None:
    """`password: ${DB_PASSWORD}` is configuration, not a leak."""
    assert not _refused(scanner, 'password: "${DB_PASSWORD}"')


def test_a_null_value_is_still_allowed(scanner) -> None:
    """`token: null` says there is no token."""
    assert not _refused(scanner, "token: null")


# --- there is no advisory level any more -------------------------------------


def test_the_scanner_has_no_advisory_level(scanner) -> None:
    """The only thing it warned about was the fact of setup, which is now allowed outright.

    Keeping a warning level would mean every allowed report still looks like a finding — and the
    gate would keep telling the operator "suspicious" about the one thing the spec requires.
    """
    assert not hasattr(scanner, "ADVISORY_PATTERNS"), (
        "the advisory level is gone; setup facts are allowed, not warned about"
    )


def test_the_finding_type_no_longer_carries_an_advisory_flag(scanner) -> None:
    """A field that nothing can set is a field a caller will misread."""
    finding = scanner.scan("api_key: sk-abcdef1234567890abcdef")[0]
    assert not hasattr(finding, "advice_only")


# --- the CLI and the gate agree ---------------------------------------------


def test_the_cli_is_clean_on_a_setup_fact(tmp_path: Path) -> None:
    """Step 4.6a runs this command; it must not fail on the report the skill asks for."""
    path = tmp_path / "clean.txt"
    path.write_text("api_key: configured\nprovider configured=yes\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SECRETS), "--text", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == "", "an allowed report must be silent"


def test_the_cli_refuses_a_length(tmp_path: Path) -> None:
    """And the other half: the number that used to be recommended is now a refusal."""
    path = tmp_path / "dirty.txt"
    path.write_text("api_key_len=51\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_SECRETS), "--text", str(path)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "derived" in result.stderr
