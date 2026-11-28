"""A measurement that can be wrong out loud.

Spec §0 says a test that cannot fail proves nothing. A probe is worse: nothing re-runs it, and
its number is copied into a decision. TASK-086 ST-17 measured four wrong numbers in one session,
each printed with complete confidence, so a probe whose number enters a decision needs the same
discipline a test has — and the two defences that actually caught those failures:

* **a control with a known answer** — the defect was the instrument, not the claim, so the
  instrument is run against a case whose answer is known before its number is believed;
* **a known-good range** — a gross error (`0` where the repository has `7`) stops the run
  instead of being reported.

Two independent derivations of one number, where a second path exists, are the third:
broken the same way twice is a much smaller set than broken once.

The name is ``measurement``, not ``probe``: in this project a *probe* is a production edit made
to test a named hypothesis (§1). This module is about a number, not about a hypothesis.

The stand helper exists because two "defects" in that session were artefacts of a test project
created inside the plugin tree (F17): a stand inside a scanned tree measures itself.

Nothing here decides anything about the plugin. It refuses, loudly, when it cannot measure.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

#: A case whose known answer is that the instrument sees the thing.
POSITIVE: Final[str] = "positive"

#: A case whose known answer is that the instrument misses the thing.
NEGATIVE: Final[str] = "negative"

#: A string that is not what the instrument looks for: it must be ignored.
CONTROL: Final[str] = "control"

#: Two derivations of one number must produce the same answer.
AGREEMENT: Final[str] = "agreement"

#: The kinds a number needs before it may be believed.
REQUIRED_KINDS: Final[tuple[str, ...]] = (POSITIVE, NEGATIVE, CONTROL)

#: ``<plugin>/lib/bugfixer/measurement.py`` -> ``<plugin>``.
_PLUGIN_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


class Unmeasurable(RuntimeError):
    """The instrument has not been shown to work, so its number is not a measurement.

    Raised rather than printed: a probe that returns ``0`` for input ``7`` must fail, and a
    failure that is only a sentence in stdout is exactly the silent wrong number ST-17 exists to
    stop (§0).
    """


def plugin_root() -> Path:
    """The plugin tree a stand must stay out of.

    Returns:
        Resolved plugin directory.
    """
    return _PLUGIN_ROOT


def repository_root() -> Path:
    """The outermost checkout enclosing the plugin.

    The *outermost* ancestor carrying ``.git``: inside a host project the plugin is a submodule
    with its own git entry, and a stand inside the host project is still inside a scanned tree.

    Returns:
        Resolved repository root, or the plugin directory when nothing encloses it.
    """
    found = _PLUGIN_ROOT
    for parent in _PLUGIN_ROOT.parents:
        if (parent / ".git").exists():
            found = parent
    return found


def forbidden_roots() -> tuple[Path, ...]:
    """Trees no stand may live in.

    Returns:
        The plugin, and the repository enclosing it when that is a different tree.
    """
    plugin = plugin_root()
    repository = repository_root()
    return (plugin,) if repository == plugin else (plugin, repository)


def assert_outside(path: Path, *, roots: tuple[Path, ...] | None = None) -> Path:
    """Refuse a stand that lives inside a tree the measurement scans.

    Args:
        path: Directory the probe will create files in.
        roots: Trees to stay out of; the plugin and its repository by default.

    Returns:
        The resolved path, when it is outside every root.

    Raises:
        Unmeasurable: When the path is inside a root — the probe would measure its own output,
            which is how one session produced two defects that did not exist (F17).
    """
    resolved = Path(path).resolve()
    for root in roots if roots is not None else forbidden_roots():
        if resolved == root or root in resolved.parents:
            raise Unmeasurable(
                f"the stand {resolved} is inside {root}. A probe whose test project lives in a "
                "tree it scans reports its own files as findings; create the stand with "
                "`make_stand()`, which uses `tempfile.mkdtemp()` and no `dir=`."
            )
    return resolved


def make_stand(prefix: str = "bugfixer-stand-") -> Path:
    """Create a scratch project outside the plugin and outside the repository.

    Args:
        prefix: Directory name prefix, so a leaked stand is recognisable.

    Returns:
        The resolved stand directory.

    Raises:
        Unmeasurable: When the system temporary directory is inside a forbidden root, which
            happens when the repository is checked out under `/tmp` — then no stand can be
            trusted and the probe must say so rather than measure.
    """
    return assert_outside(Path(tempfile.mkdtemp(prefix=prefix)))


@dataclass(frozen=True)
class Case:
    """One recorded observation.

    Attributes:
        kind: One of :data:`POSITIVE`, :data:`NEGATIVE`, :data:`CONTROL`, :data:`AGREEMENT`.
        label: What was observed, in words a reader can check without the probe.
        held: Whether the observation matched the known answer.
        detail: What was expected against what was seen.
    """

    kind: str
    label: str
    held: bool
    detail: str


@dataclass
class Measurement:
    """One number, with the controls that let it be wrong out loud.

    Attributes:
        subject: What is measured, printed with the number — a figure whose subject is not named
            is how the audit compared the list with its own parser instead of with the code.
        cases: Recorded observations.
        value: The number, once set by :meth:`count`.
        plausible: Inclusive known-good range, measured before the probe runs.
    """

    subject: str
    cases: list[Case] = field(default_factory=list)
    value: int | None = None
    plausible: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise Unmeasurable("a measurement without a subject describes nothing")

    def expect(self, kind: str, label: str, *, expected: object, got: object) -> bool:
        """Record one case whose answer was known before the run.

        Args:
            kind: One of :data:`POSITIVE`, :data:`NEGATIVE`, :data:`CONTROL`, :data:`AGREEMENT`.
            label: What was observed.
            expected: The known answer.
            got: What the instrument produced.

        Returns:
            Whether they match.
        """
        held = expected == got
        self.cases.append(
            Case(kind=kind, label=label, held=held, detail=f"expected {expected!r}, got {got!r}")
        )
        return held

    def agree(self, label: str, *, first: object, second: object) -> bool:
        """Record two derivations of one number (ST-17 requirement 2).

        Args:
            label: What the two paths derive.
            first: First derivation's answer.
            second: Second derivation's answer.

        Returns:
            Whether they match.
        """
        return self.expect(AGREEMENT, label, expected=first, got=second)

    def count(self, value: int, *, plausible: tuple[int, int]) -> None:
        """Set the number and the range it is possible to land in.

        Args:
            value: The measured count.
            plausible: Inclusive known-good range. It is measured on the repository before the
                probe runs: a range widened after seeing the number is not a control (Rubin
                2025).
        """
        self.value = value
        self.plausible = plausible

    def problems(self) -> list[str]:
        """Everything that makes the number untrustworthy.

        Returns:
            One line per problem, empty when the measurement stands.
        """
        problems: list[str] = []
        kinds = {case.kind for case in self.cases}
        problems += [
            f"no {kind} case: without one the instrument has not been shown to work (ST-17 §1)"
            for kind in REQUIRED_KINDS
            if kind not in kinds
        ]
        problems += [
            f"{case.kind} case {case.label!r} did not hold: {case.detail}"
            for case in self.cases
            if not case.held
        ]
        if self.value is not None:
            if self.plausible is None:
                problems.append(
                    f"the number {self.value} has no known-good range, so nothing can tell it "
                    "apart from a gross error (ST-17 §3)"
                )
            elif not self.plausible[0] <= self.value <= self.plausible[1]:
                problems.append(
                    f"the number {self.value} is outside the known-good range "
                    f"{self.plausible[0]}..{self.plausible[1]}: the instrument is broken or the "
                    "subject is not the one named"
                )
        return problems

    def report(self) -> str:
        """The run in text, including the cases that failed.

        Returns:
            Multi-line report, one line per case.
        """
        lines = [f"subject: {self.subject}"]
        if self.value is not None:
            span = (
                f" (known-good {self.plausible[0]}..{self.plausible[1]})"
                if self.plausible is not None
                else " (no known-good range)"
            )
            lines.append(f"value  : {self.value}{span}")
        for case in self.cases:
            lines.append(f"  {'ok  ' if case.held else 'FAIL'} {case.kind:10} {case.label}")
            if not case.held:
                lines.append(f"       {case.detail}")
        return "\n".join(lines)

    def finish(self) -> int:
        """Print the report, and refuse when anything is unproven.

        Returns:
            ``0`` only when every case held and the number is inside its range.

        Raises:
            Unmeasurable: When the number cannot be trusted. The caller must not catch it: a
                probe whose number is fiction exits non-zero (§0).
        """
        print(self.report())
        problems = self.problems()
        if problems:
            raise Unmeasurable(f"{self.subject}: " + "; ".join(problems))
        return 0
