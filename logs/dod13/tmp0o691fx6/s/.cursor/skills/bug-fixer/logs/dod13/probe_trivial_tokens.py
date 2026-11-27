"""Is the rule-1 check vacuous for the `| Kind |` row?

`_marker_tokens` derives a first token by splitting `| Kind | route|research|probe` on
whitespace and punctuation, which yields a bare `|`. Every table cell contains `|`, so the
membership check would pass no matter what the writer cell says — a test that cannot fail.

This measures it instead of reasoning about it: for each edge-read row, ask whether the token
list contains something that is present in *every* cell in the document.

Development helper. Not shipped.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[4]
TEST = REPO / "tests" / "test_bugfixer_spec_invariant_writers.py"


def main() -> int:
    """Print the token list for each edge-read row and flag trivially-satisfied tokens.

    Returns:
        0 when no token is trivial, 1 when at least one is.
    """
    spec = importlib.util.spec_from_file_location("inv17", TEST)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["inv17"] = module
    spec.loader.exec_module(module)

    trivial = 0
    for marker, writer, reader, _ in module._four_rows():
        if not module._is_edge_read(reader):
            continue
        tokens = module._marker_tokens(marker)
        # A token that is one character of punctuation appears in every cell by construction.
        suspicious = [t for t in tokens if len(t.strip()) <= 1]
        if suspicious:
            trivial += 1
            print(f"TRIVIAL {marker!r}")
            print(f"  tokens: {tokens}")
            print(f"  writer: {writer}  readers: {reader.strip()}")
            print(f"  -> {suspicious} appears in every cell, so this row cannot fail")
    print(f"\nedge-read rows with a trivial token: {trivial}")
    return 0 if not trivial else 1


if __name__ == "__main__":
    raise SystemExit(main())
