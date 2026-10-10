from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_dataflow as subject

EVAL = subject.JOBS / "eval" / "dataflow.json"


class RealJobsTests(unittest.TestCase):
    def test_every_job_dataflow_is_clean(self):
        paths = sorted(subject.JOBS.glob("*/dataflow.json"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(job=path.parent.name):
                self.assertEqual(subject.check(path), [])


class DriftTests(unittest.TestCase):
    """Each case breaks a copy of the eval dataflow one way and expects a report."""

    def problems(self, mutate) -> list[str]:
        doc = json.loads(EVAL.read_text(encoding="utf-8"))
        mutate(doc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval" / "dataflow.json"
            path.parent.mkdir()
            path.write_text(json.dumps(doc), encoding="utf-8")
            return subject.check(path)

    def assertReports(self, mutate, needle: str):
        problems = self.problems(mutate)
        self.assertTrue(any(needle in p for p in problems),
                        f"expected {needle!r} in {problems}")

    def test_flag_missing_from_params(self):
        def drop(doc):
            doc["params"] = [p for p in doc["params"] if p.get("flag") != "--json-repair-turns"]
            for e in doc["edges"]:
                if (e.get("limit") or {}).get("flag") == "--json-repair-turns":
                    del e["limit"]
        self.assertReports(drop, "--json-repair-turns is accepted by the job but missing")

    def test_flag_that_no_longer_exists(self):
        def add(doc):
            doc["params"].append({"flag": "--gone", "kind": "info", "help": "x"})
        self.assertReports(add, "--gone is in params but no flag source accepts it")

    def test_default_drift(self):
        def change(doc):
            for p in doc["params"]:
                if p.get("flag") == "--solver-timeout":
                    p["default"] = 600
        self.assertReports(change, "--solver-timeout: default is 600 here but 900")

    def test_edge_to_unknown_node(self):
        self.assertReports(lambda d: d["edges"].append({"from": "author", "to": "nowhere"}),
                           "unknown to node 'nowhere'")

    def test_enabled_by_must_be_a_toggle(self):
        def point(doc):
            doc["nodes"][0]["enabled_by"] = "--tol"
        self.assertReports(point, "enabled_by --tol: not a toggle")

    def test_loop_limit_must_cap_something(self):
        def unhook(doc):
            for e in doc["edges"]:
                e.pop("limit", None)
        self.assertReports(unhook, "--json-repair-turns: loop limit that caps no edge")

    def test_schema_rejects_unknown_kind(self):
        def bad(doc):
            doc["nodes"][0]["kind"] = "robot"
        self.assertReports(bad, "schema: nodes/0/kind")

    def test_shell_branch_parsing(self):
        with tempfile.TemporaryDirectory() as tmp:
            sh = Path(tmp) / "job.sh"
            sh.write_text("# --not-a-flag) in a comment\n"
                          "case \"$1\" in\n"
                          "  --a|--b) x ;;\n"
                          "  -h|--help) y ;;\n"
                          "esac\n", encoding="utf-8")
            self.assertEqual(set(subject.shell_flags(sh)), {"--a", "--b", "--help"})


if __name__ == "__main__":
    unittest.main()
