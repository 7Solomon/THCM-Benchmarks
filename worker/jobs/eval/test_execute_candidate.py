from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import execute_candidate as subject


class ExecutionProjectionTests(unittest.TestCase):
    def test_projection_is_non_mutating_and_strips_references(self):
        candidate = {
            "results": {
                "queries": [{"id": "t", "type": "time"}],
                "reference_curves": [{"id": "paper", "file": "reference/x.csv"}],
                "plots": [
                    {"name": "mixed", "curves": [
                        {"source": "simulation", "x": {"key": "t"}, "y": {"key": "T"}},
                        {"source": "reference", "ref_id": "paper"}]},
                    {"name": "only_ref", "curves": [
                        {"source": "reference", "ref_id": "paper"}]},
                    {"name": "legacy", "x_axis": {"source": "t"},
                     "y_axis": {"source": "T"}, "compare_with": ["paper"]},
                ],
                "console": {"reference_deviation": {"enabled": True}},
                "save_db": False,
                "export_vtu": True,
                "save_checkpoint": True,
            }
        }
        original = json.loads(json.dumps(candidate))
        projected, changes = subject.execution_projection(candidate)
        self.assertEqual(candidate, original)
        self.assertEqual(projected["results"]["reference_curves"], [])
        plots = {p["name"]: p for p in projected["results"]["plots"]}
        self.assertEqual(sorted(plots), ["legacy", "mixed"])
        self.assertEqual([c["source"] for c in plots["mixed"]["curves"]],
                         ["simulation"])
        self.assertNotIn("compare_with", plots["legacy"])
        self.assertTrue(projected["results"]["save_db"])
        self.assertFalse(projected["results"]["export_vtu"])
        self.assertFalse(projected["results"]["save_checkpoint"])
        self.assertTrue(changes)

    def test_baseline_projection_keeps_references(self):
        candidate = {"results": {
            "reference_curves": [{"id": "paper", "file": "reference/x.csv"}],
            "plots": [{"name": "p", "curves": [{"source": "reference",
                                                "ref_id": "paper"}]}]}}
        projected, _ = subject.execution_projection(candidate, keep_references=True)
        self.assertEqual(projected["results"]["reference_curves"],
                         candidate["results"]["reference_curves"])
        self.assertEqual(projected["results"]["plots"], candidate["results"]["plots"])
        refs = subject._asset_references(projected, ("input/", "reference/"))
        self.assertEqual(refs, {"reference/x.csv"})
        self.assertEqual(subject._asset_references(projected, ("input/",)), set())

    def test_postprocess_traceback_detection(self):
        crash = ("Traceback (most recent call last):\n"
                 '  File "cli.py", line 143, in _cmd_oofem\n'
                 "    bm.postprocess()\n"
                 '  File "base.py", line 460, in postprocess\n'
                 "ValueError: boom\n")
        solve = ("Traceback (most recent call last):\n"
                 '  File "base.py", line 193, in run\n'
                 "OofemStepError: step 3 did not converge\n")
        self.assertTrue(subject._failed_in_postprocess(crash))
        self.assertFalse(subject._failed_in_postprocess(solve))
        self.assertFalse(subject._failed_in_postprocess("no traceback"))

    def test_database_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "query_results.db"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE results (step INTEGER, time REAL, temp REAL)")
                con.executemany("INSERT INTO results VALUES (?, ?, ?)",
                                [(1, 0.0, 20.0), (2, 1.0, 21.0)])
            info = subject.export_database(db, root / "results.csv")
            self.assertEqual(info["rows"], 2)
            self.assertEqual(info["time_range"], [0.0, 1.0])
            self.assertEqual(info["series_summary"]["temp"]["last"], 21.0)
            self.assertTrue((root / "results.csv").exists())

    def test_execute_with_fake_solver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "thcm"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sqlite3, sys\n"
                "kind, work = sys.argv[1], pathlib.Path(sys.argv[2])\n"
                "if kind == 'mesh':\n"
                " (work/'input').mkdir(parents=True, exist_ok=True)\n"
                " (work/'input'/'geometry.msh').write_text('mesh')\n"
                "else:\n"
                " out=work/'output'/'OOFEM'; out.mkdir(parents=True, exist_ok=True)\n"
                " con=sqlite3.connect(out/'query_results.db')\n"
                " con.execute('CREATE TABLE results (step INTEGER, time REAL, q REAL)')\n"
                " con.execute('INSERT INTO results VALUES (1, 0.0, 2.0)')\n"
                " con.commit(); con.close()\n",
                encoding="utf-8")
            cli.chmod(0o755)
            candidate = root / "candidate.json"
            candidate.write_text(json.dumps({"geometry": {}, "results": {"queries": []}}),
                                 encoding="utf-8")
            with patch.dict(os.environ, {"THCM": str(cli), "OOFEM_LD_PRELOAD": ""}):
                result = subject.execute(candidate, "fake", root / "run", timeout=5)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["output"]["rows"], 1)
            self.assertTrue((root / "run" / "results.csv").exists())
            self.assertEqual(result["output"]["plots"], [])

    def test_postprocess_crash_after_solve_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = root / "thcm"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sqlite3, sys\n"
                "kind, work = sys.argv[1], pathlib.Path(sys.argv[2])\n"
                "if kind == 'oofem':\n"
                " out=work/'output'/'OOFEM'; (out/'plots').mkdir(parents=True)\n"
                " (out/'plots'/'a.png').write_bytes(b'png')\n"
                " con=sqlite3.connect(out/'query_results.db')\n"
                " con.execute('CREATE TABLE results (step INTEGER, time REAL, q REAL)')\n"
                " con.execute('INSERT INTO results VALUES (1, 0.0, 2.0)')\n"
                " con.commit(); con.close()\n"
                " print('  \\u2717 Plot \\'bad\\': no key q2')\n"
                " def postprocess(): raise ValueError('boom')\n"
                " postprocess()\n",
                encoding="utf-8")
            cli.chmod(0o755)
            candidate = root / "candidate.json"
            candidate.write_text(json.dumps({"geometry": {}, "results": {"queries": []}}),
                                 encoding="utf-8")
            with patch.dict(os.environ, {"THCM": str(cli), "OOFEM_LD_PRELOAD": ""}):
                result = subject.execute(candidate, "fake", root / "run", timeout=5)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["stage"], "postprocess")
            self.assertIn("boom", result["plot_error"])
            self.assertEqual(result["output"]["plots"], ["output/OOFEM/plots/a.png"])
            self.assertEqual(len(result["output"]["plot_failures"]), 1)


if __name__ == "__main__":
    unittest.main()
