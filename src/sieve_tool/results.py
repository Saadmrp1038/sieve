"""Oracle verdicts and their existing serialized result fields."""

from dataclasses import dataclass
from typing import TypedDict


class VerdictFields(TypedDict):
    injection_completed: bool
    workload_completed: bool
    number_errors: int
    detected_errors: list[str]
    no_exception: bool
    exception_message: str


@dataclass
class TestResult:
    injection_completed: bool
    workload_completed: bool
    common_errors: list[str]
    end_state_errors: list[str]
    history_errors: list[str]
    no_exception: bool
    exception_message: str

    def to_dict(self) -> VerdictFields:
        errors = self.common_errors + self.end_state_errors + self.history_errors
        return {
            "injection_completed": self.injection_completed,
            "workload_completed": self.workload_completed,
            "number_errors": len(errors),
            "detected_errors": errors,
            "no_exception": self.no_exception,
            "exception_message": self.exception_message,
        }
