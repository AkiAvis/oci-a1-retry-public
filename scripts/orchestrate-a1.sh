#!/usr/bin/env bash
set -euo pipefail

: "${OCI_CORE_STACK_ID:?OCI_CORE_STACK_ID is required}"
: "${OCI_SMALL_01_STACK_ID:?OCI_SMALL_01_STACK_ID is required}"
: "${OCI_SMALL_02_STACK_ID:?OCI_SMALL_02_STACK_ID is required}"
: "${OCI_A1_COMPARTMENT_ID:?OCI_A1_COMPARTMENT_ID is required}"

OCI_BIN="${OCI_BIN:-oci}"
PYTHON_BIN="${PYTHON_BIN:-python}"
INSTANCE_LIST_SCRIPT="${INSTANCE_LIST_SCRIPT:-scripts/list-a1-instances.py}"
POLL_SECONDS="${POLL_SECONDS:-30}"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-100}"
CREATE_JOB_MAX_ATTEMPTS="${CREATE_JOB_MAX_ATTEMPTS:-4}"
CREATE_JOB_BACKOFF_SECONDS="${CREATE_JOB_BACKOFF_SECONDS:-30}"
CORE_NAME="${CORE_NAME:-hermes-core-01}"
SMALL_01_NAME="${SMALL_01_NAME:-hermes-a1-small-01}"
SMALL_02_NAME="${SMALL_02_NAME:-hermes-a1-small-02}"
CREATED_JOB_ID=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "$CREATE_JOB_MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "CREATE_JOB_MAX_ATTEMPTS must be a positive integer."
[[ "$CREATE_JOB_BACKOFF_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "CREATE_JOB_BACKOFF_SECONDS must be a positive integer."

job_state() {
  "$OCI_BIN" resource-manager job get --job-id "$1" --query 'data."lifecycle-state"' --raw-output
}

has_active_job() {
  local stack_id="$1" jobs
  jobs="$("$OCI_BIN" resource-manager job list --stack-id "$stack_id" --all --output json)" || fail "Could not list Resource Manager jobs."
  jq -e '.data[]? | select(."lifecycle-state" == "ACCEPTED" or ."lifecycle-state" == "IN_PROGRESS" or ."lifecycle-state" == "CANCELING")' >/dev/null <<<"$jobs"
}

latest_job_id() {
  local stack_id="$1" jobs
  jobs="$("$OCI_BIN" resource-manager job list --stack-id "$stack_id" --sort-by TIMECREATED --sort-order DESC --limit 1 --output json)" || fail "Could not read the latest Resource Manager job."
  jq -r '.data[0].id // empty' <<<"$jobs"
}

ensure_retry_allowed() {
  local stack_id="$1" latest state logs
  latest="$(latest_job_id "$stack_id")"
  [[ -z "$latest" ]] && return 0
  state="$(job_state "$latest")" || fail "Could not read the latest Resource Manager job state."
  case "$state" in
    FAILED)
      logs="$("$OCI_BIN" resource-manager job get-job-logs-content --job-id "$latest" 2>&1)" || fail "Could not read failed-job logs."
      grep -qi 'Out of host capacity' <<<"$logs" || fail "Latest job failed for a reason other than host capacity; retry is stopped."
      ;;
    SUCCEEDED)
      fail "Latest job succeeded but the expected instance is absent; refusing another Apply."
      ;;
    ACCEPTED|IN_PROGRESS|CANCELING)
      fail "A Resource Manager job is active; refusing another Apply."
      ;;
    *)
      fail "Unexpected latest job state: $state"
      ;;
  esac
}

create_apply_job() {
  local stack_id="$1" response job_id attempt delay
  CREATED_JOB_ID=""
  delay="$CREATE_JOB_BACKOFF_SECONDS"

  for ((attempt = 1; attempt <= CREATE_JOB_MAX_ATTEMPTS; attempt++)); do
    if response="$("$OCI_BIN" resource-manager job create-apply-job --stack-id "$stack_id" --execution-plan-strategy AUTO_APPROVED --output json 2>&1)"; then
      job_id="$(jq -r '.data.id // empty' <<<"$response")" || {
        echo "Could not parse Apply job response." >&2
        return 1
      }
      if [[ -z "$job_id" ]]; then
        echo "Apply job response did not include an ID." >&2
        return 1
      fi
      CREATED_JOB_ID="$job_id"
      return 0
    fi

    if grep -Eqi 'TooManyRequests|Too many requests|"status"[[:space:]]*:[[:space:]]*429' <<<"$response"; then
      if (( attempt == CREATE_JOB_MAX_ATTEMPTS )); then
        echo "OCI Resource Manager is still rate limiting Apply creation after $attempt attempts." >&2
        return 75
      fi

      echo "OCI Resource Manager rate limited Apply creation (attempt $attempt/$CREATE_JOB_MAX_ATTEMPTS). Waiting ${delay}s before retry." >&2
      sleep "$delay"
      delay=$((delay * 2))
      continue
    fi

    echo "$response" >&2
    return 1
  done

  return 1
}

