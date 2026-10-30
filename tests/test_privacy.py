import json
import os
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "retry-a1.yml"
SCRIPT = REPO / "scripts" / "orchestrate-a1.sh"
RESOURCE_IDS = (
    "OCI_CORE_STACK_ID",
    "OCI_SMALL_01_STACK_ID",
    "OCI_SMALL_02_STACK_ID",
    "OCI_A1_COMPARTMENT_ID",
)


class PrivacyWorkflowTests(unittest.TestCase):
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

    def test_privacy_change_does_not_add_classifier_logic_or_alter_flow(self):
        script = SCRIPT.read_text()
        self.assertNotRegex(script, r"PROVIDER_TIMEOUT|classify_failure_records|history_invalid")
        self.assertIn("failure_is_capacity", script)
        self.assertIn('grep -qi \'Out of host capacity\'', script)


class RuntimePrivacyMaskTests(unittest.TestCase):
    OCI_STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if os.environ.get("FAKE_TRACE"):
    with open(os.environ["FAKE_TRACE"], "a") as trace:
        trace.write(" ".join(args[:3]) + "\n")
if args[:3] == ["resource-manager", "job", "list"]:
    if os.environ.get("FAKE_LIST_ERROR"):
        print(os.environ["FAKE_LIST_ERROR"], file=sys.stderr)
        raise SystemExit(2)
    if os.environ.get("FAKE_LIST_RESPONSE") and "--all" in args:
        print(os.environ["FAKE_LIST_RESPONSE"])
        raise SystemExit(0)
    if "--all" not in args and os.environ.get("FAKE_HISTORY"):
        print(json.dumps({"data": [{"id": os.environ["FAKE_HISTORY"]}]}))
    else:
        print(json.dumps({"data": []}))
elif args[:3] == ["resource-manager", "job", "create-apply-job"]:
    if os.environ.get("FAKE_CREATE_ERROR"):
        print(os.environ["FAKE_CREATE_ERROR"], file=sys.stderr)
        raise SystemExit(2)
    stack = args[args.index("--stack-id") + 1]
    print(json.dumps({"data": {"id": "synthetic-job-" + stack}}))
elif args[:3] == ["resource-manager", "job", "get"]:
    if os.environ.get("FAKE_STATE_ERROR"):
        print(os.environ["FAKE_STATE_ERROR"], file=sys.stderr)
        raise SystemExit(2)
    if os.environ.get("FAKE_HISTORY") and os.environ["FAKE_HISTORY"] in args:
        print("MYSTERY")
    else:
        print("FAILED" if os.environ.get("FAKE_CORE_STATE") == "FAILED" and "synthetic-job-stack-core" in args else "SUCCEEDED")
elif args[:3] == ["resource-manager", "job", "get-job-logs-content"]:
    print("Out of host capacity")
else:
    raise SystemExit("unexpected synthetic OCI invocation: " + repr(args))
'''
    INVENTORY_STUB = r'''#!/usr/bin/env python3
