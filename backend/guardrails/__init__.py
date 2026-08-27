"""Guardrails: pure deterministic Python, no LLM calls, ever.

Every guardrail returns a GuardrailResult. Never a bare bool, never an exception on the
normal blocking path. blocking=True means the action cannot proceed under any circumstance.
blocking=False with passed=False downgrades the tier; it does not forbid the action.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class GuardrailResult:
    name: str
    passed: bool
    blocking: bool
    message: str
    evaluated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "blocking": self.blocking,
            "message": self.message,
            "evaluated_at": self.evaluated_at,
        }
