#!/usr/bin/env python3
from pathlib import Path

import yaml

from sieve_tool.common.common import sieve_modes
from sieve_tool.reproduce import reprod_map

operators_for_CI = {
    # "cass-operator": ["recreate", "scaledown-scaleup"],
    "cassandra-operator": ["recreate", "scaledown-scaleup"],
    "casskop-operator": ["recreate", "scaledown-to-zero", "reducepdb"],
    # "elastic-operator": ["recreate", "scaledown-scaleup"],
    "mongodb-operator": [
        "recreate",
        "scaleup-scaledown",
        "disable-enable-shard",
        "disable-enable-arbiter",
        "run-cert-manager",
    ],
    "nifikop-operator": ["recreate", "scaledown-scaleup", "change-config"],
    "rabbitmq-operator": ["recreate", "scaleup-scaledown", "resize-pvc"],
    "xtradb-operator": [
        "recreate",
        "disable-enable-haproxy",
        "disable-enable-proxysql",
        "run-cert-manager",
        "scaleup-scaledown",
    ],
    "yugabyte-operator": [
        "recreate",
        "scaleup-scaledown-tserver",
        "disable-enable-tls",
        "disable-enable-tuiport",
    ],
    "zookeeper-operator": ["recreate", "scaledown-scaleup"],
}

operators_to_run_tests = [
    # "cass-operator",
    # "cassandra-operator",
    # "casskop-operator",
    # "elastic-operator",
    # "mongodb-operator",
    # "nifikop-operator",
    # "rabbitmq-operator",
    # "xtradb-operator",
    # "yugabyte-operator",
    # "zookeeper-operator",
]


def job_template(self_hosted):
    return {
        "runs-on": "self-hosted" if self_hosted else "ubuntu-22.04",
        "env": {
            "GOPATH": "/home/runner/go",
            "KUBECONFIG": "/home/runner/.kube/config",
        },
        "steps": [
            {"uses": "actions/checkout@v6"},
            {
                "name": "Setup Go environment",
                "uses": "actions/setup-go@v6",
                "with": {
                    "go-version-file": "go.work",
                    "cache-dependency-path": "**/go.sum",
                },
            },
            {
                "name": "Install uv",
                "uses": "astral-sh/setup-uv@07a047e608db84b3d8c06cd9a7c7dcc9c355c18e",  # v10.3.2
                "with": {"enable-cache": True, "cache-dependency-glob": "uv.lock"},
            },
            {
                "name": "Setup GitHub Package Registry",
                "run": 'echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u "${{ github.actor }}" --password-stdin',
            },
            {
                "name": "Install locked Python environment",
                "run": "uv sync --locked --no-dev",
            },
            {
                "name": "Install Kind",
                "run": 'GO111MODULE="on" go install sigs.k8s.io/kind@v0.33.0\nkind',
            },
            {
                "name": "Install Helm",
                "run": "wget https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz\ntar -zxvf helm-v4.3.1-linux-amd64.tar.gz\nsudo mv linux-amd64/helm /usr/local/bin/helm\nhelm",
            },
            {
                "name": "Sieve CI config generate",
                "run": "mkdir -p configs\n"
                + "printf '%s\\n' '{\"workload_conditional_wait_timeout\": 1000}' > configs/local.json",
            },
        ],
    }


def collect_log_step(operator):
    return {
        "uses": "actions/upload-artifact@v4",
        "if": "always()",
        "with": {"name": "sieve-%s-log" % (operator), "path": "runs"},
    }


def persistent_data_step(operator):
    return {
        "uses": "JamesIves/github-pages-deploy-action@4.1.5",
        "name": "Persistent oracle data",
        "with": {
            "branch": "oracle-data",
            "folder": "runs/oracles/%s" % (operator),
            "target-folder": "%s/oracle" % (operator),
        },
    }


def remove_cluster_step():
    return {
        "name": "Remove cluster",
        "if": "always()",
        "run": "kind delete cluster",
    }


def clean_images_step():
    return {
        "name": "Clean images",
        "if": "always()",
        "run": "docker image prune -a -f && docker builder prune -a -f && docker system df",
    }


def generate_controller_image_build_jobs(self_hosted):
    jobs = {}
    for operator in operators_for_CI:
        job = job_template(self_hosted)
        build_modes = [
            sieve_modes.LEARN,
            sieve_modes.TEST,
            sieve_modes.VANILLA,
        ]
        build_image = [
            {
                "name": "Build Image - %s" % (mode),
                "run": "uv run --locked --no-dev sieve build -c %s -m %s --push -r $IMAGE_NAMESPACE "
                % (operator, mode),
            }
            for mode in build_modes
        ]
        job["steps"].extend(build_image)
        job["steps"].append(clean_images_step())
        jobs[operator] = job
    return jobs


