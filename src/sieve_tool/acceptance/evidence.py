"""Verify actual fault/workload evidence rather than CLI exit codes alone."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sieve_tool.acceptance.suite import (
    Case,
    ImageManifest,
    Selection,
    Suite,
    file_hash,
    image_references,
)
from sieve_tool.oracle.artifacts import (
    WorkloadOutcome,
    read_artifact,
    read_outcome,
    read_state,
)
from sieve_tool.plans import PauseControllerAction, RestartControllerAction, load_plan
from sieve_tool.revisions import SourceRevision
from sieve_tool.trace import iter_api_events


class Phase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str
    log: str
    returncode: int | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    version: Literal[1] = 1
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    selection: Selection
    source: SourceRevision
    images: ImageManifest
    phases: list[Phase] = Field(default_factory=list)
    runtime_registry: str
    aliases_removed: bool = False
    error: str | None = None


class RawResult(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    injection_completed: bool
    workload_completed: bool
    no_exception: bool
    number_errors: int
    detected_errors: list[str]
    test_plan_content: str
    execution: WorkloadOutcome


class Observations(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    final_consistency_checked: bool
    kube_root_ca_configmap_present: bool
    final_marker: str
    original_cr_uid: str
    final_cr_uid: str
    final_secret_uid: str
    secret_owner_uids: list[str]
    status_secret_uid: str


class ResourceMetadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    uid: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)
    deletion_timestamp: str | None = Field(default=None, alias="deletionTimestamp")


class ResourceItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    kind: str
    metadata: ResourceMetadata
    status: dict[str, object] | None = None


class ResourceSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    items: list[ResourceItem]


SNAPSHOT_ADAPTER = TypeAdapter(ResourceSnapshot)


RUN_ADAPTER = TypeAdapter(RunRecord)
RAW_ADAPTER = TypeAdapter(dict[str, dict[str, dict[str, dict[str, RawResult]]]])
OBSERVATIONS_ADAPTER = TypeAdapter(Observations)


class EvidenceError(ValueError):
    pass


def require(condition: bool, path: Path, message: str) -> None:
    if not condition:
        raise EvidenceError(f"{path}: {message}")


def verify_workload(directory: Path, *, capture_ca: bool = True) -> None:
    outcome = read_outcome(directory / "process-outcome.json")
    require(
        outcome.status == "completed" and outcome.returncode == 0,
        directory,
        "workload process did not complete successfully",
    )
    require(
        (directory / "workload.log").read_text().strip() == "FINISH-SIEVE-TEST",
        directory,
        "workload finish marker missing or accompanied by errors",
    )
    observation = read_artifact(
        directory / "workload-observations.json", OBSERVATIONS_ADAPTER
    )
    require(
        observation.final_consistency_checked
        and observation.kube_root_ca_configmap_present,
        directory,
        "final consistency or CA observation missing",
    )
    require(
        observation.final_marker == "recreated"
        and bool(observation.original_cr_uid)
        and bool(observation.final_cr_uid)
        and observation.original_cr_uid != observation.final_cr_uid,
        directory,
        "custom resource was not recreated",
    )
    require(
        bool(observation.final_secret_uid)
        and observation.status_secret_uid == observation.final_secret_uid
        and observation.final_cr_uid in observation.secret_owner_uids,
        directory,
        "Secret ownership or status reference is inconsistent",
    )
    if capture_ca:
        require(
            "configmap/default/kube-root-ca.crt"
            in read_state(directory / "state.json"),
            directory,
            "CA ConfigMap missing from captured state",
        )


def case_paths(output: Path, suite: Suite, case: Case) -> tuple[Path, Path]:
    base = output / "test"
    directory = base / suite.controller / suite.workload / "test" / case.name
    raw = base / f"{suite.controller}-{suite.workload}-{case.name}.yaml.json"
    return directory, raw


def verify_case(output: Path, suite: Suite, case: Case) -> dict[str, str | int]:
    plan_path = output / "plans" / case.relative_path
    require(
        file_hash(plan_path) == case.sha256,
        plan_path,
        "plan hash differs from the frozen suite",
    )
    plan = load_plan(plan_path)
    directory, raw_path = case_paths(output, suite, case)
    envelope = read_artifact(raw_path, RAW_ADAPTER)
    try:
        records = envelope[suite.controller][suite.workload]["test"]
    except KeyError as error:
        raise EvidenceError(
            f"{raw_path}: result belongs to a different controller/workload"
        ) from error
    require(len(records) == 1, raw_path, "expected one raw test result")
    raw = next(iter(records.values()))
    require(
        raw.test_plan_content == plan_path.read_text(),
        raw_path,
        "raw result describes a different plan",
    )
    require(
        raw.no_exception and raw.injection_completed and raw.workload_completed,
        raw_path,
        "injection/workload incomplete or harness exception recorded",
    )
    require(
        raw.number_errors == 0 and not raw.detected_errors,
        raw_path,
        "raw oracle alarms present; inspect this unmodified result",
    )
    require(
        raw.execution.status == "completed" and raw.execution.returncode == 0,
        raw_path,
        "raw execution record did not complete",
    )
    verify_workload(directory)
    server = (directory / "sieve-server.log").read_text()
    require(
        "Sieve test coordinator finishes all actions" in server,
        directory,
        "coordinator did not complete its actions",
    )
    restarts = server.count("controller pod gets deleted")
    held = released = 0
    for action in plan.actions or []:
        if isinstance(action, PauseControllerAction):
            held = server.count(
                f": Pause controller at {action.pause_at} {action.pause_scope}\n"
            )
            released = server.count(
                f": End pause controller at {action.pause_at} {action.pause_scope}\n"
            )
            require(
                held > 0 and held == released,
                directory,
                "requested reads were not blocked and released in the matching scope",
            )
    if case.family == "intermediate-state":
        require(
            sum(isinstance(a, RestartControllerAction) for a in plan.actions or [])
            == restarts
            == 1,
            directory,
            "expected exactly one observed restart",
        )
        require(
            "controller pod 0 ready now" in server,
            directory,
            "replacement pod did not reach readiness",
        )
        # Actual pod UIDs in the API trace distinguish replacement from the
        # initial pod; readiness messages alone cannot establish a restart.
        pod_uids = []
        for event in iter_api_events(directory / "apiserver1.log"):
            if (
                event.rtype == "pod"
                and (event.get_metadata_value("labels") or {}).get("sievetag")
                == suite.controller
            ):
                uid = event.get_metadata_value("uid")
                if isinstance(uid, str) and uid not in pod_uids:
                    pod_uids.append(uid)
        require(
            len(pod_uids) >= 2,
            directory,
            "no distinct controller pod identities in API evidence",
        )
        resources = read_artifact(
            directory / "resources-snapshot.json", SNAPSHOT_ADAPTER
        )
        pods = [
            item
            for item in resources.items
            if item.kind == "Pod"
            and item.metadata.labels.get("sievetag") == suite.controller
        ]
        require(bool(pods), directory, "controller pod snapshot missing")
        for pod in pods:
            require(
                pod.metadata.uid in pod_uids[1:]
                and not pod.metadata.deletion_timestamp,
                directory,
                "snapshot does not contain a replacement pod",
            )
            conditions = (pod.status or {}).get("conditions")
            require(
                isinstance(conditions, list),
                directory,
                "pod readiness conditions missing",
            )
            assert isinstance(conditions, list)
            require(
                any(
                    isinstance(c, dict)
                    and c.get("type") == "Ready"
                    and c.get("status") == "True"
                    for c in conditions
                ),
                directory,
                "replacement pod is not Ready",
            )

    else:
        require(
            restarts == 0 and held > 0,
            directory,
            "unexpected fault evidence for a missed-state plan",
        )
    return {
        "plan": case.name,
        "family": case.family,
        "pod_restarts": restarts,
        "blocked_reads": held,
        "released_reads": released,
        "raw_alarms": raw.number_errors,
    }


def verify(
    output: Path, suite: Suite, *, require_done: bool = True
) -> dict[str, object]:
    record = read_artifact(output / "run.json", RUN_ADAPTER)
    require(
        record.status == "completed"
        and record.aliases_removed
        and record.error is None,
        output,
        "acceptance run is not complete or image alias cleanup failed",
    )
    if require_done:
        require(
            (output / "DONE").read_text().strip() == "completed",
            output,
            "terminal marker missing",
        )
    require(
        bool(record.images.build_inputs)
        and set(record.images.images)
        == set(image_references(suite, record.images.registry)),
        output,
        "image provenance is incomplete",
    )
    cases = suite.select(record.selection)
    expected_phases = ["vanilla", "learn", *(case.name for case in cases)]
    require(
        [p.name for p in record.phases] == expected_phases
        and all(p.returncode == 0 for p in record.phases),
        output,
        "missing or failed execution phases",
    )
    for mode in ("learn", "learn_prev"):
        verify_workload(output / "learn" / suite.controller / suite.workload / mode)
    verify_workload(
        output / "vanilla" / suite.controller / suite.workload / "vanilla",
        capture_ca=False,
    )
    rows = [verify_case(output, suite, case) for case in cases]
    return {
        "status": "passed",
        "source_revision": record.source["revision"],
        "selection": record.selection,
        "plans_executed": len(rows),
        "injections_completed": len(rows),
        "workloads_completed": len(rows),
        "observed_restarts": sum(int(row["pod_restarts"]) for row in rows),
        "blocked_and_released_reads": sum(
            int(row["blocked_reads"]) > 0 for row in rows
        ),
        "raw_alarms": 0,
        "root_ca_states_verified": len(rows) + 2,
        "tests": rows,
    }