import json, os
print(os.environ["FAKE_INVENTORY"])
'''

    def run_case(self, inventory, core_state=None, history=None, **extra_env):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            oci = root / "oci"
            python = root / "inventory-python"
            oci.write_text(self.OCI_STUB)
            python.write_text(self.INVENTORY_STUB)
            oci.chmod(0o755)
            python.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "OCI_BIN": str(oci),
                "PYTHON_BIN": str(python),
                "INSTANCE_LIST_SCRIPT": "unused.py",
                "OCI_CORE_STACK_ID": "stack-core",
                "OCI_SMALL_01_STACK_ID": "stack-small-01",
                "OCI_SMALL_02_STACK_ID": "stack-small-02",
                "OCI_A1_COMPARTMENT_ID": "compartment-synthetic",
                "GITHUB_ACTIONS": "true",
                "FAKE_INVENTORY": json.dumps({"data": inventory}),
                "POLL_SECONDS": "1",
                "POLL_ATTEMPTS": "1",
            })
            if core_state:
                env["FAKE_CORE_STATE"] = core_state
            if history:
                env["FAKE_HISTORY"] = history
            env.update(extra_env)
            return __import__("subprocess").run(
                ["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False
            )

    def run_case_with_trace(self, inventory, **extra_env):
        with tempfile.TemporaryDirectory() as directory:
            trace_path = Path(directory) / "oci-trace"
            if extra_env.pop("FAKE_JQ_FAILURE", False):
                bin_path = Path(directory) / "bin"
                bin_path.mkdir()
                fake_jq = bin_path / "jq"
                fake_jq.write_text("#!/bin/sh\nexit 2\n")
                fake_jq.chmod(0o755)
                extra_env["PATH"] = f"{bin_path}:{os.environ['PATH']}"
            result = self.run_case(inventory, FAKE_TRACE=str(trace_path), **extra_env)
            trace = trace_path.read_text().splitlines() if trace_path.exists() else []
            return result, trace

    @staticmethod
    def instance(name):
        return {
            "display-name": name,
            "shape": "VM.Standard.A1.Flex",
            "lifecycle-state": "RUNNING",
            "shape-config": {"ocpus": 1, "memory-in-gbs": 6},
        }

    def test_created_ids_are_masked_before_status_lines_for_core_small01_small02(self):
        cases = (
            ([], None, (("synthetic-job-stack-core", "Started core 2/12 Apply job"),)),
            ([self.instance("hermes-a1-small-01")], None, (("synthetic-job-stack-small-02", "Started small-02 Apply job"),)),
            ([], "FAILED", (
                ("synthetic-job-stack-core", "Started core 2/12 Apply job"),
                ("synthetic-job-stack-small-01", "Started small-01 Apply job"),
            )),
        )
        for inventory, core_state, expected in cases:
            with self.subTest(expected=expected):
                result = self.run_case(inventory, core_state)
                self.assertEqual(result.returncode, 0, result.stderr)
                for job_id, status in expected:
                    mask = f"::add-mask::{job_id}"
                    self.assertIn(mask, result.stdout)
                    self.assertLess(result.stdout.index(mask), result.stdout.index(status))
                    self.assertNotIn(job_id, result.stdout[result.stdout.index(status):])
                self.assertIn("redacted-job-id", result.stdout)

    def test_historical_job_id_is_masked_before_invalid_state_error(self):
        job_id = "synthetic-historical-private-id"
        result = self.run_case([], history=job_id)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"::add-mask::{job_id}", result.stdout)
        self.assertIn("Unexpected latest job state; retry is stopped.", result.stderr)
        self.assertNotIn(job_id, result.stderr)
        self.assertNotIn("Started ", result.stdout)

    def test_unknown_apply_error_is_generic_and_keeps_failure_status(self):
        secret = "ocid1.ormjob.synthetic.private-identifier"
        result = self.run_case([], FAKE_CREATE_ERROR=f"synthetic upstream error {secret}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not create Apply job for core 2/12.", result.stderr)
        self.assertNotIn(secret, result.stderr)

    def test_oci_list_and_state_errors_are_suppressed(self):
        secret = "ocid1.synthetic.private-identifier"
        cases = (
            {"FAKE_LIST_ERROR": f"synthetic list failure {secret}"},
            {"FAKE_LIST_RESPONSE": json.dumps({"data": [secret]})},
            {"FAKE_HISTORY": "synthetic-history", "FAKE_STATE_ERROR": f"synthetic state failure {secret}"},
        )
        for extra_env in cases:
            with self.subTest(extra_env=extra_env):
                result = self.run_case([], **extra_env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(secret, result.stderr)

    def test_notification_jq_errors_are_suppressed(self):
        workflow = WORKFLOW.read_text()
        notification = workflow.split("      - name: Notify once when the A1 target is acquired\n", 1)[1]
        notification_run = notification.split("        run: |\n", 1)[1]
        self.assertIn("jq -e '.data | type == \"array\"' >/dev/null 2>/dev/null <<<\"$instances\"", notification_run)
        self.assertIn("' 2>/dev/null <<<\"$instances\")", notification_run)
        for expression in (".total | length", ".expected", ".core | length", ".small01 | length", ".small02 | length"):
            self.assertIn(f"jq -r '{expression}' 2>/dev/null <<<\"$classification\"", notification_run)

    def test_job_list_rejects_multiple_or_trailing_json_documents_before_apply(self):
        active = json.dumps({"data": [{"lifecycle-state": "IN_PROGRESS"}]})
        empty = json.dumps({"data": []})
        malformed_streams = (
            f"{active}\n{empty}",
            f"{empty}\n{active}",
            f"{empty}\nnot-json",
            f"{empty}\n{empty}",
        )
        for response in malformed_streams:
            with self.subTest(response=response):
                result, trace = self.run_case_with_trace([], FAKE_LIST_RESPONSE=response)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Started ", result.stdout)
                self.assertNotIn("resource-manager job create-apply-job", trace)

    def test_job_list_requires_object_rows_with_known_lifecycle_states(self):
        invalid_lists = (
            {},
            {"data": None},
            {"data": [{}]},
            {"data": [{"lifecycle-state": None}]},
            {"data": [{"lifecycle-state": 7}]},
            {"data": [{"lifecycle-state": "UNRECOGNIZED"}]},
            {"data": [{"lifecycle-state": "NEEDS_ATTENTION"}]},
            {"data": [{"lifecycle-state": "IN_PROGRESS"}, {}]},
            {"data": ["FAILED"]},
            {"data": [None]},
        )
        for job_list in invalid_lists:
            with self.subTest(job_list=job_list):
                result, trace = self.run_case_with_trace([], FAKE_LIST_RESPONSE=json.dumps(job_list))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Started ", result.stdout)
                self.assertNotIn("resource-manager job create-apply-job", trace)

    def test_jq_failure_stops_without_apply(self):
        result, trace = self.run_case_with_trace([], FAKE_JQ_FAILURE=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("resource-manager job create-apply-job", trace)
        self.assertNotIn("Started ", result.stdout)

    def test_valid_job_states_preserve_empty_inactive_and_active_routing(self):
        for state in ("SUCCEEDED", "FAILED", "CANCELED"):
            job_list = {"data": [{"lifecycle-state": state}]}
            with self.subTest(state=state):
                result, trace = self.run_case_with_trace([], FAKE_LIST_RESPONSE=json.dumps(job_list))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("resource-manager job create-apply-job", trace)
        for state in ("ACCEPTED", "IN_PROGRESS", "CANCELING"):
            job_list = {"data": [{"lifecycle-state": state}]}
            with self.subTest(state=state):
                result, trace = self.run_case_with_trace([], FAKE_LIST_RESPONSE=json.dumps(job_list))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("resource-manager job create-apply-job", trace)
                self.assertNotIn("Started ", result.stdout)
        for job_list in ({"data": []}, {"data": [{"lifecycle-state": "SUCCEEDED"}, {"lifecycle-state": "FAILED"}]}):
            with self.subTest(job_list=job_list):
                result, trace = self.run_case_with_trace([], FAKE_LIST_RESPONSE=json.dumps(job_list))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("resource-manager job create-apply-job", trace)


if __name__ == "__main__":
    unittest.main()
