#!/usr/bin/env python3
"""Run every Lab 27 gate with Unicode-safe, non-destructive verification."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "reports" / "evidence"
COPY_DIRS = (
    "contracts",
    "dashboard",
    "data",
    "dbt_project",
    "docs",
    "gx",
    "observability",
    "reports",
    "scripts",
    "src",
    "tests_public",
    "tests_acceptance",
)
COPY_FILES = (
    ".gitignore",
    "lab_config.yaml",
    "Makefile",
    "README.md",
    "requirements.txt",
    "student_api.py",
)
IGNORED_COPY_NAMES = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".pytest-tmp-lab",
    "__pycache__",
    "target",
    "logs",
    "warehouse",
    "generated",
    "latest_metrics.json",
}


def _dbt_executable() -> str:
    sibling = Path(sys.executable).with_name(
        "dbt.exe" if os.name == "nt" else "dbt"
    )
    if sibling.exists():
        return str(sibling)
    discovered = shutil.which("dbt")
    if discovered:
        return discovered
    raise SystemExit(
        "dbt executable not found for the active Python environment. "
        "Run: python -m pip install -r requirements.txt"
    )


def _ignore_copy(_: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORED_COPY_NAMES}


def _copy_cleanroom(destination: Path) -> None:
    for directory in COPY_DIRS:
        shutil.copytree(
            ROOT / directory,
            destination / directory,
            ignore=_ignore_copy,
        )
    for filename in COPY_FILES:
        shutil.copy2(ROOT / filename, destination / filename)


def _run(label: str, command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"\n=== {label} ===", flush=True)
    print(" ".join(command), flush=True)
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(command, cwd=cwd, env=environment, check=False)
    if completed.returncode != 0:
        raise SystemExit(
            f"{label} failed with exit code {completed.returncode}"
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _preserve_cleanroom_evidence(cleanroom: Path) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    sources = {
        cleanroom / "reports" / "latest_metrics.json":
            EVIDENCE_DIR / "healthy_baseline_metrics.json",
        cleanroom / "reports" / "generated" / "gx_orders_validation.json":
            EVIDENCE_DIR / "gx_healthy_validation.json",
        cleanroom / "dbt_project" / "target" / "run_results.json":
            EVIDENCE_DIR / "dbt_run_results.json",
    }
    for source, destination in sources.items():
        if not source.exists():
            raise SystemExit(f"Thiếu minh chứng clean-room: {source}")
        shutil.copy2(source, destination)


def _write_verification_summary(junit_path: Path) -> None:
    baseline_path = EVIDENCE_DIR / "healthy_baseline_metrics.json"
    gx_healthy_path = EVIDENCE_DIR / "gx_healthy_validation.json"
    gx_action_path = EVIDENCE_DIR / "gx_action_drill.json"
    dbt_path = EVIDENCE_DIR / "dbt_run_results.json"
    incident_path = EVIDENCE_DIR / "incident_drill.json"
    required = (
        junit_path,
        baseline_path,
        gx_healthy_path,
        gx_action_path,
        dbt_path,
        incident_path,
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"Thiếu tệp minh chứng: {missing}")

    junit_root = ET.parse(junit_path).getroot()
    suites = (
        [junit_root]
        if junit_root.tag == "testsuite"
        else list(junit_root.findall("./testsuite"))
    )
    pytest_result = {
        "tests": sum(int(suite.attrib.get("tests", 0)) for suite in suites),
        "failures": sum(
            int(suite.attrib.get("failures", 0)) for suite in suites
        ),
        "errors": sum(int(suite.attrib.get("errors", 0)) for suite in suites),
        "skipped": sum(int(suite.attrib.get("skipped", 0)) for suite in suites),
        "time_seconds": sum(
            float(suite.attrib.get("time", 0.0)) for suite in suites
        ),
    }
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    gx_healthy = json.loads(gx_healthy_path.read_text(encoding="utf-8"))
    gx_action = json.loads(gx_action_path.read_text(encoding="utf-8"))
    dbt = json.loads(dbt_path.read_text(encoding="utf-8"))
    incident = json.loads(incident_path.read_text(encoding="utf-8"))
    dbt_statuses = Counter(
        str(result.get("status", "unknown")).lower()
        for result in dbt.get("results", [])
    )

    summary = {
        "artifact_schema_version": 1,
        "ket_qua_tong": "PASS",
        "pytest": pytest_result,
        "healthy_baseline": {
            "orders_rows": baseline["orders_rows"],
            "failed_contract_issue_count": baseline["failed_contract_issue_count"],
            "row_count_anomaly": baseline["row_count_anomaly"],
            "orders_freshness_minutes": baseline["orders_freshness_minutes"],
            "kb_freshness_minutes": baseline["kb_freshness_minutes"],
            "contract_slo": baseline["contract_slo"],
            "multiwindow_burn_signal": baseline["multiwindow_burn_signal"],
        },
        "gx": {
            "healthy_decision": gx_healthy["decision"],
            "healthy_expectation_count": len(gx_healthy["expectations"]),
            "healthy_failed_expectations": sum(
                not item["success"] for item in gx_healthy["expectations"]
            ),
            "warning_case": gx_action["warning_case"]["decision"],
            "critical_case": gx_action["critical_case"]["decision"],
            "critical_quarantine_rows": gx_action["critical_case"][
                "quarantine_row_count"
            ],
        },
        "dbt": {
            "result_count": len(dbt.get("results", [])),
            "status_counts": dict(sorted(dbt_statuses.items())),
        },
        "incident_drill": {
            "healthy_orders_rows": incident["healthy"]["orders_rows"],
            "incident_orders_rows": incident["incident"]["orders_rows"],
            "incident_row_anomaly": incident["incident"]["row_count_signal"][
                "is_anomaly"
            ],
            "kb_freshness_failed": (
                incident["incident"]["kb_freshness_failure"] is not None
            ),
            "multiwindow_page": incident["incident"]["multiwindow"]["decision"][
                "page"
            ],
            "recovery_row_anomaly": incident["recovery"]["row_count_signal"][
                "is_anomaly"
            ],
            "recovery_page": incident["recovery"]["multiwindow"]["page"],
        },
        "evidence_files": {
            path.relative_to(ROOT).as_posix(): {
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in required
        },
    }
    summary_path = EVIDENCE_DIR / "verification_summary.json"
    summary_path.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _assert_cleanroom_health(cleanroom: Path) -> None:
    metrics = json.loads(
        (cleanroom / "reports" / "latest_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    gx_evidence = json.loads(
        (
            cleanroom
            / "reports"
            / "generated"
            / "gx_orders_validation.json"
        ).read_text(encoding="utf-8")
    )
    checks = {
        "contract_issue_count_zero": metrics["failed_contract_issue_count"] == 0,
        "row_count_not_anomalous": not metrics["row_count_anomaly"]["is_anomaly"],
        "orders_fresh": (
            metrics["orders_freshness_minutes"] is not None
            and -5.0 <= metrics["orders_freshness_minutes"] <= 30.0
        ),
        "kb_fresh": (
            metrics["kb_freshness_minutes"] is not None
            and -5.0 <= metrics["kb_freshness_minutes"] <= 60.0
        ),
        "kb_length_stable": not metrics["kb_text_length_signal"]["is_anomaly"],
        "kb_embedding_stable": not metrics["kb_embedding_signal"]["is_anomaly"],
        "contract_slo_healthy": not metrics["contract_slo"]["breached"],
        "multiwindow_no_page": not metrics["multiwindow_burn_signal"]["page"],
        "gx_pass": gx_evidence["decision"] == "pass",
        "gx_quarantine_empty": gx_evidence["quarantine"]["row_count"] == 0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise SystemExit(f"Clean-room health assertions failed: {failed}")
    print(
        "CLEAN-ROOM HEALTH: PASS "
        f"(orders_rows={metrics['orders_rows']}, "
        f"row_score={metrics['row_count_anomaly']['score']:.4f}, "
        f"orders_freshness={metrics['orders_freshness_minutes']:.2f}m, "
        f"kb_freshness={metrics['kb_freshness_minutes']:.2f}m)",
        flush=True,
    )


def main() -> None:
    python = sys.executable
    dbt = _dbt_executable()
    basetemp = ROOT / ".pytest-tmp-lab"
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    junit_path = EVIDENCE_DIR / "pytest_results.xml"

    _run(
        "Compile",
        [
            python,
            "-X",
            "utf8",
            "-m",
            "compileall",
            "-q",
            "student_api.py",
            "src",
            "observability",
            "scripts",
            "gx",
            "dashboard",
        ],
    )
    _run(
        "Public and acceptance tests",
        [
            python,
            "-X",
            "utf8",
            "-m",
            "pytest",
            "tests_public",
            "tests_acceptance",
            "-q",
            "--basetemp",
            str(basetemp),
            "--junitxml",
            str(junit_path),
        ],
    )

    # Reset changes tracked sample timestamps/counts by design. Verify the true
    # healthy flow inside a disposable copy so the user's source data stays exact.
    with tempfile.TemporaryDirectory(prefix="day27-cleanroom-") as temp:
        cleanroom = Path(temp)
        _copy_cleanroom(cleanroom)
        _run(
            "Clean-room reset",
            [python, "-X", "utf8", "scripts/reset_lab.py"],
            cwd=cleanroom,
        )
        _run(
            "Clean-room baseline",
            [python, "-X", "utf8", "scripts/run_baseline.py"],
            cwd=cleanroom,
        )
        _run(
            "Clean-room GX Checkpoint",
            [python, "-X", "utf8", "gx/validate_orders.py"],
            cwd=cleanroom,
        )
        _run(
            "Clean-room dbt seed sync",
            [python, "-X", "utf8", "scripts/sync_dbt_seeds.py"],
            cwd=cleanroom,
        )
        _run(
            "Clean-room dbt build",
            [
                dbt,
                "build",
                "--project-dir",
                "dbt_project",
                "--profiles-dir",
                "dbt_project",
                "--no-partial-parse",
            ],
            cwd=cleanroom,
        )
        _assert_cleanroom_health(cleanroom)
        _preserve_cleanroom_evidence(cleanroom)

    _run(
        "GX warning/critical action drill",
        [python, "-X", "utf8", "scripts/run_gx_action_drill.py"],
    )
    _run(
        "Deterministic incident drill",
        [python, "-X", "utf8", "scripts/run_incident_drill.py"],
    )
    _write_verification_summary(junit_path)
    print("\nLAB 27 VERIFICATION: PASS", flush=True)


if __name__ == "__main__":
    main()
