#!/usr/bin/env python3
import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "orchestrate-a1.sh"
SCRATCH = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))

TIMEOUT_LOG = "\n".join(
    [
        "Error: Failed to install provider",
        "failed to retrieve authentication checksums for provider: ",
        "https://github.com/oracle/terraform-provider-oci/releases/download/v8.26.0/terraform-provider-oci_8.26.0_SHA256SUMS",
        "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
    ]
)

CAPACITY_LOG = "\n".join(
    [
        "2026/10/26 17:09:27\tINFO\tGetting providers from registry and/or custom terraform providers",
        "2026/10/26 17:09:27\tINFO\tInitializing provider plugins...",
        "2026/10/26 17:09:29\tINFO\tTerraform has been successfully initialized!",
        "2026/10/26 17:09:33\tINFO\tTerraform used the selected providers to generate the following execution plan.",
        "2026/10/26 17:09:33\tINFO\tTerraform will perform the following actions:",
        '2026/10/26 17:09:33\tINFO\t  + resource "oci_core_instance" "generated_oci_core_instance" {',
        "2026/10/26 17:09:33\tINFO\tPlan: 1 to add, 0 to change, 0 to destroy.",
        "2026/10/26 17:09:35\tINFO\toci_core_instance.generated_oci_core_instance: Creating...",
        "2026/10/26 17:09:36\tERROR\tError: 500-InternalError, Out of host capacity.",
        "2026/10/26 17:09:36\tINFO\tSuggestion: The service for this resource encountered an error.",
        "2026/10/26 17:09:36\tINFO\tDocumentation: https://registry.terraform.io/providers/oracle/oci/latest/docs/resources/core_instance",
        "2026/10/26 17:09:36\tINFO\tProvider version: 9.11.0",
        "2026/10/26 17:09:36\tINFO\tService: Core Instance",
        "2026/10/26 17:09:36\tINFO\tOperation Name: LaunchInstance",
        "2026/10/26 17:09:36\tINFO\tCorrelation marker: synthetic-correlation",
    ]
)

LONG_CAPACITY_DIAGNOSTIC = (
    "Suggestion: The service for this resource encountered an error. "
    "Please contact support for help with service: Core Instance"
)
FULL_CAPACITY_LOG = "\n".join(
    [
        line.replace(
            "Suggestion: The service for this resource encountered an error.",
            LONG_CAPACITY_DIAGNOSTIC,
        )
        for line in CAPACITY_LOG.splitlines()
        if not any(
            marker in line
            for marker in (
                "Provider version:",
                "Service:",
                "Operation Name:",
                "Correlation marker:",
            )
        )
    ]
    + [
        "2026/10/26 17:09:36\tINFO\tRequest Target: POST <redacted-url>",
        "2026/10/26 17:09:36\tINFO\tAPI Reference: https://docs.oracle.com/en-us/iaas/api/#/en/compute/20160918/Instance/LaunchInstance",
        "2026/10/26 17:09:36\tINFO\tProvider version: 9.11.0, released on <redacted-date>.",
        "2026/10/26 17:09:36\tINFO\tService: Core Instance",
        "2026/10/26 17:09:36\tINFO\tOperation Name: LaunchInstance",
        "2026/10/26 17:09:36\tINFO\tOPC request ID: <redacted-request-id>",
        "2026/10/26 17:09:36\tINFO\t  with oci_core_instance.redacted_instance,",
        '2026/10/26 17:09:36\tINFO\t  on main.tf line 3, in resource "oci_core_instance" "redacted_instance":',
        '2026/10/26 17:09:36\tINFO\t  3: resource "oci_core_instance" "redacted_instance" {',
    ]
)

_CAPACITY_ERROR_TAIL = CAPACITY_LOG.split(
    "2026/10/26 17:09:36\tERROR\t", 1
)[1]
VARIABLE_PRELUDE_CAPACITY_LOG = "\n".join(
    [
        "Synthetic Resource Manager log export",
        "job identifier: redacted-job",
        "2026/10/26 17:09:27\tINFO\t",
        "Terraform selected a provider and loaded custom variables",
        "2026/10/26 17:09:29\tWARN\tprovider lock metadata is incomplete",
        "  - synthetic provider detail: retained for diagnostics",
        "2026/10/26 17:09:33\tINFO\t",
        "  # synthetic resource will be created",
        '      + availability_domain = "synthetic-ad-1"',
        "      + custom_unknown_plan_field = (known after apply)",
        "2026/10/26 17:09:36\tERROR\t",
        _CAPACITY_ERROR_TAIL,
    ]
)


