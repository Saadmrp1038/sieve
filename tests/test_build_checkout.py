import os
from pathlib import Path
import subprocess
import tempfile
import unittest
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
