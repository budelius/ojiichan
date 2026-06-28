"""Predicate-driven provisioning engine.

A miniature of obachan's idempotent step registry: each provisioning unit is a
:class:`ProvisionStep` pairing a ``check`` predicate (is the resource already
present?) with an ``apply`` action (create it). :func:`run_steps` walks the list,
skipping any step whose ``check`` reports the resource present, so re-running a
provision is safe. With ``dry_run=True`` no ``apply`` runs — the planner reports
what *would* change.

Steps run in declared order and **stop on the first failure**, because later
steps usually depend on earlier ones (a role-mapping needs its role to exist).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Returns ``True`` when the resource already exists. May raise; a raising check
#: is treated as "unknown" and the step is attempted (apply is create-or-replace).
CheckFn = Callable[[], bool]
#: Performs the create-or-replace. Return value (if any) becomes the outcome detail.
ApplyFn = Callable[[], Any]


@dataclass
class ProvisionStep:
    name: str
    description: str
    apply: ApplyFn
    check: CheckFn | None = None


@dataclass
class StepOutcome:
    name: str
    #: ``exists`` | ``created`` | ``would-create`` | ``failed``
    status: str
    detail: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {"exists", "created", "would-create"}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "status": self.status}
        if self.detail:
            out["detail"] = self.detail
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class ProvisionResult:
    target: str
    dry_run: bool
    steps: list[StepOutcome] = field(default_factory=list)
    #: Env-var names whose values were written to Vault during this run.
    secrets_written: list[str] = field(default_factory=list)
    #: Free-form notes for the agent/operator (e.g. why a secret was skipped).
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(step.ok for step in self.steps)

    @property
    def created(self) -> bool:
        """True when this run actually changed backend state."""
        return any(step.status == "created" for step in self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "steps": [step.to_dict() for step in self.steps],
            "secrets_written": self.secrets_written,
            "notes": self.notes,
        }


def run_steps(target: str, steps: list[ProvisionStep], *, dry_run: bool) -> ProvisionResult:
    """Execute ``steps`` in order, skipping ones already satisfied.

    Stops at the first failure so dependent steps don't run against a missing
    prerequisite. The returned :class:`ProvisionResult` records one
    :class:`StepOutcome` per step that was reached.
    """
    result = ProvisionResult(target=target, dry_run=dry_run)
    for step in steps:
        present = False
        if step.check is not None:
            try:
                present = bool(step.check())
            except Exception:  # noqa: BLE001 - unknown existence; fall through to apply.
                present = False

        if present:
            result.steps.append(StepOutcome(step.name, "exists", step.description))
            continue
        if dry_run:
            result.steps.append(StepOutcome(step.name, "would-create", step.description))
            continue
        try:
            detail = step.apply()
            result.steps.append(
                StepOutcome(step.name, "created", str(detail) if detail else step.description)
            )
        except Exception as exc:  # noqa: BLE001 - surface as a structured failure, don't crash.
            result.steps.append(
                StepOutcome(step.name, "failed", step.description, error=str(exc))
            )
            break
    return result