def generate_oracle_build_jobs(self_hosted):
    jobs = {}
    for operator in operators_for_CI:
        job = job_template(self_hosted)
        workload_set = set(operators_for_CI[operator])
        sieve_learn = [
            {
                "name": "Sieve Learn - %s %s" % (operator, workload),
                "run": "uv run --locked --no-dev sieve learn -c %s -w %s --build-oracle -r $IMAGE_NAMESPACE"
                % (operator, workload),
            }
            for workload in sorted(workload_set)
        ]
        job["steps"].extend(sieve_learn)
        job["steps"].append(collect_log_step(operator))
        job["steps"].append(remove_cluster_step())
        job["steps"].append(persistent_data_step(operator))
        job["steps"].append(clean_images_step())
        jobs[operator] = job
    return jobs


def generate_bug_reproduction_jobs(self_hosted):
    jobs = {}
    for operator in operators_for_CI:
        job = job_template(self_hosted)
        sieve_test = []
        for bug in sorted(reprod_map[operator].keys()):
            if "indirect" in bug:
                continue
            sieve_test.append(
                {
                    "name": "Sieve Test - %s %s" % (operator, bug),
                    "run": "uv run --locked --no-dev sieve reproduce -c %s -b %s -r $IMAGE_NAMESPACE"
                    % (operator, bug),
                }
            )
        job["steps"].extend(sieve_test)
        job["steps"].append(collect_log_step(operator))
        job["steps"].append(remove_cluster_step())
        job["steps"].append(clean_images_step())
        jobs[operator] = job
    return jobs


def generate_test_jobs(self_hosted):
    jobs = {}
    for operator in operators_for_CI:
        job = job_template(self_hosted)
        build_modes = [
            sieve_modes.LEARN,
            sieve_modes.TEST,
            sieve_modes.VANILLA,
        ]
        workload_set = set(operators_for_CI[operator])
        build_image = [
            {
                "name": "Build Image - %s" % (mode),
                "run": "uv run --locked --no-dev sieve build -c %s -m %s -r $IMAGE_NAMESPACE"
                % (operator, mode),
            }
            for mode in build_modes
        ]
        job["steps"].extend(build_image)
        if operator in operators_to_run_tests:
            sieve_learn = [
                {
                    "name": "Sieve Learn - %s %s" % (operator, workload),
                    "run": "uv run --locked --no-dev sieve learn -c %s -w %s --build-oracle -r $IMAGE_NAMESPACE"
                    % (operator, workload),
                }
                for workload in sorted(workload_set)
            ]
            sieve_test = []
            for bug in sorted(reprod_map[operator].keys()):
                if "indirect" in bug:
                    continue
                sieve_test.append(
                    {
                        "name": "Sieve Test - %s %s" % (operator, bug),
                        "run": "uv run --locked --no-dev sieve reproduce -c %s -b %s -r $IMAGE_NAMESPACE"
                        % (operator, bug),
                    }
                )
            job["steps"].extend(sieve_learn)
            job["steps"].extend(sieve_test)
            job["steps"].append(collect_log_step(operator))
            job["steps"].append(remove_cluster_step())
        jobs[operator] = job
    return jobs


def workflow_definitions():
    return {
        "regression-testing.yml": {
            "name": "Regression Testing",
            "on": {"pull_request": None, "workflow_dispatch": None},
            "env": {"IMAGE_NAMESPACE": "ghcr.io/sieve-project/action"},
            "jobs": generate_test_jobs(False),
        },
        "example-controller-image-build.yml": {
            "name": "Controller Image Build",
            "on": {"workflow_dispatch": None},
            "permissions": {"contents": "read", "packages": "write"},
            "env": {"IMAGE_NAMESPACE": "ghcr.io/saadmrp1038/sieve"},
            "jobs": generate_controller_image_build_jobs(False),
        },
        "oracle-generation.yml": {
            "name": "Oracle Generation",
            "on": {"workflow_dispatch": None},
            "env": {"IMAGE_NAMESPACE": "ghcr.io/sieve-project/action"},
            "jobs": generate_oracle_build_jobs(True),
        },
        "bug-reproduction.yml": {
            "name": "Bug Reproduction",
            "on": {"workflow_dispatch": None},
            "env": {"IMAGE_NAMESPACE": "ghcr.io/sieve-project/action"},
            "jobs": generate_bug_reproduction_jobs(True),
        },
    }


def generate_workflows(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in workflow_definitions().items():
        (directory / name).write_text(
            "# Generated by `uv run sieve ci generate`.\n"
            + yaml.safe_dump(data, sort_keys=False, width=1000)
        )
