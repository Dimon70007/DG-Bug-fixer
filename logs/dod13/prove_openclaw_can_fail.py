"""DoD-6 evidence: the OpenClaw tests fail when the manifest or the adapter is wrong.

Nine green tests prove nothing unless each can go red. This breaks one thing at a time in memory —
the files are never written — and requires the matching test to fail.

Development helper. Not shipped, not collected by pytest.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[4]
PLUGIN = REPO / "skills" / "bug-fixer"
TEST = PLUGIN / "tests" / "test_bugfixer_openclaw_v3.py"
MANIFEST = PLUGIN / "openclaw.plugin.json"
ADAPTER = PLUGIN / "hooks" / "openclaw-plugin.ts"
README = PLUGIN / "README.md"


def load() -> object:
    """Import a fresh copy of the OpenClaw test module.

    Returns:
        The imported module.
    """
    sys.path.insert(0, str(PLUGIN / "tests"))
    name = "openclaw_probe"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class _Text:
    """A path-like object that serves replacement text instead of the file."""

    def __init__(self, text: str) -> None:
        """Store the text.

        Args:
            text: Content to serve.
        """
        self._text = text

    def is_file(self) -> bool:
        """Claim the file exists.

        Returns:
            True.
        """
        return True

    def read_text(self, encoding: str = "utf-8") -> str:
        """Return the stored text.

        Args:
            encoding: Ignored.

        Returns:
            The replacement text.
        """
        return self._text

    def __str__(self) -> str:
        """Name the object in failure text.

        Returns:
            A short label.
        """
        return "<mutated>"


def run(module: object, name: str) -> tuple[bool, str]:
    """Call one test and report whether it failed.

    Args:
        module: The test module.
        name: Test function name.

    Returns:
        ``(failed, detail)``.
    """
    try:
        getattr(module, name)()
    except AssertionError as error:
        return True, (str(error).splitlines() or ["assertion failed"])[0][:100]
    return False, "passed"


def main() -> int:
    """Break each artefact and confirm the matching test notices.

    Returns:
        0 when every mutation is caught, 1 otherwise.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    adapter = ADAPTER.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    cases: list[tuple[str, str, object, str, str]] = [
        (
            "the manifest is missing its id",
            "test_the_native_manifest_exists_and_is_valid_json",
            "manifest",
            json.dumps({**manifest, "id": "bugfixer-gate"}),
            "",
        ),
        (
            "the schema declares an option nothing reads",
            "test_the_manifest_declares_no_option_that_nothing_reads",
            "manifest",
            json.dumps(
                {**manifest, "configSchema": {**manifest["configSchema"], "properties": {"mode": {}}}}
            ),
            "",
        ),
        (
            "a refusal path loses its reason",
            "test_a_denial_becomes_block_with_a_reason",
            "adapter",
            # Target the code, not the docstring: the first `blockReason` in the file is in a
            # comment, which a code-only check strips — mutating it would prove nothing.
            adapter.replace(
                "        blockReason:\n          `bug-fixer gate could not start",
                "        detail:\n          `bug-fixer gate could not start",
                1,
            ),
            "",
        ),
        (
            "a refusal reason can render as empty",
            "test_every_refusal_reason_is_a_non_empty_template",
            "adapter",
            adapter.replace(
                "blockReason:\n          `bug-fixer gate could not start",
                "blockReason:\n          `` + `bug-fixer gate could not start",
                1,
            ),
            "",
        ),
        (
            "the adapter stops failing closed",
            "test_the_adapter_denies_when_the_gate_cannot_start",
            "adapter",
            adapter.replace('child.on("error"', 'child.on("close"', 1),
            "",
        ),
        (
            "the adapter reaches for Bun",
            "test_the_adapter_does_not_depend_on_bun",
            "adapter",
            adapter.replace("spawn(PYTHON", "Bun.spawn(PYTHON", 1),
            "",
        ),
        (
            "the README stops admitting it was never executed",
            "test_the_readme_says_execution_was_not_observed",
            "readme",
            readme.replace("**Not executed on a machine.**", "**Verified.**"),
            "",
        ),
    ]

    failures = 0
    for description, test_name, target, content, _ in cases:
        module = load()
        if content == "":
            print(f"SKIP  {description}: mutation did not apply")
            failures += 1
            continue
        if target == "manifest":
            module._MANIFEST = _Text(content)
        elif target == "adapter":
            module._ADAPTER = _Text(content)
        else:
            module._README = _Text(content)

        caught, detail = run(module, test_name)
        print(f"{'OK  ' if caught else 'BAD '}{description}: {'RED' if caught else 'green'}")
        print(f"       {test_name} -> {detail}")
        if not caught:
            failures += 1

    module = load()
    ok, _ = run(module, "test_the_native_manifest_exists_and_is_valid_json")
    print(f"\ncontrol (unmutated): {'RED' if ok else 'green'}")
    if ok:
        failures += 1

    print(f"\n{'every mutation is caught' if not failures else f'{failures} case(s) wrong'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
