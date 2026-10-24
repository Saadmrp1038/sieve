"""Build instrumented controller images in the repository cache."""

import os
import shutil
from pathlib import Path
from sieve_tool.common.common import sieve_modes, os_system, rmtree_if_exists
from sieve_tool.common.config import ControllerConfig
from sieve_tool.paths import repository_root
from .dependencies import (
    DEFAULT_K8S_VERSION,
    k8s_version_to_apimachinery_version,
    update_sieve_client_go_mod_with_version,
    install_module,
)
from .instrumenter import run_instrumenter


def download_controller(
    controller_config: ControllerConfig,
):
    """
    Download the controller.

    :param controller_config: the controller configuration
    """
    application_dir = os.path.join(
        ".cache/controllers", controller_config.controller_name
    )
    rmtree_if_exists(application_dir)
    Path(application_dir).parent.mkdir(parents=True, exist_ok=True)
    os_system(
        "git clone {} {} >> /dev/null".format(
            controller_config.github_link, application_dir
        )
    )
    os.chdir(application_dir)
    os_system("git checkout {} >> /dev/null".format(controller_config.commit))
    os_system("git checkout -b sieve >> /dev/null")
    for commit in controller_config.cherry_pick_commits:
        os_system("git cherry-pick {}".format(commit))
    os.chdir(repository_root())


def install_lib_for_controller(controller_config: ControllerConfig):
    """
    Copy (1) sieve client, (2) client-go and (3) other modules that require instrumentation to the controller folder
    for later instrumentation.

    :param controller_config: the controller configuration
    """
    application_dir = os.path.join(
        ".cache/controllers", controller_config.controller_name
    )
    sieve_dep_src_dir = os.path.join(
        application_dir,
        "sieve-dependency/src",
    )

    # install sieve_client
    shutil.copytree(
        "go/client",
        os.path.join(
            sieve_dep_src_dir,
            "sieve.client",
        ),
    )
    if controller_config.kubernetes_version != DEFAULT_K8S_VERSION:
        update_sieve_client_go_mod_with_version(
            os.path.join(sieve_dep_src_dir, "sieve.client", "go.mod"),
            k8s_version_to_apimachinery_version(controller_config.kubernetes_version),
        )
    elif controller_config.apimachinery_version is not None:
        update_sieve_client_go_mod_with_version(
            os.path.join(sieve_dep_src_dir, "sieve.client", "go.mod"),
            controller_config.apimachinery_version,
        )

    # download client-go
    install_module(
        "k8s.io/client-go@{}".format(controller_config.client_go_version),
        sieve_dep_src_dir,
    )

    # download the other dependencies
    downloaded_module = set()
    for api_to_instrument in controller_config.apis_to_instrument:
        module = api_to_instrument["module"]
        if module in downloaded_module:
            continue
        downloaded_module.add(module)
        install_module(module, sieve_dep_src_dir)

    os.chdir(application_dir)
    os_system("git add -A >> /dev/null")
    os_system(
        'git -c user.name=Sieve -c user.email=sieve@localhost commit -m "Install instrumentation dependencies" >> /dev/null'
    )
    os.chdir(repository_root())


def remove_replacement_in_go_mod_file(file):
    """
    Remove the existing `k8s.io/client-go => ` in the go.mod of the controller
    because later we will modify the go.mod to import the instrumented client-go.

    :param file: the go.mod file of the controller
    """
    # TODO: we should also remove the replace entry for other instrumented modules
    lines = []
    with open(file, "r") as go_mod_file:
        lines = go_mod_file.readlines()
    with open(file, "w") as go_mod_file:
        for line in lines:
            if "k8s.io/client-go => " in line:
                continue
            go_mod_file.write(line)


def update_go_mod_for_controller(
    controller_config_dir,
    controller_config: ControllerConfig,
):
    """
    Update the go.mod of the controller to import sieve client and the local client-go and other modules.

    :param controller_config_dir: the folder containing the build script provided by controller developers
    :param controller_config: the controller configuration
    """
    application_dir = os.path.join(
        ".cache/controllers", controller_config.controller_name
    )
    # modify the go.mod to import the libs
    remove_replacement_in_go_mod_file(os.path.join(application_dir, "go.mod"))
    with open("{}/go.mod".format(application_dir), "a") as go_mod_file:
        go_mod_file.write("require sieve.client v0.0.0\n")
        go_mod_file.write(
            "replace sieve.client => ./sieve-dependency/src/sieve.client\n"
        )
        go_mod_file.write(
            "replace k8s.io/client-go => ./sieve-dependency/src/k8s.io/client-go@{}\n".format(
                controller_config.client_go_version
            )
        )
        added_module = set()
        for api_to_instrument in controller_config.apis_to_instrument:
            module = api_to_instrument["module"]
            if module in added_module:
                continue
            added_module.add(module)
            go_mod_file.write(
                "replace {} => ./sieve-dependency/src/{}\n".format(
                    module.split("@")[0], module
                )
            )

    # copy the build.sh and Dockerfile
    shutil.copy(
        os.path.join(controller_config_dir, "build", "build.sh"),
        os.path.join(application_dir, "build.sh"),
    )
    shutil.copy(
        os.path.join(controller_config_dir, "build", "Dockerfile"),
        os.path.join(application_dir, controller_config.dockerfile_path),
    )


def instrument_controller(controller_config: ControllerConfig, mode):
    application = (
        repository_root() / ".cache/controllers" / controller_config.controller_name
    )
    run_instrumenter(
        {
            "project": controller_config.controller_name,
            "mode": mode,
            "app_file_path": str(application),
            "client_go_filepath": str(
                application
                / "sieve-dependency/src/k8s.io"
                / ("client-go@" + controller_config.client_go_version)
            ),
            "annotated_reconcile_functions": controller_config.annotated_reconcile_functions,
            "apis_to_instrument": controller_config.apis_to_instrument,
        }
    )


