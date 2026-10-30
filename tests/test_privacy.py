import json
import unittest
from pathlib import Path

from test_orchestrate_a1 import CAPACITY_LOG, OrchestrateA1TestCase, _job

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "retry-a1.yml"
SCRIPT = REPO / "scripts" / "orchestrate-a1.sh"
RESOURCE_IDS = (
    "OCI_CORE_STACK_ID",
    "OCI_SMALL_01_STACK_ID",
    "OCI_SMALL_02_STACK_ID",
    "OCI_A1_COMPARTMENT_ID",
)


class PrivacyWorkflowTests(OrchestrateA1TestCase):
    def test_resource_ids_are_secret_inputs_in_masking_bootstrap_not_job_env(self):
        workflow = WORKFLOW.read_text()
        job_env = workflow.split("    env:\n", 1)[1].split("\n    steps:\n", 1)[0]
        bootstrap = workflow.split("    steps:\n", 1)[1].split("      - uses: actions/checkout@", 1)[0]
        for name in RESOURCE_IDS:
            self.assertNotIn(name, job_env)
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", bootstrap)
        self.assertIn("::add-mask::", bootstrap)
        self.assertIn("$GITHUB_ENV", bootstrap)
        self.assertLess(bootstrap.index("::add-mask::"), bootstrap.index('>> "$GITHUB_ENV"'))

    def test_orchestrator_masks_historical_and_created_ids_before_output(self):
        script = SCRIPT.read_text()
        self.assertIn("mask_job_id()", script)
        self.assertIn("::add-mask::", script)
        self.assertIn('mask_job_id "$latest"', script)
        self.assertIn('mask_job_id "$job_id"', script)
        for msg in (
            'Started small-02 Apply job:',
            'Started core 2/12 Apply job:',
            'Started small-01 Apply job after core capacity failure:',
        ):
            line = next(line for line in script.splitlines() if msg in line)
            self.assertRegex(line, r"safe_job_id")
        self.assertLess(script.index('mask_job_id "$job_id"'), script.index('CREATED_JOB_ID="$job_id"'))

    def test_privacy_edits_preserve_the_strict_retry_classifier_and_small_retry(self):
        script = SCRIPT.read_text()
        self.assertIn("PROVIDER_TIMEOUT_MAX_CONSECUTIVE=3", script)
        self.assertIn("classify_failure_records()", script)
        self.assertIn("provider_timeout_history_allows", script)
        self.assertIn("core_capacity_history_allows", script)
        self.assertIn('ensure_retry_allowed "$OCI_SMALL_02_STACK_ID" "small-02"', script)
        self.assertIn('ensure_retry_allowed "$OCI_SMALL_01_STACK_ID" "small-01"', script)
        self.assertIn("failure_is_capacity", script)

    def test_notification_jq_errors_are_suppressed(self):
        workflow = WORKFLOW.read_text()
        notification = workflow.split("      - name: Notify once when the A1 target is acquired\n", 1)[1]
        notification_run = notification.split("        run: |\n", 1)[1]
        self.assertIn("jq -e '.data | type == \"array\"' >/dev/null 2>/dev/null <<<\"$instances\"", notification_run)
        self.assertIn("' 2>/dev/null <<<\"$instances\")", notification_run)
        for expression in (".total | length", ".expected", ".core | length", ".small01 | length", ".small02 | length"):
            self.assertIn(f"jq -r '{expression}' 2>/dev/null <<<\"$classification\"", notification_run)

    def test_created_job_id_is_masked_before_actions_status_and_redacted(self):
        job_id = "provider-job-id.synthetic-private-value"
        result, _calls = self.run_orchestrator(
            create={"stack-core": {"id": job_id, "state": "SUCCEEDED", "logs": ""}},
            extra_env={"GITHUB_ACTIONS": "true"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"::add-mask::{job_id}", result.stdout)
        self.assertIn("Started core 2/12 Apply job: redacted-job-id", result.stdout)
        self.assertNotIn(job_id, result.stdout[result.stdout.index("Started core 2/12 Apply job:"):])

    def test_historical_job_id_is_masked_before_diagnostic(self):
        job_id = "synthetic-historical-private-id"
        result, _calls = self.run_orchestrator(
            jobs={"stack-core": [_job(job_id, logs=CAPACITY_LOG)]},
            create={"stack-core": {"id": "job-new", "state": "SUCCEEDED", "logs": ""}},
            extra_env={"GITHUB_ACTIONS": "true"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"::add-mask::{job_id}", result.stdout)
        self.assertIn("Observed core 2/12 Apply history", result.stderr)
        self.assertNotIn(job_id, result.stderr)

    def test_malformed_inventory_numeric_value_is_not_emitted_to_stderr(self):
        marker = "private-"
        inventory = {
            "data": [
                {
                    "display-name": "hermes-core-01",
                    "lifecycle-state": "RUNNING",
                    "shape": "VM.Standard.A1.Flex",
                    "shape-config": {"ocpus": marker, "memory-in-gbs": 12},
                }
            ]
        }
        result, calls = self.run_orchestrator(inventory=inventory)

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(marker, result.stderr)
        self.assertFalse(self.create_calls(calls))

    def test_job_list_rejects_multiple_or_trailing_json_documents_before_apply(self):
        active = json.dumps({"data": [{"id": "synthetic-active-id", "lifecycle-state": "IN_PROGRESS"}]})
        empty = json.dumps({"data": []})
        malformed_streams = (
            f"{active}\n{empty}",
            f"{empty}\n{active}",
            f"{empty}\nnot-json",
            f"{empty}\n{empty}",
        )
        for response in malformed_streams:
            with self.subTest(response_count=response.count("\\n") + 1):
                result, calls = self.run_orchestrator(job_list_outputs={"stack-core": response})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Started ", result.stdout)
                self.assertFalse(self.create_calls(calls))
                self.assertNotIn("synthetic-active-id", result.stderr)

    def test_job_list_rejects_unknown_states_and_malformed_rows_before_apply(self):
        invalid_lists = (
            {},
            {"data": None},
            {"data": [{}]},
            {"data": [{"id": "synthetic-id", "lifecycle-state": None}]},
            {"data": [{"id": "synthetic-id", "lifecycle-state": 7}]},
            {"data": [{"id": "synthetic-id", "lifecycle-state": "UNRECOGNIZED"}]},
            {"data": [{"id": "synthetic-id", "lifecycle-state": "NEEDS_ATTENTION"}]},
            {"data": [{"id": "synthetic-id", "lifecycle-state": "IN_PROGRESS"}, {}]},
            {"data": ["FAILED"]},
            {"data": [None]},
        )
        for job_list in invalid_lists:
            with self.subTest(job_list=job_list):
                result, calls = self.run_orchestrator(job_list_outputs={"stack-core": json.dumps(job_list)})
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Started ", result.stdout)
                self.assertFalse(self.create_calls(calls))

    def test_valid_active_job_stops_and_valid_inactive_states_continue(self):
        active = {"data": [{"id": "synthetic-active-id", "lifecycle-state": "IN_PROGRESS"}]}
        result, calls = self.run_orchestrator(job_list_outputs={"stack-core": json.dumps(active)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("active Resource Manager job", result.stdout)
        self.assertFalse(self.create_calls(calls))

        for state in ("SUCCEEDED", "FAILED", "CANCELED"):
            with self.subTest(state=state):
                inactive = {"data": [{"id": f"synthetic-{state}", "lifecycle-state": state}]}
                result, calls = self.run_orchestrator(job_list_outputs={"stack-core": json.dumps(inactive)})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(self.create_calls(calls))


if __name__ == "__main__":
    unittest.main()
