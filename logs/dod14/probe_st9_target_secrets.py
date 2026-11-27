"""ST-9 probe 3: would a `target` field leak a secret, and does the masking hold?

A shell command line can carry a credential (`curl -H 'authorization: Bearer …'`). The
journal is never scanned by the gate today — `scan_records` reads `### F<n>` blocks in the
ledger only — so a raw `target` would be the first place a secret could land unredacted.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
SKILL = REPO / "skills" / "bug-fixer"
sys.path.insert(0, str(SKILL / "hooks"))
sys.path.insert(0, str(SKILL / "lib"))

import bugfixer_secrets as secrets  # noqa: E402

SAMPLES = [
    "pytest -q 2>/dev/null",
    "cat >> tasks/TASK-086-x.md <<'EOF'\ntext\nEOF",
    "curl -H 'authorization: Bearer abcdefghijklmnop' https://example.invalid/x",
    "export OPENAI_API_KEY=plainvalue123 && python run.py",
    "python -c \"print(open('.env').read())\"",
    "sed -i '' 's/a/b/' skills/bug-fixer/hooks/bugfixer_gate.py",
]


def main() -> None:
    for sample in SAMPLES:
        findings = secrets.scan(sample)
        verdict = "clean" if not findings else "; ".join(secrets.describe(f) for f in findings)
        print(f"  {sample[:60]!r}\n    -> {verdict}")

    print("\n== what a redacted target would look like ==")
    sample = SAMPLES[2]
    masked = sample
    for finding in secrets.scan(sample):
        # The gate's own vocabulary: replace the whole command with the masked finding.
        masked = f"<redacted: {finding.rule}>"
    print(f"  raw    : {sample}")
    print(f"  masked : {masked}")

    print("\n== cost of a 400-char cap on the target field ==")
    long_command = "cat > tasks/TASK-086-x.md <<'EOF'\n" + ("line of ledger text\n" * 400) + "EOF"
    print(f"  sample command : {len(long_command)} chars")
    print(f"  capped at 400  : {len(long_command[:400])} chars (+1 marker flag)")


if __name__ == "__main__":
    main()
