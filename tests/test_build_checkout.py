import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from sieve_tool.build import kubernetes


class KubernetesCheckoutTests(unittest.TestCase):
    def test_reuse_resets_instrumentation_and_preserves_ignored_build_output(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                checkout = Path(directory) / "kubernetes"
                checkout.mkdir(parents=True)

                def git(*args):
                    return subprocess.run(
                        ["git", "-C", str(checkout), *args],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

                git("init")
                (checkout / ".gitignore").write_text("_output/\n")
                source = checkout / "source.go"
                source.write_text("original")
                git("add", ".")
                git(
                    "-c",
                    "user.name=Sieve test",
                    "-c",
                    "user.email=test@localhost",
                    "commit",
                    "-m",
                    "fixture",
                )
                git("tag", "v1.24.10")
                source.write_text("instrumented")
                output = checkout / "_output/compiled"
                output.parent.mkdir()
                output.write_text("cache")
                dependency = checkout / "staging/src/sieve.client"
                dependency.mkdir(parents=True)
                (dependency / "client.go").write_text("instrumented client")
                link = checkout / "vendor/sieve.client"
                link.parent.mkdir()
                link.symlink_to("../staging/src/sieve.client")

                kubernetes.download_kubernetes("v1.24.10", checkout=checkout)
                self.assertEqual(source.read_text(), "original")
                self.assertEqual(output.read_text(), "cache")
                self.assertFalse(dependency.exists())
                self.assertFalse(link.is_symlink())
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()


def test_controller_patches_do_not_need_global_git_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sieve_tool.build.controllers import download_controller
    from sieve_tool.paths import repository

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for variable in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(variable, raising=False)
    upstream = tmp_path / "operator"
    upstream.mkdir()
    git = ["git", "-C", str(upstream)]
    subprocess.run([*git, "init"], check=True, capture_output=True)

    def commit(message):
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run(
            [
                *git,
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@localhost",
                "commit",
                "-m",
                message,
            ],
            check=True,
            capture_output=True,
        )
        return subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()

    (upstream / "logic.go").write_text("base")
    base = commit("base")
    (upstream / "logic.go").write_text("patched")
    patch = commit("patch")
    checkout = tmp_path / "sieve"
    checkout.mkdir()
    with repository(checkout):
        download_controller(
            SimpleNamespace(
                controller_name="operator",
                github_link=str(upstream),
                commit=base,
                cherry_pick_commits=[patch],
            )
        )
    assert (checkout / ".cache/controllers/operator/logic.go").read_text() == "patched"


def test_cached_checkout_without_requested_tag_fetches_release(tmp_path, monkeypatch):
    from sieve_tool.process import run as execute

    remote = tmp_path / "release"
    remote.mkdir()
    git = ["git", "-C", str(remote)]
    subprocess.run([*git, "init"], check=True, capture_output=True)
    (remote / "source.go").write_text("release")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@localhost",
            "commit",
            "-m",
            "release",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run([*git, "tag", "v1.18.9"], check=True)
    cached = tmp_path / "cached"
    cached.mkdir()
    subprocess.run(["git", "-C", str(cached), "init"], check=True, capture_output=True)
    cloned = []

    def offline(argv, **kwargs):
        if "clone" in argv:
            argv = list(argv)
            argv[-2] = str(remote)
            cloned.append(True)
        return execute(argv, **kwargs)

    monkeypatch.setattr(kubernetes, "run", offline)
    kubernetes.download_kubernetes("v1.18.9", checkout=cached)
    assert cloned == [True]
    assert (cached / "source.go").read_text() == "release"
