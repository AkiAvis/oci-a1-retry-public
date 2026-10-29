#!/usr/bin/env python3
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "orchestrate-a1.sh"
BASELINE_COMMIT = "0b0dd9b049ac30761d04b0d6326e0bbccc1601b4"
SCRATCH = Path(os.environ.get("TMPDIR", tempfile.gettempdir()))

OFFICIAL_PROVIDER_CHECKSUM_URL = (
    "https://github.com/oracle/terraform-provider-oci/releases/download/"
    "v8.26.0/terraform-provider-oci_8.26.0_SHA256SUMS"
)

TIMEOUT_LOG = "\n".join(
    [
        "Error: Failed to install provider",
        "failed to retrieve authentication checksums for provider: ",
        OFFICIAL_PROVIDER_CHECKSUM_URL,
        "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
    ]
)


def _provider_timeout_log(provider_version: str | None, url_version: str = "8.26.0") -> str:
    url = OFFICIAL_PROVIDER_CHECKSUM_URL.replace("v8.26.0/", f"v{url_version}/")
    url = url.replace("_8.26.0_SHA256SUMS", f"_{url_version}_SHA256SUMS")
    lines = ["Error: Failed to install provider"]
    if provider_version is not None:
        lines.append(
            f"Error while installing oracle/oci v{provider_version}: could not query provider registry"
        )
    lines.extend(
        [
            "failed to retrieve authentication checksums for provider: ",
            url,
            "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
        ]
    )
    return "\n".join(lines)


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


def _bracket_row(timestamp: str, level: str, message: str = "") -> str:
    return f"[{timestamp}] {level} - {message}"


BRACKET_CAPACITY_LOG = "\n".join(
    [
        _bracket_row(
            "2026/10/28 10:00:00",
            "INFO",
            "Getting providers from registry and/or custom terraform providers",
        ),
        _bracket_row("2026/10/28 10:00:01", "INFO", "Initializing provider plugins..."),
        _bracket_row("2026/10/28 10:00:01", "INFO", "- Finding latest version of oracle/oci..."),
        _bracket_row(
            "2026/10/28 10:00:02",
            "ERROR",
            "Error: 500-InternalError, Out of host capacity.",
        ),
        _bracket_row(
            "2026/10/28 10:00:02",
            "INFO",
            "Suggestion: The service for this resource encountered an error.",
        ),
        _bracket_row(
            "2026/10/28 10:00:02",
            "INFO",
            "Documentation: https://registry.terraform.io/providers/oracle/oci/latest/docs/resources/core_instance",
        ),
        _bracket_row("2026/10/28 10:00:02", "INFO", "Provider version: 9.11.0"),
        _bracket_row("2026/10/28 10:00:02", "INFO", "Service: Core Instance"),
        _bracket_row("2026/10/28 10:00:02", "INFO", "Operation Name: LaunchInstance"),
        _bracket_row("2026/10/28 10:00:02", "INFO", "Correlation marker: synthetic-marker"),
    ]
)


def _bracket_provider_attempt(started_at: str, failed_at: str) -> list[str]:
    return [
        _bracket_row(started_at, "INFO", "Getting providers from registry and/or custom terraform providers"),
        _bracket_row(started_at, "INFO"),
        _bracket_row(started_at, "INFO", "Initializing provider plugins..."),
        _bracket_row(started_at, "INFO", "- Finding latest version of oracle/oci..."),
        _bracket_row(failed_at, "ERROR", "Error: Failed to install provider"),
        _bracket_row(failed_at, "INFO"),
        _bracket_row(
            failed_at,
            "INFO",
            "Error while installing oracle/oci v8.26.0: could not query provider registry",
        ),
        _bracket_row(
            failed_at,
            "INFO",
            "for registry.terraform.io/oracle/oci: failed to retrieve authentication",
        ),
        _bracket_row(failed_at, "INFO"),
        _bracket_row(
            failed_at,
            "INFO",
            "checksums for provider: the request failed after 2 attempts, please try again",
        ),
        _bracket_row(failed_at, "INFO"),
        _bracket_row(failed_at, "INFO", "later: Get"),
        _bracket_row(
            failed_at,
            "INFO",
            '"https://github.com/oracle/terraform-provider-oci/releases/download/v8.26.0/terraform-provider-oci_8.26.0_SHA256SUMS":',
        ),
        _bracket_row(
            failed_at,
            "INFO",
            "context deadline exceeded (Client.Timeout exceeded while awaiting headers)",
        ),
    ]


BRACKET_PROVIDER_TIMEOUT_LOG = "\r\n".join(
    _bracket_provider_attempt("2026/10/28 10:00:00", "2026/10/28 10:00:01")
    + _bracket_provider_attempt("2026/10/28 10:00:02", "2026/10/28 10:00:03")
)