def _small01_console_attempt(started_at: str, failed_at: str) -> list[str]:
    return [
        f"{started_at}\tINFO\t",
        "Getting providers from registry and/or custom terraform providers",
        f"{started_at}\tINFO\t",
        f"{started_at}\tINFO\t",
        "Initializing provider plugins...",
        f"{started_at}\tINFO\t",
        "- Finding latest version of oracle/oci...",
        f"{failed_at}\tINFO\t",
        f"{failed_at}\tERROR\t",
        "Error: Failed to install provider",
        f"{failed_at}\tINFO\t",
        f"{failed_at}\tINFO\t",
        "Error while installing oracle/oci v8.26.0: could not query provider registry",
        f"{failed_at}\tINFO\t",
        "for registry.terraform.io/oracle/oci: failed to retrieve authentication",
        f"{failed_at}\tINFO\t",
        "",
        f"{failed_at}\tINFO\t",
        "checksums for provider: the request failed after 2 attempts, please try again",
        f"{failed_at}\tINFO\t",
        "",
        f"{failed_at}\tINFO\t",
        "later: Get",
        f"{failed_at}\tINFO\t",
        '"https://github.com/oracle/terraform-provider-oci/releases/download/v8.26.0/terraform-provider-oci_8.26.0_SHA256SUMS":',
        f"{failed_at}\tINFO\t",
        "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
    ]


def _small01_cli_wrapped_attempt(started_at: str, failed_at: str) -> list[str]:
    return [
        f"{started_at}\tINFO\tGetting providers from registry and/or custom terraform providers",
        f"{started_at}\tINFO\tInitializing provider plugins...",
        f"{started_at}\tINFO\t-Finding latest version of oracle/oci...",
        f"{failed_at}\tERROR\tError: Failed to install provider",
        f"{failed_at}\tINFO\tError while installing oracle/oci v8.26.0: could not query provider registry",
        f"{failed_at}\tINFO\tfor registry.terraform.io/oracle/oci: failed to retrieve authentication",
        f"{failed_at}\tINFO\tchecksums for provider: the request failed after 2 attempts, please try again",
        f"{failed_at}\tINFO\tlater: Get",
        f'{failed_at}\tINFO\t"https://github.com/oracle/terraform-provider-oci/releases/download/v8.26.0/terraform-provider-oci_8.26.0_SHA256SUMS":',
        f"{failed_at}\tINFO\tcontext deadline exceeded (Client.Timeout exceeded while awaiting headers)",
    ]


_SMALL01_ATTEMPTS = (
    ("2026/08/07 15:26:45", "2026/08/07 15:27:07"),
    ("2026/08/07 15:28:27", "2026/08/07 15:28:48"),
    ("2026/08/07 15:31:29", "2026/08/07 15:31:50"),
    ("2026/08/07 15:35:10", "2026/08/07 15:35:31"),
)

SMALL01_CONSOLE_LOG = "\n".join(
    line
    for started_at, failed_at in _SMALL01_ATTEMPTS
    for line in _small01_console_attempt(started_at, failed_at)
)

SMALL01_CLI_WRAPPED_LOG = "\n".join(
    line
    for started_at, failed_at in _SMALL01_ATTEMPTS
    for line in _small01_cli_wrapped_attempt(started_at, failed_at)
)

WRONG_ORIGIN_TIMEOUT_LOG = TIMEOUT_LOG.replace(
    "https://github.com/oracle/terraform-provider-oci/",
    "https://downloads.example.invalid/oracle/terraform-provider-oci/",
)
WRONG_PROVIDER_TIMEOUT_LOG = TIMEOUT_LOG.replace(
    "oracle/terraform-provider-oci",
    "hashicorp/terraform-provider-aws",
)
STRUCTURED_ERROR_RECORD = '{"level":"ERROR","message":"Unexpected provider failure"}'
JSON_RECORD_LOG = "\n".join(
    [
        '{"level":"INFO","message":"Initializing provider plugins..."}',
        '{"level":"ERROR","message":"Error: 500-InternalError, Out of host capacity."}',
    ]
)
UNKNOWN_INFO_DIAGNOSTICS = (
    "AccountSuspended",
    "InvalidParameter, invalid subnet configuration",
    "Fatal configuration error: incorrect region",
    "ServiceUnavailable: retry later",
    "NotAuthorizedOrNotFound",
    "Unexpected provider failure",
)
UNKEYWORDED_INFO_DIAGNOSTICS = (
    "Credentials rejected",
    "Account is disabled",
    "Access revoked",
    "LicenseSuspended",
)


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _job(job_id: str, state: str = "FAILED", operation: str = "APPLY", logs: str = "") -> dict:
    return {
        "id": job_id,
        "lifecycle-state": state,
        "operation": operation,
        "logs": logs,
    }


