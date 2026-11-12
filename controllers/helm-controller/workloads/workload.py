"""Exercise a failed Helm upgrade, rollback remediation, and state recovery."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import sys
import tarfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import yaml

from sieve_tool.process import ProcessError, execution
from sieve_tool.testing.child_repair import get, kubectl

RELEASE = "sieve-upgrade"
SOURCE = "sieve-upgrade-chart"
SOURCE_SERVER = "sieve-helm-source"
REVISION_1 = "revision-1"
REVISION_2 = "revision-2"
VERSION_1 = "0.1.0"
VERSION_2 = "0.2.0"


def chart_archive(version: str, revision: str, *, fail_upgrade: bool) -> bytes:
    chart = {
        "apiVersion": "v2",
        "name": RELEASE,
        "description": "Synthetic Sieve Helm upgrade fixture",
        "type": "application",
        "version": version,
        "appVersion": revision,
    }
    labels = """app.kubernetes.io/name: sieve-upgrade
    app.kubernetes.io/instance: {{ .Release.Name }}"""
    files = {
        "Chart.yaml": yaml.safe_dump(chart, sort_keys=False),
        "values.yaml": "{}\n",
        "templates/configmap.yaml": f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: {{{{ .Release.Name }}}}-content
  labels:
    {labels}
data:
  index.html: {revision}
""",
        "templates/deployment.yaml": f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{{{ .Release.Name }}}}
  labels:
    {labels}
spec:
  replicas: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: sieve-upgrade
      app.kubernetes.io/instance: {{{{ .Release.Name }}}}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: sieve-upgrade
        app.kubernetes.io/instance: {{{{ .Release.Name }}}}
      annotations:
        sieve.io/revision: {revision}
    spec:
      containers:
        - name: http
          image: busybox:1.37.0
          command: ["httpd", "-f", "-p", "8080", "-h", "/www"]
          ports:
            - name: http
              containerPort: 8080
          readinessProbe:
            httpGet:
              path: /
              port: http
          volumeMounts:
            - name: content
              mountPath: /www
      volumes:
        - name: content
          configMap:
            name: {{{{ .Release.Name }}}}-content
""",
        "templates/service.yaml": f"""apiVersion: v1
kind: Service
metadata:
  name: {{{{ .Release.Name }}}}
  labels:
    {labels}
spec:
  selector:
    app.kubernetes.io/name: sieve-upgrade
    app.kubernetes.io/instance: {{{{ .Release.Name }}}}
  ports:
    - name: http
      port: 80
      targetPort: http
""",
    }
    if fail_upgrade:
        files["templates/post-upgrade-fail.yaml"] = """apiVersion: batch/v1
kind: Job
metadata:
  name: {{ .Release.Name }}-post-upgrade-fail
  annotations:
    helm.sh/hook: post-upgrade
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: fail
          image: busybox:1.37.0
          command: ["/bin/sh", "-c", "exit 1"]
