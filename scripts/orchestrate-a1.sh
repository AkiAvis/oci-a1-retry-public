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
PROVIDER_TIMEOUT_MAX_CONSECUTIVE=3
CREATED_JOB_ID=""

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[[ "$CREATE_JOB_MAX_ATTEMPTS" =~ ^[1-9][0-9]*$ ]] || fail "CREATE_JOB_MAX_ATTEMPTS must be a positive integer."
[[ "$CREATE_JOB_BACKOFF_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "CREATE_JOB_BACKOFF_SECONDS must be a positive integer."

job_state() {
  local state
  state="$("$OCI_BIN" resource-manager job get --job-id "$1" --query 'data."lifecycle-state"' --raw-output 2>&1)" || return 1
  case "$state" in
    ACCEPTED|IN_PROGRESS|CANCELING|SUCCEEDED|FAILED|CANCELED)
      printf '%s\n' "$state"
      ;;
    *)
      printf '%s\n' UNKNOWN
      ;;
  esac
}

has_active_job() {
  local stack_id="$1" jobs active_count
  jobs="$("$OCI_BIN" resource-manager job list --stack-id "$stack_id" --all --output json 2>&1)" || fail "Could not list Resource Manager jobs."
  active_count="$(jq -er -s '
    if length != 1 then error("expected one JSON document")
    else
      .[0] as $document |
      if ($document | type) == "object" and
         ($document.data | type) == "array" and
         all($document.data[];
           type == "object" and
           (.["id"] | type == "string" and length > 0) and
           (.["lifecycle-state"] | type == "string" and (
             . == "ACCEPTED" or . == "IN_PROGRESS" or . == "CANCELING" or
             . == "SUCCEEDED" or . == "FAILED" or . == "CANCELED"
           ))
         )
      then [$document.data[] | select(.["lifecycle-state"] == "ACCEPTED" or .["lifecycle-state"] == "IN_PROGRESS" or .["lifecycle-state"] == "CANCELING")] | length
      else error("invalid job list")
      end
    end
  ' 2>/dev/null <<<"$jobs")" || fail "Could not validate Resource Manager job list."
  [[ "$active_count" =~ ^[0-9]+$ ]] || fail "Could not validate Resource Manager job list."
  (( active_count > 0 ))
}

ordered_job_history() {
  local stack_id="$1" jobs
  jobs="$("$OCI_BIN" resource-manager job list --stack-id "$stack_id" --limit "$PROVIDER_TIMEOUT_MAX_CONSECUTIVE" --sort-by TIMECREATED --sort-order DESC --output json 2>&1)" || return 1
  jq -e '.data | type == "array"' >/dev/null 2>/dev/null <<<"$jobs" || return 1
  printf '%s\n' "$jobs"
}

history_is_safe() {
  jq -e '.data | type == "array"' >/dev/null 2>/dev/null
}

history_entry_is_safe() {
  jq -e '
    type == "object" and
    (.id | type == "string" and length > 0) and
    (."lifecycle-state" | type == "string" and length > 0) and
    (.operation == "APPLY") and
    (."lifecycle-state" == "FAILED" or ."lifecycle-state" == "SUCCEEDED" or ."lifecycle-state" == "ACCEPTED" or ."lifecycle-state" == "IN_PROGRESS" or ."lifecycle-state" == "CANCELING" or ."lifecycle-state" == "CANCELED")
  ' >/dev/null 2>/dev/null
}

provider_timeout_mutation_present() {
  local logs="$1"
  grep -Eqi 'Terraform has been successfully initialized|terraform[[:space:]]+(plan|apply)|Terraform will perform the following actions|(^|[^[:alnum:]])Plan:[[:space:]]*[0-9]|module\.[A-Za-z0-9_.-]+|resource[[:space:]]+"|(^|[[:space:]])(Creating|Created|Modifying|Destroying|Refreshing state)(\.\.\.|[[:space:]])' <<<"$logs"
}

unsafe_failure_markers_present() {
  local logs="$1"
  grep -Eqi 'TooManyRequests|Too many requests|HTTP[[:space:]]+429|"status"[[:space:]]*:[[:space:]]*429|quota|limit exceeded|permission denied|not authorized|notauthorizedornotfound|forbidden|unauthorized|authentication failed|authentication error|(^|[^0-9])401([^0-9]|$)|(^|[^0-9])403([^0-9]|$)' <<<"$logs"
}

