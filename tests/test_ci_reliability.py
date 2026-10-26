"""CI selects immutable code and leaves shared Docker resources alone."""

import subprocess
from pathlib import Path

import pytest
import yaml

from sieve_tool import ci
from sieve_tool.process import execution
from sieve_tool.revisions import verify_revision

ROOT = Path(__file__).resolve().parents[1]


def git(root, *arguments):
    return subprocess.check_output(
        ["git", "-C", str(root), *arguments], text=True
    ).strip()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    (root / "source.txt").write_text("original\n")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=CI test",
        "-c",
        "user.email=ci@example.invalid",
        "commit",
        "-qm",
        "initial",
    )
    return root, git(root, "rev-parse", "HEAD")


def test_revision_verification_checks_actual_checkout_and_changes(tmp_path, checkout):
    root, revision = checkout
    with execution(log_dir=tmp_path / "commands"):
        assert verify_revision(root, revision)["revision"] == revision
        with pytest.raises(ValueError, match="expected"):
            verify_revision(root, "0" * 40)
        (root / "source.txt").write_text("modified\n")
        with pytest.raises(ValueError, match="source changes"):
            verify_revision(root, revision)
        git(root, "checkout", "--", "source.txt")
        (root / "extra.py").write_text("untracked code\n")
        with pytest.raises(ValueError, match="source changes"):
            verify_revision(root, revision)


@pytest.mark.parametrize(
    "revision", ["main", "HEAD", "abc123", "a" * 39, "a" * 41, "$(touch unwanted)"]
)
def test_revision_must_be_an_immutable_commit_before_git_runs(tmp_path, revision):
    with pytest.raises(ValueError, match="full lowercase"):
        verify_revision(tmp_path, revision)
    assert not list(tmp_path.iterdir())


def test_no_ci_workflow_prunes_or_blindly_deletes_shared_resources():
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        text = path.read_text()
        assert "docker system prune" not in text, path
        assert "docker image prune" not in text, path
        assert "docker builder prune" not in text, path
        assert "kind delete cluster" not in text, path
    for definition in ci.workflow_definitions().values():
        for job in definition["jobs"].values():
            checkout = job["steps"][0]
            assert (
                checkout["with"]["ref"]
                == "${{ github.event.pull_request.head.sha || github.sha }}"
            )
            assert checkout["with"]["persist-credentials"] is False
            assert any(
                step.get("name") == "Verify tested revision" for step in job["steps"]
            )


def test_reusable_workflow_distinguishes_harness_and_integration_revisions():
    workflow = yaml.load(
        (ROOT / ".github/workflows/run-sieve.yml").read_text(), Loader=yaml.BaseLoader
    )
    inputs = workflow["on"]["workflow_call"]["inputs"]
    for key in ["sieve-revision", "github-ref"]:
        assert inputs[key]["required"] == "true"
        assert "default" not in inputs[key]
    steps = workflow["jobs"]["run-sieve"]["steps"]
    checkouts = [
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert [step["with"]["ref"] for step in checkouts] == [
        "${{ inputs.sieve-revision }}",
        "${{ inputs.github-ref }}",
    ]
    assert checkouts[0]["with"]["repository"] == "Saadmrp1038/sieve"
    assert all(step["with"]["persist-credentials"] == "false" for step in checkouts)
    assert steps[0]["name"] == "Validate immutable revision inputs"


def test_integration_copy_uses_only_tracked_commit_assets(tmp_path, checkout):
    import shutil

    from sieve_tool.revisions import install_integration

    source, _ = checkout
    profile = source / "profile"
    shutil.copytree(ROOT / "controllers/kubernetes-secret-generator", profile)
    (source / ".gitignore").write_text("profile/ignored.txt\n")
    git(source, "add", ".")
    git(
        source,
        "-c",
        "user.name=CI test",
        "-c",
        "user.email=ci@example.invalid",
        "commit",
        "-qm",
        "integration",
    )
    revision = git(source, "rev-parse", "HEAD")
    (profile / "ignored.txt").write_text("untracked host data must not be copied")
    destination = tmp_path / "sieve"
    destination.mkdir()
    with execution(log_dir=tmp_path / "commands"):
        install_integration(
            destination, source, revision, "profile", "kubernetes-secret-generator"
        )
    copied = destination / "controllers/kubernetes-secret-generator"
    assert (copied / "config.json").read_bytes() == (
        profile / "config.json"
    ).read_bytes()
    assert not (copied / "ignored.txt").exists()


@pytest.mark.parametrize(
    "subdirectory", ["../outside", "/outside", "profile/../../outside"]
)
def test_integration_path_cannot_escape_checkout(tmp_path, subdirectory):
    from sieve_tool.revisions import install_integration

    with pytest.raises(ValueError, match="subdirectory"):
        install_integration(tmp_path, tmp_path, "a" * 40, subdirectory, "example")
    assert not list(tmp_path.iterdir())


def test_acceptance_is_manual_and_keeps_dispatch_source_and_failure_artifacts():
    data = yaml.load(
        (ROOT / ".github/workflows/acceptance.yml").read_text(), Loader=yaml.BaseLoader
    )
    assert set(data["on"]) == {"workflow_dispatch"}
    job = data["jobs"]["acceptance"]
    assert job["runs-on"] == ["self-hosted", "linux", "x64", "sieve-acceptance"]
    assert job["env"]["EXPECTED_REVISION"] == "${{ github.sha }}"
    assert job["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert any(
        step.get("name") == "Independently verify saved results"
        for step in job["steps"]
    )
    upload = next(
        step
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert upload["if"] == "always()"
    assert job["timeout-minutes"] == "${{ inputs.build-images && 360 || 120 }}"
