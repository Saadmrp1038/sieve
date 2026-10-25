import os
import json
import time
from sieve_tool.common.common import cprint, bcolors, rmtree_if_exists
import shutil
from sieve_tool.process import run as run_process
import sys
from pathlib import Path
from sieve_tool.paths import repository_root
from sieve_tool.runtime import runner


def before_reproducing_yugabyte_operator_indirect_1():
    current_crd = (
        "controllers/yugabyte-operator/deploy/crds/yugabyte.com_ybclusters_crd.yaml"
    )
    bkp_crd = (
        "controllers/yugabyte-operator/deploy/crds/yugabyte.com_ybclusters_crd.yaml.bkp"
    )
    current_crd, bkp_crd = repository_root() / current_crd, repository_root() / bkp_crd
    shutil.copy(current_crd, bkp_crd)
    fin = open(repository_root() / current_crd)
    data = fin.read()
    data = data.replace("# minimum: 1", "minimum: 1")
    fin.close()
    fout = open(repository_root() / current_crd, "w")
    fout.write(data)
    fout.close()


def after_reproducing_yugabyte_operator_indirect_1():
    current_crd = (
        "controllers/yugabyte-operator/deploy/crds/yugabyte.com_ybclusters_crd.yaml"
    )
    bkp_crd = (
        "controllers/yugabyte-operator/deploy/crds/yugabyte.com_ybclusters_crd.yaml.bkp"
    )
    shutil.move(repository_root() / bkp_crd, repository_root() / current_crd)


def before_reproducing_cassandra_operator_indirect_1():
    current_cfg = "controllers/cassandra-operator/config.json"
    bkp_cfg = "controllers/cassandra-operator/config.json.bkp"
    current_cfg, bkp_cfg = repository_root() / current_cfg, repository_root() / bkp_cfg
    shutil.copy(current_cfg, bkp_cfg)
    data = json.load(open(repository_root() / current_cfg))
    del data["cherry_pick_commits"]
    json.dump(data, open(repository_root() / current_cfg, "w"), indent=4)
    print(
        "building cassandra-operator image without the fix of https://github.com/instaclustr/cassandra-operator/issues/400"
    )
    run_process(
        [
            sys.executable,
            "-m",
            "sieve_tool",
            "build",
            "-c",
            "cassandra-operator",
            "-m",
            "test",
        ],
        category="build",
        cwd=repository_root(),
        operation="build reproduction controller",
    )


def after_reproducing_cassandra_operator_indirect_1():
    current_cfg = "controllers/cassandra-operator/config.json"
    bkp_cfg = "controllers/cassandra-operator/config.json.bkp"
    shutil.move(repository_root() / bkp_cfg, repository_root() / current_cfg)
    print(
        "building cassandra-operator image with the fix of https://github.com/instaclustr/cassandra-operator/issues/400"
    )
    run_process(
        [
            sys.executable,
            "-m",
            "sieve_tool",
            "build",
            "-c",
            "cassandra-operator",
            "-m",
            "test",
        ],
        category="build",
        cwd=repository_root(),
        operation="build reproduction controller",
    )


manifest_map = {
    "cass-operator": "controllers/cass-operator/",
    "cassandra-operator": "controllers/cassandra-operator/",
    "casskop-operator": "controllers/casskop-operator/",
    "elastic-operator": "controllers/elastic-operator/",
    "mongodb-operator": "controllers/mongodb-operator/",
    "nifikop-operator": "controllers/nifikop-operator/",
    "rabbitmq-operator": "controllers/rabbitmq-operator/",
    "xtradb-operator": "controllers/xtradb-operator/",
    "yugabyte-operator": "controllers/yugabyte-operator/",
    "zookeeper-operator": "controllers/zookeeper-operator/",
}

