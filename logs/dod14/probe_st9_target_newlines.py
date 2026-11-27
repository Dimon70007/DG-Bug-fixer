"""ST-9 probe 11: can a shell command (which contains newlines) live in a `target` field?

`append_event` folds `why` with `" ".join(why.split())` because one record is one line. A
heredoc command is inherently multi-line, so the proposed `target` field has to answer the
same question: is a newline in it a broken record, or is JSON escaping enough?
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_journal as journal  # noqa: E402

COMMAND = "cat >> tasks/TASK-086-x.md <<'EOF'\none\ntwo\nEOF"


def main() -> None:
    scratch = Path(tempfile.mkdtemp(dir=SKILL / "logs" / "dod14"))
    try:
        ledger = scratch / "T.md"
        ledger.write_text("x\n", encoding="utf-8")

        record = journal.append_event(
            ledger, event="deny", why="multi\nline reason", tool="shell", target=COMMAND
        )
        lines = journal.journal_path(ledger).read_text(encoding="utf-8").splitlines()
        print(f"records written : 1 expected, got {len(lines)}")
        print(f"`why` folded    : {record['why']!r}")
        print(f"`target` stored : {record['target']!r}")
        parsed = json.loads(lines[0])
        print(f"target roundtrip: {parsed['target'] == COMMAND}")
        print(f"record bytes    : {len(lines[0])}")

        print("\n== does `read_events` accept it? ==")
        print(f"  events read back: {len(journal.read_events(ledger))}")

        print("\n== how an unfolded 8 KB heredoc behaves ==")
        big = "cat > x <<'EOF'\n" + ("line\n" * 1600) + "EOF"
        big_record = journal.append_event(ledger, event="deny", why="big", tool="shell", target=big)
        print(f"  stored target length: {len(big_record['target'])}")
        print(f"  journal grew by      : {journal.journal_path(ledger).stat().st_size} B total")
    finally:
        shutil.rmtree(scratch)


if __name__ == "__main__":
    main()
