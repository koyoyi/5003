"""Validate E2 evidence and all 387 E3 records without accessing databases."""

from __future__ import annotations

import argparse
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from database_support import DBMS, equal_results, hashlib_sql
from run_mechanism import read_csv, sha256


def validate(suite: Path) -> None:
    metadata = json.loads((suite / "suite.json").read_text())
    config = metadata["config"]
    if metadata["status"] not in ("complete", "complete-with-recorded-failures"):
        raise ValueError("Database suite is incomplete")
    for name, digest in metadata["artifact_sha256"].items():
        if sha256(suite / name) != digest:
            raise ValueError(f"Artifact checksum mismatch: {name}")
    license_annex = json.loads((suite / "sql" / "license-annex.json").read_text())
    if license_annex["license"] != "CC BY-NC-SA 4.0":
        raise ValueError("Missing upstream workload licensing terms")
    for name, digest in license_annex["files"].items():
        if sha256(suite / "sql" / name) != digest:
            raise ValueError("Upstream workload license/attribution checksum mismatch")
    figure_manifest = json.loads((suite / "figures" / "manifest.json").read_text())
    for name, digest in figure_manifest["source"].items():
        if sha256(suite / name) != digest:
            raise ValueError("Figure source checksum mismatch")
    if set(figure_manifest["figures"]) != {"native-read-rows.svg", "clickbench-hot.svg"}:
        raise ValueError("Missing expected E2/E3 figures")
    for name, digest in figure_manifest["figures"].items():
        path = suite / "figures" / name
        if sha256(path) != digest:
            raise ValueError("Figure checksum mismatch")
        ET.parse(path)
    if metadata["original_container_before"] != metadata["original_container_after"]:
        raise ValueError("Original container/volume evidence changed")
    for dbms in DBMS:
        resource = metadata["containers"][dbms]
        if resource["memory_bytes"] != 4294967296 or resource["nano_cpus"] != 2000000000:
            raise ValueError("Resource limits differ from the approved protocol")
        if not resource["image_digest"] or resource["final_state"]["Running"]:
            raise ValueError("Missing image digest or experiment container still running")
        for bindings in resource["port_bindings"].values():
            if any(binding["HostIp"] != "127.0.0.1" for binding in bindings):
                raise ValueError("Database port not restricted to localhost")
        if "timeout_probes" in metadata:
            probe = metadata["timeout_probes"][dbms]
            if (probe["configured_timeout_s"] != 1 or probe["active_queries_after"] != 0 or
                    not 0.5 <= probe["observed_elapsed_s"] <= 5):
                raise ValueError("Native timeout/termination probe failed")
    manifest = json.loads((suite / "data-manifest.json").read_text())
    if manifest["source_rows"] != 1000000 or len(manifest["columns"]) != 105:
        raise ValueError("Wrong ClickBench dataset shape")
    for dbms in DBMS:
        for table, expected in (("hits", manifest["complete_fingerprint"]),
                                ("hits_probe", manifest["probe_fingerprint"])):
            if metadata["imports"][f"{dbms}-{table}"]["fingerprint"] != expected:
                raise ValueError("Cross-system full-row input fingerprint mismatch")
    native = json.loads((suite / "native-validation" / "metadata.json").read_text())
    if {r["query_name"] for r in native["records"]} != {"narrow", "medium", "near_full", "non_key"}:
        raise ValueError("Missing native validation query")
    for record in native["records"]:
        if record["validation_status"] != "passed" or not record["row_ids_validated"]:
            raise ValueError("Native row-ID validation failed")
        if not record["query_id"] or record["read_rows"] < record["result_count"]:
            raise ValueError("Missing/invalid native query statistics")
        plan = (suite / "native-validation" / f"{record['query_name']}-explain.txt").read_text()
        if "Granules" not in plan:
            raise ValueError("EXPLAIN contains no granule evidence")
    by_name = {r["query_name"]: r for r in native["records"]}
    if not (by_name["narrow"]["read_rows"] < by_name["medium"]["read_rows"]
            < by_name["near_full"]["read_rows"] <= 1000000
            and by_name["non_key"]["read_rows"] == 1000000):
        raise ValueError("Native pruning behavior does not match the tested predicates")
    records = read_csv(suite / "records.csv")
    with (suite / "status-records.jsonl").open() as source:
        journal = [json.loads(line) for line in source]
    if len(records) != 387 or len(journal) != 387:
        raise ValueError("Expected 387 records including failures")
    expected_order = [(dbms, query_id, run_no) for dbms in DBMS
                      for query_id in range(1, 44) for run_no in range(1, 4)]
    results = {}
    for dbms in DBMS:
        checks = json.loads((suite / f"{dbms}-validation-results.json").read_text())
        if [check["query_id"] for check in checks] != list(range(1, 44)):
            raise ValueError("Incomplete pre-timing correctness checks")
        results[dbms] = {check["query_id"]: check["result"] for check in checks}
    valid_queries = set()
    for query_id in range(1, 44):
        values = [results[dbms][query_id] for dbms in DBMS]
        if all(value is not None for value in values) and all(
                equal_results(values[0], value, config["float_relative_tolerance"],
                              config["float_absolute_tolerance"]) for value in values[1:]):
            valid_queries.add(query_id)
    successes = 0
    for row, entry, expected_key in zip(records, journal, expected_order):
        key = (row["dbms"], int(row["query_id"]), int(row["run_no"]))
        if key != expected_key:
            raise ValueError("Incorrect Q1 Run1/2/3 then Q2 ordering")
        for field, value in entry.items():
            if row[field] != ("" if value is None else str(value)):
                raise ValueError(f"CSV/journal disagreement: {field}")
        if row["dataset_rows"] != "1000000" or row["dataset_sha256"] != manifest["source_sha256"]:
            raise ValueError("Dataset provenance mismatch")
        if row["cache_protocol"] != ("first_run" if key[2] == 1 else "repeat_run"):
            raise ValueError("Incorrect cache labeling")
        if row["timing_scope"] != config["timing_scope"]:
            raise ValueError("Different timing scopes across systems")
        sql = (suite / "sql" / key[0] / f"Q{key[1]:02d}-adapted.sql").read_text().strip().removesuffix(";")
        if hashlib_sql(sql) != row["query_sha256"]:
            raise ValueError("Query SHA-256 mismatch")
        raw = json.loads((suite / row["log_path"]).read_text())
        for field, value in raw["record"].items():
            if entry[field] != value:
                raise ValueError("Raw result/status disagreement")
        if row["status"] == "success":
            if key[1] not in valid_queries or row["validation_status"] != "passed":
                raise ValueError("Unvalidated query entered performance conclusions")
            elapsed = float(row["elapsed_s"])
            if not math.isfinite(elapsed) or elapsed <= 0:
                raise ValueError("Invalid successful time")
            if not equal_results(raw["result"], results["clickhouse"][key[1]],
                                 config["float_relative_tolerance"], config["float_absolute_tolerance"]):
                raise ValueError("Timed result disagrees with prevalidated reference")
            successes += 1
        elif row["elapsed_s"] != "" or row["validation_status"] == "passed":
            raise ValueError("Failed query has a success-shaped time/validation")
    if successes != metadata["successful_records"] or len(valid_queries) != metadata["validated_queries"]:
        raise ValueError("Coverage totals disagree")
    hot = read_csv(suite / "hot.csv")
    if len(hot) != 129:
        raise ValueError("Expected 129 hot summary records")
    for row in hot:
        repeats = [r for r in journal if r["dbms"] == row["dbms"]
                   and r["query_id"] == int(row["query_id"]) and r["run_no"] in (2, 3)]
        valid = len(repeats) == 2 and all(r["status"] == "success" for r in repeats)
        if (row["valid"] == "True") != valid:
            raise ValueError("Hot eligibility mismatch")
        if valid:
            if not math.isclose(float(row["hot_elapsed_s"]), min(r["elapsed_s"] for r in repeats),
                                rel_tol=1e-12):
                raise ValueError("Hot is not min(Run2,Run3)")
        elif row["hot_elapsed_s"]:
            raise ValueError("Failed repeats contributed a hot time")
    print(f"PASS E2/E3 artifact integrity: four native queries; "
          f"{len(valid_queries)}/43 cross-system queries; {successes}/387 successful states")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    validate(parser.parse_args().suite)
