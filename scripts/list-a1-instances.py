#!/usr/bin/env python3
"""Return the active A1 Flex instances in one compartment as stable JSON."""

import json
import sys

import oci


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: list-a1-instances.py <compartment-ocid>", file=sys.stderr)
        return 2

    try:
        config = oci.config.from_file()
        client = oci.core.ComputeClient(config)
        response = oci.pagination.list_call_get_all_results(
            client.list_instances,
            sys.argv[1],
        )
    except Exception:
        print("Could not list Compute instances.", file=sys.stderr)
        return 1

    instances = []
    for instance in response.data:
        if instance.shape != "VM.Standard.A1.Flex" or instance.lifecycle_state == "TERMINATED":
            continue
        instances.append(
            {
                "display-name": instance.display_name,
                "lifecycle-state": instance.lifecycle_state,
                "shape": instance.shape,
                "shape-config": {
                    "ocpus": instance.shape_config.ocpus,
                    "memory-in-gbs": instance.shape_config.memory_in_gbs,
                },
            }
        )

    print(json.dumps({"data": instances}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
