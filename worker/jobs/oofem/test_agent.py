#!/usr/bin/env python3
from __future__ import annotations

import json
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worker.jobs.oofem import agent


GOOD_CANDIDATE = """\
from pathlib import Path
Path('results.csv').write_text('curve,x,y\\nrequired,0,1\\nrequired,1,2\\n')
Path('plot.png').write_bytes(b'not-empty')
print('candidate ran')
"""


class CandidateValidationTests(unittest.TestCase):
    def make_sandbox(self, root: Path) -> agent.Sandbox:
        work = root / "work"
        oofem = root / "oofem"
        (work / "reference").mkdir(parents=True)
        oofem.mkdir()
        return agent.Sandbox(
            work, oofem, sys.executable, "", spike_timeout=5,
            candidate_timeout=5, curve_ids=["required"])

    def test_successful_validation_allows_submit_until_candidate_changes(self):
        with tempfile.TemporaryDirectory() as td:
            sb = self.make_sandbox(Path(td))
            accepted, _ = sb.submit()
            self.assertFalse(accepted)

            sb.write_file("candidate.py", GOOD_CANDIDATE)
            self.assertIn("validation successful", sb.run_candidate())
            accepted, message = sb.submit()
            self.assertTrue(accepted, message)
            self.assertTrue((sb.work / "results.csv").is_file())
            self.assertTrue((sb.work / "plot.png").is_file())

            sb.write_file("candidate.py", GOOD_CANDIDATE + "\n# changed\n")
            accepted, message = sb.submit()
            self.assertFalse(accepted)
            self.assertIn("run run_candidate", message)

    def test_missing_required_curve_fails_validation(self):
        with tempfile.TemporaryDirectory() as td:
            sb = self.make_sandbox(Path(td))
            source = GOOD_CANDIDATE.replace("required", "wrong")
            sb.write_file("candidate.py", source)
            result = sb.run_candidate()
            self.assertIn("missing required curve ids: required", result)
            self.assertFalse(sb.candidate_dirty)
            self.assertFalse(sb.submit()[0])

            previous = sb.last_validation
            sb.write_file("candidate.py", source + "\n# repair attempt\n")
            self.assertEqual(sb.last_validation, previous)
            self.assertTrue(sb.candidate_dirty)
            self.assertIn("previous result", sb.submit()[1])

    def test_stale_work_outputs_do_not_make_candidate_pass(self):
        with tempfile.TemporaryDirectory() as td:
            sb = self.make_sandbox(Path(td))
            (sb.work / "results.csv").write_text(
                "curve,x,y\nrequired,0,1\n", encoding="utf-8")
            (sb.work / "plot.png").write_bytes(b"old")
            sb.write_file("candidate.py", "print('writes no outputs')\n")
            result = sb.run_candidate()
            self.assertIn("did not write results.csv", result)
            self.assertIn("did not write a non-empty plot.png", result)
            self.assertFalse(sb.submit()[0])

    def test_agent_accepts_write_validate_submit_sequence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sb = self.make_sandbox(root)
            calls = [
                {"id": "write", "function": {"name": "write_file",
                 "arguments": json.dumps(
                     {"path": "candidate.py", "content": GOOD_CANDIDATE})}},
                {"id": "run", "function": {"name": "run_candidate",
                 "arguments": "{}"}},
                {"id": "submit", "function": {"name": "submit",
                 "arguments": "{}"}},
            ]

            def call(messages, tools):
                return {"choices": [{"message": {"content": "", "tool_calls": calls}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

            totals = agent.run_agent(
                call, "system", sb, root / "transcript.jsonl", 1, None)
            self.assertTrue(totals["submitted"])
            self.assertTrue(totals["validated"])
            self.assertEqual(totals["stop"], "submitted")

    def test_finalization_turns_allow_last_edit_to_run_and_submit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sb = self.make_sandbox(root)
            calls = [
                ("write_file", {"path": "candidate.py", "content": GOOD_CANDIDATE}),
                ("run_candidate", {}),
                ("submit", {}),
            ]
            index = 0

            def call(messages, tools):
                nonlocal index
                name, args = calls[index]
                index += 1
                return {"choices": [{"message": {"content": "", "tool_calls": [{
                    "id": str(index), "function": {
                        "name": name, "arguments": json.dumps(args)}}]}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

            totals = agent.run_agent(
                call, "system", sb, root / "transcript.jsonl", max_turns=1,
                price=None, finalization_turns=2)
            self.assertTrue(totals["submitted"])
            self.assertEqual(totals["turns"], 3)
            self.assertEqual(totals["finalization_turns"], 2)

    def test_draft_nudge_starts_after_one_third_elapsed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sb = self.make_sandbox(root)
            received = []
            index = 0

            def call(messages, tools):
                nonlocal index
                received.append([m.get("content", "") for m in messages
                                 if m.get("role") == "user"])
                index += 1
                return {"choices": [{"message": {"content": "", "tool_calls": [{
                    "id": str(index), "function": {
                        "name": "list_dir", "arguments": '{"path":"."}'}}]}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

            agent.run_agent(call, "system", sb, root / "transcript.jsonl",
                            max_turns=9, price=None, finalization_turns=0)
            self.assertTrue(any(
                "first candidate.py is due now" in message
                for turn_messages in received for message in turn_messages))

    def test_transient_api_failure_is_retried_without_spending_a_turn(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sb = self.make_sandbox(root)
            attempts = 0

            def call(messages, tools):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    return {"error": "temporary connection failure"}
                return {"choices": [{"message": {"content": "", "tool_calls": [{
                    "id": "ok", "function": {
                        "name": "list_dir", "arguments": '{"path":"."}'}}]}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

            with mock.patch.object(agent.time, "sleep"):
                totals = agent.run_agent(
                    call, "system", sb, root / "transcript.jsonl", max_turns=1,
                    price=None, finalization_turns=0, api_retries=2)
            self.assertEqual(attempts, 2)
            self.assertEqual(totals["api_retries"], 1)
            self.assertEqual(totals["turns"], 1)

    def test_inspect_oofem_collects_examples_and_keywords(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            oofem = root / "oofem"
            (work / "reference").mkdir(parents=True)
            tests = oofem / "bindings" / "python" / "tests"
            source = oofem / "src" / "tm"
            tests.mkdir(parents=True)
            source.mkdir(parents=True)
            (tests / "test_transport.py").write_text(
                "problem = oofempy.transientTransport(nSteps=2, alpha=0.5)\n",
                encoding="utf-8")
            (source / "transienttransport.h").write_text(
                '#define _IFT_TransientTransportProblem_Name "transienttransport"\n'
                '#define _IFT_TransientTransportProblem_alpha "alpha"\n',
                encoding="utf-8")
            sb = agent.Sandbox(work, oofem, sys.executable, "", 5)
            result = sb.inspect_oofem("transientTransport")
            self.assertIn("OFFICIAL PYTHON EXAMPLES", result)
            self.assertIn("_IFT_TransientTransportProblem_alpha", result)

    def test_paper_page_tools_sort_text_and_render_image(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sb = self.make_sandbox(root)
            doc = pymupdf.open()
            page = doc.new_page()
            # Insert in the wrong content-stream order. Positional sorting must
            # still present the upper line first.
            page.insert_text((72, 140), "BOTTOM PARAMETER")
            page.insert_text((72, 72), "TOP PARAMETER")
            doc.save(sb.work / "paper.pdf")
            doc.close()

            text = sb.read_paper_page(1)
            self.assertLess(text.index("TOP PARAMETER"),
                            text.index("BOTTOM PARAMETER"))
            status, image_url = sb.view_paper_page(1)
            self.assertIn("page-1.png", status)
            self.assertTrue(image_url.startswith("data:image/png;base64,"))
            self.assertTrue((sb.work / ".paper-pages" / "page-1.png").is_file())

    def test_visual_page_is_added_to_next_model_turn(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sb = self.make_sandbox(root)
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((72, 72), "A figure page")
            doc.save(sb.work / "paper.pdf")
            doc.close()
            received = []

            def call(messages, tools):
                received.append(copy.deepcopy(messages))
                if len(received) == 1:
                    self.assertIn(
                        "view_paper_page",
                        [t["function"]["name"] for t in tools])
                    return {"choices": [{"message": {"content": "", "tool_calls": [{
                        "id": "view", "function": {
                            "name": "view_paper_page",
                            "arguments": '{"page":1}'}}]}}],
                            "usage": {}}
                if len(received) == 2:
                    return {"choices": [{"message": {"content": "", "tool_calls": [{
                        "id": "continue1", "function": {
                            "name": "list_dir", "arguments": '{"path":"."}'}}]}}],
                            "usage": {}}
                return {"choices": [{"message": {"content": "done"}}],
                        "usage": {}}

            agent.run_agent(
                call, "system", sb, root / "transcript.jsonl", max_turns=3,
                price=None, finalization_turns=0, vision=True)
            visual = [m for m in received[1] if isinstance(m.get("content"), list)]
            self.assertEqual(len(visual), 1)
            self.assertEqual(visual[0]["content"][1]["type"], "image_url")
            self.assertFalse(any(
                isinstance(m.get("content"), list) for m in received[2]))
            self.assertTrue(any(
                "image supplied and inspected" in str(m.get("content", ""))
                for m in received[2]))


if __name__ == "__main__":
    unittest.main()