core_mixed_serious_error_present() {
  local logs="$1" parser_result
  if parser_result="$(awk '
    BEGIN {
      pending_level = ""
      serious = 0
      json_active = 0
      json_depth = 0
      json_in_string = 0
      json_escape = 0
      json_level_error = 0
      json_message_error = 0
      json_invalid = 0
      json_pending_key = ""
      json_bracket_prefix = "^\\[[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\\][[:space:]]+(INFO|WARN|ERROR)[[:space:]]-[[:space:]]*"
      json_timestamp_prefix = "^[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9][[:space:]]+(INFO|WARN|ERROR)[[:space:]]*"
    }

    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }

    function normalized(value) {
      value = tolower(trim(value))
      gsub(/[[:space:]]+/, " ", value)
      sub(/[.][[:space:]]*$/, "", value)
      return value
    }

    function reset_json_record() {
      json_active = 0
      json_depth = 0
      json_in_string = 0
      json_escape = 0
      json_level_error = 0
      json_message_error = 0
      json_invalid = 0
      json_pending_key = ""
    }

    function json_line_has_level_error(value, lower_value) {
      lower_value = tolower(value)
      return lower_value ~ /(^|[[:space:]{,])"level"[[:space:]]*:[[:space:]]*"error"[[:space:]]*([,}]|$)/
    }

    function json_line_has_message_error(value, lower_value) {
      lower_value = tolower(value)
      return lower_value ~ /(^|[[:space:]{,])"message"[[:space:]]*:[[:space:]]*"[[:space:]]*error:[^"]*"/
    }

    function json_value_line_is_error(key, value, lower_value) {
      lower_value = tolower(trim(value))
      if (key == "level") {
        return lower_value ~ /^"error"[[:space:]]*[,}]?[[:space:]]*$/
      }
      if (key == "message") {
        return lower_value ~ /^"[[:space:]]*error:[^"]*"[[:space:]]*[,}]?[[:space:]]*$/
      }
      return 0
    }

    function scan_json_record(value, trimmed, i, character, closed_on_line) {
      if (!json_active) {
        trimmed = value
        sub(/^[[:space:]]+/, "", trimmed)
        if (trimmed !~ /^\{/) return
        json_active = 1
        json_depth = 0
        json_in_string = 0
        json_escape = 0
        json_level_error = 0
        json_message_error = 0
        json_invalid = 0
        json_pending_key = ""
      }

      if (json_pending_key != "") {
        if (json_value_line_is_error(json_pending_key, value)) {
          if (json_pending_key == "level") json_level_error = 1
          if (json_pending_key == "message") json_message_error = 1
        }
        json_pending_key = ""
      }
      if (json_line_has_level_error(value)) json_level_error = 1
      if (json_line_has_message_error(value)) json_message_error = 1
      if (tolower(value) ~ /(^|[[:space:]{,])"level"[[:space:]]*:[[:space:]]*$/) {
        json_pending_key = "level"
      } else if (tolower(value) ~ /(^|[[:space:]{,])"message"[[:space:]]*:[[:space:]]*$/) {
        json_pending_key = "message"
      }

      closed_on_line = 0
      for (i = 1; i <= length(value); i++) {
        character = substr(value, i, 1)
        if (json_in_string) {
          if (json_escape) {
            json_escape = 0
          } else if (character == "\\") {
            json_escape = 1
          } else if (character == "\"") {
            json_in_string = 0
          }
        } else if (character == "\"") {
          json_in_string = 1
        } else if (character == "{") {
          json_depth++
          closed_on_line = 0
        } else if (character == "}") {
          json_depth--
          if (json_depth < 0) json_invalid = 1
          if (json_depth == 0) closed_on_line = 1
        } else if (closed_on_line && character !~ /[[:space:]]/) {
          json_invalid = 1
        }
      }

      if (json_depth == 0 && !json_in_string) {
        if (!json_invalid && (json_level_error || json_message_error)) serious = 1
        reset_json_record()
      }
    }

    function is_capacity_error(message) {
      message = tolower(trim(message))
      return message ~ /^error:[[:space:]]*500-internalerror,[[:space:]]*out of host capacity[.]?$/
    }

    function is_explicit_auth_info(message, value) {
      value = normalized(message)
      return value == "credentials rejected" ||
             value == "accountsuspended" ||
             value == "account suspended"
    }

    function inspect_record(level, message, lower_message) {
      message = trim(message)
      if (message == "") return
      lower_message = tolower(message)

      # Keep the exact provider capacity diagnostic usable. Any other
      # explicit Error:/ERROR record is a separate failure signal.
      if (lower_message ~ /^error:[[:space:]]*/ && !is_capacity_error(message)) {
        serious = 1
        return
      }
      if (level == "ERROR" && !is_capacity_error(message)) {
        serious = 1
        return
      }
      if (level == "INFO" && is_explicit_auth_info(message)) {
        serious = 1
      }
    }

    {
      raw = $0
      sub(/\r$/, "", raw)

      if (substr(raw, 20, 1) == "[") {
        wrapper = "[TERRAFORM_CONSOLE] ["
        if (substr(raw, 20, length(wrapper)) != wrapper) {
          serious = 1
        } else {
          level_start = 20 + length(wrapper)
          level_end = index(substr(raw, level_start), "] ")
          if (level_end == 0) serious = 1
          else {
            level = substr(raw, level_start, level_end - 1)
            if (level != "INFO" && level != "WARN" && level != "ERROR") serious = 1
            else raw = substr(raw, 1, 19) "\t" level "\t" substr(raw, level_start + level_end + 1)
          }
        }
      }
      json_line = raw
      if (json_line ~ json_bracket_prefix) {
        sub(json_bracket_prefix, "", json_line)
      } else if (json_line ~ json_timestamp_prefix) {
        sub(json_timestamp_prefix, "", json_line)
      }
      scan_json_record(json_line)

      bracket_prefix = "^\\[[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\\][[:space:]]+"
      bracket_record_prefix = bracket_prefix "(INFO|WARN|ERROR)[[:space:]]-[[:space:]]"
      if (raw ~ bracket_record_prefix) {
        parsed = raw
        sub(bracket_prefix, "", parsed)
        level = parsed
        sub(/[[:space:]]-[[:space:]].*$/, "", level)
        message = raw
        sub(bracket_record_prefix, "", message)
        inspect_record(level, message)
        pending_level = ""
        next
      }

      timestamp_prefix = "^[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9][[:space:]]+"
      if (raw ~ timestamp_prefix "(INFO|WARN|ERROR)([[:space:]]|$)") {
        parsed = raw
        sub(timestamp_prefix, "", parsed)
        level = parsed
        sub(/[[:space:]].*$/, "", level)
        message = parsed
        sub(/^[A-Z]+[[:space:]]*/, "", message)
        if (message == "") {
          pending_level = level
        } else {
          inspect_record(level, message)
          pending_level = ""
        }
        next
      }

      if (raw ~ /^ERROR[[:space:]]*-[[:space:]]*/) {
        message = raw
        sub(/^ERROR[[:space:]]*-[[:space:]]*/, "", message)
        inspect_record("ERROR", message)
        pending_level = ""
        next
      }

      if (raw ~ /^ERROR([[:space:]]|$)/) {
        message = raw
        sub(/^ERROR[[:space:]]*/, "", message)
        inspect_record("ERROR", message)
        pending_level = ""
        next
      }

      if (trim(raw) == "") next
      if (pending_level != "") {
        inspect_record(pending_level, raw)
        pending_level = ""
      } else {
        inspect_record("", raw)
      }
    }

    END {
      printf "%s", serious ? "serious" : "clean"
      exit 0
    }
  ' 2>/dev/null <<<"$logs")"; then
    case "$parser_result" in
      serious) return 0 ;;
      clean) return 1 ;;
      *) return 2 ;;
    esac
  else
    return 2
  fi
}

