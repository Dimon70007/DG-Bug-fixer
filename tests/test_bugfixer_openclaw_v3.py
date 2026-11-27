"""DoD-6: the OpenClaw manifest and adapter, written from the vendor's documented shape.

OpenClaw cannot be run on this machine, so nothing here proves it *executed*. What is proved is
what can be: the manifest is a valid native manifest, and the adapter turns the gate's `deny` into
`{ block: true, blockReason: <non-empty> }` — the one shape OpenClaw documents as terminal.

The distinction is the point of the requirement, not a hedge. A test that claimed "works in
OpenClaw" without a machine would be the failure §0 names: a check that cannot fail reported as a
success. So the gap is written into the code and into `SKILL.md`/`README`, and `README` says plainly
that execution was not observed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import bugfixer_helpers as helpers

_SKILL = helpers.SKILL_DIR
_HOOKS = _SKILL / "hooks"
_MANIFEST = _SKILL / "openclaw.plugin.json"
_ADAPTER = _HOOKS / "openclaw-plugin.ts"
_README = _SKILL / "README.md"


def _manifest() -> dict[str, object]:
    """The native manifest, parsed.

    Returns:
        Parsed manifest object.

    Raises:
        AssertionError: When the file is missing — absence is the defect DoD-6 names.
    """
    assert _MANIFEST.is_file(), f"the native manifest is missing: {_MANIFEST}"
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def _code_only(text: str) -> str:
    """Strip comments so a check inspects code rather than prose about it.

    Args:
        text: TypeScript source.

    Returns:
        Source with block and line comments removed.
    """
    without_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(
        line for line in without_block.splitlines() if not line.strip().startswith("//")
    )


def test_the_native_manifest_exists_and_is_valid_json() -> None:
    """OpenClaw reads `openclaw.plugin.json`; a Cursor manifest is only detected, never run."""
    manifest = _manifest()
    assert manifest["id"] == "bug-fixer", f"id must match the skill id: {manifest['id']!r}"
    assert manifest["name"], "a native manifest needs a display name"
    assert manifest["description"], "a native manifest needs a description"


def test_the_manifest_carries_a_config_schema() -> None:
    """The vendor requires `configSchema`; an empty schema is allowed and is what we need.

    The gate takes no configuration: it finds the ledger from the project contract in the tool
    call's working directory. Declaring options nobody reads would be a second source of truth.
    """
    schema = _manifest()["configSchema"]
    assert isinstance(schema, dict), "`configSchema` must be an object"
    assert schema.get("type") == "object", f"expected an object schema: {schema!r}"
    assert schema.get("properties") == {}, (
        f"the gate reads no options, so the schema declares none: {schema.get('properties')!r}"
    )


def test_the_manifest_declares_no_option_that_nothing_reads() -> None:
    """A schema property is a promise; an unread one is a promise nobody keeps.

    The adapter reads no config: every value it needs comes from the tool call or the environment.
    If a property is ever added, the adapter has to read it in the same change — this test is where
    that shows up.
    """
    properties = _manifest()["configSchema"].get("properties", {})
    code = _code_only(_ADAPTER.read_text(encoding="utf-8"))
    unread = [name for name in properties if name not in code]
    assert not unread, f"the manifest declares options the adapter never reads: {unread}"


def test_a_denial_becomes_block_with_a_reason() -> None:
    """The documented terminal shape: `{ block: true, blockReason }`, reason non-empty.

    §0 forbids a guard that refuses without saying why: the agent reads `blockReason` while it is
    blocked, so an empty one is a dead end. Each blocking return is checked on its own, so deleting
    any single reason fails here rather than being averaged away by a count.
    """
    code = _code_only(_ADAPTER.read_text(encoding="utf-8"))
    assert "block: true" in code, "the adapter never produces the documented blocking shape"

    # A blocking return is `finish({ ... block: true ... })`; the body may span lines.
    returns = re.findall(r"finish\(\s*\{(.*?)\}\s*\)", code, re.DOTALL)
    blocking = [body for body in returns if re.search(r"block:\s*true", body)]
    assert len(blocking) >= 3, (
        "expected the three documented refusal paths (spawn error, gate message, gate crash), "
        f"found {len(blocking)}: {[b.strip()[:40] for b in returns]}"
    )
    missing = [body for body in blocking if "blockReason" not in body]
    assert not missing, f"a blocking return carries no reason: {missing[0].strip()[:100]!r}"

def test_every_refusal_reason_is_a_non_empty_template() -> None:
    """A `blockReason` that can evaluate to `""` is a refusal with no words in it.

    Two refusal paths build their own sentence; the third passes the gate's `agent_message`
    through, which is the whole point of asking the gate — so it is checked for being guarded at
    the source, and the literals are checked for having words of their own.
    """
    code = _code_only(_ADAPTER.read_text(encoding="utf-8"))

    literals = re.findall(r'blockReason:\s*(`[^`]*`|"[^"]*")', code, re.DOTALL)
    assert len(literals) >= 2, f"expected the two self-authored reasons, found {len(literals)}"
    for value in literals:
        body = value[1:-1]
        fixed = re.sub(r"\$\{[^}]*\}", "", body).strip()
        assert fixed, f"`blockReason` is only interpolation and can render empty: {value[:80]!r}"
        assert re.search(r"[A-Za-z]", fixed), f"`blockReason` has no words in it: {value[:80]!r}"

    # The forwarded reason is only safe because the adapter refuses to forward an empty one.
    forwarded = re.findall(r"blockReason:\s*([A-Za-z_][\w.]*)", code)
    assert forwarded, "expected one path to forward the gate's own message"
    for name in forwarded:
        field = name.rsplit(".", 1)[-1]
        guard = re.search(rf"if\s*\(\s*{re.escape(name)}\s*\)", code)
        assert guard, (
            f"`{name}` is forwarded as a reason without an emptiness guard, so a gate that "
            "answers with an empty message would block the agent with no words"
        )


def test_the_adapter_registers_a_typed_hook() -> None:
    """OpenClaw runs typed lifecycle hooks through `api.on`; `registerHook` never fires."""
    code = _code_only(_ADAPTER.read_text(encoding="utf-8"))
    assert "api.on(" in code
    assert "before_tool_call" in code
    assert "api.registerHook(" not in code, "`registerHook` is never invoked for typed names"


def test_the_adapter_denies_when_the_gate_cannot_start() -> None:
    """Fail closed: a gate that cannot run must block, not allow.

    Without the interpreter there is no verdict, and no verdict is not permission (§0).
    """
    code = _code_only(_ADAPTER.read_text(encoding="utf-8"))
    assert "child.on(\"error\"" in code or "child.on('error'" in code, (
        "the spawn failure path is missing: without it a missing interpreter looks like an allow"
    )
    assert "catch" in code, "an unparsable gate answer must fall through to a refusal"


def test_the_adapter_does_not_depend_on_bun() -> None:
    """OpenClaw is a Node program; a `Bun` global is a ReferenceError inside the guard."""
    code = _code_only(_ADAPTER.read_text(encoding="utf-8"))
    assert "Bun." not in code, "the adapter must spawn through `node:child_process`"


def _openclaw_section() -> str:
    """The README's OpenClaw section, from its heading to the next one.

    Returns:
        The section body.

    Raises:
        AssertionError: When the section is gone — the limitation would have nowhere to live.
    """
    readme = _README.read_text(encoding="utf-8")
    marker = "### OpenClaw"
    assert marker in readme, "the README has no OpenClaw section"
    body = readme[readme.index(marker) + len(marker) :]
    end = re.search(r"\n###? ", body)
    return body[: end.start()] if end else body


def test_the_readme_says_execution_was_not_observed() -> None:
    """DoD-6 requires the limitation stated, not implied.

    A reader must not be able to conclude from the repo that OpenClaw was exercised: it was not,
    and the manifest is written to the documented shape only. The check is confined to the OpenClaw
    section, because a disclaimer anywhere in the file would let that section read as verified.
    """
    readme = _README.read_text(encoding="utf-8")
    assert "openclaw.plugin.json" in readme, "the README's tree must list the native manifest"

    section = _openclaw_section()
    assert re.search(
        r"(?:not|never)\s+(?:been\s+)?(?:run|executed|tested|verified)", section, re.IGNORECASE
    ) or re.search(r"executed on a machine", section, re.IGNORECASE), (
        "the OpenClaw section must say plainly that it was not executed on a machine, "
        f"in that section rather than elsewhere: {section[:160]!r}"
    )


def test_the_disclaimer_is_not_the_only_thing_holding_the_claim_up() -> None:
    """The section names the concrete gap: adapter and manifest exist, execution was not observed.

    A bare "not verified" can be written over anything. What a reader needs is which part is
    unwitnessed — the load and the firing, not the files.
    """
    section = _openclaw_section()
    assert re.search(r"hook|before_tool_call", section, re.IGNORECASE), (
        "the section must name what was not observed — the hook firing"
    )
    assert re.search(r"adapter|manifest", section, re.IGNORECASE), (
        "the section must name what *was* produced, so the reader can tell the difference"
    )
