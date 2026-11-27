"""Whether a run can start here: one answer, read by `doctor` and by the session hook.

Spec §18.2 requires the hook and `doctor` to agree, because two readers of one contract that
disagree is how a plugin tells the agent "you are fine" and then refuses its first edit. So the
inspection lives here once: `bin/bugfixer doctor` prints it for a human, `hooks/session_start.py`
turns the same result into one short sentence, and neither re-derives the answer.

:func:`message` is the only "what to say" function. Adapters in ``hooks/adapters`` lay its output
into each harness's format and add nothing to it: a second sentence composed per harness would be
a second judgement about the project's state.

The spec's three states (§18.4, invariant 41) map onto :data:`NO_CONTRACT`, :data:`UNVERIFIED`
and :data:`INCOMPLETE`; :data:`NO_LEDGER` is added because the gate refuses in that case too and
saying nothing about it would be the silence §0 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from . import contract

#: No `bug-fixer.project.md` at or above the project directory.
NO_CONTRACT: Final[str] = "no_contract"

#: A contract exists and a required field is missing, a placeholder, or names no suite.
INCOMPLETE: Final[str] = "incomplete"

#: Every field is filled, but nobody confirmed the derived values against a runtime.
UNVERIFIED: Final[str] = "unverified"

#: The contract is usable, but its ledger pattern matches no file the gate can bind to.
NO_LEDGER: Final[str] = "no_ledger"

#: Nothing stands between the agent and work.
READY: Final[str] = "ready"

#: The plugin root, derived from this file: ``<plugin>/lib/bugfixer/readiness.py``.
_PLUGIN_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Readiness:
    """What the project would tell the gate, in one object.

    Attributes:
        project: Directory the answer is about. Always the caller's working directory, never
            the plugin's location (invariant 42): a status computed from where the plugin is
            installed would describe a project nobody asked about.
        contract: The contract file, or ``None`` when there is none.
        state: One of the module's state constants.
        problems: Refusals from the contract schema.
        warnings: Non-blocking remarks from the schema.
        missing: Required field names that are absent or still placeholders.
        ledger_pattern: The glob the gate would read, empty when unreadable.
        test_paths: Prefixes the gate treats as tests.
        ledger: The file the gate would bind to, when one can be identified.
        ledger_problem: Why the ledger cannot be identified, empty when it can.
        extra_reasons: Reasons the schema does not phrase, e.g. `Full suite: none`.
    """

    project: Path
    contract: Path | None = None
    state: str = NO_CONTRACT
    problems: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    ledger_pattern: str = ""
    test_paths: tuple[str, ...] = ()
    ledger: Path | None = None
    ledger_problem: str = ""
    extra_reasons: tuple[str, ...] = field(default=())

    @property
    def ready(self) -> bool:
        """Whether a run can start without further setup."""
        return self.state == READY


def _with(readiness: Readiness, **changes: object) -> Readiness:
    """Return a copy of a readiness with fields replaced.

    Args:
        readiness: Base object.
        **changes: Fields to replace.

    Returns:
        The updated readiness.
    """
    data = {
        "project": readiness.project,
        "contract": readiness.contract,
        "state": readiness.state,
        "problems": readiness.problems,
        "warnings": readiness.warnings,
        "missing": readiness.missing,
        "ledger_pattern": readiness.ledger_pattern,
        "test_paths": readiness.test_paths,
        "ledger": readiness.ledger,
        "ledger_problem": readiness.ledger_problem,
        "extra_reasons": readiness.extra_reasons,
    }
    data.update(changes)
    return Readiness(**data)  # type: ignore[arg-type]


def _gate_module() -> object:
    """Import the gate so ledger discovery has one implementation.

    Imported lazily: `doctor` must stay cheap, and the session hook must not pay for a module
    it may never reach.

    Returns:
        The ``bugfixer_gate`` module.
    """
    import sys  # noqa: PLC0415 — see docstring

    hooks = str(_PLUGIN_ROOT / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import bugfixer_gate  # noqa: PLC0415

    return bugfixer_gate


def _resolve_ledger(root: Path) -> tuple[Path | None, str]:
    """Find the ledger the gate would bind to, without reimplementing its rules.

    Args:
        root: Project directory.

    Returns:
        ``(path, "")`` when one file can be identified, ``(None, reason)`` otherwise.
    """
    gate = _gate_module()
    try:
        return gate.discover_ledger(root), ""  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — any refusal is reported, never swallowed
        return None, str(exc)


def inspect(project: Path) -> Readiness:
    """Report whether a run can start in a project.

    Args:
        project: Directory to inspect; the caller's working directory.

    Returns:
        A readiness whose ``state`` says what, if anything, stands in the way.
    """
    root = project.expanduser().resolve()
    path = contract.find(root)
    if path is None:
        return Readiness(project=root, state=NO_CONTRACT)

    text = path.read_text(encoding="utf-8", errors="replace")
    report = contract.check(text)
    missing = tuple(name for name in contract.REQUIRED_FIELDS if name not in report.values)
    reasons: list[str] = []
    if not contract.has_suite(text):
        reasons.append("в контракте `Full suite: none` — набора тестов нет")

    state = READY
    if report.problems:
        state = INCOMPLETE
    elif not contract.is_verified(text):
        state = UNVERIFIED

    readiness = Readiness(
        project=root,
        contract=path,
        state=state,
        problems=tuple(report.problems),
        warnings=tuple(report.warnings),
        missing=missing,
        ledger_pattern=contract.ledger_pattern(text),
        test_paths=tuple(contract.test_paths(text)),
        extra_reasons=tuple(reasons),
    )
    # The ledger is resolved even when the contract is unverified: `doctor` reports what the gate
    # would bind to, and withholding that because a different field is unfinished would send the
    # operator looking for a second problem. Only a contract with unusable fields stops here.
    if report.problems:
        return readiness

    ledger, problem = _resolve_ledger(root)
    if ledger is not None:
        return _with(readiness, ledger=ledger, ledger_problem="")
    if state == READY:
        return _with(readiness, state=NO_LEDGER, ledger_problem=problem)
    return _with(readiness, ledger_problem=problem)


def message(readiness: Readiness) -> str:
    """One short sentence saying what is not ready, or nothing when everything is.

    The hook injects this; `doctor` prints the long form. Both come from the same inspection, so
    they cannot describe different projects or different reasons.

    Args:
        readiness: Result of :func:`inspect`.

    Returns:
        The sentence to inject, or an empty string when a run can start.
    """
    if readiness.ready:
        return ""

    if readiness.state == NO_CONTRACT:
        reason = f"`{contract.CONTRACT_NAME}` не найден"
    elif readiness.state == UNVERIFIED:
        reason = f"контракт не подтверждён (`Contract verified by: {contract.UNVERIFIED}`)"
    elif readiness.state == NO_LEDGER:
        pattern = readiness.ledger_pattern or "<не задан>"
        reason = f"леджер по `{pattern}` не найден — создай файл задачи"
    else:
        parts = list(readiness.extra_reasons)
        if readiness.missing:
            names = ", ".join(f"`{name}`" for name in readiness.missing)
            parts.append(f"не заполнено: {names}")
        reason = "; ".join(parts) or "контракт не проходит проверку"

    return (
        f"bug-fixer: проект не готов — {reason}. Правки кода гейт отклонит. "
        "Пройди `references/init.md`, затем `bugfixer doctor` до кода `0`."
    )