classify_failure_records() {
  local logs="$1" normalized_logs
  # OCI CLI 3.95.1 renders selected raw log rows as timestamp[source] [LEVEL] text.
  # Normalize only the observed source and known levels; malformed wrappers remain
  # untouched so the classifier rejects them rather than dropping metadata.
  if ! normalized_logs="$(awk '
    {
      raw = $0
      if (raw ~ /^[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\[TERRAFORM_CONSOLE\][[:space:]]+\[(INFO|WARN|ERROR)\][[:space:]]/) {
        sub(/\[TERRAFORM_CONSOLE\][[:space:]]+\[/, "\t", raw)
        sub(/\][[:space:]]+/, "\t", raw)
      }
      print raw
    }
  ' <<<"$logs" 2>/dev/null)"; then
    return 1
  fi
  logs="$normalized_logs"
  awk '
    BEGIN {
      seen_blocks = 0
      post_provider = 0
      saw_nonempty_prelude = 0
      payload_depth = 0
    }

    function trim(value) {
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      return value
    }

    function reset_block() {
      block_open = 0
      block_kind = ""
      block_has_error_level = 0
      block_invalid = 0
      block_authentication = 0
      block_checksums = 0
      block_official_url = 0
      block_deadline = 0
      block_provider_version = ""
      block_url_version = ""
    }

    function known_provider_retry_prelude(message) {
      if (message == "Getting providers from registry and/or custom terraform providers" ||
          message == "Initializing provider plugins..." ||
          message ~ /^-[[:space:]]*Finding latest version of oracle[/]oci[.][.][.]$/) {
        return 1
      }
      return 0
    }

    function known_terraform_prelude(message) {
      if (message ~ /^-[[:space:]]*(Finding latest version of|Installing|Installed|Reusing previous version|Using previously-installed)[[:space:]]+[^[:space:]]+/ ||
          message ~ /^-[[:space:]]+[A-Za-z0-9_.-]+[/][A-Za-z0-9_.-]+$/) {
        return 1
      }
      if (message ~ /^Terraform has created a lock file[[:space:]]+[^[:space:]]+[[:space:]]+to record the provider$/ ||
          message == "selections it made above. Include this file in your version control repository" ||
          message == "so that Terraform can guarantee to make the same selections by default when" ||
          message ~ /^you run "[^"]+" in the future[.]$/ ||
          message == "Warning: Incomplete lock file information for providers" ||
          message == "Due to your customized provider installation methods, Terraform was forced to" ||
          message == "calculate lock file checksums locally for the following providers:" ||
          message ~ /^The current[[:space:]]+\.terraform\.lock\.hcl file only includes checksums for[[:space:]]+[A-Za-z0-9_.-]+,$/ ||
          message == "so Terraform running on another platform will fail to install these" ||
          message == "providers." ||
          message == "To calculate additional checksums for another platform, run:" ||
          message ~ /^terraform providers lock -platform=[A-Za-z0-9_.-]+$/ ||
          message ~ /^\(where[[:space:]]+[A-Za-z0-9_.-]+[[:space:]]+is the platform to generate\)$/) {
        return 1
      }
      if (message == "Terraform used the selected providers to generate the following execution" ||
          message == "plan. Resource actions are indicated with the following symbols:" ||
          message == "+ create" ||
          message == "- destroy" ||
          message == "~ update" ||
          message == "<= read" ||
          message ~ /^You may now begin working with Terraform[.] Try running "[^"]+" to see$/ ||
          message == "any changes that are required for your infrastructure. All Terraform commands" ||
          message == "should now work." ||
          message == "If you ever set or change modules or backend configuration for Terraform," ||
          message == "rerun this command to reinitialize your working directory. If you forget, other" ||
          message == "commands will detect it and remind you to do so if necessary." ||
          message ~ /^Loading Environment variables from[[:space:]]+[A-Za-z0-9_.-]+[[:space:]]+file[.][.][.]$/) {
        return 1
      }
      if (message ~ /^[A-Za-z0-9_.-]+([.][A-Za-z0-9_.-]+)*:[[:space:]]+Refreshing state[.][.][.]([[:space:]]+\[[[:space:]]*id=[^]]+\])?$/) {
        return 1
      }
      return 0
    }

    function known_capacity_diagnostic(message) {
      if (message ~ /^Suggestion: The service for this resource encountered an error[.]([[:space:]]+Please contact support for help with service:[[:space:]]+[A-Za-z][A-Za-z0-9 ._-]*[.]?)?$/ ||
          message ~ /^(Documentation|API Reference):[[:space:]]+https:[/][/]((registry[.]terraform[.]io)|(docs[.]oracle[.]com))[/][^[:space:]]+$/ ||
          message ~ /^Request Target:[[:space:]]+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)[[:space:]]+(https:[/][/][^[:space:]]+|<redacted-url>)$/ ||
          message ~ /^Provider version:[[:space:]]+[0-9]+[.][0-9]+[.][0-9]+(,[[:space:]]+released on[[:space:]]+([0-9]{4}[-/][0-9]{2}[-/][0-9]{2}|[[:alpha:]]+[[:space:]]+[0-9]{1,2},[[:space:]]+[0-9]{4}|<redacted-date>)[.]?)?$/ ||
          message == "Service: Core Instance" ||
          message == "Operation Name: LaunchInstance" ||
          message ~ /^OPC[[:space:]]+[Rr]equest ID:[[:space:]]+[^[:space:]]+$/ ||
          message ~ /^Correlation marker:[[:space:]]+[A-Za-z0-9._-]+$/ ||
          message ~ /^with[[:space:]]+[A-Za-z0-9_.-]+,[[:space:]]*$/ ||
          message ~ /^on[[:space:]]+[A-Za-z0-9_.-]+[[:space:]]+line[[:space:]]+[0-9]+,[[:space:]]+in[[:space:]]+(resource|data)[[:space:]]+"[^"]+"[[:space:]]+"[^"]+":$/ ||
          message ~ /^(Source:[[:space:]]+)?[0-9]+:[[:space:]]+(resource|data)[[:space:]]+"[^"]+"[[:space:]]+"[^"]+"[[:space:]]*\{[[:space:]]*$/) {
        return 1
      }
      return 0
    }

    function official_provider_checksum_url_shape(message) {
      return message ~ /^https:[/][/]github[.]com[/]oracle[/]terraform-provider-oci[/]releases[/]download[/]v[0-9][A-Za-z0-9._-]*[/]terraform-provider-oci_[0-9][A-Za-z0-9._-]*_SHA256SUMS$/ ||
             message ~ /^"https:[/][/]github[.]com[/]oracle[/]terraform-provider-oci[/]releases[/]download[/]v[0-9][A-Za-z0-9._-]*[/]terraform-provider-oci_[0-9][A-Za-z0-9._-]*_SHA256SUMS":$/
    }

    function checksum_url_version(message,   url, prefix, suffix, rest, slash, release_version, artifact, artifact_prefix, artifact_version) {
      if (!official_provider_checksum_url_shape(message)) return ""
      url = message
      if (url ~ /^"/) {
        sub(/^"/, "", url)
        sub(/"[:]$/, "", url)
      }
      prefix = "https://github.com/oracle/terraform-provider-oci/releases/download/v"
      suffix = "_SHA256SUMS"
      artifact_prefix = "terraform-provider-oci_"
      rest = substr(url, length(prefix) + 1)
      slash = index(rest, "/")
      release_version = substr(rest, 1, slash - 1)
      artifact = substr(rest, slash + 1)
      artifact_version = substr(artifact, length(artifact_prefix) + 1,
                                length(artifact) - length(artifact_prefix) - length(suffix))
      if (release_version != artifact_version) return ""
      return release_version
    }

    function official_provider_checksum_url(message, expected_provider_version,   version) {
      version = checksum_url_version(message)
      return version != "" && (expected_provider_version == "" || version == expected_provider_version)
    }

    function known_provider_boundary(message) {
      return known_provider_retry_prelude(message) || message == "Error: Failed to install provider"
    }

    function known_console_wrapper(message) {
      return message == "開発者サービス" ||
             message == "スタックの詳細" ||
             message == "失敗" ||
             message == "ジョブ" ||
             message == "アクション" ||
             message == "ログ" ||
             message == "検索" ||
             message == "タイム・スタンプ" ||
             message == "レベル" ||
             message == "メッセージ" ||
             message ~ /^ormjob[0-9]+$/
    }

    function prelude_record_is_safe(level, message) {
      # This is a positive grammar for normal Terraform/provider output. It
      # deliberately does not classify arbitrary INFO text by looking for
      # more failure words.
      if (level != "" && level != "INFO" && level != "WARN") return 0
      if (message == "") return 1
      if (known_console_wrapper(message) || known_provider_retry_prelude(message) || known_terraform_prelude(message)) return 1
      if (message == "Initializing the backend..." ||
          message == "Initializing modules..." ||
          message == "Terraform has been successfully initialized!" ||
          message == "Terraform used the selected providers to generate the following execution plan." ||
          message == "Terraform will perform the following actions:" ||
          message == "Changes to Outputs:" ||
          message == "Resources:") {
        return 1
      }
      if (message ~ /^Plan:[[:space:]]*[0-9]+[[:space:]]+to add,[[:space:]]*[0-9]+[[:space:]]+to change,[[:space:]]*[0-9]+[[:space:]]+to destroy[.]$/ ||
          message ~ /^No changes[.][[:space:]]+Your infrastructure matches the configuration[.]$/) {
        return 1
      }
      if (message ~ /^[A-Za-z0-9_.-]+([.][A-Za-z0-9_.-]+)*:[[:space:]]+(Creating|Created|Modifying|Modified|Destroying|Destroyed|Refreshing state)([.][.][.]|[[:space:]])$/ ||
          message ~ /^[A-Za-z0-9_.-]+([.][A-Za-z0-9_.-]+)*:[[:space:]]+Still creating[.][.][.][[:space:]]+[[][0-9]+s[[:space:]]+elapsed[]]$/ ||
          message ~ /^Refreshing state[.][.][.]$/) {
        return 1
      }
      if (message ~ /^[[:space:]]*#[[:space:]]+[A-Za-z0-9_.-]+[[:space:]]+will be (created|updated|destroyed)$/ ||
          message ~ /^[[:space:]]*[+~-][[:space:]]+resource[[:space:]]+"[^"]+"[[:space:]]+"[^"]+"[[:space:]]*\{[[:space:]]*$/) {
        return 1
      }
      return 0
    }

    function known_terraform_attribute(message, key, count, names, i) {
      if (message !~ /^[+~-][[:space:]]+("[A-Za-z_][A-Za-z0-9_]*"|[A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=/) return 0
      key = message
      sub(/^[+~-][[:space:]]+/, "", key)
      if (key ~ /^"/) {
        sub(/^"/, "", key)
        sub(/".*$/, "", key)
      } else {
        sub(/[[:space:]]*=.*$/, "", key)
      }
      count = split("are_all_plugins_disabled are_legacy_imds_endpoints_disabled assign_ipv6ip assign_private_dns_record assign_public_ip availability_domain baseline_ocpu_utilization boot_volume_id boot_volume_size_in_gbs boot_volume_vpus_per_gb capacity_reservation_id compartment_id compute_cluster_id dedicated_vm_host_id defined_tags desired_state display_name extended_metadata fault_domain freeform_tags gpu_description gpus hostname_label id image instance_configuration_id ipxe_script is_ai_enterprise_enabled is_cross_numa_node is_live_migration_preferred is_management_disabled is_monitoring_disabled is_pv_encryption_in_transit_enabled launch_mode local_disk_description local_disks local_disks_total_size_in_gbs local_volume_size_in_gbs max_vnic_attachments memory_in_gbs metadata name networking_bandwidth_in_gbps nsg_ids nvmes ocpus platform private_ip private_ip_id processor_description public_ip recovery_action region resource_management security_attributes ssh_authorized_keys security_attributes_state shape skip_source_dest_check source_id source_type state subnet_cidr subnet_id system_tags time_created time_maintenance_reboot_due vcpus vlan_id", names, " ")
      for (i = 1; i <= count; i++) if (key == names[i]) return 1
      return 0
    }

    function terraform_payload_record_is_safe(message) {
      if (message == "}" ||
          message ~ /^[+~-][[:space:]]+[A-Za-z0-9_.-]+([[:space:]]+"[^"]+")*[[:space:]]*\{[[:space:]]*$/ ||
          (known_terraform_attribute(message) && message ~ /^[+~-][[:space:]]+("[^"]+"|[A-Za-z0-9_.-]+)[[:space:]]*=[[:space:]]*.*$/)) {
        return 1
      }
      return 0
    }

    function terraform_payload_boundary(message) {
      return message ~ /^Plan:[[:space:]]*[0-9]+[[:space:]]+to add,[[:space:]]*[0-9]+[[:space:]]+to change,[[:space:]]*[0-9]+[[:space:]]+to destroy[.]$/ ||
             message ~ /^No changes[.][[:space:]]+Your infrastructure matches the configuration[.]$/
    }

    function terraform_brace_delta(message,   i, character, in_quote, delta) {
      in_quote = 0
      delta = 0
      for (i = 1; i <= length(message); i++) {
        character = substr(message, i, 1)
        if (character == "\"") {
          in_quote = !in_quote
        } else if (!in_quote && character == "{") {
          delta++
        } else if (!in_quote && character == "}") {
          delta--
        }
      }
      return delta
    }

    function starts_terraform_payload(message) {
      return message ~ /^[[:space:]]*[+~-][[:space:]]+resource[[:space:]]+"[^"]+"[[:space:]]+"[^"]+"[[:space:]]*\{[[:space:]]*$/
    }

    function finalize_block() {
      if (!block_open) return

      if (block_kind == "provider") {
        if (block_provider_version != "" && block_url_version != "" && block_provider_version != block_url_version) {
          block_invalid = 1
        }
        if (block_authentication && block_checksums && block_official_url && block_deadline && !block_invalid) {
          timeout_blocks++
          seen_blocks++
          post_provider = 1
        } else {
          invalid = 1
          seen_blocks++
          post_provider = 0
        }
      } else if (block_kind == "capacity") {
        if (!block_invalid) {
          capacity_blocks++
          seen_blocks++
          post_provider = 0
        } else {
          invalid = 1
          seen_blocks++
          post_provider = 0
        }
      } else {
        invalid = 1
        seen_blocks++
        post_provider = 0
      }
      reset_block()
    }

    function consume_message(message,   provider_version, url_version) {
      if (message == "") return

      if (block_kind == "") {
        if (message == "Error: Failed to install provider") {
          block_kind = "provider"
        } else if (message ~ /^Error:[[:space:]]*500-InternalError,[[:space:]]*Out of host capacity[.]?$/) {
          block_kind = "capacity"
        } else {
          block_invalid = 1
        }
        return
      }

      if (block_kind == "provider") {
        if (message ~ /^for registry[.]terraform[.]io[/]oracle[/]oci:[[:space:]]*failed to retrieve authentication([[:space:]]|$)/ || message ~ /^failed to retrieve authentication([[:space:]]|$)/) {
          if (block_authentication) block_invalid = 1
          block_authentication = 1
          if (message ~ /checksums for provider:/) block_checksums = 1
          return
        }
        if (message ~ /^checksums for provider:/) {
          if (!block_authentication || block_checksums) block_invalid = 1
          block_checksums = 1
          return
        }
        if (official_provider_checksum_url_shape(message)) {
          if (!official_provider_checksum_url(message, block_provider_version)) {
            block_invalid = 1
            return
          }
          if (!block_checksums || block_official_url) block_invalid = 1
          url_version = checksum_url_version(message)
          if (block_provider_version != "" && block_provider_version != url_version) block_invalid = 1
          block_official_url = 1
          block_url_version = url_version
          return
        }
        if (message ~ /https:[/][/][^[:space:]]*SHA256SUMS/) {
          block_invalid = 1
          return
        }
        if (message == "context deadline exceeded (Client.Timeout exceeded while awaiting headers)") {
          if (!block_official_url || block_deadline) block_invalid = 1
          block_deadline = 1
          return
        }
        if (message ~ /^Error while installing oracle[/]oci v[0-9A-Za-z._-]+: could not query provider registry$/) {
          provider_version = message
          sub(/^Error while installing oracle[/]oci v/, "", provider_version)
          sub(/: could not query provider registry$/, "", provider_version)
          if (block_provider_version != "" && block_provider_version != provider_version) block_invalid = 1
          if (block_url_version != "" && block_url_version != provider_version) block_invalid = 1
          block_provider_version = provider_version
          return
        }
        if (message == "could not query provider registry" || message == "later: Get") {
          return
        }
        if (message ~ /Out of host capacity/) {
          block_invalid = 1
          return
        }
        if (message ~ /^Error[[:space:]]/) {
          block_invalid = 1
          return
        }
        block_invalid = 1
        return
      }

      if (block_kind == "capacity") {
        if (message ~ /^Error[[:space:]]/ || message ~ /Out of host capacity/) {
          block_invalid = 1
        } else if (!known_capacity_diagnostic(message)) {
          block_invalid = 1
        }
        return
      }

      block_invalid = 1
    }

    function start_error_block(level, message) {
      if (block_open) {
        if (!(block_kind == "" && block_has_error_level && message ~ /^Error:/)) {
          finalize_block()
        }
      }
      if (!block_open) {
        block_open = 1
        block_kind = ""
        block_has_error_level = 0
        block_invalid = 0
        block_authentication = 0
        block_checksums = 0
        block_official_url = 0
        block_deadline = 0
        block_provider_version = ""
        block_url_version = ""
      }
      if (level == "ERROR") block_has_error_level = 1
      consume_message(message)
    }

    {
      raw = $0
      gsub(/\r/, "", raw)
      level = ""
      message = raw
      bracket_timestamp_prefix = "^\\[[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9]\\][[:space:]]+"
      bracket_record_prefix = bracket_timestamp_prefix "(INFO|WARN|ERROR)[[:space:]]-[[:space:]]"
      timestamp_prefix = "^[0-9][0-9][0-9][0-9][/][0-9][0-9][/][0-9][0-9][[:space:]]+[0-9][0-9]:[0-9][0-9]:[0-9][0-9][[:space:]]+"
      if (raw ~ bracket_record_prefix) {
        level = raw
        sub(bracket_timestamp_prefix, "", level)
        sub(/[[:space:]]-[[:space:]].*$/, "", level)
        message = raw
        sub(bracket_record_prefix, "", message)
      } else if (raw ~ timestamp_prefix "[A-Z]+([[:space:]]|$)") {
        level = raw
        sub(timestamp_prefix, "", level)
        sub(/[[:space:]].*$/, "", level)
        message = raw
        sub(timestamp_prefix "[A-Z]+[[:space:]]*", "", message)
      }
      message = trim(message)

      # Downloaded core rows can all carry a timestamped INFO wrapper. Keep
      # the Terraform payload state across those wrappers and validate each
      # continuation as a resource/attribute/closing-brace record.

      # get-job-logs-content is consumed as raw text here. Structured records
      # and unclassified error text are ambiguous, so fail closed instead of
      # guessing a JSON or alternate envelope.
      if (message ~ /^\{/ || message ~ /^\[/ ||
          message ~ /^ERROR([[:space:]]|$)/) {
        invalid = 1
        next
      }

      if (block_open && block_kind == "provider" && block_deadline && message != "") {
        if (!known_provider_boundary(message)) invalid = 1
        finalize_block()
      }

      if (level == "ERROR" || tolower(message) ~ /^error:/ || (tolower(message) ~ /^error[[:space:]]/ && !block_open)) {
        start_error_block(level, message)
        next
      }

      if (block_open) {
        consume_message(message)
        next
      }

      if (message == "") next
      if (bare_capacity > 0) {
        invalid = 1
        next
      }
      if (level == "" && message ~ /^Out of host capacity[.]?$/) {
        if (saw_nonempty_prelude) invalid = 1
        bare_capacity++
        next
      }
      if (seen_blocks == 0) {
        if (payload_depth > 0 && !terraform_payload_boundary(message)) {
          if (!terraform_payload_record_is_safe(message)) {
            invalid = 1
            next
          }
          payload_depth += terraform_brace_delta(message)
          if (payload_depth < 0) invalid = 1
          saw_nonempty_prelude = 1
          next
        }
        if (payload_depth > 0) payload_depth = 0
        if (!prelude_record_is_safe(level, message)) invalid = 1
        if (starts_terraform_payload(message)) {
          payload_depth = terraform_brace_delta(message)
        }
        saw_nonempty_prelude = 1
        next
      }
      if (post_provider && known_provider_retry_prelude(message)) next
      # After a recognized block, non-empty unclassified text is ambiguous
      # with a second failure and therefore fails closed.
      invalid = 1
    }

    END {
      finalize_block()
      if (payload_depth > 0) invalid = 1
      if (bare_capacity > 1) invalid = 1
      if (bare_capacity > 0) capacity_blocks++
      if (capacity_blocks > 1) invalid = 1
      if (timeout_blocks == 0 && capacity_blocks == 0) invalid = 1
      if (timeout_blocks > 0 && capacity_blocks > 0) invalid = 1
      if (invalid) exit 1
      if (timeout_blocks > 0) {
        print "provider_checksum_download_timeout"
      } else {
        print "capacity"
      }
    }
  ' <<<"$logs"
}

failure_classification() {
  local logs="$1" allow_provider_timeout="${2:-0}"
  local classification

  classification="$(classify_failure_records "$logs")" || return 1

  if [[ "$classification" == provider_checksum_download_timeout ]]; then
    [[ "$allow_provider_timeout" == 1 ]] || return 1
    provider_timeout_mutation_present "$logs" && return 1
    unsafe_failure_markers_present "$logs" && return 1
    grep -Fqi 'Out of host capacity' <<<"$logs" && return 1
    printf '%s\n' provider_checksum_download_timeout
    return 0
  fi

  if [[ "$classification" == capacity ]]; then
    unsafe_failure_markers_present "$logs" && return 1
    grep -Fqi 'Failed to install provider' <<<"$logs" && return 1
    grep -Eqi 'context deadline exceeded|timed out|timeout' <<<"$logs" && return 1
    printf '%s\n' capacity
    return 0
  fi

  return 1
}

mask_job_id() {
  if [[ "${GITHUB_ACTIONS:-}" == "true" && -n "$1" ]]; then
    printf '::add-mask::%s\n' "$1"
  fi
}

safe_job_id() {
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    printf '%s' 'redacted-job-id'
  elif [[ "$1" =~ ^(job|test)-[A-Za-z0-9._-]+$ ]]; then
    printf '%s\n' "$1"
  else
    printf '%s\n' redacted-job-id
  fi
}

stop_for_failure() {
  local label="$1" state="$2" job_id="$3" classification="$4"
  echo "Latest $label Apply stopped: diagnostic=$classification state=$state job=$(safe_job_id "$job_id")." >&2
  exit 1
}

provider_timeout_history_allows() {
  local label="$1" history="$2" expected_state="$3" latest="$4"
  local count=0 index=0 total entry id listed_state logs classification first=1 allow_provider_timeout=0

  case "$label" in
    small-01|small-02) allow_provider_timeout=1 ;;
  esac

  total="$(jq -r '.data | length' <<<"$history")" || stop_for_failure "$label" "$expected_state" "$latest" history_malformed
  while (( index < total )); do
    entry="$(jq -c --argjson index "$index" '.data[$index]' <<<"$history")" || stop_for_failure "$label" "$expected_state" "$latest" history_malformed
    [[ "$entry" != "null" ]] || stop_for_failure "$label" "$expected_state" "$latest" history_malformed
    history_entry_is_safe <<<"$entry" || stop_for_failure "$label" "$expected_state" "$latest" history_invalid

    id="$(jq -r '.id' <<<"$entry")" || stop_for_failure "$label" "$expected_state" "$latest" history_malformed
    mask_job_id "$id"
    listed_state="$(jq -r '."lifecycle-state"' <<<"$entry")" || stop_for_failure "$label" "$expected_state" "$latest" history_malformed

    if (( first == 1 )); then
      first=0
      [[ "$listed_state" == "$expected_state" ]] || stop_for_failure "$label" "$expected_state" "$latest" history_state_mismatch
    fi

    case "$listed_state" in
      FAILED)
        logs="$("$OCI_BIN" resource-manager job get-job-logs-content --job-id "$id" --output json --query data --raw-output 2>&1)" || stop_for_failure "$label" "$listed_state" "$id" log_unavailable
        classification="$(failure_classification "$logs" "$allow_provider_timeout")" || stop_for_failure "$label" "$listed_state" "$id" unknown
        echo "Observed $label Apply history: diagnostic=$classification state=$listed_state job=$(safe_job_id "$id")." >&2
        case "$classification" in
          provider_checksum_download_timeout)
            count=$((count + 1))
            if (( count >= PROVIDER_TIMEOUT_MAX_CONSECUTIVE )); then
              stop_for_failure "$label" "$listed_state" "$id" provider_timeout_budget_exhausted
            fi
            ;;
          capacity)
            return 0
            ;;
          *)
            stop_for_failure "$label" "$listed_state" "$id" unknown
            ;;
        esac
        ;;
      ACCEPTED|IN_PROGRESS|CANCELING|CANCELED)
        stop_for_failure "$label" "$listed_state" "$id" active_or_canceled_history
        ;;
      SUCCEEDED)
        stop_for_failure "$label" "$listed_state" "$id" succeeded_history
        ;;
      *)
        stop_for_failure "$label" "$listed_state" "$id" unknown_history_state
        ;;
    esac
    index=$((index + 1))
  done

  return 0
}

# Core requires the complete bounded capacity-log grammar. Unknown records in
# prelude, diagnostics, or Terraform payload stop rather than being treated as
# harmless based on a capacity marker alone.
core_capacity_history_allows() {
  local label="$1" expected_state="$2" latest="$3" logs parser_rc classification

  logs="$("$OCI_BIN" resource-manager job get-job-logs-content --job-id "$latest" --output json --query data --raw-output 2>&1)" || stop_for_failure "$label" "$expected_state" "$latest" log_unavailable
  grep -Fqi 'Out of host capacity' <<<"$logs" || stop_for_failure "$label" "$expected_state" "$latest" unknown
  if core_mixed_serious_error_present "$logs"; then
    stop_for_failure "$label" "$expected_state" "$latest" mixed_serious_error
  else
    parser_rc=$?
  fi
  case "$parser_rc" in
    1) ;;
    *) stop_for_failure "$label" "$expected_state" "$latest" core_safety_parser_failed ;;
  esac
  classification="$(classify_failure_records "$logs")" || stop_for_failure "$label" "$expected_state" "$latest" unknown
  [[ "$classification" == capacity ]] || stop_for_failure "$label" "$expected_state" "$latest" unknown
  echo "Observed $label Apply history: diagnostic=capacity state=$expected_state job=$(safe_job_id "$latest")." >&2
  return 0
}