_CAPACITY_ERROR_TAIL = CAPACITY_LOG.split(
    "2026/10/26 17:09:36\tERROR\t", 1
)[1]
VARIABLE_PRELUDE_CAPACITY_LOG = "\n".join(
    [
        "2026/10/26 17:09:27\tINFO\tGetting providers from registry and/or custom terraform providers",
        "2026/10/26 17:09:27\tINFO\tInitializing provider plugins...",
        "2026/10/26 17:09:27\tINFO\t-Finding latest version of oracle/oci...",
        "2026/10/26 17:09:29\tINFO\tTerraform has been successfully initialized!",
        "2026/10/26 17:09:33\tINFO\tTerraform used the selected providers to generate the following execution plan.",
        "2026/10/26 17:09:33\tINFO\tTerraform will perform the following actions:",
        "2026/10/26 17:09:33\tINFO\t  # module.synthetic.oci_core_instance.generated will be created",
        '2026/10/26 17:09:33\tINFO\t  + resource "oci_core_instance" "generated_alt" {',
        '      + arbitrary_plan_field = "opaque-but-embedded"',
        '      + opaque_plan_field = "opaque-but-embedded"',
        '      + availability_domain = "synthetic-ad-2"',
        "    }",
        "2026/10/26 17:09:33\tINFO\tPlan: 1 to add, 0 to change, 0 to destroy.",
        "2026/10/26 17:09:35\tINFO\tmodule.synthetic.oci_core_instance.generated: Creating...",
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
MULTILINE_STRUCTURED_ERROR_RECORDS = (
    (
        "level-error",
        '{\n  "level": "ERROR",\n  "message": "NotAuthorizedOrNotFound"\n}',
    ),
    (
        "message-error",
        '{\n  "message": "Error: InvalidParameter"\n}',
    ),
    (
        "nested-level-error",
        '{\n  "error": {\n    "level": "ERROR",\n    "message": "NotAuthorizedOrNotFound"\n  }\n}',
    ),
)
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
STRUCTURALLY_UNKNOWN_PRE_ERROR_ROWS = (
    "{timestamp}\tINFO\tService has been deactivated",
    "{timestamp}\tINFO\tA condition has changed",
    "{timestamp}\tINFO\tOpaque unexpected INFO row",
    "bare standalone contamination",
)


def _insert_before_first_message(log: str, needle: str, marker: str) -> str:
    lines = log.splitlines()
    index = next(index for index, line in enumerate(lines) if needle in line)
    return "\n".join(lines[:index] + [marker] + lines[index:])


def _insert_at_line(log: str, index: int, marker: str) -> str:
    lines = log.splitlines()
    return "\n".join(lines[:index] + [marker] + lines[index:])


def _pre_error_marker_rows(timestamp: str) -> tuple[str, ...]:
    return tuple(
        row.format(timestamp=timestamp) if "{timestamp}" in row else row
        for row in STRUCTURALLY_UNKNOWN_PRE_ERROR_ROWS
    )


def _bracket_info_json_record(timestamp: str, body: str, *, wrap_each_line: bool) -> str:
    lines = body.splitlines()
    if wrap_each_line:
        return "\n".join(_bracket_row(timestamp, "INFO", line) for line in lines)
    return "\n".join([_bracket_row(timestamp, "INFO", lines[0]), *lines[1:]])


def _redact_private_log(text: str) -> str:
    text = re.sub(r"\bocid1\.[A-Za-z0-9._-]+\b", "<redacted-ocid>", text)
    text = re.sub(
        r"(?i)(opc(?:[- ]+request[- ]+id)\s*[:=]\s*)\S+",
        r"\1<redacted-request-id>",
        text,
    )
    text = re.sub(
        r"(?i)ssh-(?:rsa|ed25519)\s+[A-Za-z0-9+/=]+(?:\s+\S+)?",
        "<redacted-public-key>",
        text,
    )
    return text


def _downloaded_to_observed_cli_text(text: str) -> str:
    """Reproduce the observed OCI CLI 3.95.1 line renderer in memory."""
    return re.sub(
        r"^\[([^\]]+)\] (INFO|ERROR|WARN) - (.*)$",
        r"\1[TERRAFORM_CONSOLE] [\2] \3",
        text,
        flags=re.MULTILINE,
    )


def _load_private_downloaded_logs() -> tuple[str, str]:
    root_value = os.environ.get("OCI_A1_RETRY_LOG_DIR")
    if not root_value:
        raise unittest.SkipTest("set OCI_A1_RETRY_LOG_DIR for private downloaded-log replay")

    root = Path(root_value)
    if not root.is_dir():
        raise AssertionError("OCI_A1_RETRY_LOG_DIR is not a directory")

    core_candidates = []
    small_candidates = []
    for path in root.glob("*.log"):
        text = path.read_text(encoding="utf-8", errors="replace")
        line_count = len(text.splitlines())
        if (
            line_count == 208
            and "Out of host capacity" in text
            and "Failed to install provider" not in text
        ):
            core_candidates.append(text)
        if (
            line_count == 54
            and "Failed to install provider" in text
            and "Out of host capacity" not in text
        ):
            small_candidates.append(text)

    if len(core_candidates) != 1 or len(small_candidates) != 1:
        raise AssertionError("could not identify the two supplied downloaded logs")
    return _redact_private_log(core_candidates[0]), _redact_private_log(small_candidates[0])


def _load_private_console_logs() -> tuple[tuple[str, ...], str]:
    root_value = os.environ.get("OCI_A1_RETRY_LOG_DIR")
    if not root_value:
        raise unittest.SkipTest("set OCI_A1_RETRY_LOG_DIR for private Console-log replay")

    root = Path(root_value)
    if not root.is_dir():
        raise AssertionError("OCI_A1_RETRY_LOG_DIR is not a directory")

    core_names = ("貼り付けた内容 (15-2.3 KB)", "貼り付けた内容 (15-3.3 KB)")
    small_name = "貼り付けた内容 (3-2.7 KB)"
    paths = [root / name for name in (*core_names, small_name)]
    if not all(path.is_file() for path in paths):
        raise AssertionError("could not identify the supplied Console exports")

    core_logs = tuple(
        _redact_private_log(path.read_text(encoding="utf-8", errors="replace"))
        for path in paths[:2]
    )
    small_log = _redact_private_log(
        paths[2].read_text(encoding="utf-8", errors="replace")
    )
    return core_logs, small_log


def _baseline_script_text() -> str:
    result = subprocess.run(
        ["git", "show", f"{BASELINE_COMMIT}:scripts/orchestrate-a1.sh"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError("could not read the baseline orchestrator")
    return result.stdout


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fake_awk_script(stdout: str = "", stderr: str = "", status: int = 0) -> str:
    newline = chr(10)
    return newline.join(
        [
            "#!/usr/bin/bash",
            "/usr/bin/python3 - <<'PY'",
            "import sys",
            f"sys.stdout.write({json.dumps(stdout)})",
            f"sys.stderr.write({json.dumps(stderr)})",
            "PY",
            f"exit {status}",
        ]
    ) + newline


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
        log_outputs=None,
        state_failures=None,
        state_overrides=None,
        extra_env=None,
        script_text=None,
    ):
        jobs = jobs or {}
        inventory = inventory if inventory is not None else {"data": []}
        create = create or {
            "stack-core": {"id": "job-new", "state": "SUCCEEDED", "logs": ""},
        }
        history_failures = history_failures or {}
        log_failures = log_failures or {}
        log_outputs = log_outputs or {}
        state_failures = state_failures or {}
        state_overrides = state_overrides or {}

        with tempfile.TemporaryDirectory(dir=SCRATCH) as temp_dir:
            temp = Path(temp_dir)
            script_path = SCRIPT
            if script_text is not None:
                script_path = temp / "orchestrate-a1-baseline.sh"
                _make_executable(script_path, script_text)
            config_path = temp / "fake-cli-config.json"
            calls_path = temp / "fake-cli-calls.jsonl"
            config_path.write_text(
                json.dumps(
                    {
                        "jobs": jobs,
                        "create": create,
                        "history_failures": history_failures,
                        "log_failures": log_failures,
                        "log_outputs": log_outputs,
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

                    def print_log_response(logs):
                        output = option("--output") if "--output" in args else "json"
                        query = option("--query") if "--query" in args else None
                        if output == "json" and query == "data" and "--raw-output" in args:
                            print(logs, end="")
                        elif output == "json" and query == "data":
                            print(json.dumps(logs, separators=(",", ":")))
                        else:
                            print(json.dumps(dict(data=logs), separators=(",", ":")))

                    if args[:3] == ["resource-manager", "job", "list"]:
                        stack_id = option("--stack-id")
                        jobs = config["jobs"].get(stack_id, [])
                        if "--sort-by" in args:
                            if config["history_failures"].get(stack_id):
                                print("simulated history failure", file=sys.stderr)
                                raise SystemExit(8)
                            if "--limit" in args:
                                jobs = jobs[: int(option("--limit"))]
                        print(json.dumps(dict(data=jobs), separators=(",", ":")))
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
                        if job_id in config["log_outputs"]:
                            override = config["log_outputs"][job_id]
                            if override.get("stderr"):
                                print(override["stderr"], file=sys.stderr)
                            print(override.get("stdout", ""), end="")
                            raise SystemExit(override.get("returncode", 0))
                        if config["log_failures"].get(job_id):
                            print("simulated log failure", file=sys.stderr)
                            raise SystemExit(11)
                        for stack_jobs in config["jobs"].values():
                            for job in stack_jobs:
                                if job["id"] == job_id:
                                    print_log_response(job.get("logs", ""))
                                    raise SystemExit(0)
                        for create_job in config["create"].values():
                            if create_job.get("id") == job_id:
                                print_log_response(create_job.get("logs", ""))
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
                ["/usr/bin/bash", str(script_path)],
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

    @staticmethod
    def log_calls(calls):
        return [
            call
            for call in calls
            if call[:3] == ["resource-manager", "job", "get-job-logs-content"]
        ]


class OrchestrateA1ProviderTimeoutTests(OrchestrateA1TestCase):
    def test_log_download_requests_query_and_raw_output_for_both_paths(self):
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

        self.assertEqual(result.returncode, 0, result.stderr)
        log_calls = self.log_calls(calls)
        self.assertEqual(len(log_calls), 2)
        for call in log_calls:
            self.assertEqual(
                call[5:],
                ["--output", "json", "--query", "data", "--raw-output"],
            )

    def test_history_read_is_bounded_and_active_job_scan_stays_unbounded(self):
        result, calls = self.run_orchestrator()

        self.assertEqual(result.returncode, 0, result.stderr)
        list_calls = [
            call
            for call in calls
            if call[:3] == ["resource-manager", "job", "list"]
        ]
        active_calls = [call for call in list_calls if "--sort-by" not in call]
        history_calls = [call for call in list_calls if "--sort-by" in call]

        self.assertEqual(len(active_calls), 3)
        for call in active_calls:
            self.assertIn("--all", call)
        self.assertEqual(
            history_calls,
            [
                [
                    "resource-manager",
                    "job",
                    "list",
                    "--stack-id",
                    "stack-core",
                    "--limit",
                    "3",
                    "--sort-by",
                    "TIMECREATED",
                    "--sort-order",
                    "DESC",
                    "--output",
                    "json",
                ]
            ],
        )
        self.assertNotIn("--all", history_calls[0])

    def test_malformed_or_wrong_log_payloads_fail_closed_without_extra_apply(self):
        payloads = (
            ("malformed-json", '{"data":'),
            ("missing-data", '{"message":"unexpected response"}'),
            ("null-data", '{"data":null}'),
            ("array-data", '{"data":[]}'),
            ("object-data", '{"data":{}}'),
            ("json-string", json.dumps(TIMEOUT_LOG)),
        )
        for label, payload in payloads:
            with self.subTest(label=label):
                job_id = f"job-invalid-payload-{label}"
                result, calls = self.run_orchestrator(
                    jobs={"stack-core": [_job(job_id, logs=TIMEOUT_LOG)]},
                    log_outputs={job_id: {"stdout": payload}},
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.create_calls(calls))
                self.assertEqual(
                    self.log_calls(calls)[0][5:],
                    ["--output", "json", "--query", "data", "--raw-output"],
                )

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

    def test_three_consecutive_provider_timeouts_stop_even_with_older_capacity(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={
                "stack-small-02": [
                    _job("job-timeout-3", logs=TIMEOUT_LOG),
                    _job("job-timeout-2", logs=TIMEOUT_LOG),
                    _job("job-timeout-1", logs=TIMEOUT_LOG),
                    _job("job-older-capacity", logs="Out of host capacity"),
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
    def test_core_bounded_guard_stops_mixed_capacity_and_provider_timeout(self):
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

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core"],
        )

    def test_known_unsolved_core_baseline_bypass_partial_provider_error_with_capacity(self):
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

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core", "stack-small-01"],
        )

    def test_known_unsolved_core_baseline_bypass_capacity_and_generic_timeout(self):
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

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core", "stack-small-01"],
        )

    def test_capacity_failure_still_allows_retry(self):
        result, calls = self.run_orchestrator(
            jobs={"stack-core": [_job("job-capacity", logs="Out of host capacity")]}
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.create_calls(calls)), 1)
        self.assertEqual(self.create_calls(calls)[0][4], "stack-core")

    def test_core_explicit_preludes_stop_only_for_bounded_serious_markers(self):
        preludes = (
            ("2026/10/26 17:09:35\tINFO\tCredentials rejected", True),
            ("2026/10/26 17:09:35\tINFO\tAccount is disabled", False),
            ("2026/10/26 17:09:35\tINFO\tAccess revoked", False),
            ("2026/10/26 17:09:35\tINFO\t  cReDeNtIaLs\t ReJeCtEd  ", True),
            ("2026/10/26 17:09:35\tINFO\t ACCOUNT   IS\tDISABLED ", False),
            ("2026/10/26 17:09:35\tINFO\t aCcEsS\t ReVoKeD ", False),
            ("Error: AccountSuspended", True),
            ("ERROR: AccountSuspended", True),
            ("error: AccountSuspended", True),
            ("2026/10/26 17:09:35\tERROR\tAccountSuspended", True),
        )
        for prelude, should_stop in preludes:
            with self.subTest(prelude=prelude):
                mixed_log = prelude + "\n" + CAPACITY_LOG
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {"id": "job-core-new", "state": "FAILED", "logs": mixed_log},
                        "stack-small-01": {"id": "job-small-new", "state": "SUCCEEDED", "logs": ""},
                    }
                )
                if should_stop:
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core"],
                    )
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core", "stack-small-01"],
                    )

                historical, historical_calls = self.run_orchestrator(
                    jobs={"stack-core": [_job("job-old-mixed", logs=mixed_log)]},
                )
                if should_stop:
                    self.assertNotEqual(historical.returncode, 0, historical.stderr)
                    self.assertFalse(self.create_calls(historical_calls))
                else:
                    self.assertEqual(historical.returncode, 0, historical.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(historical_calls)],
                        ["stack-core"],
                    )

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

    def test_bracket_timestamp_capacity_routes_core_then_small01(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [_job("job-bracket-old-capacity", logs=BRACKET_CAPACITY_LOG)],
                "stack-small-01": [_job("job-small01-old-timeout", logs=SMALL01_CONSOLE_LOG)],
            },
            create={
                "stack-core": {
                    "id": "job-bracket-core-new",
                    "state": "FAILED",
                    "logs": BRACKET_CAPACITY_LOG,
                },
                "stack-small-01": {
                    "id": "job-bracket-small01-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )

        creates = self.create_calls(calls)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[4] for call in creates], ["stack-core", "stack-small-01"])

    def test_bracket_timestamp_provider_timeout_preserves_retries_blank_payloads_and_crlf(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={
                "stack-small-02": [
                    _job("job-bracket-timeout", logs=BRACKET_PROVIDER_TIMEOUT_LOG)
                ]
            },
            create={
                "stack-small-02": {
                    "id": "job-bracket-small02-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("diagnostic=provider_checksum_download_timeout", result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-small-02"],
        )

    def test_core_bounded_guard_and_small_provider_markers_still_fail_closed(self):
        markers = (
            _bracket_row("2026/10/28 10:00:04", "INFO", "Credentials rejected"),
            _bracket_row("2026/10/28 10:00:04", "ERROR", "AccountSuspended"),
        )
        for marker in markers:
            for block_name, block_log in (
                ("capacity", BRACKET_CAPACITY_LOG),
                ("provider", BRACKET_PROVIDER_TIMEOUT_LOG),
            ):
                separator = "\r\n" if "\r\n" in block_log else "\n"
                for position in ("before", "after"):
                    with self.subTest(marker=marker, block_name=block_name, position=position):
                        mixed_log = (
                            marker + separator + block_log
                            if position == "before"
                            else block_log + separator + marker
                        )
                        if block_name == "capacity":
                            result, calls = self.run_orchestrator(
                                create={
                                    "stack-core": {
                                        "id": "job-bracket-marker-core",
                                        "state": "FAILED",
                                        "logs": mixed_log,
                                    },
                                    "stack-small-01": {
                                        "id": "job-bracket-marker-small01",
                                        "state": "SUCCEEDED",
                                        "logs": "",
                                    },
                                }
                            )
                        else:
                            result, calls = self.run_orchestrator(
                                inventory={"data": []},
                                jobs={
                                    "stack-core": [
                                        _job("job-bracket-marker-old-core", logs=CAPACITY_LOG)
                                    ],
                                    "stack-small-01": [
                                        _job("job-bracket-marker-small01", logs=mixed_log)
                                    ],
                                },
                                create={
                                    "stack-core": {
                                        "id": "job-bracket-marker-core-new",
                                        "state": "FAILED",
                                        "logs": CAPACITY_LOG,
                                    },
                                    "stack-small-01": {
                                        "id": "job-bracket-marker-small01-new",
                                        "state": "SUCCEEDED",
                                        "logs": "",
                                    },
                                },
                            )

                        if block_name == "capacity":
                            self.assertNotEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(
                                [call[4] for call in self.create_calls(calls)],
                                ["stack-core"],
                            )
                        else:
                            self.assertNotEqual(result.returncode, 0)
                            self.assertEqual(
                                [call[4] for call in self.create_calls(calls)],
                                ["stack-core"],
                            )

    def test_core_bounded_guard_stops_explicit_errors_but_keeps_unknown_info_unsolved(self):
        suffixes = (
            ("Error: Unexpected provider failure", True),
            (_bracket_row("2026/10/28 10:00:04", "ERROR", "Unexpected provider failure"), True),
            (_bracket_row("2026/10/28 10:00:04", "INFO", "Unexpected provider failure"), False),
        )
        for suffix, should_stop in suffixes:
            with self.subTest(suffix=suffix):
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": "job-bracket-unknown-core",
                            "state": "FAILED",
                            "logs": BRACKET_CAPACITY_LOG + "\n" + suffix,
                        },
                        "stack-small-01": {
                            "id": "job-bracket-unknown-small01",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    }
                )

                if should_stop:
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core"],
                    )
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core", "stack-small-01"],
                    )

    def test_known_unsolved_core_baseline_bypass_bracket_malformed_capacity_marker(self):
        malformed_rows = (
            _bracket_row(
                "2026/10/28 10:00:04",
                "DEBUG",
                "Error: 500-InternalError, Out of host capacity.",
            ),
            "[2026/10/28 10:00:04] INFO Error: 500-InternalError, Out of host capacity.",
        )
        for malformed_row in malformed_rows:
            with self.subTest(malformed_row=malformed_row):
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": "job-bracket-malformed-core",
                            "state": "FAILED",
                            "logs": BRACKET_CAPACITY_LOG + "\n" + malformed_row,
                        },
                        "stack-small-01": {
                            "id": "job-bracket-malformed-small01",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    }
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    ["stack-core", "stack-small-01"],
                )

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

    def test_observed_cli_rendered_full_logs_route_core_then_small01(self):
        core_log, small_log = _load_private_downloaded_logs()
        cli_core = _downloaded_to_observed_cli_text(core_log)
        cli_small = _downloaded_to_observed_cli_text(small_log)
        self.assertEqual(len(cli_core.splitlines()), 208)
        self.assertEqual(len(cli_small.splitlines()), 54)

        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [_job("job-cli-core-old", logs=cli_core)],
                "stack-small-01": [_job("job-cli-small-old", logs=cli_small)],
            },
            create={
                "stack-core": {"id": "job-cli-core-new", "state": "FAILED", "logs": cli_core},
                "stack-small-01": {"id": "job-cli-small-new", "state": "SUCCEEDED", "logs": ""},
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[4] for call in self.create_calls(calls)], ["stack-core", "stack-small-01"])

    def test_observed_cli_log_wrappers_fail_closed_when_malformed_or_unknown(self):
        core_log, small_log = _load_private_downloaded_logs()
        cli_core = _downloaded_to_observed_cli_text(core_log)
        cli_small = _downloaded_to_observed_cli_text(small_log)
        first = next(line for line in cli_small.splitlines() if "[INFO]" in line)
        variants = (
            ("unknown-severity", cli_small.replace(first, first.replace("[INFO]", "[TRACE]", 1), 1)),
            ("malformed-wrapper", cli_small.replace(first, first.replace("[TERRAFORM_CONSOLE]", "[UNKNOWN_SOURCE]", 1), 1)),
            ("mixed-unknown-error", cli_small + "\n2026/08/07 15:26:45[TERRAFORM_CONSOLE] [ERROR] Error: NotAuthorizedOrNotFound"),
        )
        for label, variant in variants:
            with self.subTest(label=label):
                result, calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [_job("job-cli-history-core", logs=cli_core)],
                        "stack-small-01": [_job("job-cli-history-small", logs=variant)],
                    },
                    create={
                        "stack-core": {"id": "job-cli-invalid-core", "state": "FAILED", "logs": cli_core},
                        "stack-small-01": {"id": "job-cli-small-must-not-start", "state": "SUCCEEDED", "logs": ""},
                    },
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual([call[4] for call in self.create_calls(calls)], ["stack-core"])

    def test_known_unsolved_core_baseline_bypass_private_core_markers_small_markers_still_stop(self):
        core_log, small_log = _load_private_downloaded_logs()

        normal_result, normal_calls = self.run_orchestrator(
            jobs={
                "stack-core": [_job("job-private-core-old", logs=core_log)],
                "stack-small-01": [_job("job-private-small-old", logs=small_log)],
            },
            create={
                "stack-core": {
                    "id": "job-private-core-new",
                    "state": "FAILED",
                    "logs": core_log,
                },
                "stack-small-01": {
                    "id": "job-private-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )
        self.assertEqual(normal_result.returncode, 0, normal_result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(normal_calls)],
            ["stack-core", "stack-small-01"],
        )

        marker_cases = (
            ("core-before", "core", "before", "2026/10/28 04:09:38", "InvalidParameter"),
            ("core-after", "core", "after", "2026/10/28 04:09:38", "InvalidParameter"),
            ("small-before", "small", "before", "2026/08/07 15:26:44", "AccountSuspended"),
            ("small-after", "small", "after", "2026/08/07 15:26:44", "AccountSuspended"),
        )
        for label, block_name, position, timestamp, marker_text in marker_cases:
            with self.subTest(label=label):
                marker = _bracket_row(timestamp, "INFO", marker_text)
                block = core_log if block_name == "core" else small_log
                mixed_log = (
                    marker + "\n" + block
                    if position == "before"
                    else block + "\n" + marker
                )

                if block_name == "core":
                    result, calls = self.run_orchestrator(
                        create={
                            "stack-core": {
                                "id": "job-private-core-marker",
                                "state": "FAILED",
                                "logs": mixed_log,
                            },
                            "stack-small-01": {
                                "id": "job-private-small-new",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        }
                    )
                else:
                    result, calls = self.run_orchestrator(
                        jobs={
                            "stack-core": [_job("job-private-core-old", logs=core_log)],
                            "stack-small-01": [
                                _job("job-private-small-marker", logs=mixed_log)
                            ],
                        },
                        create={
                            "stack-core": {
                                "id": "job-private-core-new",
                                "state": "FAILED",
                                "logs": core_log,
                            },
                            "stack-small-01": {
                                "id": "job-private-small-new",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        },
                    )

                if block_name == "core":
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core", "stack-small-01"],
                    )
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core"],
                    )

        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-private-bare-marker",
                    "state": "FAILED",
                    "logs": "Out of host capacity\n"
                    + _bracket_row("2026/10/28 04:09:38", "INFO", "InvalidParameter"),
                },
                "stack-small-01": {
                    "id": "job-private-small-new",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core", "stack-small-01"],
        )

    def test_actual_full_logs_benign_core_warn_preserves_core_then_small_and_replays_baseline_core_path(self):
        core_log, small_log = _load_private_downloaded_logs()
        warned_core_log = _insert_before_first_message(
            core_log,
            "Error: 500-InternalError",
            "2026/10/28 04:09:38\tWARN\tHarmless provider prelude notice",
        )

        candidate_result, candidate_calls = self.run_orchestrator(
            jobs={
                "stack-core": [_job("job-private-core-old-capacity", logs=core_log)],
                "stack-small-01": [_job("job-private-small-old-timeout", logs=small_log)],
            },
            create={
                "stack-core": {
                    "id": "job-private-core-new-warn",
                    "state": "FAILED",
                    "logs": warned_core_log,
                },
                "stack-small-01": {
                    "id": "job-private-small-new-warn",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
        )

        self.assertEqual(candidate_result.returncode, 0, candidate_result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(candidate_calls)],
            ["stack-core", "stack-small-01"],
        )

        baseline_result, baseline_calls = self.run_orchestrator(
            jobs={
                "stack-core": [_job("job-baseline-core-old-capacity", logs=core_log)],
                "stack-small-01": [
                    _job("job-baseline-small-capacity", logs="Out of host capacity")
                ],
            },
            create={
                "stack-core": {
                    "id": "job-baseline-core-new-warn",
                    "state": "FAILED",
                    "logs": warned_core_log,
                },
                "stack-small-01": {
                    "id": "job-baseline-small-new-warn",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            },
            script_text=_baseline_script_text(),
        )

        self.assertEqual(baseline_result.returncode, 0, baseline_result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(baseline_calls)],
            ["stack-core", "stack-small-01"],
        )

    def test_core_capacity_stops_explicit_mixed_serious_records_in_history_and_fresh_failure(self):
        core_log, _ = _load_private_downloaded_logs()
        records = (
            _bracket_row(
                "2026/10/28 04:09:38",
                "ERROR",
                "Error: NotAuthorizedOrNotFound",
            ),
            _bracket_row(
                "2026/10/28 04:09:38",
                "ERROR",
                "Error: InvalidParameter",
            ),
            _bracket_row(
                "2026/10/28 04:09:38",
                "ERROR",
                "Unexpected provider failure",
            ),
            "Error: NotAuthorizedOrNotFound",
            STRUCTURED_ERROR_RECORD,
            _bracket_row("2026/10/28 04:09:38", "INFO", "Credentials rejected"),
            _bracket_row("2026/10/28 04:09:38", "INFO", "AccountSuspended"),
        )

        for record in records:
            for position in ("before", "after"):
                with self.subTest(record=record, position=position):
                    mixed_log = (
                        record + "\n" + core_log
                        if position == "before"
                        else core_log + "\n" + record
                    )
                    historical_result, historical_calls = self.run_orchestrator(
                        jobs={
                            "stack-core": [
                                _job("job-core-history-mixed-error", logs=mixed_log)
                            ]
                        },
                        create={
                            "stack-core": {
                                "id": "job-core-history-should-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                            "stack-small-01": {
                                "id": "job-small-history-should-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        },
                    )
                    self.assertNotEqual(historical_result.returncode, 0)
                    self.assertFalse(self.create_calls(historical_calls))

                    fresh_result, fresh_calls = self.run_orchestrator(
                        create={
                            "stack-core": {
                                "id": "job-core-fresh-mixed-error",
                                "state": "FAILED",
                                "logs": mixed_log,
                            },
                            "stack-small-01": {
                                "id": "job-small-fresh-should-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        }
                    )
                    self.assertNotEqual(fresh_result.returncode, 0)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(fresh_calls)],
                        ["stack-core"],
                    )

    def test_observed_cli_wrapped_core_errors_block_history_and_fresh_fallback(self):
        core_log, _ = _load_private_downloaded_logs()
        cli_core = _downloaded_to_observed_cli_text(core_log)
        for record in (
            "2026/10/29 04:09:38[TERRAFORM_CONSOLE] [ERROR] NotAuthorizedOrNotFound",
            "2026/10/29 04:09:38[TERRAFORM_CONSOLE] [INFO] Account suspended",
            "2026/10/29 04:09:38[UNKNOWN_SOURCE] [ERROR] NotAuthorizedOrNotFound",
            "2026/10/29 04:09:38[TERRAFORM_CONSOLE] [TRACE] NotAuthorizedOrNotFound",
        ):
            with self.subTest(record=record):
                mixed_log = cli_core + "\n" + record
                historical_result, historical_calls = self.run_orchestrator(
                    jobs={"stack-core": [_job("job-observed-core-history", logs=mixed_log)]},
                    create={
                        "stack-core": {"id": "job-history-must-not-start", "state": "SUCCEEDED", "logs": ""},
                        "stack-small-01": {"id": "job-small-must-not-start", "state": "SUCCEEDED", "logs": ""},
                    },
                )
                self.assertNotEqual(historical_result.returncode, 0)
                self.assertFalse(self.create_calls(historical_calls))

                fresh_result, fresh_calls = self.run_orchestrator(
                    create={
                        "stack-core": {"id": "job-observed-core-fresh", "state": "FAILED", "logs": mixed_log},
                        "stack-small-01": {"id": "job-small-must-not-start", "state": "SUCCEEDED", "logs": ""},
                    }
                )
                self.assertNotEqual(fresh_result.returncode, 0)
                self.assertEqual([call[4] for call in self.create_calls(fresh_calls)], ["stack-core"])

    def test_small_log_normalizer_nonzero_status_fails_closed_for_small_history(self):
        _, small_log = _load_private_downloaded_logs()
        small_mixed_log = small_log + chr(10) + "2026/08/07 15:26:45[TERRAFORM_CONSOLE] [ERROR] Error: Unknown failure"
        for status in (1, 2, 126, 127):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir=SCRATCH) as awk_dir:
                awk_path = Path(awk_dir) / "awk"
                marker_path = Path(awk_dir) / "normalizer-reached"
                _make_executable(
                    awk_path,
                    textwrap.dedent(
                        f'''#!/usr/bin/bash
                        if [[ "$*" == *"print raw"* && "$*" == *"TERRAFORM_CONSOLE"* ]]; then
                          printf hit > "$NORMALIZER_MARKER"
                          printf '%s' "$NORMALIZER_PARTIAL"
                          exit {status}
                        fi
                        exec /usr/bin/awk "$@"
                        '''
                    ),
                )
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={
                        "stack-small-02": [
                            _job(f"job-small-history-norm-{status}", logs=small_mixed_log)
                        ]
                    },
                    create={
                        "stack-small-02": {
                            "id": f"job-small-history-should-not-start-{status}",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                    extra_env={
                        "PATH": f"{awk_dir}:/usr/bin:/bin",
                        "NORMALIZER_MARKER": str(marker_path),
                        "NORMALIZER_PARTIAL": small_log,
                    },
                )

                self.assertTrue(marker_path.is_file(), "normalizer fault injection was not reached")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(small_log, result.stdout + result.stderr)
                self.assertEqual(self.create_calls(calls), [])

                marker_path.unlink()
                fresh_result, fresh_calls = self.run_orchestrator(
                    jobs={
                        "stack-small-01": [
                            _job(f"job-small-fresh-history-norm-{status}", logs=small_mixed_log)
                        ]
                    },
                    create={
                        "stack-core": {
                            "id": f"job-core-fresh-norm-{status}",
                            "state": "FAILED",
                            "logs": CAPACITY_LOG,
                        },
                        "stack-small-01": {
                            "id": f"job-small-fresh-should-not-start-{status}",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                    extra_env={
                        "PATH": f"{awk_dir}:/usr/bin:/bin",
                        "NORMALIZER_MARKER": str(marker_path),
                        "NORMALIZER_PARTIAL": small_log,
                    },
                )
                self.assertTrue(marker_path.is_file(), "fresh-path normalizer fault injection was not reached")
                self.assertNotEqual(fresh_result.returncode, 0)
                self.assertEqual(
                    [call[4] for call in self.create_calls(fresh_calls)],
                    ["stack-core"],
                )
                self.assertNotIn(small_log, fresh_result.stdout + fresh_result.stderr)

    def test_core_safety_parser_nonzero_status_fails_closed_for_history_and_fresh_failure(self):
        core_log, _ = _load_private_downloaded_logs()
        for status in (1, 2, 126, 127):
            with self.subTest(status=status), tempfile.TemporaryDirectory(dir=SCRATCH) as awk_dir:
                _make_executable(
                    Path(awk_dir) / "awk",
                    _fake_awk_script(
                        stdout="unexpected parser stdout" + chr(10),
                        stderr="unexpected parser stderr" + chr(10),
                        status=status,
                    ),
                )
                history_result, history_calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [
                            _job(f"job-core-history-awk-{status}", logs=core_log)
                        ]
                    },
                    extra_env={"PATH": f"{awk_dir}:/usr/bin:/bin"},
                )

                self.assertEqual(history_result.returncode, 1)
                self.assertEqual(history_result.stdout, "")
                self.assertEqual(
                    history_result.stderr,
                    "Latest core 2/12 Apply stopped: "
                    "diagnostic=core_safety_parser_failed state=FAILED "
                    f"job=job-core-history-awk-{status}." + chr(10),
                )
                self.assertFalse(self.create_calls(history_calls))

                fresh_result, fresh_calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": f"job-core-fresh-awk-{status}",
                            "state": "FAILED",
                            "logs": core_log,
                        },
                        "stack-small-01": {
                            "id": f"job-small-fresh-awk-{status}",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                    extra_env={"PATH": f"{awk_dir}:/usr/bin:/bin"},
                )

                self.assertEqual(fresh_result.returncode, 1)
                self.assertEqual(
                    fresh_result.stdout,
                    f"Started core 2/12 Apply job: job-core-fresh-awk-{status}" + chr(10),
                )
                self.assertEqual(
                    fresh_result.stderr,
                    "ERROR: Core Apply failed for a reason other than host capacity; retry is stopped." + chr(10),
                )
                self.assertEqual(
                    [call[4] for call in self.create_calls(fresh_calls)],
                    ["stack-core"],
                )

    def test_core_safety_parser_requires_exact_success_token_for_history_and_fresh_failure(self):
        core_log, _ = _load_private_downloaded_logs()
        malformed_outputs = (
            "",
            "unexpected" + chr(10),
            "clean" + chr(10) + "parser noise" + chr(10),
            "parser noise" + chr(10) + "clean" + chr(10),
            " clean" + chr(10),
        )
        for index, parser_output in enumerate(malformed_outputs):
            with self.subTest(parser_output=parser_output), tempfile.TemporaryDirectory(dir=SCRATCH) as awk_dir:
                _make_executable(
                    Path(awk_dir) / "awk",
                    _fake_awk_script(
                        stdout=parser_output,
                        stderr="unexpected parser stderr" + chr(10),
                        status=0,
                    ),
                )
                history_result, history_calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [
                            _job(f"job-core-history-malformed-{index}", logs=core_log)
                        ]
                    },
                    extra_env={"PATH": f"{awk_dir}:/usr/bin:/bin"},
                )

                self.assertEqual(history_result.returncode, 1)
                self.assertEqual(history_result.stdout, "")
                self.assertEqual(
                    history_result.stderr,
                    "Latest core 2/12 Apply stopped: "
                    "diagnostic=core_safety_parser_failed state=FAILED "
                    f"job=job-core-history-malformed-{index}." + chr(10),
                )
                self.assertFalse(self.create_calls(history_calls))

                fresh_result, fresh_calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": f"job-core-fresh-malformed-{index}",
                            "state": "FAILED",
                            "logs": core_log,
                        },
                        "stack-small-01": {
                            "id": f"job-small-fresh-malformed-{index}",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                    extra_env={"PATH": f"{awk_dir}:/usr/bin:/bin"},
                )

                self.assertEqual(fresh_result.returncode, 1)
                self.assertEqual(
                    fresh_result.stdout,
                    f"Started core 2/12 Apply job: job-core-fresh-malformed-{index}" + chr(10),
                )
                self.assertEqual(
                    fresh_result.stderr,
                    "ERROR: Core Apply failed for a reason other than host capacity; retry is stopped." + chr(10),
                )
                self.assertEqual(
                    [call[4] for call in self.create_calls(fresh_calls)],
                    ["stack-core"],
                )

    def test_core_safety_parser_clean_and_serious_tokens_preserve_expected_paths(self):
        core_log, _ = _load_private_downloaded_logs()
        for token, expected_history_creates, expected_fresh_creates, expected_history_stderr in (
            ("clean", ["stack-core"], ["stack-core", "stack-small-01"], None),
            ("serious", [], ["stack-core"], "mixed_serious_error"),
        ):
            with self.subTest(token=token), tempfile.TemporaryDirectory(dir=SCRATCH) as awk_dir:
                _make_executable(
                    Path(awk_dir) / "awk",
                    _fake_awk_script(stdout=token, status=0),
                )
                history_result, history_calls = self.run_orchestrator(
                    jobs={"stack-core": [_job(f"job-core-history-{token}", logs=core_log)]},
                    extra_env={"PATH": f"{awk_dir}:/usr/bin:/bin"},
                )
                self.assertEqual(history_result.returncode, 0 if token == "clean" else 1)
                self.assertEqual(
                    [call[4] for call in self.create_calls(history_calls)],
                    expected_history_creates,
                )
                if expected_history_stderr is None:
                    self.assertEqual(history_result.stdout, "Started core 2/12 Apply job: job-new" + chr(10))
                else:
                    self.assertEqual(history_result.stdout, "")
                    self.assertEqual(
                        history_result.stderr,
                        "Latest core 2/12 Apply stopped: "
                        f"diagnostic={expected_history_stderr} state=FAILED "
                        f"job=job-core-history-{token}." + chr(10),
                    )

                fresh_result, fresh_calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": f"job-core-fresh-{token}",
                            "state": "FAILED",
                            "logs": core_log,
                        },
                        "stack-small-01": {
                            "id": f"job-small-fresh-{token}",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                    extra_env={"PATH": f"{awk_dir}:/usr/bin:/bin"},
                )
                self.assertEqual(fresh_result.returncode, 0 if token == "clean" else 1)
                self.assertEqual(
                    [call[4] for call in self.create_calls(fresh_calls)],
                    expected_fresh_creates,
                )
                expected_fresh_stdout = (
                    "Started core 2/12 Apply job: "
                    f"job-core-fresh-{token}"
                    + chr(10)
                )
                if token == "clean":
                    expected_fresh_stdout += (
                        "Started small-01 Apply job after core capacity failure: "
                        f"job-small-fresh-{token}"
                        + chr(10)
                    )
                self.assertEqual(fresh_result.stdout, expected_fresh_stdout)
                if token == "serious":
                    self.assertEqual(
                        fresh_result.stderr,
                        "ERROR: Core Apply failed for a reason other than host capacity; retry is stopped." + chr(10),
                    )

    def test_private_console_exports_route_core_then_small01(self):
        core_logs, small_log = _load_private_console_logs()
        for index, core_log in enumerate(core_logs):
            with self.subTest(core_export=index):
                result, calls = self.run_orchestrator(
                    jobs={
                        "stack-core": [_job("job-private-console-core-old", logs=core_log)],
                        "stack-small-01": [_job("job-private-console-small-old", logs=small_log)],
                    },
                    create={
                        "stack-core": {
                            "id": "job-private-console-core-new",
                            "state": "FAILED",
                            "logs": core_log,
                        },
                        "stack-small-01": {
                            "id": "job-private-console-small-new",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    ["stack-core", "stack-small-01"],
                )

    def test_known_unsolved_core_baseline_bypass_unknown_record_inside_core_payload(self):
        core_log, _ = _load_private_downloaded_logs()
        lines = core_log.splitlines()
        resource_index = next(
            index
            for index, line in enumerate(lines)
            if "+ resource " in line
        )
        marker = _bracket_row(
            "2026/10/28 04:09:38",
            "INFO",
            "Opaque unexpected INFO row",
        )
        mixed_log = "\n".join(
            lines[: resource_index + 1] + [marker] + lines[resource_index + 1 :]
        )
        result, calls = self.run_orchestrator(
            create={
                "stack-core": {
                    "id": "job-core-unknown-payload",
                    "state": "FAILED",
                    "logs": mixed_log,
                },
                "stack-small-01": {
                    "id": "job-small01-should-not-start",
                    "state": "SUCCEEDED",
                    "logs": "",
                },
            }
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
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

    def test_known_unsolved_core_baseline_bypass_structurally_unknown_core_prelude_records(self):
        positions = (
            ("start", 0),
            ("provider-and-plan-prelude", 6),
            ("immediately-before-capacity", None),
        )
        for marker in _pre_error_marker_rows("2026/10/28 04:09:38"):
            for position, line_index in positions:
                with self.subTest(marker=marker, position=position):
                    if line_index is None:
                        mixed_log = _insert_before_first_message(
                            FULL_CAPACITY_LOG,
                            "Error: 500-InternalError",
                            marker,
                        )
                    else:
                        mixed_log = _insert_at_line(FULL_CAPACITY_LOG, line_index, marker)
                    result, calls = self.run_orchestrator(
                        create={
                            "stack-core": {
                                "id": "job-core-unknown-prelude",
                                "state": "FAILED",
                                "logs": mixed_log,
                            },
                            "stack-small-01": {
                                "id": "job-small01-should-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        }
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core", "stack-small-01"],
                    )

    def test_structurally_unknown_small_console_pre_error_records_stop_before_small01_retry(self):
        positions = (
            ("start", 0),
            ("inside-first-provider-attempt", 7),
            ("immediately-before-provider-failure", None),
        )
        for marker in _pre_error_marker_rows("2026/08/07 15:26:44"):
            for position, line_index in positions:
                with self.subTest(marker=marker, position=position):
                    if line_index is None:
                        mixed_log = _insert_before_first_message(
                            SMALL01_CONSOLE_LOG,
                            "Error: Failed to install provider",
                            marker,
                        )
                    else:
                        mixed_log = _insert_at_line(SMALL01_CONSOLE_LOG, line_index, marker)
                    result, calls = self.run_orchestrator(
                        jobs={
                            "stack-core": [
                                _job("job-core-old-capacity", logs=FULL_CAPACITY_LOG)
                            ],
                            "stack-small-01": [
                                _job("job-small01-unknown-prelude", logs=mixed_log)
                            ],
                        },
                        create={
                            "stack-core": {
                                "id": "job-core-new-capacity",
                                "state": "FAILED",
                                "logs": FULL_CAPACITY_LOG,
                            },
                            "stack-small-01": {
                                "id": "job-small01-should-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        },
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-core"],
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

    def test_private_small_downloaded_checksum_url_versions_must_match(self):
        _, small_log = _load_private_downloaded_logs()
        self.assertEqual(len(small_log.splitlines()), 54)
        self.assertEqual(small_log.count(OFFICIAL_PROVIDER_CHECKSUM_URL), 4)

        variants = (
            (
                "artifact-version-mismatch",
                small_log.replace("_8.26.0_SHA256SUMS", "_9.99.0_SHA256SUMS"),
            ),
            (
                "release-version-mismatch",
                small_log.replace("/v8.26.0/", "/v9.99.0/"),
            ),
        )

        for label, modified_log in variants:
            with self.subTest(label=label):
                self.assertEqual(len(modified_log.splitlines()), 54)
                self.assertEqual(modified_log.count("Failed to install provider"), 4)
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={"stack-small-02": [_job("job-small-url-version-mismatch", logs=modified_log)]},
                    create={
                        "stack-small-02": {
                            "id": "job-small02-version-mismatch-must-not-start",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    [],
                )

    def test_synthetic_checksum_url_versions_allow_missing_or_matching_provider_version(self):
        cases = (
            ("missing-provider-version", TIMEOUT_LOG, True),
            ("matching-provider-version", _provider_timeout_log("8.26.0"), True),
            ("matching-nondefault-version", _provider_timeout_log("9.99.0", "9.99.0"), True),
            ("mismatched-provider-version", _provider_timeout_log("9.99.0"), False),
        )

        for label, log, should_start in cases:
            with self.subTest(label=label):
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={"stack-small-02": [_job("job-small-synthetic-url-version", logs=log)]},
                    create={
                        "stack-small-02": {
                            "id": "job-small02-synthetic-url-version",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                )
                if should_start:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        ["stack-small-02"],
                    )
                else:
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(calls)],
                        [],
                    )

    def test_private_small_downloaded_checksum_url_spoofs_stop_without_small02_target(self):
        _, small_log = _load_private_downloaded_logs()
        self.assertEqual(len(small_log.splitlines()), 54)
        self.assertEqual(small_log.count(OFFICIAL_PROVIDER_CHECKSUM_URL), 4)

        variants = (
            (
                "wrong-origin-query",
                "https://downloads.example.invalid/fetch?upstream="
                + OFFICIAL_PROVIDER_CHECKSUM_URL,
            ),
            ("unknown-trailing-diagnostic", OFFICIAL_PROVIDER_CHECKSUM_URL + " AccountSuspended"),
            ("checksum-artifact-suffix", OFFICIAL_PROVIDER_CHECKSUM_URL + ".unexpected"),
            (
                "spoofed-prefix",
                "https://evil.example.invalid/prefix/" + OFFICIAL_PROVIDER_CHECKSUM_URL,
            ),
            ("query", OFFICIAL_PROVIDER_CHECKSUM_URL + "?download=1"),
            (
                "port",
                OFFICIAL_PROVIDER_CHECKSUM_URL.replace(
                    "https://github.com/", "https://github.com:443/"
                ),
            ),
            (
                "credentials",
                OFFICIAL_PROVIDER_CHECKSUM_URL.replace(
                    "https://github.com/", "https://example-user:example-pass" + "@" + "github.com/"
                ),
            ),
            (
                "userinfo-host-spoof",
                OFFICIAL_PROVIDER_CHECKSUM_URL.replace(
                    "https://github.com/", "https://github.com" + "@" + "evil.example.invalid/"
                ),
            ),
            ("fragment", OFFICIAL_PROVIDER_CHECKSUM_URL + "#artifact"),
        )

        for label, replacement in variants:
            with self.subTest(label=label):
                modified_log = small_log.replace(OFFICIAL_PROVIDER_CHECKSUM_URL, replacement)
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={"stack-small-02": [_job("job-small-url-spoof", logs=modified_log)]},
                    create={
                        "stack-small-02": {
                            "id": "job-small02-must-not-start",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    [],
                )

        malformed_log = small_log.replace(
            f'"{OFFICIAL_PROVIDER_CHECKSUM_URL}":',
            f'"{OFFICIAL_PROVIDER_CHECKSUM_URL}:',
        )
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={"stack-small-02": [_job("job-small-url-malformed", logs=malformed_log)]},
            create={
                "stack-small-02": {
                    "id": "job-small02-malformed-must-not-start",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            [],
        )

    def test_private_small_downloaded_valid_checksum_forms_start_exact_small02_target(self):
        _, small_log = _load_private_downloaded_logs()
        valid_logs = (
            ("downloaded-quoted-url-colon", small_log),
            ("bare-url", TIMEOUT_LOG),
            ("cli-wrapped-quoted-url-colon", SMALL01_CLI_WRAPPED_LOG),
        )

        for label, log in valid_logs:
            with self.subTest(label=label):
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={"stack-small-02": [_job("job-small-valid-url", logs=log)]},
                    create={
                        "stack-small-02": {
                            "id": "job-small02-valid-url",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    ["stack-small-02"],
                )

    def test_small_capacity_unknown_warn_stays_closed_while_baseline_keeps_target(self):
        core_log, _ = _load_private_downloaded_logs()
        warned_log = _insert_before_first_message(
            core_log,
            "Error: 500-InternalError",
            _bracket_row(
                "2026/10/28 04:09:50",
                "WARN",
                "Harmless provider prelude notice",
            ),
        )

        candidate_result, candidate_calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={"stack-small-02": [_job("job-small-warn", logs=warned_log)]},
            create={
                "stack-small-02": {
                    "id": "job-small02-warn-must-not-start",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
        )
        self.assertNotEqual(candidate_result.returncode, 0, candidate_result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(candidate_calls)],
            [],
        )

        baseline_result, baseline_calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={"stack-small-02": [_job("job-small-warn-baseline", logs=warned_log)]},
            create={
                "stack-small-02": {
                    "id": "job-small02-warn-baseline",
                    "state": "SUCCEEDED",
                    "logs": "",
                }
            },
            script_text=_baseline_script_text(),
        )
        self.assertEqual(baseline_result.returncode, 0, baseline_result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(baseline_calls)],
            ["stack-small-02"],
        )

    def test_small_capacity_terraform_progress_keeps_exact_target_like_baseline(self):
        core_log, _ = _load_private_downloaded_logs()
        progress_log = _insert_before_first_message(
            core_log,
            "Error: 500-InternalError",
            _bracket_row(
                "2026/10/28 04:09:50",
                "INFO",
                "oci_core_instance.generated_oci_core_instance: Still creating... [10s elapsed]",
            ),
        )

        for label, script_text in (
            ("candidate", None),
            ("baseline", _baseline_script_text()),
        ):
            with self.subTest(label=label):
                result, calls = self.run_orchestrator(
                    inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
                    jobs={"stack-small-02": [_job("job-small-progress", logs=progress_log)]},
                    create={
                        "stack-small-02": {
                            "id": "job-small02-progress",
                            "state": "SUCCEEDED",
                            "logs": "",
                        }
                    },
                    script_text=script_text,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    ["stack-small-02"],
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

    def test_core_bounded_guard_stops_capacity_plus_explicit_error(self):
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

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core"],
        )

    def test_core_bounded_guard_stops_unknown_error_level_after_capacity(self):
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

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core"],
        )

    def test_core_bounded_guard_stops_explicit_error_but_small_provider_stays_closed(self):
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
                self.assertNotEqual(result.returncode, 0, result.stderr)
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

    def test_known_unsolved_core_baseline_bypass_info_unexpected_provider_failure_after_capacity(self):
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

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core", "stack-small-01"],
        )

    def test_core_unknown_info_remains_unsolved_except_explicit_account_marker(self):
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

                if unknown_diagnostic == "AccountSuspended":
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    expected_creates = ["stack-core"]
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected_creates = ["stack-core", "stack-small-01"]
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], expected_creates)

    def test_core_unkeyed_info_remains_unsolved_except_credentials_rejected(self):
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

                if unknown_diagnostic == "Credentials rejected":
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    expected_creates = ["stack-core"]
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    expected_creates = ["stack-core", "stack-small-01"]
                creates = self.create_calls(calls)
                self.assertEqual([call[4] for call in creates], expected_creates)

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

    def test_structured_json_error_capacity_record_stops(self):
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

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [call[4] for call in self.create_calls(calls)],
            ["stack-core"],
        )

    def test_multiline_structured_errors_stop_real_core_history_and_fresh_paths(self):
        core_log, _ = _load_private_downloaded_logs()
        records = list(MULTILINE_STRUCTURED_ERROR_RECORDS) + [
            (
                "bracket-info-first-line",
                _bracket_info_json_record(
                    "2026/10/28 04:09:38",
                    MULTILINE_STRUCTURED_ERROR_RECORDS[0][1],
                    wrap_each_line=False,
                ),
            ),
            (
                "bracket-info-each-line",
                _bracket_info_json_record(
                    "2026/10/28 04:09:38",
                    MULTILINE_STRUCTURED_ERROR_RECORDS[1][1],
                    wrap_each_line=True,
                ),
            ),
        ]

        for label, record in records:
            for position in ("before", "after"):
                with self.subTest(label=label, position=position):
                    mixed_log = (
                        record + "\n" + core_log
                        if position == "before"
                        else core_log + "\n" + record
                    )

                    historical_result, historical_calls = self.run_orchestrator(
                        jobs={
                            "stack-core": [
                                _job("job-core-history-multiline", logs=mixed_log)
                            ]
                        },
                        create={
                            "stack-core": {
                                "id": "job-core-history-must-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                            "stack-small-01": {
                                "id": "job-small-history-must-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        },
                    )
                    self.assertNotEqual(historical_result.returncode, 0)
                    self.assertFalse(self.create_calls(historical_calls))

                    fresh_result, fresh_calls = self.run_orchestrator(
                        create={
                            "stack-core": {
                                "id": "job-core-fresh-multiline",
                                "state": "FAILED",
                                "logs": mixed_log,
                            },
                            "stack-small-01": {
                                "id": "job-small-fresh-must-not-start",
                                "state": "SUCCEEDED",
                                "logs": "",
                            },
                        }
                    )
                    self.assertNotEqual(fresh_result.returncode, 0)
                    self.assertEqual(
                        [call[4] for call in self.create_calls(fresh_calls)],
                        ["stack-core"],
                    )

    def test_core_guard_ignores_terraform_literal_error_values_and_resource_braces(self):
        core_log, _ = _load_private_downloaded_logs()
        benign_plan_rows = "\n".join(
            [
                _bracket_row(
                    "2026/10/28 04:09:38",
                    "INFO",
                    '  + resource "oci_core_instance" "generated" {',
                ),
                _bracket_row(
                    "2026/10/28 04:09:38",
                    "INFO",
                    '      + message = "Error: InvalidParameter"',
                ),
                _bracket_row(
                    "2026/10/28 04:09:38",
                    "INFO",
                    '      + status = "ERROR"',
                ),
                _bracket_row("2026/10/28 04:09:38", "INFO", "    }"),
            ]
        )
        mixed_log = _insert_before_first_message(
            core_log,
            "Error: 500-InternalError",
            benign_plan_rows,
        )

        for label, script_text in (("candidate", None), ("baseline", _baseline_script_text())):
            with self.subTest(label=label):
                result, calls = self.run_orchestrator(
                    create={
                        "stack-core": {
                            "id": "job-core-benign-plan",
                            "state": "FAILED",
                            "logs": mixed_log,
                        },
                        "stack-small-01": {
                            "id": "job-small-benign-plan",
                            "state": "SUCCEEDED",
                            "logs": "",
                        },
                    },
                    script_text=script_text,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [call[4] for call in self.create_calls(calls)],
                    ["stack-core", "stack-small-01"],
                )

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
                    _job("job-older-unknown", logs="untrusted older diagnostic"),
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

    def test_newest_capacity_ignores_older_unknown_history(self):
        result, calls = self.run_orchestrator(
            jobs={
                "stack-core": [
                    _job("job-newest-capacity", logs="Out of host capacity"),
                    _job("job-older-timeout-1", logs=TIMEOUT_LOG),
                    _job("job-older-timeout-2", logs=TIMEOUT_LOG),
                    _job("job-older-unknown", logs="untrusted older diagnostic"),
                ],
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[4] for call in self.create_calls(calls)], ["stack-core"])
        self.assertFalse(any("job-older-unknown" in call for call in self.log_calls(calls)))

    def test_malformed_history_entry_within_first_three_stops(self):
        result, calls = self.run_orchestrator(
            inventory={"data": [_instance("hermes-a1-small-01", 1, 6)]},
            jobs={
                "stack-small-02": [
                    _job("job-timeout-1", logs=TIMEOUT_LOG),
                    _job("job-timeout-2", logs=TIMEOUT_LOG),
                    _job("job-malformed", operation="PLAN", logs=TIMEOUT_LOG),
                    _job("job-older-capacity", logs="Out of host capacity"),
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
        self.assertIn("history_invalid", result.stderr)
        self.assertFalse(self.create_calls(calls))

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
