"""Build and check the reorganized project offline, without touching databases."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", default="native-release")
    parser.add_argument("--database-checks", action="store_true",
                        help="Also run database-driver regression tests; needs optional dependencies")
    args = parser.parse_args()
    presets = json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))
    configured = next((p for p in presets["configurePresets"] if p["name"] == args.preset), None)
    if configured is None:
        parser.error(f"Unknown configure preset: {args.preset}")
    # Only project-owned build directories may be selected by this helper.
    build = Path(configured["binaryDir"].replace("${sourceDir}", str(ROOT))).resolve()
    if not build.is_relative_to(ROOT / "build"):
        parser.error("The selected preset must build below this project's build directory")
    report = ROOT / "build" / "project-check" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report.mkdir(parents=True, exist_ok=False)
    summary: dict = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                     "preset": args.preset, "platform": sys.platform, "checks": [], "skipped": []}
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")

    def run(name: str, command: list[str]) -> None:
        print(f"Checking {name}...", flush=True)
        completed = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                   encoding="utf-8", errors="replace", capture_output=True,
                                   timeout=300, check=False)
        log = report / f"{name}.log"
        log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        summary["checks"].append({"name": name, "command": command,
                                  "exit_code": completed.returncode, "log": log.name})
        if completed.returncode:
            raise RuntimeError(f"{name} failed; see {log}")

    try:
        imported = json.loads((ROOT / "reproduction-info" / "project-import.json").read_text(encoding="utf-8"))
        evidence = [item for item in imported["files"] if item["destination"].startswith("results/")]
        for item in evidence:
            artifact = ROOT / item["destination"]
            if hashlib.sha256(artifact.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError(f"Imported historical result changed: {item['destination']}")
        summary["historical_files_sha256_verified"] = len(evidence)
        run("configure", ["cmake", "--preset", args.preset])
        run("build", ["cmake", "--build", "--preset", args.preset, "--parallel", "2"])
        run("ctest", ["ctest", "--preset", args.preset, "--output-on-failure"])
        for test in ("test_mechanism.py", "test_databases.py"):
            run(test.removesuffix(".py"), [sys.executable, "-B", "-m", "unittest", "discover",
                                          "-s", "scripts", "-p", test, "-v"])
        executable = build / "mini-index" / ("mini_index.exe" if os.name == "nt" else "mini_index")
        run("cli", [sys.executable, "-B", "scripts/test_cli.py", str(executable)])
        for suite in sorted((ROOT / "results" / "mechanism").iterdir()):
            if suite.is_dir() and (suite / "suite.json").is_file():
                metadata = json.loads((suite / "suite.json").read_text(encoding="utf-8"))
                if "artifact_sha256" not in metadata:
                    summary["skipped"].append(f"Historical E1 suite {suite.name}: old artifact format without "
                                              "artifact_sha256; retained and covered by import SHA-256 checks")
                    continue
                run(f"artifact-{suite.name}", [sys.executable, "-B", "scripts/validate_results.py", str(suite)])
        for suite in sorted((ROOT / "results" / "databases").iterdir()):
            if suite.is_dir() and (suite / "suite.json").is_file():
                run(f"artifact-{suite.name}", [sys.executable, "-B", "scripts/validate_databases.py", str(suite)])
        if args.database_checks:
            missing = [name for name in ("clickhouse_connect", "psycopg", "pymysql", "pyarrow")
                       if importlib.util.find_spec(name) is None]
            if missing:
                raise RuntimeError("Optional database checks need requirements-databases.txt; missing: "
                                   + ", ".join(missing))
            run("test_database_runner", [sys.executable, "-B", "-m", "unittest", "discover",
                                         "-s", "scripts", "-p", "test_database_runner.py", "-v"])
        else:
            summary["skipped"].append("Optional database-driver regression; "
                                      "use --database-checks after installing requirements-databases.txt")
        summary["status"] = "passed"
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        summary["status"] = "failed"
        summary["error"] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        summary["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        (report / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                                 encoding="utf-8")
        print(f"Verification report: {report}", flush=True)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