reprod_map = {
    "cass-operator": {
        "intermediate-state-1": ["recreate", "cass-operator-intermediate-state-1.yaml"],
        "intermediate-state-2": ["recreate", "cass-operator-intermediate-state-2.yaml"],
        "stale-state-1": ["recreate", "cass-operator-stale-state-1.yaml"],
    },
    "cassandra-operator": {
        "stale-state-1": ["recreate", "cassandra-operator-stale-state-1.yaml"],
        "stale-state-2": ["scaledown-scaleup", "cassandra-operator-stale-state-2.yaml"],
        "unobserved-state-1": [
            "scaledown-scaleup",
            "cassandra-operator-unobserved-state-1.yaml",
        ],
        "indirect-1": [
            "scaledown-scaleup",
            "cassandra-operator-indirect-1.yaml",
            before_reproducing_cassandra_operator_indirect_1,
            after_reproducing_cassandra_operator_indirect_1,
        ],
        "indirect-2": [
            "scaledown-scaleup-brittle",
            "cassandra-operator-indirect-2.yaml",
        ],
    },
    "casskop-operator": {
        "intermediate-state-1": [
            "scaledown-to-zero",
            "casskop-intermediate-state-1.yaml",
        ],
        "stale-state-1": ["recreate", "casskop-stale-state-1.yaml"],
        "stale-state-2": ["reducepdb", "casskop-stale-state-2.yaml"],
        "unobserved-state-1": ["scaledown-to-zero", "casskop-unobserved-state-1.yaml"],
    },
    "elastic-operator": {
        "stale-state-1": ["recreate", "elastic-operator-stale-state-1.yaml"],
        "stale-state-2": ["scaledown-scaleup", "elastic-operator-stale-state-2.yaml"],
    },
    "mongodb-operator": {
        "intermediate-state-1": [
            "disable-enable-shard",
            "mongodb-operator-intermediate-state-1.yaml",
        ],
        "intermediate-state-2": [
            "run-cert-manager",
            "mongodb-operator-intermediate-state-2.yaml",
        ],
        "stale-state-1": ["recreate", "mongodb-operator-stale-state-1.yaml"],
        "stale-state-2": [
            "disable-enable-shard",
            "mongodb-operator-stale-state-2.yaml",
        ],
        "stale-state-3": [
            "disable-enable-arbiter",
            "mongodb-operator-stale-state-3.yaml",
        ],
        "unobserved-state-1": [
            "disable-enable-arbiter",
            "mongodb-operator-unobserved-state-1.yaml",
        ],
        "indirect-1": ["disable-enable-shard", "mongodb-operator-indirect-1.yaml"],
        "indirect-2": ["recreate", "mongodb-operator-indirect-2.yaml"],
        "indirect-3": [
            "disable-enable-shard-brittle",
            "mongodb-operator-indirect-3.yaml",
        ],
    },
    "nifikop-operator": {
        "intermediate-state-1": ["change-config", "nifikop-intermediate-state-1.yaml"],
        "intermediate-state-2": ["recreate", "nifikop-intermediate-state-2.yaml"],
        "indirect-1": ["scaledown-scaleup", "nifikop-indirect-1.yaml"],
    },
    "rabbitmq-operator": {
        "intermediate-state-1": [
            "resize-pvc",
            "rabbitmq-operator-intermediate-state-1.yaml",
        ],
        "stale-state-1": ["recreate", "rabbitmq-operator-stale-state-1.yaml"],
        "stale-state-2": ["resize-pvc", "rabbitmq-operator-stale-state-2.yaml"],
        "unobserved-state-1": [
            "scaleup-scaledown",
            "rabbitmq-operator-unobserved-state-1.yaml",
        ],
    },
    "xtradb-operator": {
        "intermediate-state-1": [
            "disable-enable-proxysql",
            "xtradb-operator-intermediate-state-1.yaml",
        ],
        "intermediate-state-2": [
            "run-cert-manager",
            "xtradb-operator-intermediate-state-2.yaml",
        ],
        "intermediate-state-3": [
            "recreate",
            "xtradb-operator-intermediate-state-3.yaml",
        ],
        "stale-state-1": ["recreate", "xtradb-operator-stale-state-1.yaml"],
        "stale-state-2": [
            "disable-enable-haproxy",
            "xtradb-operator-stale-state-2.yaml",
        ],
        "stale-state-3": [
            "disable-enable-proxysql",
            "xtradb-operator-stale-state-3.yaml",
        ],
        "unobserved-state-1": [
            "scaleup-scaledown",
            "xtradb-operator-unobserved-state-1.yaml",
        ],
    },
    "yugabyte-operator": {
        "stale-state-1": ["disable-enable-tls", "yugabyte-operator-stale-state-1.yaml"],
        "stale-state-2": [
            "disable-enable-tuiport",
            "yugabyte-operator-stale-state-2.yaml",
        ],
        "unobserved-state-1": [
            "scaleup-scaledown-tserver",
            "yugabyte-operator-unobserved-state-1.yaml",
        ],
        "indirect-1": [
            "disable-enable-tuiport",
            "yugabyte-operator-indirect-1.yaml",
            before_reproducing_yugabyte_operator_indirect_1,
            after_reproducing_yugabyte_operator_indirect_1,
        ],
        "indirect-2": ["disable-enable-tls", "yugabyte-operator-indirect-2.yaml"],
    },
    "zookeeper-operator": {
        "stale-state-1": ["recreate", "zookeeper-operator-stale-state-1.yaml"],
        "stale-state-2": ["scaledown-scaleup", "zookeeper-operator-stale-state-2.yaml"],
        "unobserved-state-1": [
            "scaledown-scaleup",
            "zookeeper-operator-unobserved-state-1.yaml",
        ],
        "indirect-1": ["recreate", "zookeeper-operator-indirect-1.yaml"],
    },
}


