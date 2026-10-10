from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_eval as subject


def reply(content: str, finish: str = "stop") -> dict:
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


BAD = '{"geometry": {"origin": [0.2 - 1e-6, 0.0]}, "results": {}}'
GOOD = '{"geometry": {"origin": [0.199999, 0.0]}, "results": {}}'


class JsonRepairTests(unittest.TestCase):
    def test_error_context_marks_position(self):
        context = subject.json_error_context("Here you go:\n" + BAD + "\nthanks")
        self.assertIn("[0.2 ⟪HERE⟫- 1e-6", context)
        self.assertIsNone(subject.json_error_context(GOOD + " trailing prose"))

    def test_instruction_carries_error_and_context(self):
        text = subject.format_repair_instruction(
            subject.UNPARSEABLE, "Expecting ',' delimiter", BAD, 1)
        self.assertIn("Expecting ',' delimiter", text)
        self.assertIn("⟪HERE⟫", text)

    def test_recover_json_repairs_once(self):
        sent = []

        def fake_call(endpoint, key, model, messages, *rest):
            sent.append(messages)
            return reply(GOOD), None

        first = reply(BAD)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(subject, "call_messages", fake_call):
            parsed, repairs = subject.recover_json(
                "e", "k", "m", [{"role": "user", "content": "prompt"}], first,
                subject.interpret(first), 2, 100, 0.0, 1.0, Path(tmp), log_prefix="")
            self.assertTrue((Path(tmp) / "attempt-1" / "raw.json").exists())
        self.assertEqual(parsed[0], subject.OK)
        self.assertEqual(parsed[2]["geometry"]["origin"][0], 0.199999)
        self.assertEqual(len(repairs), 1)
        self.assertEqual([m["role"] for m in sent[0]], ["user", "assistant", "user"])
        self.assertEqual(sent[0][1]["content"], BAD)

    def test_recover_json_skips_errors_and_disabled(self):
        def never(*_):
            raise AssertionError("must not call")

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(subject, "call_messages", never):
            for resp, turns in (({"error": "timeout"}, 2), (reply(BAD), 0),
                                (reply(GOOD), 2)):
                parsed, repairs = subject.recover_json(
                    "e", "k", "m", [], resp, subject.interpret(resp), turns,
                    100, 0.0, 1.0, Path(tmp), log_prefix="")
                self.assertEqual(repairs, [])


if __name__ == "__main__":
    unittest.main()
