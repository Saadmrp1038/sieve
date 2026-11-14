"""Exercise blue-green promotion failure and recovery through real traffic."""

import argparse
import json
import math
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from sieve_tool.process import ProcessError, execution
from sieve_tool.testing.child_repair import get, kubectl

ROLLOUT = "sieve-bluegreen"
ACTIVE_SERVICE = "sieve-bluegreen-active"
PREVIEW_SERVICE = "sieve-bluegreen-preview"
ANALYSIS_TEMPLATE = "sieve-bluegreen-fail"
REVISION_1 = "revision-1"
REVISION_2 = "revision-2"
HASH_LABEL = "rollouts-pod-template-hash"
EXPECTED_THREADS = {
    "--rollout-threads=10",
    "--service-threads=10",
    "--experiment-threads=10",
    "--analysis-threads=30",
    "--ingress-threads=10",
}


def apply(manifest: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(kubectl("apply", "-f", "-", "-o", "json", payload=manifest)),
    )


def list_resources(kind: str, *arguments: str) -> list[dict[str, Any]]:
    value = json.loads(kubectl("get", kind, *arguments, "-o", "json"))
    return cast(list[dict[str, Any]], value.get("items") or [])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["bluegreen-post-analysis"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    args = parser.parse_args()
    limits = (args.timeout, args.poll_seconds, args.settle_seconds)
    if not all(math.isfinite(value) for value in limits):
        parser.error("timeouts must be finite")
    if args.timeout <= 0 or args.poll_seconds <= 0 or args.settle_seconds < 0:
        parser.error(
            "timeouts must be positive and settle-seconds must be non-negative"
        )
    return args


def validate_controller(evidence: dict[str, Any]) -> None:
    deployment = get("deployment", "argo-rollouts")
    if deployment is None:
        raise RuntimeError("argo-rollouts Deployment is missing")
    evidence["controllerDeployment"] = deployment
    containers = deployment["spec"]["template"]["spec"]["containers"]
    controller = next(item for item in containers if item["name"] == "argo-rollouts")
    expected_names = {value.split("=", 1)[0] for value in EXPECTED_THREADS}
    thread_args = {
        value
        for value in controller.get("args", [])
        if value.split("=", 1)[0] in expected_names
    }
    if thread_args != EXPECTED_THREADS:
        missing = ", ".join(sorted(EXPECTED_THREADS - thread_args))
        raise RuntimeError("Experiment requires native worker settings: " + missing)

    definitions = list_resources("customresourcedefinitions")
    installed = {item["metadata"]["name"] for item in definitions}
    required = {
        "rollouts.argoproj.io",
        "analysisruns.argoproj.io",
        "analysistemplates.argoproj.io",
        "experiments.argoproj.io",
    }
    if missing_definitions := required - installed:
        raise RuntimeError(
            "Missing native watched APIs: "
            + ", ".join(sorted(missing_definitions))
        )


def controller_identity() -> dict[str, Any] | None:
    pods = list_resources("pods", "-l", "sievetag=argo-rollouts")
    if not pods:
        return None
    pod = sorted(pods, key=lambda item: item["metadata"]["name"])[0]
    status = next(
        (
            item
            for item in pod.get("status", {}).get("containerStatuses") or []
            if item.get("name") == "argo-rollouts"
        ),
        {},
    )
    return {
        "name": pod["metadata"].get("name"),
        "uid": pod["metadata"].get("uid"),
        "restartCount": status.get("restartCount", 0),
    }


def service_response() -> dict[str, Any]:
    try:
        value = kubectl(
            "get",
            "--raw",
            "/api/v1/namespaces/default/services/"
            f"http:{ACTIVE_SERVICE}:http/proxy/",
        ).strip()
        return {"ok": True, "value": value}
    except ProcessError as error:
        return {"ok": False, "error": str(error)}


def service_hash(service: dict[str, Any] | None) -> str | None:
    return cast(
        str | None,
        (service or {}).get("spec", {}).get("selector", {}).get(HASH_LABEL),
    )


def replica_revision(
    replicasets: list[dict[str, Any]], pod_hash: str | None
) -> str | None:
    for replicaset in replicasets:
        if replicaset.get("metadata", {}).get("labels", {}).get(HASH_LABEL) == pod_hash:
            return cast(
                str | None,
                replicaset.get("spec", {})
                .get("template", {})
                .get("metadata", {})
                .get("labels", {})
                .get("sieve.io/revision"),
            )
    return None


def endpoint_revisions(
    endpoint_slices: list[dict[str, Any]], pods: list[dict[str, Any]]
) -> list[str]:
    pod_revisions = {
        pod.get("metadata", {}).get("uid"): pod.get("metadata", {})
        .get("labels", {})
        .get("sieve.io/revision")
        for pod in pods
    }
    revisions = []
    for endpoint_slice in endpoint_slices:
        for endpoint in endpoint_slice.get("endpoints") or []:
            if endpoint.get("conditions", {}).get("ready") is False:
                continue
            uid = endpoint.get("targetRef", {}).get("uid")
            if revision := pod_revisions.get(uid):
                revisions.append(cast(str, revision))
    return sorted(revisions)


def observe(phase: str) -> dict[str, Any]:
    rollout = get("rollout", ROLLOUT)
    active_service = get("service", ACTIVE_SERVICE)
    preview_service = get("service", PREVIEW_SERVICE)
    replicasets = list_resources("replicasets", "-l", f"app={ROLLOUT}")
    pods = list_resources("pods", "-l", f"app={ROLLOUT}")
    analysis_runs = list_resources("analysisruns")
    jobs = list_resources("jobs")
    endpoint_slices = list_resources(
        "endpointslices.discovery.k8s.io",
        "-l",
        f"kubernetes.io/service-name={ACTIVE_SERVICE}",
    )
    active_hash = service_hash(active_service)
    return {
        "phase": phase,
        "monotonicNS": time.monotonic_ns(),
        "controller": controller_identity(),
        "rollout": rollout,
        "activeService": active_service,
        "previewService": preview_service,
        "activeHash": active_hash,
        "previewHash": service_hash(preview_service),
        "activeRevision": replica_revision(replicasets, active_hash),
        "replicaSets": replicasets,
        "pods": pods,
        "analysisRuns": analysis_runs,
        "jobs": jobs,
        "endpointSlices": endpoint_slices,
        "endpointRevisions": endpoint_revisions(endpoint_slices, pods),
        "service": service_response(),
    }


def rollout_status(sample: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], (sample.get("rollout") or {}).get("status", {}))


