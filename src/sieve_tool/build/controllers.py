"""Build instrumented controller images in an explicit repository cache."""

from pathlib import Path
import shutil

from sieve_tool.common.common import rmtree_if_exists, sieve_modes
from sieve_tool.common.config import ControllerConfig
from sieve_tool.paths import repository_root
from sieve_tool.process import bounded, run
from .dependencies import install_module
from .instrumenter import run_instrumenter
from .resources import docker_build_scope


def application(config):
    return repository_root() / ".cache/controllers" / config.controller_name


@bounded("network")
def download_controller(controller_config: ControllerConfig):
    target = application(controller_config)
    rmtree_if_exists(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["git", "clone", controller_config.github_link, target],
        operation="clone controller",
        category="network",
    )
    git = ["git", "-C", target]
    run(
        [*git, "checkout", controller_config.commit],
        operation="select controller revision",
    )
    run([*git, "checkout", "-b", "sieve"], operation="create instrumented branch")
    for commit in controller_config.cherry_pick_commits:
        run(
            [
                *git,
                "-c",
                "user.name=Sieve",
                "-c",
                "user.email=sieve@localhost",
                "cherry-pick",
                commit,
            ],
            operation="apply controller patch",
        )


def commit_instrumentation(config, message):
    target = application(config)
    run(["git", "add", "-A"], cwd=target, operation="stage instrumentation")
    run(
        [
            "git",
            "-c",
            "user.name=Sieve",
            "-c",
            "user.email=sieve@localhost",
            "commit",
            "-m",
            message,
        ],
        cwd=target,
        operation="record instrumentation",
    )


def install_lib_for_controller(controller_config: ControllerConfig):
    destination = application(controller_config) / "sieve-dependency/src"
    shutil.copytree(repository_root() / "go/client", destination / "sieve.client")
    install_module(
        f"k8s.io/client-go@{controller_config.client_go_version}", destination
    )
    for module in dict.fromkeys(
        api["module"] for api in controller_config.apis_to_instrument
    ):
        install_module(module, destination)
    commit_instrumentation(controller_config, "Install instrumentation dependencies")


def remove_replacement_in_go_mod_file(file):
    path = Path(file)
    path.write_text(
        "".join(
            line
            for line in path.read_text().splitlines(keepends=True)
            if "k8s.io/client-go => " not in line
        )
    )


def copy_build_files(controller_config_dir, config):
    target = application(config)
    source = Path(controller_config_dir) / "build"
    shutil.copy(source / "build.sh", target / "build.sh")
    shutil.copy(source / "Dockerfile", target / config.dockerfile_path)


def update_go_mod_for_controller(controller_config_dir, controller_config):
    target = application(controller_config) / "go.mod"
    remove_replacement_in_go_mod_file(target)
    with target.open("a") as stream:
        stream.write(
            "require sieve.client v0.0.0\nreplace sieve.client => ./sieve-dependency/src/sieve.client\n"
        )
        stream.write(
            f"replace k8s.io/client-go => ./sieve-dependency/src/k8s.io/client-go@{controller_config.client_go_version}\n"
        )
        for module in dict.fromkeys(
            api["module"] for api in controller_config.apis_to_instrument
        ):
            stream.write(
                f"replace {module.split('@')[0]} => ./sieve-dependency/src/{module}\n"
            )
    copy_build_files(controller_config_dir, controller_config)


def instrumentation_config(config, mode, client_path):
    return {
        "project": config.controller_name,
        "mode": mode,
        "app_file_path": str(application(config)),
        "client_go_filepath": str(client_path),
        "annotated_reconcile_functions": config.annotated_reconcile_functions,
        "apis_to_instrument": config.apis_to_instrument,
    }


def instrument_controller(controller_config, mode):
    run_instrumenter(
        instrumentation_config(
            controller_config,
            mode,
            application(controller_config)
            / "sieve-dependency/src/k8s.io"
            / ("client-go@" + controller_config.client_go_version),
        )
    )


def install_lib_for_controller_with_vendor(controller_config):
    shutil.copytree(
        repository_root() / "go/client",
        application(controller_config) / controller_config.vendored_sieve_client_path,
    )
    commit_instrumentation(controller_config, "Install instrumentation dependencies")


def update_go_mod_for_controller_with_vendor(controller_config_dir, controller_config):
    with (application(controller_config) / "go.mod").open("a") as stream:
        stream.write("require sieve.client v0.0.0\n")
    copy_build_files(controller_config_dir, controller_config)
    commit_instrumentation(controller_config, "Import instrumentation dependencies")


def instrument_controller_with_vendor(controller_config, mode):
    run_instrumenter(
        instrumentation_config(
            controller_config,
            mode,
            application(controller_config) / controller_config.vendored_client_go_path,
        )
    )


def build_controller(controller_config, image_tag, container_registry):
    with docker_build_scope() as environment:
        run(
            ["bash", "./build.sh", container_registry, image_tag],
            cwd=application(controller_config),
            operation="build controller image",
            category="build",
            env=environment,
        )
    run(
        [
            "docker",
            "tag",
            controller_config.controller_image_name,
            f"{container_registry}/{controller_config.controller_name}:{image_tag}",
        ],
        operation="tag controller image",
    )


def push_controller(controller_config, image_tag, container_registry):
    run(
        [
            "docker",
            "push",
            f"{container_registry}/{controller_config.controller_name}:{image_tag}",
        ],
        operation="push controller image",
        category="network",
    )


@bounded("build")
def setup_controller(
    controller_config_dir,
    controller_config,
    mode,
    image_tag,
    build_only,
    push_to_remote,
    container_registry,
):
    if not build_only:
        download_controller(controller_config)
        if controller_config.go_mod == "mod":
            install_lib_for_controller(controller_config)
            update_go_mod_for_controller(controller_config_dir, controller_config)
            instrument_controller(controller_config, mode)
        else:
            install_lib_for_controller_with_vendor(controller_config)
            update_go_mod_for_controller_with_vendor(
                controller_config_dir, controller_config
            )
            instrument_controller_with_vendor(controller_config, mode)
    build_controller(controller_config, image_tag, container_registry)
    if push_to_remote:
        push_controller(controller_config, image_tag, container_registry)


def build_controllers(
    controller_config_dir,
    controller_config,
    mode,
    build_only=False,
    push=False,
    registry=None,
):
    modes = (
        (sieve_modes.LEARN, sieve_modes.TEST, sieve_modes.VANILLA)
        if mode == sieve_modes.ALL
        else (mode,)
    )
    for selected in modes:
        setup_controller(
            controller_config_dir,
            controller_config,
            selected,
            selected,
            build_only,
            push,
            registry,
        )