create_apply_job_or_defer() {
  local stack_id="$1" label="$2" rc

  if create_apply_job "$stack_id"; then
    return 0
  else
    rc=$?
  fi

  if [[ "$rc" -eq 75 ]]; then
    echo "OCI Resource Manager rate limit persisted while starting $label; no Apply was started. The next Cron run will retry."
    exit 0
  fi

  fail "Could not create Apply job for $label."
}

wait_for_terminal() {
  local job_id="$1" attempt state
  for ((attempt = 1; attempt <= POLL_ATTEMPTS; attempt++)); do
    state="$(job_state "$job_id")" || fail "Could not read Apply job state while waiting."
    case "$state" in
      SUCCEEDED|FAILED|CANCELED)
        printf '%s\n' "$state"
        return 0
        ;;
      ACCEPTED|IN_PROGRESS|CANCELING)
        sleep "$POLL_SECONDS"
        ;;
      *)
        fail "Unexpected Apply job state while waiting: $state"
        ;;
    esac
  done
  fail "Apply job did not finish before the workflow timeout; the next run will observe the active job."
}

failure_is_capacity() {
  local job_id="$1" logs
  logs="$("$OCI_BIN" resource-manager job get-job-logs-content --job-id "$job_id" 2>&1)" || fail "Could not read failed-job logs."
  grep -qi 'Out of host capacity' <<<"$logs"
}

for stack_id in "$OCI_CORE_STACK_ID" "$OCI_SMALL_01_STACK_ID" "$OCI_SMALL_02_STACK_ID"; do
  if has_active_job "$stack_id"; then
    echo "An A1 stack already has an active Resource Manager job; no Apply started."
    exit 0
  fi
done

instances="$("$PYTHON_BIN" "$INSTANCE_LIST_SCRIPT" "$OCI_A1_COMPARTMENT_ID")" || fail "Could not list Compute instances; refusing to treat this as zero instances."
jq -e '.data | type == "array"' >/dev/null <<<"$instances" || fail "Compute instance list did not return an array."

classification="$(jq -c \
  --arg core "$CORE_NAME" \
  --arg small01 "$SMALL_01_NAME" \
  --arg small02 "$SMALL_02_NAME" '
  def active: .["lifecycle-state"] != "TERMINATED";
  def a1: .shape == "VM.Standard.A1.Flex" and active;
  def cfg: .["shape-config"];
  [ .data[] | select(a1) ] as $a1 |
  {
    core:    [ $a1[] | select(.["display-name"] == $core and (cfg.ocpus | tonumber) == 2 and (cfg."memory-in-gbs" | tonumber) == 12) ],
    small01: [ $a1[] | select(.["display-name"] == $small01 and (cfg.ocpus | tonumber) == 1 and (cfg."memory-in-gbs" | tonumber) == 6) ],
    small02: [ $a1[] | select(.["display-name"] == $small02 and (cfg.ocpus | tonumber) == 1 and (cfg."memory-in-gbs" | tonumber) == 6) ],
    total: $a1
  } |
  . + {expected: (.core + .small01 + .small02 | length)}
' <<<"$instances")" || fail "Could not classify A1 instances."

total="$(jq -r '.total | length' <<<"$classification")"
expected="$(jq -r '.expected' <<<"$classification")"
[[ "$total" == "$expected" ]] || fail "Unexpected active A1 instance detected; refusing Apply to avoid paid usage."

core_count="$(jq -r '.core | length' <<<"$classification")"
small01_count="$(jq -r '.small01 | length' <<<"$classification")"
small02_count="$(jq -r '.small02 | length' <<<"$classification")"
[[ "$core_count" -le 1 && "$small01_count" -le 1 && "$small02_count" -le 1 ]] || fail "Duplicate target A1 instances detected; refusing Apply."

if [[ "$core_count" == 1 || $((small01_count + small02_count)) -eq 2 ]]; then
  echo "Always Free A1 target is already satisfied; no Apply started."
  exit 0
fi

if [[ "$small01_count" == 1 && "$small02_count" == 0 ]]; then
  ensure_retry_allowed "$OCI_SMALL_02_STACK_ID"
  create_apply_job_or_defer "$OCI_SMALL_02_STACK_ID" "small-02"
  job_id="$CREATED_JOB_ID"
  echo "Started small-02 Apply job: $job_id"
  exit 0
fi

if [[ "$small01_count" == 0 && "$small02_count" == 1 ]]; then
  fail "small-02 exists without small-01; refusing to infer a replacement target."
fi

ensure_retry_allowed "$OCI_CORE_STACK_ID"
create_apply_job_or_defer "$OCI_CORE_STACK_ID" "core 2/12"
core_job_id="$CREATED_JOB_ID"
echo "Started core 2/12 Apply job: $core_job_id"
core_result="$(wait_for_terminal "$core_job_id")"
[[ "$core_result" == SUCCEEDED ]] && exit 0
[[ "$core_result" == FAILED ]] || fail "Core Apply did not succeed: $core_result"
failure_is_capacity "$core_job_id" || fail "Core Apply failed for a reason other than host capacity; retry is stopped."

ensure_retry_allowed "$OCI_SMALL_01_STACK_ID"
create_apply_job_or_defer "$OCI_SMALL_01_STACK_ID" "small-01"
small_job_id="$CREATED_JOB_ID"
echo "Started small-01 Apply job after core capacity failure: $small_job_id"