def observed_current_generation(sample: dict[str, Any]) -> bool:
    rollout = sample.get("rollout") or {}
    return rollout_status(sample).get("observedGeneration") == str(
        rollout.get("metadata", {}).get("generation")
    )


def initial_consistent(sample: dict[str, Any]) -> bool:
    status = rollout_status(sample)
    bluegreen = status.get("blueGreen", {})
    stable_hash = status.get("stableRS")
    return bool(
        observed_current_generation(sample)
        and status.get("phase") == "Healthy"
        and stable_hash
        and status.get("currentPodHash") == stable_hash
        and bluegreen.get("activeSelector") == stable_hash
        and sample.get("activeHash") == stable_hash
        and sample.get("previewHash") == stable_hash
        and sample.get("activeRevision") == REVISION_1
        and sample.get("endpointRevisions") == [REVISION_1]
        and sample.get("service") == {"ok": True, "value": REVISION_1}
    )


def revision_two_serving(sample: dict[str, Any]) -> bool:
    status = rollout_status(sample)
    return bool(
        observed_current_generation(sample)
        and status.get("stableRS")
        and status.get("currentPodHash") != status.get("stableRS")
        and sample.get("activeHash") == status.get("currentPodHash")
        and sample.get("activeRevision") == REVISION_2
        and sample.get("endpointRevisions") == [REVISION_2]
        and sample.get("service") == {"ok": True, "value": REVISION_2}
    )


def failed_analysis(sample: dict[str, Any]) -> bool:
    return any(
        run.get("status", {}).get("phase") in {"Failed", "Error"}
        for run in sample.get("analysisRuns", [])
    )


