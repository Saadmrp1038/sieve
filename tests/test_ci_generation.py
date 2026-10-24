import os
from pathlib import Path
import runpy
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class WorkflowGenerationTests(unittest.TestCase):
    def test_committed_workflows_match_generator_and_keep_all_build_modes(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                workflows = Path(".github/workflows")
                workflows.mkdir(parents=True)
                generated = runpy.run_path(str(ROOT / "gen_github_action.py"))
                for path in workflows.glob("*.yml"):
                    self.assertEqual(
                        yaml.safe_load(path.read_text()),
                        yaml.safe_load((ROOT / path).read_text()),
                        str(path),
                    )
                regression = yaml.safe_load(
                    (workflows / "regression-testing.yml").read_text()
                )
                self.assertEqual(
                    set(regression["jobs"]), set(generated["operators_for_CI"])
                )
                for name, job in regression["jobs"].items():
                    with self.subTest(operator=name):
                        builds = [
                            step["name"]
                            for step in job["steps"]
                            if step.get("name", "").startswith("Build Image - ")
                        ]
                        self.assertEqual(
                            builds,
                            [
                                "Build Image - learn",
                                "Build Image - test",
                                "Build Image - vanilla",
                            ],
                        )
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
