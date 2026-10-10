#!/usr/bin/env python3
"""Run an eval candidate through THCM-FEM in an isolated directory.

The authoring candidate remains immutable.  A documented execution projection
removes the model's reference curves (their files do not exist in the run
directory), enables the query database and disables large output before
THCM-FEM sees it.  The model's own plots are kept, minus any reference curve,
so a run still draws what the solver produced.  The curated baseline keeps its
plots and reference curves intact, with benchmarks/<case>/reference copied in.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
BENCHMARKS = REPO / "benchmarks"
PROJECTION_VERSION = 2


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def solver_cli() -> Path:
    configured = os.environ.get("THCM")
    if configured:
        return Path(configured)
    # Do not resolve the venv's Python symlink: the console script is beside the
    # venv path, not beside the base Conda interpreter it points to.
    beside_python = Path(sys.executable).parent / "thcm"
    if beside_python.exists():
        return beside_python
    found = shutil.which("thcm")
    return Path(found) if found else beside_python


def solver_environment() -> dict[str, str]:
    env = os.environ.copy()
    preload = env.get("OOFEM_LD_PRELOAD", "").strip()
    if preload:
        old = env.get("LD_PRELOAD", "").strip()
        env["LD_PRELOAD"] = f"{preload}:{old}" if old else preload
    # Native numerical libraries otherwise create one worker pool per process.
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    return env


def _git_commit(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
    except Exception:
        return None


def _git_dirty(path: Path | None) -> bool | None:
    if not path or not path.exists():
        return None
    try:
        status = subprocess.check_output(
            ["git", "-C", str(path), "status", "--porcelain"],
            text=True, stderr=subprocess.DEVNULL, timeout=5)
        return bool(status.strip())
    except Exception:
        return None


def environment_info(backend: str) -> dict[str, Any]:
    spec = importlib.util.find_spec("tools")
    locations = list(spec.submodule_search_locations or []) if spec else []
    thcm_root = Path(locations[0]).parent if locations else None
    try:
        oofem_version = importlib.metadata.version("oofem")
    except importlib.metadata.PackageNotFoundError:
        oofem_version = None
    return {
        "backend": backend,
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "thcm_cli": str(solver_cli()),
        "thcm_fem_root": str(thcm_root) if thcm_root else None,
        "thcm_fem_commit": _git_commit(thcm_root),
        "thcm_fem_dirty": _git_dirty(thcm_root),
        "oofem_version": oofem_version,
        "projection_version": PROJECTION_VERSION,
    }


def preflight(backend: str = "oofem") -> dict[str, Any]:
    """Fail before model calls if the requested solver cannot even import."""
    cli = solver_cli()
    if not cli.exists():
        raise RuntimeError(f"THCM-FEM CLI not found at {cli}")
    env = solver_environment()
    checks = [[str(cli), "--help"]]
    if backend == "oofem":
        checks.append([
            sys.executable, "-c",
            "from tools.simulations.oofem.src.base import OofemBenchmark; "
            "import oofem; "
            "required=('hydratingConcreteMat','materialOfType','elementOfType','bcOfType'); "
            "missing=[x for x in required if not hasattr(oofem,x)]; "
            "assert not missing, 'missing OOFEM bindings: '+','.join(missing); "
            "print(oofem.__file__)",
        ])
    else:
        checks.append([
            sys.executable, "-c",
            "from tools.simulations.fem.base import FEMBenchmark; print('FEM import ok')",
        ])
    outputs = []
    for command in checks:
        proc = subprocess.run(command, env=env, text=True, capture_output=True,
                              timeout=30)
        if proc.returncode:
            detail = (proc.stderr or proc.stdout).strip()[-2000:]
            raise RuntimeError(
                f"solver preflight failed ({' '.join(command)}): {detail}")
        outputs.append((proc.stdout or "").strip().splitlines()[-1:])
    info = environment_info(backend)
    info["preflight"] = "ok"
    return info


def _strip_reference_curves(results: dict, changes: list[dict]) -> None:
    """Keep the plots, drop what points at reference files the run cannot see."""
    if results.get("reference_curves"):
        changes.append({"path": "results.reference_curves", "action": "cleared",
                        "reason": "reference files are not model output"})
    results["reference_curves"] = []

    plots = results.get("plots")
    if plots is None:
        results["plots"] = []
        return
    if not isinstance(plots, list):
        changes.append({"path": "results.plots", "action": "cleared",
                        "reason": "not a list"})
        results["plots"] = []
        return
    kept = []
    for i, plot in enumerate(plots):
        if not isinstance(plot, dict):
            changes.append({"path": f"results.plots[{i}]", "action": "removed",
                            "reason": "not an object"})
            continue
        # compare_with is the legacy spelling of a reference curve.
        if plot.pop("compare_with", None):
            changes.append({"path": f"results.plots[{i}].compare_with",
                            "action": "removed",
                            "reason": "reference files are not model output"})
        curves = plot.get("curves")
        if isinstance(curves, list):
            own = [c for c in curves
                   if not (isinstance(c, dict) and c.get("source") == "reference")]
            if len(own) != len(curves):
                changes.append({"path": f"results.plots[{i}].curves",
                                "action": "removed",
                                "count": len(curves) - len(own),
                                "reason": "reference files are not model output"})
                plot["curves"] = own
            if not own:
                changes.append({"path": f"results.plots[{i}]", "action": "removed",
                                "reason": "no curves left after removing references"})
                continue
        kept.append(plot)
    results["plots"] = kept


def execution_projection(candidate: dict, keep_vtu: bool = False,
                         keep_references: bool = False) -> tuple[dict, list[dict]]:
    projected = json.loads(json.dumps(candidate))
    changes: list[dict] = []
    results = projected.get("results")
    if not isinstance(results, dict):
        results = {}
        projected["results"] = results
        changes.append({"path": "results", "action": "created"})

    if not keep_references:
        _strip_reference_curves(results, changes)

    console = results.get("console")
    if not isinstance(console, dict):
        console = {}
        results["console"] = console
    deviation = console.get("reference_deviation")
    if not isinstance(deviation, dict):
        deviation = {}
        console["reference_deviation"] = deviation
    if deviation.get("enabled") is not False:
        changes.append({"path": "results.console.reference_deviation.enabled",
                        "action": "set", "value": False})
    deviation["enabled"] = False

    for key, value in (("save_db", True), ("export_vtu", bool(keep_vtu)),
                       ("save_checkpoint", False)):
        if results.get(key) != value:
            changes.append({"path": f"results.{key}", "action": "set",
                            "value": value})
        results[key] = value
    return projected, changes


def _asset_references(value: Any, prefixes: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(_asset_references(child, prefixes))
    elif isinstance(value, list):
        for child in value:
            found.update(_asset_references(child, prefixes))
    elif isinstance(value, str):
        normalized = value.replace("\\", "/")
        if normalized.startswith(prefixes):
            found.add(normalized)
    return found


def copy_declared_assets(projected: dict, case: str, work_dir: Path,
                         prefixes: tuple[str, ...] = ("input/",)
                         ) -> tuple[list[dict], list[str]]:
    source_root = (BENCHMARKS / case).resolve()
    copied, missing = [], []
    for rel_text in sorted(_asset_references(projected, prefixes)):
        rel = Path(rel_text)
        if rel.is_absolute() or ".." in rel.parts:
            missing.append(f"unsafe path: {rel_text}")
            continue
        # geometry.msh is always generated from the candidate geometry.
        if rel.as_posix() == "input/geometry.msh":
            continue
        source = (source_root / rel).resolve()
        try:
            source.relative_to(source_root)
        except ValueError:
            missing.append(f"unsafe path: {rel_text}")
            continue
        if not source.is_file():
            missing.append(rel_text)
            continue
        target = work_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"path": rel.as_posix(), "sha256": _sha256(target),
                       "source": str(source.relative_to(REPO))})
    return copied, missing


def _run(command: list[str], log_prefix: Path, timeout: float,
         env: dict[str, str]) -> dict[str, Any]:
    stdout_path = log_prefix.with_suffix(".stdout.log")
    stderr_path = log_prefix.with_suffix(".stderr.log")
    started = time.monotonic()
    timed_out = False
    with stdout_path.open("w", encoding="utf-8") as stdout, \
            stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.Popen(command, stdout=stdout, stderr=stderr, text=True,
                                env=env, start_new_session=True)
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            returncode = proc.returncode
    return {
        "command": command,
        "returncode": returncode,
        "signal": -returncode if returncode is not None and returncode < 0 else None,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 3),
        "stdout": stdout_path.name,
        "stderr": stderr_path.name,
    }


def _tail(paths: list[Path], lines: int = 35) -> str:
    collected: list[str] = []
    for path in paths:
        if path.exists():
            collected.extend(path.read_text(encoding="utf-8", errors="replace").splitlines())
    return "\n".join(collected[-lines:])[-8000:]


def export_database(db_path: Path, csv_path: Path) -> dict[str, Any]:
    with sqlite3.connect(db_path) as con:
        table = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='results'"
        ).fetchone()
        if not table:
            return {"database": str(db_path), "rows": 0, "columns": [],
                    "error": "results table missing"}
        cur = con.execute('SELECT * FROM "results"')
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    time_col = "time" if "time" in columns else ("t" if "t" in columns else None)
    time_range = None
    if rows and time_col:
        idx = columns.index(time_col)
        values = [r[idx] for r in rows if isinstance(r[idx], (int, float))]
        if values:
            time_range = [min(values), max(values)]
    series_summary = {}
    for idx, column in enumerate(columns[:40]):
        values = [float(row[idx]) for row in rows
                  if isinstance(row[idx], (int, float)) and math.isfinite(row[idx])]
        if values:
            series_summary[column] = {
                "min": min(values), "max": max(values), "last": values[-1]
            }
    return {"database": str(db_path), "csv": str(csv_path), "rows": len(rows),
            "columns": columns, "time_range": time_range,
            "series_summary": series_summary}


def _failure_status(run: dict, tail: str, stage: str) -> tuple[str, str]:
    low = tail.lower()
    if run.get("timed_out"):
        return "timeout", f"{stage} exceeded its time limit"
    if run.get("signal"):
        return "native_crash", f"{stage} killed by signal {run['signal']}"
    # Avoid matching the CLI option/stack variable `on_nonconverged`.
    if ("did not converge" in low or "non-converged" in low or
            "nonconvergence" in low or "oofemsteperror" in low):
        return "nonconverged", "solver did not converge"
    if "not implemented" in low or "unsupported" in low:
        return "unsupported_feature", "candidate uses an unsupported solver feature"
    return ("mesh_failed" if stage == "mesh" else "model_build_or_solve_failed",
            f"{stage} exited {run.get('returncode')}")


def _failed_in_postprocess(log_text: str) -> bool:
    """True when the last traceback in the log went through postprocess().

    Plotting runs after the solve has written its query database, so a crash
    there must not turn a finished solve into a failed one.
    """
    start = log_text.rfind("Traceback (most recent call last):")
    if start < 0:
        return False
    tb = log_text[start:]
    return "bm.postprocess()" in tb or re.search(r", in postprocess\b", tb) is not None


def execute(candidate_path: Path, case: str, attempt_dir: Path, *,
            backend: str = "oofem", timeout: float = 900,
            keep_vtu: bool = False,
            keep_references: bool = False) -> dict[str, Any]:
    """Execute one candidate and always write attempt_dir/execution.json.

    keep_references is for the curated baseline: its plots keep their reference
    curves and benchmarks/<case>/reference/ is copied in so they can be drawn.
    """
    attempt_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result: dict[str, Any] = {
        "status": "preparation_failed", "stage": "prepare", "case": case,
        "backend": backend, "candidate": str(candidate_path),
        "candidate_sha256": _sha256(candidate_path),
        "environment": environment_info(backend), "commands": [],
    }
    try:
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        projected, changes = execution_projection(candidate, keep_vtu,
                                                  keep_references)
        (attempt_dir / "benchmark.json").write_text(
            json.dumps(projected, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        prefixes = ("input/", "reference/") if keep_references else ("input/",)
        copied, missing = copy_declared_assets(projected, case, attempt_dir,
                                               prefixes)
        transform = {"version": PROJECTION_VERSION, "changes": changes,
                     "copied_assets": copied, "missing_assets": missing}
        _atomic_json(attempt_dir / "execution_transform.json", transform)
        result["transform"] = transform
        if missing:
            result.update(status="asset_missing", stage="prepare",
                          message="missing declared input asset(s): " + ", ".join(missing))
            return result

        cli = solver_cli()
        env = solver_environment()
        mesh_run = _run([str(cli), "mesh", str(attempt_dir)],
                        attempt_dir / "mesh", timeout, env)
        result["commands"].append(mesh_run)
        mesh_tail = _tail([attempt_dir / "mesh.stdout.log",
                           attempt_dir / "mesh.stderr.log"])
        if mesh_run["returncode"] != 0:
            status, message = _failure_status(mesh_run, mesh_tail, "mesh")
            result.update(status=status, stage="mesh", message=message,
                          log_tail=mesh_tail)
            return result

        command = [str(cli), backend, str(attempt_dir)]
        if backend == "oofem":
            command.extend(["--on-nonconverged", "abort"])
        solver_run = _run(command, attempt_dir / "solver", timeout, env)
        result["commands"].append(solver_run)
        solver_tail = _tail([attempt_dir / "solver.stdout.log",
                             attempt_dir / "solver.stderr.log"])

        db_candidates = list((attempt_dir / "output").glob("*/query_results.db"))
        if db_candidates:
            result["output"] = export_database(db_candidates[0], attempt_dir / "results.csv")
        else:
            result["output"] = {"rows": 0, "columns": []}
        result["output"]["vtu_files"] = len(list((attempt_dir / "output").rglob("*.vtu")))
        checkpoints = list((attempt_dir / "output").rglob("*.h5"))
        result["output"]["checkpoints"] = [str(p.relative_to(attempt_dir))
                                             for p in checkpoints]
        result["output"]["plots"] = sorted(
            str(p.relative_to(attempt_dir))
            for p in (attempt_dir / "output").glob("*/plots/*")
            if p.suffix.lower() in (".png", ".svg", ".pdf"))
        stdout_text = (attempt_dir / "solver.stdout.log").read_text(
            encoding="utf-8", errors="replace")
        stderr_text = (attempt_dir / "solver.stderr.log").read_text(
            encoding="utf-8", errors="replace")
        # ResultPlotter catches each plot's exception and prints this marker.
        failed_plots = [line.strip() for line in stdout_text.splitlines()
                        if "\u2717 Plot" in line]
        if failed_plots:
            result["output"]["plot_failures"] = failed_plots[:20]
        rows = result["output"].get("rows", 0)
        postprocess_crash = (solver_run["returncode"] != 0 and not solver_run["timed_out"]
                             and rows > 0 and _failed_in_postprocess(stderr_text))

        if postprocess_crash:
            result.update(status="ok", stage="postprocess",
                          message=f"{rows} query row(s) written; plotting crashed",
                          plot_error=solver_tail, log_tail=solver_tail)
        elif solver_run["returncode"] != 0:
            status, message = _failure_status(solver_run, solver_tail, "solver")
            result.update(status=status, stage="solver", message=message,
                          log_tail=solver_tail)
        elif not db_candidates or result["output"].get("rows", 0) == 0:
            result.update(status="ran_no_queries", stage="output",
                          message="solver finished but wrote no query rows",
                          log_tail=solver_tail)
        else:
            result.update(status="ok", stage="complete",
                          message=f"{result['output']['rows']} query row(s) written",
                          log_tail=solver_tail)
        return result
    except Exception as exc:
        result.update(status="preparation_failed", stage=result.get("stage", "prepare"),
                      message=f"{type(exc).__name__}: {exc}")
        return result
    finally:
        result["seconds"] = round(time.monotonic() - started, 3)
        _atomic_json(attempt_dir / "execution.json", result)


def feedback_text(execution: dict) -> str:
    output = execution.get("output") or {}
    return "\n".join([
        "THCM-FEM execution feedback:",
        f"- status: {execution.get('status')}",
        f"- stage: {execution.get('stage')}",
        f"- message: {execution.get('message')}",
        f"- result rows: {output.get('rows', 0)}",
        f"- result columns: {', '.join(output.get('columns') or []) or '(none)'}",
        f"- numerical summary: {json.dumps(output.get('series_summary') or {})[:3000]}",
        "- final solver log:",
        (execution.get("log_tail") or "(no log output)")[-6000:],
    ])
