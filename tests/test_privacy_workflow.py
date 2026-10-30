import unittest
from pathlib import Path

from test_orchestrate_a1 import CAPACITY_LOG, OrchestrateA1TestCase, _job


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "retry-a1.yml"
SCRIPT = REPO / "scripts" / "orchestrate-a1.sh"


class PrivacyWorkflowTests(unittest.TestCase):
    def test_resource_ids_are_not_job_level_environment_values(self):
        workflow = WORKFLOW.read_text()
        job_env = workflow.split("    env:\n", 1)[1].split("\n    steps:\n", 1)[0]

        for name in (
            "OCI_CORE_STACK_ID",
            "OCI_SMALL_01_STACK_ID",
            "OCI_SMALL_02_STACK_ID",
            "OCI_A1_COMPARTMENT_ID",
        ):
            self.assertNotIn(name, job_env)

    def test_first_step_uses_github_secrets_for_resource_ids(self):
        workflow = WORKFLOW.read_text()
        steps = workflow.split("    steps:\n", 1)[1]
        bootstrap = steps.split("      - uses: actions/checkout@", 1)[0]
        for name in (
            "OCI_CORE_STACK_ID",
            "OCI_SMALL_01_STACK_ID",
            "OCI_SMALL_02_STACK_ID",
            "OCI_A1_COMPARTMENT_ID",
        ):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", bootstrap)
            self.assertNotIn(f"{name}: ${{{{ vars.{name} }}}}", bootstrap)
        self.assertIn("::add-mask::", bootstrap)
        self.assertIn("$GITHUB_ENV", bootstrap)

    def test_runner_bootstrap_echoes_only_masked_secret_values(self):
        workflow = WORKFLOW.read_text()
        steps = workflow.split("    steps:\n", 1)[1]
        bootstrap = steps.split("      - uses: actions/checkout@", 1)[0]
        # Synthetic Actions runner order: emit step env and script before execution.
        names = ("OCI_CORE_STACK_ID", "OCI_SMALL_01_STACK_ID", "OCI_SMALL_02_STACK_ID", "OCI_A1_COMPARTMENT_ID")
        self.assertTrue(all(f"{name}: ${{{{ secrets.{name} }}}}" in bootstrap for name in names))
        secret_values = {name: f"synthetic-{name}-private" for name in names}
        runner_output = "\\n".join(
            [f"{name}={secret_values[name]}" for name in names]
            + ["--- script ---", bootstrap.split("        run: |\n", 1)[1]]
        )
        for value in secret_values.values():
            runner_output = runner_output.replace(value, "***")
        self.assertNotRegex(runner_output, r"synthetic-OCI_.*-private")
        self.assertIn("***", runner_output)

    def test_orchestrator_masks_historical_and_created_job_ids(self):
        script = SCRIPT.read_text()
        self.assertIn("mask_job_id", script)
        self.assertIn('mask_job_id "$id"', script)
        self.assertIn('mask_job_id "$latest"', script)
        created_mask = script.index('mask_job_id "$job_id"')
        created_assignment = script.index('CREATED_JOB_ID="$job_id"')
        self.assertLess(created_mask, created_assignment)


class RuntimePrivacyMaskTests(OrchestrateA1TestCase):
    def test_core_history_and_both_created_jobs_are_masked_before_status_output(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-fake-history", logs=CAPACITY_LOG)]},
            create={
                "stack-core": {
                    "id": "job-fake-core-created",
                    "state": "FAILED",
                    "logs": CAPACITY_LOG,
                },
                "stack-small-01": {
                    "id": "job-fake-small-created",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
            extra_env={"GITHUB_ACTIONS": "true"},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [line for line in result.stdout.splitlines() if line.startswith("::add-mask::")],
            [
                "::add-mask::job-fake-history",
                "::add-mask::job-fake-core-created",
                "::add-mask::job-fake-small-created",
            ],
        )
        self.assertLess(
            result.stdout.index("::add-mask::job-fake-core-created"),
            result.stdout.index("Started core 2/12 Apply job"),
        )
        self.assertLess(
            result.stdout.index("::add-mask::job-fake-small-created"),
            result.stdout.index("Started small-01 Apply job"),
        )
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core", "stack-small-01"],
        )


if __name__ == "__main__":
    unittest.main()