def final_converged(sample: dict[str, Any], initial_hash: str) -> bool:
    status = rollout_status(sample)
    bluegreen = status.get("blueGreen", {})
    return bool(
        observed_current_generation(sample)
        and status.get("phase") == "Degraded"
        and status.get("abort") is True
        and status.get("stableRS") == initial_hash
        and status.get("currentPodHash") != initial_hash
        and bluegreen.get("activeSelector") == initial_hash
        and sample.get("activeHash") == initial_hash
        and sample.get("activeRevision") == REVISION_1
        and sample.get("endpointRevisions") == [REVISION_1]
        and sample.get("service") == {"ok": True, "value": REVISION_1}
        and failed_analysis(sample)
    )


def evaluate_semantics(
    initial: dict[str, Any], promoted: dict[str, Any], final: dict[str, Any]
) -> dict[str, bool]:
    initial_status = rollout_status(initial)
    final_status = rollout_status(final)
    initial_hash = cast(str, initial_status.get("stableRS"))
    return {
        "initial_revision_one_is_healthy_and_serving": initial_consistent(initial),
        "revision_two_was_served_before_analysis_failed": revision_two_serving(
            promoted
        ),
        "post_promotion_analysis_failed": failed_analysis(final),
        "abort_preserved_the_original_stable_hash": (
            final_status.get("abort") is True
            and final_status.get("stableRS") == initial_hash
            and final_status.get("currentPodHash") != initial_hash
        ),
        "active_service_and_status_returned_to_stable": (
            final.get("activeHash") == initial_hash
            and final_status.get("blueGreen", {}).get("activeSelector")
            == initial_hash
        ),
        "live_traffic_returned_to_revision_one": (
            final.get("activeRevision") == REVISION_1
            and final.get("service") == {"ok": True, "value": REVISION_1}
        ),
        "settled_active_endpoints_only_select_revision_one": (
            final.get("endpointRevisions") == [REVISION_1]
        ),
    }