ensure_retry_allowed() {
  local stack_id="$1" label="$2" history latest state history_state latest_entry history_count
  if ! history="$(ordered_job_history "$stack_id")"; then
    stop_for_failure "$label" UNKNOWN "" history_unavailable
  fi
  if ! history_is_safe <<<"$history"; then
    stop_for_failure "$label" UNKNOWN "" history_invalid
  fi

  history_count="$(jq -r '.data | length' <<<"$history")" || stop_for_failure "$label" UNKNOWN "" history_malformed
  [[ "$history_count" == 0 ]] && return 0
  latest_entry="$(jq -c '.data[0]' <<<"$history")" || stop_for_failure "$label" UNKNOWN "" history_malformed
  [[ "$latest_entry" != "null" ]] || stop_for_failure "$label" UNKNOWN "" history_invalid
  history_entry_is_safe <<<"$latest_entry" || stop_for_failure "$label" UNKNOWN "" history_invalid

  latest="$(jq -r '.id' <<<"$latest_entry")" || stop_for_failure "$label" UNKNOWN "" history_malformed
  mask_job_id "$latest"
  state="$(job_state "$latest")" || stop_for_failure "$label" UNKNOWN "$latest" state_unavailable
  history_state="$(jq -r '."lifecycle-state"' <<<"$latest_entry")" || stop_for_failure "$label" "$state" "$latest" history_malformed
  [[ "$state" == "$history_state" ]] || stop_for_failure "$label" "$state" "$latest" history_state_mismatch

  case "$state" in
    FAILED)
      if [[ "$label" == "core 2/12" ]]; then
        core_capacity_history_allows "$label" "$state" "$latest"
      else
        provider_timeout_history_allows "$label" "$history" "$state" "$latest"
      fi
      ;;
    SUCCEEDED)
      stop_for_failure "$label" "$state" "$latest" succeeded
      ;;
    ACCEPTED|IN_PROGRESS|CANCELING)
      stop_for_failure "$label" "$state" "$latest" active
      ;;
    CANCELED)
      stop_for_failure "$label" "$state" "$latest" canceled
      ;;
    *)
      stop_for_failure "$label" "$state" "$latest" unknown
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
      mask_job_id "$job_id"
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

    echo "Apply job create failed with an unrecognized error; no Apply was started." >&2
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
  local job_id="$1" logs parser_rc classification
  logs="$("$OCI_BIN" resource-manager job get-job-logs-content --job-id "$job_id" --output json --query data --raw-output 2>&1)" || fail "Could not read failed-job logs."
  grep -Fqi 'Out of host capacity' <<<"$logs" || return 1
  if core_mixed_serious_error_present "$logs"; then
    return 1
  else
    parser_rc=$?
  fi
  case "$parser_rc" in
    1) ;;
    *) return 1 ;;
  esac
  classification="$(classify_failure_records "$logs")" || return 1
  [[ "$classification" == capacity ]] || return 1
  return 0
}