def _instance(name: str, ocpus: int, memory: int, state: str = "RUNNING") -> dict:
    return {
        "display-name": name,
        "lifecycle-state": state,
        "shape": "VM.Standard.A1.Flex",
        "shape-config": {"ocpus": ocpus, "memory-in-gbs": memory},
    }


class OrchestrateA1TestCase(unittest.TestCase):
    def run_orchestrator(
        self,
        *,
        jobs=None,
        inventory=None,
        inventory_mode="json",
        create=None,
        history_failures=None,
        log_failures=None,
        state_failures=None,
        state_overrides=None,
        extra_env=None,
    ):
        jobs = jobs or {}
        inventory = inventory if inventory is not None else {"data": []}
        create = create or {
            "stack-core": {"id": "job-new", "state": "SUCCEEDED", "logs": ""},
        }
        history_failures = history_failures or {}
        log_failures = log_failures or {}
        state_failures = state_failures or {}
        state_overrides = state_overrides or {}

        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp_dir:
            temp = Path(temp_dir)
            config_path = temp / "fake-cli-config.json"
            calls_path = temp / "fake-cli-calls.jsonl"
            config_path.write_text(
                json.dumps(
                    {
                        "jobs": jobs,
                        "create": create,
                        "history_failures": history_failures,
                        "log_failures": log_failures,
                        "state_failures": state_failures,
                        "state_overrides": state_overrides,
                    }
                )
            )

            fake_cli = temp / "fake-oci.py"
            _make_executable(
                fake_cli,
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json
                    import sys
                    from pathlib import Path

                    config = json.loads(Path(__CONFIG_PATH__).read_text())
                    calls_path = Path(__CALLS_PATH__)
                    args = sys.argv[1:]
                    with calls_path.open("a") as calls_file:
                        calls_file.write(json.dumps(args) + "\\n")

                    def option(name):
                        index = args.index(name)
                        return args[index + 1]

                    if args[:3] == ["resource-manager", "job", "list"]:
                        stack_id = option("--stack-id")
                        if "--sort-by" in args and config["history_failures"].get(stack_id):
                            print("simulated history failure", file=sys.stderr)
                            raise SystemExit(8)
                        print(json.dumps(dict(data=config["jobs"].get(stack_id, [])), separators=(",", ":")))
                    elif args[:3] == ["resource-manager", "job", "get"]:
                        job_id = option("--job-id")
                        if config["state_failures"].get(job_id):
                            print("simulated state failure", file=sys.stderr)
                            raise SystemExit(9)
                        state = None
                        for stack_jobs in config["jobs"].values():
                            for job in stack_jobs:
                                if job["id"] == job_id:
                                    state = job["lifecycle-state"]
                        for create_job in config["create"].values():
                            if create_job.get("id") == job_id:
                                state = create_job.get("state", "SUCCEEDED")
                        state = config["state_overrides"].get(job_id, state)
                        if state is None:
                            print("unknown job", file=sys.stderr)
                            raise SystemExit(10)
                        print(state)
                    elif args[:3] == ["resource-manager", "job", "get-job-logs-content"]:
                        job_id = option("--job-id")
                        if config["log_failures"].get(job_id):
                            print("simulated log failure", file=sys.stderr)
                            raise SystemExit(11)
                        for stack_jobs in config["jobs"].values():
                            for job in stack_jobs:
                                if job["id"] == job_id:
                                    print(job.get("logs", ""), end="")
                                    raise SystemExit(0)
                        for create_job in config["create"].values():
                            if create_job.get("id") == job_id:
                                print(create_job.get("logs", ""), end="")
                                raise SystemExit(0)
                        print("unknown job", file=sys.stderr)
                        raise SystemExit(12)
                    elif args[:3] == ["resource-manager", "job", "create-apply-job"]:
                        stack_id = option("--stack-id")
                        create_job = config["create"].get(stack_id, {})
                        if create_job.get("error"):
                            print(create_job["error"])
                            raise SystemExit(15)
                        if create_job.get("rate_limit"):
                            print('{"status":429,"code":"TooManyRequests"}')
                            raise SystemExit(13)
                        print(json.dumps(dict(data=dict(id=create_job.get("id", "job-new"))), separators=(",", ":")))
                    else:
                        print("unexpected fake OCI call", file=sys.stderr)
                        raise SystemExit(14)
                    """
                ).replace("__CONFIG_PATH__", repr(str(config_path))).replace("__CALLS_PATH__", repr(str(calls_path))),
            )

            inventory_script = temp / "inventory.py"
            if inventory_mode == "error":
                inventory_source = "#!/usr/bin/env python3\nraise SystemExit(20)\n"
            elif inventory_mode == "malformed":
                inventory_source = "#!/usr/bin/env python3\nprint('not-json')\n"
            else:
                inventory_source = (
                    "#!/usr/bin/env python3\nprint(" + repr(json.dumps(inventory)) + ")\n"
                )
            _make_executable(inventory_script, inventory_source)

            env = {
                "PATH": os.environ["PATH"],
                "HOME": str(temp),
                "OCI_BIN": str(fake_cli),
                "PYTHON_BIN": sys.executable,
                "INSTANCE_LIST_SCRIPT": str(inventory_script),
                "OCI_CORE_STACK_ID": "stack-core",
                "OCI_SMALL_01_STACK_ID": "stack-small-01",
                "OCI_SMALL_02_STACK_ID": "stack-small-02",
                "OCI_A1_COMPARTMENT_ID": "compartment-test",
                "CREATE_JOB_MAX_ATTEMPTS": "1",
                "POLL_SECONDS": "1",
                "POLL_ATTEMPTS": "1",
                "CREATE_JOB_BACKOFF_SECONDS": "1",
            }
            if extra_env:
                env.update(extra_env)

            result = subprocess.run(
                ["/usr/bin/bash", str(SCRIPT)],
                cwd=REPO,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            calls = []
            if calls_path.exists():
                calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
            return result, calls

    @staticmethod
    def create_calls(calls):
        return [
            call
            for call in calls
            if call[:3] == ["resource-manager", "job", "create-apply-job"]
        ]


class OrchestrateA1ProviderTimeoutTests(OrchestrateA1TestCase):
    def test_provider_checksum_download_timeout_allows_retry_below_budget(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={
                "stack-small-02": [_job("job-timeout-1", logs=TIMEOUT_LOG)],
            },
            create={
                "stack-small-02": {
                    "id": "job-small02-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Started small-02 Apply job: job-small02-new", result.stdout)
        self.assertIn("diagnostic=provider_checksum_download_timeout", result.stderr)
        self.assertIn("state=FAILED", result.stderr)
        self.assertIn("job=job-timeout-1", result.stderr)
        self.assertTrue(self.create_calls(calls))

    def test_core_checksum_download_timeout_does_not_use_small_side_retry_policy(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-core-timeout", logs=TIMEOUT_LOG)]},
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))

    def test_three_consecutive_provider_timeouts_stop_across_history(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={
                "stack-small-02": [
                    _job("job-timeout-3", logs=TIMEOUT_LOG),
                    _job("job-timeout-2", logs=TIMEOUT_LOG),
                    _job("job-timeout-1", logs=TIMEOUT_LOG),
                ],
            },
            create={
                "stack-small-02": {
                    "id": "job-small02-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provider_timeout_budget_exhausted", result.stderr)
        self.assertFalse(self.create_calls(calls))
    def test_mixed_capacity_and_provider_timeout_does_not_fallback_to_small01(self):
        mixed_log = "\n".join(["Out of host capacity", TIMEOUT_LOG])
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": mixed_log,
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_partial_provider_error_with_capacity_is_ambiguous(self):
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": "Out of host capacity\\nFailed to install provider",
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")
    def test_capacity_and_generic_timeout_is_ambiguous(self):
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": "Out of host capacity\\ncontext deadline exceeded",
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_capacity_failure_still_allows_retry(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-capacity", logs="Out of host capacity")]}
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_explicit_prelude_failures_stop_before_fallback(self):
        preludes = (
            "2026/10/26 17:09:35\tINFO\tCredentials rejected",
            "2026/10/26 17:09:35\tINFO\tAccount is disabled",
            "2026/10/26 17:09:35\tINFO\tAccess revoked",
            "2026/10/26 17:09:35\tINFO\t  cReDeNtIaLs\t ReJeCtEd  ",
            "2026/10/26 17:09:35\tINFO\t ACCOUNT   IS\tDISABLED ",
            "2026/10/26 17:09:35\tINFO\t aCcEsS\t ReVoKeD ",
            "Error: AccountSuspended",
            "ERROR: AccountSuspended",
            "error: AccountSuspended",
            "2026/10/26 17:09:35\tERROR\tAccountSuspended",
        )
        for prelude in preludes:
            with self.subTest(prelude=prelude):
                mixed_log = prelude + "\n" + CAPACITY_LOG
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {"id": "job-core-new", "state": "FAILED", "logs": mixed_log},
                        "stack-small-01": {"id": "job-small-new", "state": "SUCCEEDED", "logs": ""},
                    }
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([call[4] for call in self.create_calls(calls)], ["stack-core"])

                historical, historical_calls = self.run_orchestrator(
                    jobs={"stack-core": [_job("job-old-mixed", logs=mixed_log)]},
                )
                self.assertNotEqual(historical.returncode, 0)
                self.assertEqual(self.create_calls(historical_calls), [])

    def test_realistic_capacity_log_allows_core_retry_and_small01_fallback(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-old-capacity", logs=CAPACITY_LOG)]},
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": CAPACITY_LOG,
                },
                "stack-small-01": {
                    "id": "job-small01-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[4] for call in creates], ["stack-core", "stack-small-01"])

    def test_known_terraform_capacity_diagnostics_remain_accepted(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-known-capacity", logs=CAPACITY_LOG)]},
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        creates = self.create_calls(calls)
        self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_sanitized_full_user_logs_route_core_then_small01(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [
                    _job("job-core-old-capacity", logs=FULL_CAPACITY_LOG)
                ],
                "stack-small-01": [
                    _job("job-small01-old-timeout", logs=SMALL01_CONSOLE_LOG)
                ],
            },
            create={
                "stack-core": {
                    "id": "job-core-new-capacity",
                    "state": "FAILED",
                    "logs": FULL_CAPACITY_LOG,
                },
                "stack-small-01": {
                    "id": "job-small01-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in creates],
            ["stack-core", "stack-small-01"],
        )

    def test_variable_plan_prelude_routes_core_then_small01(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [
                    _job("job-core-variable-capacity", logs=VARIABLE_PRELUDE_CAPACITY_LOG)
                ],
                "stack-small-01": [
                    _job("job-small01-old-timeout", logs=SMALL01_CONSOLE_LOG)
                ],
            },
            create={
                "stack-core": {
                    "id": "job-core-new-variable-capacity",
                    "state": "FAILED",
                    "logs": VARIABLE_PRELUDE_CAPACITY_LOG,
                },
                "stack-small-01": {
                    "id": "job-small01-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in creates],
            ["stack-core", "stack-small-01"],
        )

    def test_full_small01_provider_log_formats_allow_core_then_small01_fallback(self):
        for log_format, small01_log in (
            ("console", SMALL01_CONSOLE_LOG),
            ("cli-wrapped", SMALL01_CLI_WRAPPED_LOG),
        ):
            with self.subTest(log_format=log_format):
                result, calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [
                            _job("job-core-old-capacity", logs=CAPACITY_LOG)
                        ],
                        "stack-small-01": [
                            _job("job-small01-old-timeout", logs=small01_log)
                        ],
                    },
                    create={
                        "stack-core": {
                            "id": "job-core-new-capacity",
                            "state": "FAILED",
                            "logs": CAPACITY_LOG,
                        },
                        "stack-small-01": {
                            "id": "job-small01-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                )

                creates = self.create_calls(calls)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in creates],
                    ["stack-core", "stack-small-01"],
                )

    def test_small01_provider_timeout_plus_unknown_error_stops(self):
        for unknown_error in (
            "InvalidParameter, invalid subnet configuration",
            "AccountSuspended",
        ):
            with self.subTest(unknown_error=unknown_error):
                mixed_log = "\n".join(
                    [
                        SMALL01_CLI_WRAPPED_LOG,
                        "2026/08/07 15:35:32\tERROR\tError: " + unknown_error,
                    ]
                )
                result, calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [_job("job-core-old-capacity", logs=CAPACITY_LOG)],
                        "stack-small-01": [
                            _job("job-small01-mixed-unknown", logs=mixed_log)
                        ],
                    },
                    create={
                        "stack-core": {
                            "id": "job-core-new-capacity",
                            "state": "FAILED",
                            "logs": CAPACITY_LOG,
                        },
                        "stack-small-01": {
                            "id": "job-small01-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                )

                self.assertNotEqual(result.returncode, 0)
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_capacity_plus_unknown_error_stops_without_small01_fallback(self):
        mixed_log = "\n".join(
            [
                CAPACITY_LOG,
                "2026/10/26 17:09:37\tERROR\tError: InvalidParameter, invalid subnet configuration",
            ]
        )
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": mixed_log,
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_unknown_error_level_after_capacity_stops(self):
        mixed_log = "\n".join(
            [
                CAPACITY_LOG,
                "2026/10/26 17:09:37\tERROR\tUnexpected provider failure",
            ]
        )
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": mixed_log,
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_unknown_error_text_or_level_after_any_accepted_block_stops(self):
        suffixes = (
            "Error: Unexpected provider failure",
            "2026/10/26 17:09:37\tERROR\tUnexpected provider failure",
        )
        for suffix in suffixes:
            with self.subTest(block_kind="capacity", suffix=suffix):
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": "job-core-new-unknown",
                            "state": "FAILED",
                            "logs": CAPACITY_LOG + "\n" + suffix,
                        },
                        "stack-small-01": {
                            "id": "job-small-new-unknown",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    }
                )
                self.assertNotEqual(result.returncode, 0)
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], ["stack-core"])

        for suffix in suffixes:
            with self.subTest(block_kind="provider", suffix=suffix):
                mixed_log = SMALL01_CLI_WRAPPED_LOG + "\n" + suffix
                result, calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [_job("job-core-old-capacity", logs=CAPACITY_LOG)],
                        "stack-small-01": [
                            _job("job-small01-mixed-unknown", logs=mixed_log)
                        ],
                    },
                    create={
                        "stack-core": {
                            "id": "job-core-new-capacity",
                            "state": "FAILED",
                            "logs": CAPACITY_LOG,
                        },
                        "stack-small-01": {
                            "id": "job-small01-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_info_unexpected_provider_failure_after_capacity_stops(self):
        mixed_log = "\n".join(
            [
                CAPACITY_LOG,
                "2026/10/26 17:09:37\tINFO\tUnexpected provider failure",
            ]
        )
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-new",
                    "state": "FAILED",
                    "logs": mixed_log,
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_core_unknown_info_diagnostics_stop_before_small01_fallback(self):
        for unknown_diagnostic in UNKNOWN_INFO_DIAGNOSTICS:
            with self.subTest(unknown_diagnostic=unknown_diagnostic):
                mixed_log = "\n".join(
                    [
                        CAPACITY_LOG,
                        "2026/10/26 17:09:37\tINFO\t" + unknown_diagnostic,
                    ]
                )
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": "job-core-new",
                            "state": "FAILED",
                            "logs": mixed_log,
                        },
                        "stack-small-01": {
                            "id": "job-small-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    }
                )

                self.assertNotEqual(result.returncode, 0)
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_unkeyed_info_after_capacity_stops_before_small01_fallback(self):
        for unknown_diagnostic in UNKEYWORDED_INFO_DIAGNOSTICS:
            with self.subTest(unknown_diagnostic=unknown_diagnostic):
                mixed_log = "\n".join(
                    [
                        CAPACITY_LOG,
                        "2026/10/26 17:09:37\tINFO\t" + unknown_diagnostic,
                    ]
                )
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": "job-core-new",
                            "state": "FAILED",
                            "logs": mixed_log,
                        },
                        "stack-small-01": {
                            "id": "job-small-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    }
                )

                self.assertNotEqual(result.returncode, 0)
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_provider_timeout_followed_by_structured_error_stops(self):
        mixed_log = "\n".join(
            [
                SMALL01_CLI_WRAPPED_LOG,
                "2026/08/07 15:35:32\tINFO\t" + STRUCTURED_ERROR_RECORD,
            ]
        )
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [_job("job-core-old-capacity", logs=CAPACITY_LOG)],
                "stack-small-01": [
                    _job("job-small01-mixed-structured-error", logs=mixed_log)
                ],
            },
            create={
                "stack-core": {
                    "id": "job-core-new-capacity",
                    "state": "FAILED",
                    "logs": CAPACITY_LOG,
                },
                "stack-small-01": {
                    "id": "job-small01-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )

        self.assertNotEqual(result.returncode, 0)
        creates = self.create_calls(calls)
        self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_small01_unknown_info_diagnostics_stop_before_small01_retry(self):
        for unknown_diagnostic in UNKNOWN_INFO_DIAGNOSTICS + UNKEYWORDED_INFO_DIAGNOSTICS:
            with self.subTest(unknown_diagnostic=unknown_diagnostic):
                mixed_log = "\n".join(
                    [
                        SMALL01_CLI_WRAPPED_LOG,
                        "2026/08/07 15:35:32\tINFO\t" + unknown_diagnostic,
                    ]
                )
                result, calls = self.run_orchestrator(
                    jobs={
                        "stack-small-01": [
                            _job("job-small01-mixed-info", logs=mixed_log)
                        ],
                    },
                    create={
                        "stack-core": {
                            "id": "job-core-new-capacity",
                            "state": "FAILED",
                            "logs": CAPACITY_LOG,
                        },
                        "stack-small-01": {
                            "id": "job-small01-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                )

                self.assertNotEqual(result.returncode, 0)
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], ["stack-core"])

    def test_json_log_records_are_rejected_as_non_raw_text(self):
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-json",
                    "state": "FAILED",
                    "logs": JSON_RECORD_LOG,
                },
                "stack-small-01": {
                    "id": "job-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_old_non_apply_history_after_capacity_boundary_is_ignored(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [
                    _job("job-capacity", logs=CAPACITY_LOG),
                    _job("job-old-plan", operation="PLAN", logs="untrusted old plan"),
                ],
            }
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[4] for call in creates], ["stack-core"])
        self.assertFalse(
            any(
                call[:3] == ["resource-manager", "job", "get-job-logs-content"]
                and "job-old-plan" in call
                for call in calls
            )
        )

    def test_malformed_job_data_fails_closed_before_inventory_decision(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-small-02": {"not": "an array"}},
            inventory={"data": [_instance("hermes-core-01", 2, 12)]},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not parse Resource Manager jobs", result.stderr)
        self.assertFalse(self.create_calls(calls))

    def test_capacity_history_resets_timeout_streak(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={
                "stack-small-02": [
                    _job("job-timeout-2", logs=TIMEOUT_LOG),
                    _job("job-timeout-1", logs=TIMEOUT_LOG),
                    _job("job-capacity", logs="Out of host capacity"),
                ],
            },
            create={
                "stack-small-02": {
                    "id": "job-small02-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.create_calls(calls)), 1)

    def test_timeout_budget_is_enforced_across_independent_invocations(self):
        for history_length in (1, 2, 3):
            with self.subTest(history_length=history_length):
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={
                        "stack-small-02": [
                            _job(f"job-timeout-{index}", logs=TIMEOUT_LOG)
                            for index in range(history_length, 0, -1)
                        ],
                    },
                    create={
                        "stack-small-02": {
                            "id": "job-small02-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                )
                if history_length < 3:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(self.create_calls(calls)), 1)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse(self.create_calls(calls))

    def test_unrecognized_permission_and_quota_logs_stop_without_create(self):
        for log in (
            "",
            "context deadline exceeded",
            "permission denied",
            "quota exceeded",
            "HTTP 429",
            "unauthorized",
        ):
            with self.subTest(log=log):
                result, calls = self.run_orchestrator(
                    jobs={"stack-core": [_job("job-unknown", logs=log)]}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.create_calls(calls))

    def test_malformed_and_unreadable_logs_stop_without_create(self):
        malformed_result, malformed_calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-malformed", logs="not a diagnostic")]}
        )
        self.assertNotEqual(malformed_result.returncode, 0)
        self.assertFalse(self.create_calls(malformed_calls))

        unreadable_result, unreadable_calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-unreadable", logs=TIMEOUT_LOG)]},
            log_failures={"job-unreadable": True},
        )
        self.assertNotEqual(unreadable_result.returncode, 0)
        self.assertFalse(self.create_calls(unreadable_calls))

    def test_lookalike_provider_timeout_after_mutation_stops(self):
        for suffix in (
            "Terraform has been successfully initialized",
            "terraform plan timed out",
            "module.foo resource is being created",
        ):
            with self.subTest(suffix=suffix):
                result, calls = self.run_orchestrator(
                    jobs={"stack-core": [_job("job-lookalike", logs=TIMEOUT_LOG + "\\n" + suffix)]}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.create_calls(calls))

    def test_checksum_timeout_requires_official_provider_url_and_all_signatures(self):
        candidates = (
            WRONG_ORIGIN_TIMEOUT_LOG,
            WRONG_PROVIDER_TIMEOUT_LOG,
            TIMEOUT_LOG.replace(
                "failed to retrieve authentication checksums for provider: ",
                "failed to retrieve provider metadata: ",
            ),
            TIMEOUT_LOG.replace(
                "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
                "context deadline exceeded",
            ),
        )
        for index, log in enumerate(candidates):
            with self.subTest(index=index):
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={"stack-small-02": [_job(f"job-invalid-timeout-{index}", logs=log)]},
                    create={
                        "stack-small-02": {
                            "id": f"job-small02-new-{index}",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.create_calls(calls))

    def test_mixed_timeout_and_dangerous_error_stops_and_redacts_log(self):
        secret = "synthetic-secret PRIVATE KEY fixture-secret"
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [
                    _job("job-mixed", logs=TIMEOUT_LOG + "\\npermission denied " + secret)
                ]
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))
        self.assertNotIn(secret, result.stderr)
        self.assertIn("state=FAILED", result.stderr)
        self.assertIn("job=job-mixed", result.stderr)

    def test_non_apply_history_stops(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-plan", operation="PLAN", logs=TIMEOUT_LOG)]}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))

    def test_canceled_history_does_not_reauthorize_timeout(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [
                    _job("job-timeout", logs=TIMEOUT_LOG),
                    _job("job-canceled", state="CANCELED", logs=""),
                ],
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))

    def test_history_retrieval_failure_stops(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-timeout", logs=TIMEOUT_LOG)]},
            history_failures={"stack-core": True},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))
        self.assertNotIn("simulated history failure", result.stderr)

    def test_active_job_on_any_target_skips_all_creates(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-small-02": [_job("job-active", state="IN_PROGRESS", logs="")],
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no Apply started", result.stdout)
        self.assertFalse(self.create_calls(calls))

    def test_inventory_failure_malformed_unknown_and_duplicate_stop_without_create(self):
        for mode in ("error", "malformed"):
            with self.subTest(mode=mode):
                result, calls = self.run_orchestrator(inventory_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.create_calls(calls))

        unknown_result, unknown_calls = self.run_orchestrator(
            inventory={"data": [_instance("unexpected-a1", 1, 6)]}
        )
        self.assertNotEqual(unknown_result.returncode, 0)
        self.assertFalse(self.create_calls(unknown_calls))

        duplicate_result, duplicate_calls = self.run_orchestrator(
            inventory={
                "data": [
                    _instance("hermes-core-01", 2, 12),
                    _instance("hermes-core-01", 2, 12),
                ]
            }
        )
        self.assertNotEqual(duplicate_result.returncode, 0)
        self.assertFalse(self.create_calls(duplicate_calls))

    def test_target_satisfied_skips_create(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-core-01", 2, 12)]}
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already satisfied", result.stdout)
        self.assertFalse(self.create_calls(calls))

    def test_small01_present_creates_only_small02(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            create={
                "stack-small-02": {
                    "id": "job-small02-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][4], "stack-small-02")

    def test_small02_only_stops_without_create(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-02", 1, 6)]}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("small-02 exists without small-01", result.stderr)
        self.assertFalse(self.create_calls(calls))

    def test_state_read_failure_stops(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-timeout", logs=TIMEOUT_LOG)]},
            state_failures={"job-timeout": True},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))
        self.assertNotIn("simulated state failure", result.stderr)

    def test_unknown_history_state_stops(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-unknown-state", state="MYSTERY", logs=TIMEOUT_LOG)]}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))

    def test_unknown_lifecycle_state_in_active_job_listing_fails_closed(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-small-02": [
                    _job("job-unknown-active-state", state="MYSTERY", logs="")
                ]
            }
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Could not parse Resource Manager jobs", result.stderr)
        self.assertFalse(self.create_calls(calls))

    def test_unknown_job_state_is_clamped_to_unknown_in_diagnostics(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-state-mismatch", logs=TIMEOUT_LOG)]},
            state_overrides={"job-state-mismatch": "MYSTERY"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))
        self.assertIn("state=UNKNOWN", result.stderr)
        self.assertNotIn("state=MYSTERY", result.stderr)

    def test_latest_succeeded_job_stops_when_target_is_missing(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-succeeded", state="SUCCEEDED", logs="")]}
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.create_calls(calls))

    def test_success_output_redacts_real_looking_job_id(self):
        real_looking_job_id = "provider-job-id.with-sensitive-shape"
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": real_looking_job_id,
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(real_looking_job_id, result.stdout)
        self.assertNotIn(real_looking_job_id, result.stderr)
        self.assertIn("redacted-job-id", result.stdout)

    def test_create_error_is_fixed_and_does_not_print_raw_cli_error(self):
        raw_error = "permission denied synthetic-secret PRIVATE KEY fixture-secret"
        result, calls = self.run_orchestrator(
            create={"stack-core": {"error": raw_error}},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertNotIn(raw_error, result.stderr)
        self.assertIn("Apply job create failed", result.stderr)

    def test_oci_429_defers_without_an_extra_apply(self):
        result, calls = self.run_orchestrator(
            create={"stack-core": {"rate_limit": True}},
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no Apply was started", result.stdout)
        self.assertEqual(len(creates), 1)


if __name__ == "__main__":
    unittest.main()