def reproduce_single_bug(controller, bug, registry, skip, output, common_config):
    entry = reprod_map[controller][bug]
    before = entry[2] if len(entry) >= 3 else None
    after = entry[3] if len(entry) >= 4 else None
    root = repository_root()
    plan = root / "fixtures/reproductions" / entry[1]
    if not skip:
        try:
            if before:
                before()
            started = time.time()
            result, context = runner.run(
                str(root / "controllers" / controller),
                entry[0],
                str(output),
                "test",
                str(plan),
                registry,
                False,
                False,
                oracle_root=str(root / "fixtures/oracles"),
                common_config=common_config,
            )
            runner.save_run_result(context, result, started)
        finally:
            if after:
                after()
    path = output / f"{controller}-{entry[0]}-{plan.name}.json"
    data = json.loads(path.read_text())[controller][entry[0]]["test"]
    content = next(iter(data.values()))
    return {
        "reproduced": bool(
            content["number_errors"]
            and content["injection_completed"]
            and content["no_exception"]
        ),
        "test-result-file": str(path),
    }


def reproduce_bug(controller, bug, registry, skip, output, common_config):
    if bug == "all":
        names = [
            name for name in reprod_map[controller] if not name.startswith("indirect")
        ]
    elif bug in ("intermediate-state", "unobserved-state", "stale-state", "indirect"):
        names = [name for name in reprod_map[controller] if name.startswith(bug)]
    else:
        names = [bug]
    return {
        name: reproduce_single_bug(
            controller, name, registry, skip, output, common_config
        )
        for name in names
    }


def run(controller, bug, registry, skip, output, common_config):
    controllers = list(reprod_map) if controller == "all" else [controller]
    if any(name not in reprod_map for name in controllers):
        raise ValueError(f"No historical reproductions for {controller}")
    results = {
        name: reproduce_bug(name, bug, registry, skip, output, common_config)
        for name in controllers
    }
    rows = ["controller\tbug\treproduced\ttest-result-file"]
    for name, cases in results.items():
        for identifier, result in cases.items():
            rows.append(
                f"{name}\t{identifier}\t{result['reproduced']}\t{result['test-result-file']}"
            )
    path = output / "reproduction-stats.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n")
    print(path.read_text())
    return (
        0
        if all(
            result["reproduced"]
            for cases in results.values()
            for result in cases.values()
        )
        else 1
    )
