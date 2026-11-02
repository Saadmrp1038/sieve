"""Small, deterministic metadata lifetimes and adjacent delivery placements."""

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from sieve_tool.plans import DeliveryOverlap, FaultPlan
from sieve_tool.testing.sequences import MetadataHistory, MetadataStep

OPERATORS = ("external-secrets", "cert-manager")
CHANNELS: tuple[Literal["labels", "annotations"], ...] = ("labels", "annotations")
HISTORIES: tuple[Literal["remove-readd", "restore-value"], ...] = (
    "remove-readd",
    "restore-value",
)
SCOPES = {
    "external-secrets": (
        "externalsecret",
        "github.com/external-secrets/external-secrets/pkg/controllers/externalsecret.(*Reconciler).Reconcile",
    ),
    "cert-manager": (
        "certificate",
        "github.com/cert-manager/cert-manager/pkg/controller/certificates/issuing.(*controller).ProcessItem",
    ),
}


@dataclass(frozen=True)
class MetadataCase:
    operator: str
    channel: Literal["labels", "annotations"]
    history_name: Literal["remove-readd", "restore-value"]
    hold_after_step: int

    def __post_init__(self) -> None:
        if (
            self.operator not in OPERATORS
            or self.channel not in CHANNELS
            or self.history_name not in HISTORIES
            or type(self.hold_after_step) is not int
            or self.hold_after_step not in (1, 2)
        ):
            raise ValueError(
                "metadata case requires a supported operator/history/channel and adjacent placement 1 or 2"
            )

    @property
    def release_after_step(self) -> int:
        return self.hold_after_step + 1

    @property
    def history(self) -> MetadataHistory:
        steps = (
            (
                MetadataStep("add", "one"),
                MetadataStep("remove", None),
                MetadataStep("add", "two"),
            )
            if self.history_name == "remove-readd"
            else (
                MetadataStep("add", "one"),
                MetadataStep("change", "two"),
                MetadataStep("change", "one"),
            )
        )
        return MetadataHistory(self.channel, steps)

    @property
    def workload(self) -> str:
        return f"metadata-{self.channel}-{self.history_name}-at-{self.hold_after_step}-overlap"

    @property
    def case_id(self) -> str:
        return f"{self.operator}/{self.workload}"

    def metadata_plan(self) -> dict[str, object]:
        return {
            "schema_version": 2,
            "operator": self.operator,
            "case_id": self.case_id,
            "workload": self.workload,
            "history_name": self.history_name,
            "generated": [
                asdict(c.history)
                for c in explore(operators=(self.operator,))
                if c.hold_after_step == 2
            ],
            "selected": asdict(self.history),
            "schedule": "overlap",
            "workers": 10,
            "placement": {
                "hold_after_step": self.hold_after_step,
                "release_after_step": self.release_after_step,
            },
            "monitors": ["latest_state", "metadata_cleanup", "history_equivalence"],
            "repair_timeout": 180,
            "settle_seconds": 15,
        }

    def plan(self, mode: Literal["observe", "hold"]) -> FaultPlan:
        kind, reconciler = SCOPES[self.operator]
        key = kind + "/default/sieve-metadata-subject"
        return FaultPlan(
            workload=self.workload,
            actions=[],
            coverage_resources=[key],
            delivery_overlap=DeliveryOverlap(
                mode=mode,
                resource_key=key,
                reconciler=reconciler,
                hold_phase=f"metadata-{self.hold_after_step}-{self.history.steps[self.hold_after_step - 1].operation}",
                release_phase=f"metadata-{self.release_after_step}-{self.history.steps[self.release_after_step - 1].operation}",
                timeout_seconds=20,
            ),
        )


def explore(
    *, operators: tuple[str, ...] = OPERATORS, max_cases: int = 16
) -> list[MetadataCase]:
    if type(max_cases) is not int or not 1 <= max_cases <= 16:
        raise ValueError("metadata exploration limit requires 1–16 cases")
    if (
        not operators
        or len(set(operators)) != len(operators)
        or any(op not in OPERATORS for op in operators)
    ):
        raise ValueError("select unique supported operators")
    return [
        MetadataCase(operator, channel, history, hold)
        for hold in (2, 1)
        for history in HISTORIES
        for channel in CHANNELS
        for operator in operators
    ][:max_cases]


def case_for_workload(operator: str, workload: str) -> MetadataCase:
    for case in explore(operators=(operator,)):
        if case.workload == workload:
            return case
    raise ValueError("unknown metadata history/placement workload")


def validate_case_plan(value: dict[str, Any]) -> MetadataCase:
    case = case_for_workload(value["operator"], value["workload"])
    expected = case.metadata_plan()
    timed = ("repair_timeout", "settle_seconds")
    if set(value) != set(expected) or json.dumps(
        {k: value[k] for k in value if k not in timed}, sort_keys=True
    ) != json.dumps(
        {k: expected[k] for k in expected if k not in timed}, sort_keys=True
    ):
        raise ValueError(
            "metadata case history/placement does not match its frozen workload"
        )
    for key in timed:
        number = value[key]
        if (
            isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not math.isfinite(number)
            or number < 0
            or key == "repair_timeout"
            and number == 0
        ):
            raise ValueError("invalid metadata case duration")
    return case
