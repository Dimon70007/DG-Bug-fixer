"""ST-9 probe 6: why a read-only command was refused as a production write.

Measured during this session (2026-11-28T22:11:55Z, journal of TASK-086): the command

    cd skills/bug-fixer && ./.venv/bin/python -c "import tempfile;print('TMPDIR ->',tempfile.gettempdir())"

was denied with "Phase 1 forbids production-code changes". Nothing there writes a file.
`->` inside a string contains `>`, and `_WRITE_COMMAND_RE` treats every `>` as a redirection
unless what follows is `/dev/…`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_gate as gate  # noqa: E402

CASES = [
    ("./.venv/bin/python -c \"print('a -> b')\"", "read-only, but contains ->"),
    ("rg 'a>b' docs/", "read-only search for a literal >"),
    ("rg -- '->' -n hooks/", "read-only search for an arrow"),
    ("git diff HEAD~1 | grep '>'", "read-only pipe"),
    ("pytest -q 2>/dev/null", "the corpus's own Shell payload"),
    ("cat >> tasks/TASK-086-x.md <<'EOF'\ntext\nEOF", "a real write: heredoc append"),
    ("echo done", "no redirect at all"),
]


def main() -> None:
    pattern = gate._WRITE_COMMAND_RE
    print("command -> classified as write? / destinations the gate extracts")
    for command, label in CASES:
        is_write = bool(pattern.search(command))
        targets = gate._shell_write_targets(command)
        print(f"  {label:42} write={is_write!s:5} targets={targets}")

    print("\n== the regex fragment responsible ==")
    print(f"  {pattern.pattern[-120:]}")

    print("\n== how far the false positive reaches ==")
    corpus = REPO / "skills" / "bug-fixer" / "tests" / "fixtures" / "cursor_payloads.json"
    print(f"  corpus: {corpus.name} (7 payloads, 1 Shell) — cannot quantify field behaviour")
    print("  measured instead: a `>` anywhere means 'write' unless followed by /dev/*")


if __name__ == "__main__":
    main()