def install_lib_for_controller_with_vendor(controller_config: ControllerConfig):
    """
    A variant of install_lib_for_controller for controllers that use vendor.

    :param controller_config: the controller configuration
    """
    application_dir = os.path.join(
        ".cache/controllers", controller_config.controller_name
    )
    shutil.copytree(
        "go/client",
        os.path.join(application_dir, controller_config.vendored_sieve_client_path),
    )
    if controller_config.kubernetes_version != DEFAULT_K8S_VERSION:
        update_sieve_client_go_mod_with_version(
            os.path.join(
                application_dir, controller_config.vendored_sieve_client_path, "go.mod"
            ),
            k8s_version_to_apimachinery_version(controller_config.kubernetes_version),
        )
    elif controller_config.apimachinery_version is not None:
        update_sieve_client_go_mod_with_version(
            os.path.join(
                application_dir, controller_config.vendored_sieve_client_path, "go.mod"
            ),
            controller_config.apimachinery_version,
        )
    os.chdir(application_dir)
    os_system("git add -A >> /dev/null")
    os_system(
        'git -c user.name=Sieve -c user.email=sieve@localhost commit -m "Install instrumentation dependencies" >> /dev/null'
    )
    os.chdir(repository_root())


def update_go_mod_for_controller_with_vendor(
    controller_config_dir,
    controller_config: ControllerConfig,
):
    """
    A variant of update_go_mod_for_controller for controllers that use vendor.

    :param controller_config_dir: the folder containing the build script provided by controller developers
    :param controller_config: the controller configuration
    """
    application_dir = os.path.join(
        ".cache/controllers", controller_config.controller_name
    )
    with open(os.path.join(application_dir, "go.mod"), "a") as go_mod_file:
        go_mod_file.write("require sieve.client v0.0.0\n")
    # copy the build.sh and Dockerfile
    shutil.copy(
        os.path.join(controller_config_dir, "build", "build.sh"),
        os.path.join(application_dir, "build.sh"),
    )
    shutil.copy(
        os.path.join(controller_config_dir, "build", "Dockerfile"),
        os.path.join(application_dir, controller_config.dockerfile_path),
    )
    os.chdir(application_dir)
    os_system("git add -A >> /dev/null")
    os_system(
        'git -c user.name=Sieve -c user.email=sieve@localhost commit -m "Import instrumentation dependencies" >> /dev/null'
    )
    os.chdir(repository_root())


def instrument_controller_with_vendor(controller_config: ControllerConfig, mode):
    application = (
        repository_root() / ".cache/controllers" / controller_config.controller_name
    )
    run_instrumenter(
        {
            "project": controller_config.controller_name,
            "mode": mode,
            "app_file_path": str(application),
            "client_go_filepath": str(
                application / controller_config.vendored_client_go_path
            ),
            "annotated_reconcile_functions": controller_config.annotated_reconcile_functions,
            "apis_to_instrument": controller_config.apis_to_instrument,
        }
    )


def build_controller(
    controller_config: ControllerConfig,
    image_tag,
    container_registry,
):
    """
    Build the controller docker image.

    :param controller_config: the controller configuration
    :param container_registry: the name of the container registry as part of the image name
    :param image_tag: the tag of the image
    """
    application_dir = os.path.join(
        ".cache/controllers", controller_config.controller_name
    )
    os.chdir(application_dir)
    os_system("./build.sh {} {}".format(container_registry, image_tag))
    os.chdir(repository_root())
    os_system(
        "docker tag {} {}/{}:{}".format(
            controller_config.controller_image_name,
            container_registry,
            controller_config.controller_name,
            image_tag,
        )
    )


def push_controller(
    controller_config: ControllerConfig,
    image_tag,
    container_registry,
):
    """
    Push the controller image to a remote registry.

    :param controller_config: the controller configuration
    :param container_registry: the name of the container registry to push the image to
    :param image_tag: the tag of the image
    """
    os_system(
        "docker push {}/{}:{}".format(
            container_registry,
            controller_config.controller_name,
            image_tag,
        )
    )


def setup_controller(
    controller_config_dir,
    controller_config: ControllerConfig,
    mode,
    image_tag,
    build_only,
    push_to_remote,
    container_registry,
):
    """
    Download the controller, import sieve client, client-go and other modules that require instrumentation,
    build the controller image and push to the remote registry if needed.
    It works for controllers that use mod or vendor.

    :param controller_config_dir: the folder containing the build script provided by the controller developer
    :param controller_config: the controller configuration
    :param mode: the building mode
    :param image_tag: the tag of the image
    :param build_only: if true then directly build the local controller code instead of downloading it
    :param push_to_remote: if true then push the image to container_registry
    :param container_registry: the name of the container registry to push the image to
    """
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
    controller_config: ControllerConfig,
    mode,
    build_only,
    push_to_remote,
    container_registry,
):
    image_tag = mode
    if mode == "all":
        for this_mode in [
            sieve_modes.LEARN,
            sieve_modes.TEST,
            sieve_modes.VANILLA,
        ]:
            image_tag = this_mode
            setup_controller(
                controller_config_dir,
                controller_config,
                this_mode,
                image_tag,
                build_only,
                push_to_remote,
                container_registry,
            )
    else:
        setup_controller(
            controller_config_dir,
            controller_config,
            mode,
            image_tag,
            build_only,
            push_to_remote,
            container_registry,
        )