def record_outcome(
    output: Path, evidence: dict[str, Any], *, error: str | None = None
) -> None:
    if error is not None:
        evidence["error"] = error
        evidence["traceback"] = traceback.format_exc()
    save_evidence(output, evidence)
    if checks := evidence.get("checks"):
        output.with_name("semantic-outcome.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checks": [
                        {"name": name, "passed": passed}
                        for name, passed in checks.items()
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    if error is None:
        output.write_text("FINISH-SIEVE-TEST\n")
    elif evidence.get("observations"):
        output.write_text("FAILED-SIEVE-TEST: " + error + "\n")


def save_evidence(output: Path, evidence: dict[str, Any]) -> None:
    output.with_name("argo-rollouts-bluegreen.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )


def run(args: argparse.Namespace, evidence: dict[str, Any]) -> None:
    validate_controller(evidence)
    evidence["status"] = "setup-failed"

    def accept(value: dict[str, Any]) -> dict[str, Any]:
        evidence["accepted"].append(value)
        return value

    for name in (ACTIVE_SERVICE, PREVIEW_SERVICE):
        accept(
            apply(
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": name},
                    "spec": {
                        "selector": {"app": ROLLOUT},
                        "ports": [
                            {"name": "http", "port": 80, "targetPort": "http"}
                        ],
                    },
                }
            )
        )

    accept(
        apply(
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "AnalysisTemplate",
                "metadata": {"name": ANALYSIS_TEMPLATE},
                "spec": {
                    "metrics": [
                        {
                            "name": "deterministic-failure",
                            "count": 1,
                            "provider": {
                                "job": {
                                    "spec": {
                                        "backoffLimit": 0,
                                        "template": {
                                            "spec": {
                                                "restartPolicy": "Never",
                                                "containers": [
                                                    {
                                                        "name": "fail",
                                                        "image": "busybox:1.37.0",
                                                        "imagePullPolicy": "IfNotPresent",
                                                        "command": [
                                                            "/bin/sh",
                                                            "-c",
                                                            "sleep 8; exit 1",
                                                        ],
                                                    }
                                                ],
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    ]
                },
            }
        )
    )

    accept(
        apply(
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Rollout",
                "metadata": {"name": ROLLOUT},
                "spec": {
                    "replicas": 1,
                    "revisionHistoryLimit": 2,
                    "selector": {"matchLabels": {"app": ROLLOUT}},
                    "strategy": {
                        "blueGreen": {
                            "activeService": ACTIVE_SERVICE,
                            "previewService": PREVIEW_SERVICE,
                            "autoPromotionEnabled": True,
                            "postPromotionAnalysis": {
                                "templates": [
                                    {"templateName": ANALYSIS_TEMPLATE}
                                ]
                            },
                        }
                    },
                    "template": {
                        "metadata": {
                            "labels": {
                                "app": ROLLOUT,
                                "sieve.io/revision": REVISION_1,
                            }
                        },
                        "spec": {
                            "containers": [
                                {
                                    "name": "http",
                                    "image": "busybox:1.37.0",
                                    "imagePullPolicy": "IfNotPresent",
                                    "command": ["/bin/sh", "-c"],
                                    "args": [
                                        "mkdir -p /www; "
                                        "printf '%s' \"$REVISION\" > /www/index.html; "
                                        "exec httpd -f -p 8080 -h /www"
                                    ],
                                    "env": [
                                        {"name": "REVISION", "value": REVISION_1}
                                    ],
                                    "ports": [
                                        {"name": "http", "containerPort": 8080}
                                    ],
                                    "readinessProbe": {
                                        "httpGet": {"path": "/", "port": "http"},
                                        "periodSeconds": 1,
                                        "failureThreshold": 30,
                                    },
                                }
                            ]
                        },
                    },
                },
            }
        )
    )

    def sample_until(
        predicate: Callable[[dict[str, Any]], bool],
        phase: str,
        description: str,
        *,
        settle_seconds: float = 0,
    ) -> dict[str, Any]:
        end = time.monotonic() + args.timeout
        stable_since: float | None = None
        stable_controller: str | None = None
        last = None
        while time.monotonic() < end:
            last = observe(phase)
            evidence["observations"].append(last)
            save_evidence(args.output, evidence)
            controller_uid = (last.get("controller") or {}).get("uid")
            if predicate(last):
                if stable_since is None or stable_controller != controller_uid:
                    stable_since = time.monotonic()
                    stable_controller = controller_uid
                if time.monotonic() - stable_since >= settle_seconds:
                    return last
            else:
                stable_since = None
                stable_controller = controller_uid
            time.sleep(args.poll_seconds)
        raise RuntimeError(f"Timed out: {description}; last={last!r}")

    evidence["status"] = "initial-incomplete"
    initial = sample_until(
        initial_consistent,
        "initial",
        "revision 1 health, selectors, endpoints, and live traffic agreement",
        settle_seconds=args.settle_seconds,
    )
    evidence["initial"] = initial
    initial_hash = cast(str, rollout_status(initial)["stableRS"])

    evidence["status"] = "promotion-incomplete"
    patch = [
        {
            "op": "replace",
            "path": "/spec/template/metadata/labels/sieve.io~1revision",
            "value": REVISION_2,
        },
        {
            "op": "replace",
            "path": "/spec/template/spec/containers/0/env/0/value",
            "value": REVISION_2,
        },
    ]
    evidence["revision2Patch"] = json.loads(
        kubectl(
            "patch",
            "rollout",
            ROLLOUT,
            "--type=json",
            "-p",
            json.dumps(patch),
            "-o",
            "json",
        )
    )
    promoted = sample_until(
        revision_two_serving,
        "post-promotion-analysis",
        "revision 2 selected by the active Service and reachable through it",
    )
    evidence["promoted"] = promoted

    evidence["status"] = "abort-recovery-incomplete"
    final = sample_until(
        lambda sample: final_converged(sample, initial_hash),
        "abort-recovery",
        "failed analysis fallback to revision 1",
        settle_seconds=args.settle_seconds,
    )
    evidence["final"] = final
    evidence["checks"] = evaluate_semantics(initial, promoted, final)
    evidence["semantic_status"] = (
        "passed" if all(evidence["checks"].values()) else "failed"
    )
    if evidence["semantic_status"] != "passed":
        raise RuntimeError("Independent semantic checks failed")
    evidence["status"] = "completed"


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "workload": args.workload,
        "status": "configuration-failed",
        "semantic_status": "not-evaluated",
        "expectedThreads": sorted(EXPECTED_THREADS),
        "accepted": [],
        "observations": [],
    }

    try:
        with execution(
            {"request": 45, "workload": 900},
            args.output.parent / "kubectl-commands",
        ):
            run(args, evidence)
    except Exception as error:
        record_outcome(args.output, evidence, error=str(error))
        print(str(error), file=sys.stderr)
        return 1

    record_outcome(args.output, evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
