"""Static audits for the self-contained VisualOptimise package."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


FORBIDDEN_RUNTIME_REFERENCES = (
    "experiments" + ".archive",
    "experiments" + ".current",
    "experiments" + ".shared",
    "VisualOptimization" + ".experiments",
    "VisualOptimization" + ".run_pipeline",
    "VisualOptimization" + ".pipeline",
    "I:" + r"\Disertation\VisualOptimization" + r"\experiments",
)


def iter_runtime_python_files(project_root: Path) -> list[Path]:
    files = [project_root / "run_main_pipeline.py"]
    files.extend(sorted((project_root / "visualoptimise").glob("*.py")))
    return [path for path in files if path.is_file()]


def count_functions(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) for node in ast.walk(tree))


def audit_import_isolation(project_root: Path) -> dict[str, Any]:
    findings = []
    for path in iter_runtime_python_files(project_root):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_RUNTIME_REFERENCES:
            if pattern in text:
                findings.append(
                    {
                        "file": str(path),
                        "pattern": pattern,
                    }
                )
    return {
        "schema_version": "visualoptimise_import_isolation_audit_v1",
        "passed": not findings,
        "forbidden_references": findings,
        "files_scanned": len(iter_runtime_python_files(project_root)),
        "forbidden_patterns": list(FORBIDDEN_RUNTIME_REFERENCES),
    }


def audit_code_inventory(project_root: Path) -> dict[str, Any]:
    files = iter_runtime_python_files(project_root)
    rows = []
    total_lines = 0
    total_functions = 0
    for path in files:
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        function_count = count_functions(path)
        total_lines += line_count
        total_functions += function_count
        rows.append(
            {
                "path": str(path),
                "lines": line_count,
                "functions": function_count,
            }
        )
    return {
        "schema_version": "visualoptimise_code_inventory_v1",
        "files": rows,
        "totals": {
            "python_files": len(rows),
            "lines": total_lines,
            "functions": total_functions,
        },
    }


def run_audit(project_root: Path, output_dir: Path | None = None) -> dict[str, Any]:
    isolation = audit_import_isolation(project_root)
    inventory = audit_code_inventory(project_root)
    report = {
        "schema_version": "visualoptimise_static_audit_v1",
        "passed": isolation["passed"],
        "import_isolation": isolation,
        "code_inventory": inventory,
    }
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "import_isolation_audit.json").write_text(json.dumps(isolation, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "code_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        (output_dir / "static_audit_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
