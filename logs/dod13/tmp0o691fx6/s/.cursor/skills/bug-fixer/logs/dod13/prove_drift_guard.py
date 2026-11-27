"""DoD-13 evidence: the drift guard goes red when the matrix stops quoting the spec.

Spec §0: a test that cannot fail proves nothing. The spec and the matrix are both inputs, so
both are mutated in memory — neither file is written.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[4]
TEST = REPO / "tests" / "test_bugfixer_spec_fixture.py"
MATRIX = REPO / "skills" / "bug-fixer" / "references" / "invariant-matrix.json"


def load() -> object:
    """Import the guard module by path.

    Returns:
        The imported module.
    """
    spec = importlib.util.spec_from_file_location("fixture_guard", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fixture_guard"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    """Mutate the matrix three ways and confirm the guard catches each.

    Returns:
        0 when every mutation is caught, 1 otherwise.
    """
    module = load()
    original = json.loads(MATRIX.read_text(encoding="utf-8"))
    scratch = pathlib.Path(tempfile.mkdtemp(dir=REPO / "skills" / "bug-fixer" / "logs" / "dod13"))
    cases: list[tuple[str, dict[str, object]]] = []

    dropped = json.loads(json.dumps(original))
    dropped["invariants"].pop("30")
    cases.append(("an invariant is missing from the matrix", dropped))

    extra = json.loads(json.dumps(original))
    extra["invariants"]["50"] = {"text": "invented", "tests": []}
    cases.append(("the matrix invents an invariant", extra))

    reworded = json.loads(json.dumps(original))
    reworded["invariants"]["1"]["text"] = "same rule, different words"
    cases.append(("an invariant is reworded", reworded))

    failures = 0
    for description, payload in cases:
        path = scratch / "matrix.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        module._MATRIX = path  # type: ignore[attr-defined]
        results = []
        for name in (
            "test_the_matrix_covers_exactly_the_spec_invariants",
            "test_every_matrix_wording_is_the_spec_wording",
        ):
            try:
                getattr(module, name)()
                results.append(f"{name}: green")
            except AssertionError as error:
                results.append(f"{name}: RED ({str(error).splitlines()[0][:60]})")
        caught = any("RED" in result for result in results)
        print(f"{'CAUGHT' if caught else 'MISSED'}  {description}")
        for result in results:
            print(f"          {result}")
        if not caught:
            failures += 1

    module._MATRIX = MATRIX  # type: ignore[attr-defined]
    print(f"\n{'every divergence is caught' if not failures else f'{failures} missed'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