"""

    tar_data = io.BytesIO()
    with tarfile.open(fileobj=tar_data, mode="w") as archive:
        for name, content in sorted(files.items()):
            body = content.encode()
            entry = tarfile.TarInfo(f"{RELEASE}/{name}")
            entry.mode = 0o644
            entry.mtime = 0
            entry.size = len(body)
            archive.addfile(entry, io.BytesIO(body))
    return gzip.compress(tar_data.getvalue(), mtime=0)


def apply(manifest: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(kubectl("apply", "-f", "-", "-o", "json", payload=manifest)),
    )


def patch(
    kind: str,
    name: str,
    value: dict[str, Any],
    *,
    status: bool = False,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            kubectl(
                "patch",
                kind,
                name,
                "--type=merge",
                "-p",
                json.dumps(value),
                *(["--subresource=status"] if status else []),
                "-o",
                "json",
            )
        ),
    )


def decode_release(value: str) -> dict[str, Any]:
    outer = base64.b64decode(value)
    try:
        compressed = base64.b64decode(outer, validate=True)
    except ValueError:
        compressed = outer
    if compressed[:2] == b"\x1f\x8b":
        compressed = gzip.decompress(compressed)
    return cast(dict[str, Any], json.loads(compressed))


def helm_releases() -> list[dict[str, Any]]:
    raw = kubectl(
        "get",
        "secrets",
        "-l",
        f"owner=helm,name={RELEASE}",
        "-o",
        "json",
    )
    result = []
    for secret in json.loads(raw).get("items", []):
        encoded = secret.get("data", {}).get("release")
        if not encoded:
            continue
        release = decode_release(encoded)
        info = release.get("info", {})
        chart = release.get("chart", {}).get("metadata", {})
        manifest = release.get("manifest", "")
        result.append(
            {
                "secret": secret.get("metadata", {}).get("name"),
                "uid": secret.get("metadata", {}).get("uid"),
                "resourceVersion": secret.get("metadata", {}).get("resourceVersion"),
                "name": release.get("name"),
                "namespace": release.get("namespace"),
                "version": release.get("version"),
                "status": info.get("status"),
                "description": info.get("description"),
                "chartVersion": chart.get("version"),
                "appVersion": chart.get("appVersion"),
                "manifestSHA256": hashlib.sha256(manifest.encode()).hexdigest(),
            }
        )
    return sorted(result, key=lambda item: item.get("version") or 0)


def ready_condition(resource: dict[str, Any] | None) -> dict[str, Any] | None:
    if resource is None:
        return None
    return next(
        (
            condition
            for condition in resource.get("status", {}).get("conditions", [])
            if condition.get("type") == "Ready"
        ),
        None,
    )


def controller_identity() -> dict[str, Any] | None:
    raw = kubectl("get", "pods", "-l", "sievetag=helm-controller", "-o", "json")
    pods = cast(list[dict[str, Any]], json.loads(raw).get("items", []))
    if not pods:
        return None
    pod = sorted(pods, key=lambda item: item["metadata"]["name"])[0]
    status: dict[str, Any] = next(
        (
            item
            for item in pod.get("status", {}).get("containerStatuses", [])
            if item.get("name") == "manager"
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
        response = kubectl(
            "get",
            "--raw",
            f"/api/v1/namespaces/default/services/http:{RELEASE}/proxy/",
        ).strip()
        return {"ok": True, "value": response}
    except ProcessError as error:
        return {"ok": False, "error": str(error)}


def observe(phase: str) -> dict[str, Any]:
    parent = get("helmrelease", RELEASE)
    deployment = get("deployment", RELEASE)
    configmap = get("configmap", f"{RELEASE}-content")
    pods = json.loads(
        kubectl(
            "get",
            "pods",
            "-l",
            f"app.kubernetes.io/instance={RELEASE}",
            "-o",
            "json",
        )
    ).get("items", [])
    return {
        "phase": phase,
        "monotonicNS": time.monotonic_ns(),
        "controller": controller_identity(),
        "helmRelease": parent,
        "ready": ready_condition(parent),
        "history": (parent or {}).get("status", {}).get("history", []),
        "storage": helm_releases(),
        "deployment": deployment,
        "configMap": configmap,
        "pods": pods,
        "service": service_response() if deployment is not None else None,
    }


def deployment_revision(sample: dict[str, Any]) -> str | None:
    deployment = sample.get("deployment") or {}
    return cast(
        str | None,
        deployment.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("annotations", {})
        .get("sieve.io/revision"),
    )


def deployment_ready(sample: dict[str, Any]) -> bool:
    deployment = sample.get("deployment") or {}
    status = deployment.get("status", {})
    return bool(
        status.get("observedGeneration")
        == deployment.get("metadata", {}).get("generation")
        and status.get("readyReplicas", 0) == 1
        and status.get("updatedReplicas", 0) == 1
    )


def initial_consistent(sample: dict[str, Any]) -> bool:
    parent = sample.get("helmRelease") or {}
    ready = sample.get("ready") or {}
    storage = sample.get("storage") or []
    latest = storage[-1] if storage else {}
    history = sample.get("history") or []
    service = sample.get("service") or {}
    return (
        ready.get("status") == "True"
        and ready.get("observedGeneration")
        == parent.get("metadata", {}).get("generation")
        and latest.get("status") == "deployed"
        and latest.get("chartVersion") == VERSION_1
        and bool(history)
        and history[0].get("chartVersion") == VERSION_1
        and deployment_revision(sample) == REVISION_1
        and deployment_ready(sample)
        and (sample.get("configMap") or {}).get("data", {}).get("index.html")
        == REVISION_1
        and service == {"ok": True, "value": REVISION_1}
    )


def final_converged(sample: dict[str, Any]) -> bool:
    parent = sample.get("helmRelease") or {}
    ready = sample.get("ready") or {}
    storage = sample.get("storage") or []
    latest = storage[-1] if storage else {}
    history = sample.get("history") or []
    history_versions = {item.get("version") for item in history}
    service = sample.get("service") or {}
    conditions = parent.get("status", {}).get("conditions", [])
    return (
        ready.get("status") == "False"
        and ready.get("observedGeneration")
        == parent.get("metadata", {}).get("generation")
        and any(
            condition.get("type") == "Stalled" and condition.get("status") == "True"
            for condition in conditions
        )
        and latest.get("status") == "deployed"
        and latest.get("chartVersion") == VERSION_1
        and latest.get("version") in history_versions
        and deployment_revision(sample) == REVISION_1
        and deployment_ready(sample)
        and (sample.get("configMap") or {}).get("data", {}).get("index.html")
        == REVISION_1
        and service == {"ok": True, "value": REVISION_1}
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["failed-upgrade-remediation"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
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


def run(args: argparse.Namespace, evidence: dict[str, Any]) -> None:
    deployment = get("deployment", "helm-controller")
    if deployment is None:
        raise RuntimeError("helm-controller Deployment is missing")
    evidence["controllerDeployment"] = deployment
    containers = deployment["spec"]["template"]["spec"]["containers"]
    manager = next(item for item in containers if item["name"] == "manager")
    worker_args = [
        value for value in manager.get("args", []) if value.startswith("--concurrent")
    ]
    if worker_args != ["--concurrent=10"]:
        raise RuntimeError("Experiment requires native --concurrent=10")

    crds = json.loads(kubectl("get", "customresourcedefinitions", "-o", "json"))
    installed = {item["metadata"]["name"] for item in crds.get("items", [])}
    required = {
        "helmreleases.helm.toolkit.fluxcd.io",
        "helmcharts.source.toolkit.fluxcd.io",
        "ocirepositories.source.toolkit.fluxcd.io",
        "externalartifacts.source.toolkit.fluxcd.io",
    }
    if missing := required - installed:
        raise RuntimeError("Missing native watched APIs: " + ", ".join(sorted(missing)))

    evidence["status"] = "setup-failed"
    revision_1 = chart_archive(VERSION_1, REVISION_1, fail_upgrade=False)
    revision_2 = chart_archive(VERSION_2, REVISION_2, fail_upgrade=True)
    charts = {"revision-1.tgz": revision_1, "revision-2.tgz": revision_2}
    for name, body in charts.items():
        args.output.parent.joinpath(name).write_bytes(body)
        evidence["charts"][name] = {
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    evidence["accepted"].append(
        apply(
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": SOURCE_SERVER},
                "binaryData": {
                    name: base64.b64encode(body).decode()
                    for name, body in charts.items()
                },
            }
        )
    )
    evidence["accepted"].append(
        apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": SOURCE_SERVER},
                "spec": {
                    "selector": {"app": SOURCE_SERVER},
                    "ports": [{"port": 8080, "targetPort": 8080}],
                },
            }
        )
    )
    evidence["accepted"].append(
        apply(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": SOURCE_SERVER},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"app": SOURCE_SERVER}},
                    "template": {
                        "metadata": {"labels": {"app": SOURCE_SERVER}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "http",
                                    "image": "busybox:1.37.0",
                                    "command": [
                                        "httpd",
                                        "-f",
                                        "-p",
                                        "8080",
                                        "-h",
                                        "/www",
                                    ],
                                    "volumeMounts": [
                                        {"name": "charts", "mountPath": "/www"}
                                    ],
                                }
                            ],
                            "volumes": [
                                {
                                    "name": "charts",
                                    "configMap": {"name": SOURCE_SERVER},
                                }
                            ],
                        },
                    },
                },
            }
        )
    )
    kubectl("rollout", "status", f"deployment/{SOURCE_SERVER}", "--timeout=120s")

    source = apply(
        {
            "apiVersion": "source.toolkit.fluxcd.io/v1",
            "kind": "HelmChart",
            "metadata": {"name": SOURCE},
            "spec": {
                "interval": "1h",
                "chart": "./synthetic",
                "sourceRef": {"kind": "GitRepository", "name": "synthetic"},
            },
        }
    )
    evidence["accepted"].append(source)

    def publish(filename: str, revision: str) -> dict[str, Any]:
        chart = evidence["charts"][filename]
        result = patch(
            "helmchart",
            SOURCE,
            {
                "status": {
                    "observedGeneration": source["metadata"]["generation"],
                    "conditions": [
                        {
                            "type": "Ready",
                            "status": "True",
                            "reason": "ChartPullSucceeded",
                            "message": "synthetic chart is ready",
                            "observedGeneration": source["metadata"]["generation"],
                            "lastTransitionTime": "2026-11-11T00:00:00Z",
                        }
                    ],
                    "artifact": {
                        "digest": "sha256:" + chart["sha256"],
                        "lastUpdateTime": "2026-11-11T00:00:00Z",
                        "path": f"helmchart/default/{SOURCE}/{filename}",
                        "revision": revision,
                        "size": chart["bytes"],
                        "url": (
                            f"http://{SOURCE_SERVER}.default.svc.cluster.local:8080/"
                            + filename
                        ),
                    },
                }
            },
            status=True,
        )
        evidence["accepted"].append(result)
        return result

    publish("revision-1.tgz", VERSION_1)
    evidence["accepted"].append(
        apply(
            {
                "apiVersion": "helm.toolkit.fluxcd.io/v2",
                "kind": "HelmRelease",
                "metadata": {"name": RELEASE},
                "spec": {
                    "interval": "1m",
                    "timeout": "1m",
                    "releaseName": RELEASE,
                    "targetNamespace": "default",
                    "storageNamespace": "default",
                    "chartRef": {
                        "kind": "HelmChart",
                        "name": SOURCE,
                        "namespace": "default",
                    },
                    "upgrade": {
                        "timeout": "1m",
                        "strategy": {"name": "RemediateOnFailure"},
                        "remediation": {
                            "retries": 0,
                            "strategy": "rollback",
                            "remediateLastFailure": True,
                        },
                    },
                    "rollback": {"timeout": "1m"},
                },
            }
        )
    )

    def sample_until(
        predicate: Callable[[dict[str, Any]], bool],
        phase: str,
        description: str,
    ) -> dict[str, Any]:
        end = time.monotonic() + args.timeout
        while time.monotonic() < end:
            sample = observe(phase)
            evidence["observations"].append(sample)
            if predicate(sample):
                return sample
            time.sleep(args.poll_seconds)
        raise RuntimeError("Timed out: " + description)

    evidence["initial"] = sample_until(
        initial_consistent,
        "initial",
        "revision 1 status, storage, resources, and service agreement",
    )
    evidence["status"] = "upgrade-incomplete"
    publish("revision-2.tgz", VERSION_2)

    stable_since: float | None = None
    stable_controller: str | None = None
    end = time.monotonic() + args.timeout
    final = None
    while time.monotonic() < end:
        sample = observe("upgrade-and-remediation")
        evidence["observations"].append(sample)
        controller_uid = (sample.get("controller") or {}).get("uid")
        if final_converged(sample):
            if stable_since is None or controller_uid != stable_controller:
                stable_since = time.monotonic()
                stable_controller = controller_uid
            if time.monotonic() - stable_since >= args.settle_seconds:
                final = sample
                break
        else:
            stable_since = None
            stable_controller = controller_uid
        time.sleep(args.poll_seconds)
    if final is None:
        raise RuntimeError("Timed out: failed upgrade rollback convergence")

    evidence["final"] = final
    storage_versions = {item.get("version") for item in final["storage"]}
    history_versions = {item.get("version") for item in final["history"]}
    ready = final.get("ready") or {}
    parent = final.get("helmRelease") or {}
    latest = final["storage"][-1]
    evidence["checks"] = {
        "initial_status_storage_live_service_agree": initial_consistent(
            evidence["initial"]
        ),
        "failed_upgrade_is_not_reported_ready": (
            ready.get("status") == "False"
            and ready.get("observedGeneration")
            == parent.get("metadata", {}).get("generation")
        ),
        "rollback_storage_and_status_agree": (
            latest.get("status") == "deployed"
            and latest.get("chartVersion") == VERSION_1
            and latest.get("version") in history_versions
        ),
        "rollback_live_resources_and_service_agree": (
            deployment_revision(final) == REVISION_1
            and (final.get("configMap") or {}).get("data", {}).get("index.html")
            == REVISION_1
            and final.get("service") == {"ok": True, "value": REVISION_1}
        ),
        "durable_storage_revisions_are_in_status_history": (
            storage_versions <= history_versions
        ),
    }
    evidence["semantic_status"] = (
        "passed" if all(evidence["checks"].values()) else "failed"
    )
    evidence["status"] = "completed"


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence_path = args.output.with_name("helm-upgrade.json")
    evidence: dict[str, Any] = {
        "workload": args.workload,
        "status": "configuration-failed",
        "semantic_status": "not-evaluated",
        "workers": 10,
        "charts": {},
        "accepted": [],
        "observations": [],
    }

    def save() -> None:
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    try:
        with execution(
            {"request": 45, "workload": 900},
            args.output.parent / "kubectl-commands",
        ):
            run(args, evidence)
    except Exception as error:
        evidence["error"] = str(error)
        save()
        print(str(error), file=sys.stderr)
        return 1

    save()
    args.output.with_name("semantic-outcome.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checks": [
                    {"name": name, "passed": passed}
                    for name, passed in evidence["checks"].items()
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
