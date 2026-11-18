"""マージ前のCI（.github/workflows/ci.yml）が、検証だけを行い、配備しないことを確かめる。"""
import unittest
from pathlib import Path

CI = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "ci.yml"


class TestCiWorkflow(unittest.TestCase):
    def setUp(self):
        self.yml = CI.read_text(encoding="utf-8")

    def test_runs_on_ci_runner_only(self):
        self.assertIn("runs-on: [self-hosted, linux, x64, shaman-ci]", self.yml)
        self.assertNotIn("shaman-deploy]", self.yml)

    def test_receives_no_secrets_and_does_not_deploy(self):
        self.assertNotIn("secrets.", self.yml)
        self.assertNotIn("deploy_shaman_google_sheets.py --", self.yml)
        self.assertNotIn("--publish", self.yml)
        self.assertNotIn("127.0.0.1:5678", self.yml)
        self.assertIn("permissions:\n  contents: read", self.yml)

    def test_triggered_by_pull_requests_to_main_not_by_merge(self):
        self.assertIn("pull_request:\n    branches: [main]\n    types: [opened, synchronize, reopened]", self.yml)
        self.assertNotIn("closed", self.yml)
        self.assertNotIn("pull_request_target", self.yml)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", self.yml)

    def test_runs_every_test_suite_and_parity_check(self):
        for step in ("python3 -m pytest -q",
                     "python3 -m unittest discover -s SHAMAN/tools/luna -p 'test_*.py'",
                     "python3 generate_workflows.py",
                     "git diff --exit-code -- workflows/",
                     "npm test -- --runInBand",
                     "gitleaks git --redact"):
            self.assertIn(step, self.yml)


if __name__ == "__main__":
    unittest.main()