for stack_id in "$OCI_CORE_STACK_ID" "$OCI_SMALL_01_STACK_ID" "$OCI_SMALL_02_STACK_ID"; do
  if has_active_job "$stack_id"; then
    echo "An A1 stack already has an active Resource Manager job; no Apply started."
    exit 0
  fi
done

instances="$("$PYTHON_BIN" "$INSTANCE_LIST_SCRIPT" "$OCI_A1_COMPARTMENT_ID")" || fail "Could not list Compute instances; refusing to treat this as zero instances."
jq -e '.data | type == "array"' >/dev/null 2>/dev/null <<<"$instances" || fail "Compute instance list did not return an array."

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
' 2>/dev/null <<<"$instances")" || fail "Could not classify A1 instances."

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
  ensure_retry_allowed "$OCI_SMALL_02_STACK_ID" "small-02"
  create_apply_job_or_defer "$OCI_SMALL_02_STACK_ID" "small-02"
  job_id="$CREATED_JOB_ID"
  echo "Started small-02 Apply job: $(safe_job_id "$job_id")"
  exit 0
fi

if [[ "$small01_count" == 0 && "$small02_count" == 1 ]]; then
  fail "small-02 exists without small-01; refusing to infer a replacement target."
fi

ensure_retry_allowed "$OCI_CORE_STACK_ID" "core 2/12"
create_apply_job_or_defer "$OCI_CORE_STACK_ID" "core 2/12"
core_job_id="$CREATED_JOB_ID"
echo "Started core 2/12 Apply job: $(safe_job_id "$core_job_id")"
core_result="$(wait_for_terminal "$core_job_id")"
[[ "$core_result" == SUCCEEDED ]] && exit 0
[[ "$core_result" == FAILED ]] || fail "Core Apply did not succeed: $core_result"
failure_is_capacity "$core_job_id" || fail "Core Apply failed for a reason other than host capacity; retry is stopped."

ensure_retry_allowed "$OCI_SMALL_01_STACK_ID" "small-01"
create_apply_job_or_defer "$OCI_SMALL_01_STACK_ID" "small-01"
small_job_id="$CREATED_JOB_ID"
echo "Started small-01 Apply job after core capacity failure: $(safe_job_id "$small_job_id")"
