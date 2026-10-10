#!/usr/bin/env python3
"""
agent.py -- the agentic arm: let the model work its way to a simulation.

The one-shot arm asks a model to write a correct oofempy script blind. Its first
three real failures were all API knowledge, not physics: a material that does not
exist, a required keyword it could not know about, a setup that segfaults. None
of that is what this benchmark is trying to measure.

The bindings cannot answer those questions -- every factory function documents
itself as `isoLE(*args, **kwargs) -> object`. The answers exist only in the OOFEM
source, as 2415 `#define _IFT_...` macros. So this arm gives the model the source
tree to read and a Python sandbox to try things in, and lets it converge.

    system prompt: scope + curve contract + API list   (small, fixed)
    tools:         list_dir, read_file, read_paper_page, view_paper_page,
                   grep, run_python, write_file, run_candidate, submit
    loop:          until submit, or the turn / wall-clock budget runs out

THE PAPER IS NOT IN CONTEXT. The one-shot prompt carries up to 400k characters of
paper text; across forty turns that is fatal. Here the complete layout-aware text
and original PDF are written to the workdir, so the model reads only the text or
rendered pages it needs.

COMPARABILITY. Different models take different numbers of turns, so a turn count
is not a fair axis on its own. Every turn's `usage` is accumulated and reported --
prompt tokens, completion tokens, and a cost when models.yaml carries a price --
so an agentic result is read next to what it spent, not instead of it.

CHEATING is possible here and is not prevented. The model can read reference/ and
fit to it without solving anything -- but it can equally read the values out of
the paper, so withholding the CSV would buy very little. What this does instead is
make it AUDITABLE: the full transcript is on disk, and `solver_evidence` in the
result records whether the final run actually produced oofem output. That is a
recorded signal, not a gate.
"""
from __future__ import annotations

import ast
import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

MAX_TOOL_CHARS = 6000        # per tool result, before truncation
MAX_GREP_HITS = 80
MAX_READ_LINES = 400
MAX_IDLE_TURNS = 3           # consecutive no-tool-call turns before giving up
# A degenerate turn: a very long message whose lines mostly repeat. models.yaml
# already records this for kit.deepseek-v4-flash at temperature 0.0 in the
# one-shot arm ("134k characters of the same sentence"), and it reproduces here
# -- one observed turn was 92k characters with "Wait, let me reconsider" 102
# times. Left alone it burns ~100k completion tokens per turn until the budget
# dies, so it is worth detecting and stopping on.
DEGENERATE_CHARS = 20000
DEGENERATE_UNIQUE_RATIO = 0.5


def _tools_spec(vision: bool = False) -> list:
    def fn(name, desc, props, required):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": required}}}
    tools = [
        fn("list_dir", "List a directory. Use it to explore the OOFEM source tree.",
           {"path": {"type": "string", "description": "directory path"}}, ["path"]),
        fn("read_file",
           "Read a text file. Returns at most %d lines from `start_line`."
           % MAX_READ_LINES,
           {"path": {"type": "string"},
            "start_line": {"type": "integer", "description": "1-based, default 1"}},
           ["path"]),
        fn("read_paper_page",
           "Read one 1-based page directly from paper.pdf using layout-aware "
           "positional text extraction. Use this for equations, tables, or a "
           "section whose paper.txt representation is scrambled.",
           {"page": {"type": "integer", "description": "1-based PDF page number"}},
           ["page"]),
        fn("grep",
           "Search files for a regular expression. This is how you find which "
           "keywords a material accepts: the OOFEM source defines them as "
           "`#define _IFT_<Class>_<keyword>`.",
           {"pattern": {"type": "string"},
            "path": {"type": "string", "description": "file or directory to search"}},
           ["pattern", "path"]),
        fn("inspect_oofem",
           "Look up one OOFEM class or factory name in the installed source. "
           "Returns matching official Python examples, registration/name lines, "
           "and the class's _IFT_ keyword definitions. Use this before guessing "
           "constructor spelling or keyword names.",
           {"symbol": {"type": "string", "description":
                       "class or factory name, for example transientTransport or concreteFcm"}},
           ["symbol"]),
        fn("run_python",
           "Run a Python snippet in the working directory, with oofempy "
           "importable. Use it to try things out before committing to them. "
           "Returns stdout and stderr.",
           {"code": {"type": "string"}}, ["code"]),
        fn("write_file",
           "Write a file in the working directory. Write your final simulation "
           "to `candidate.py`.",
           {"path": {"type": "string"}, "content": {"type": "string"}},
           ["path", "content"]),
        fn("run_candidate",
           "Run candidate.py from a clean directory and validate results.csv, "
           "plot.png, and all required curve ids. You must get a successful "
           "result after the last edit to candidate.py before submit will be "
           "accepted. Returns stdout, stderr, and validation errors.",
           {}, []),
        fn("submit",
           "Submit the exact candidate.py most recently validated successfully "
           "by run_candidate. Submission is refused if it is untested, failed, "
           "or has changed since validation.",
           {}, []),
    ]
    if vision:
        tools.insert(3, fn(
            "view_paper_page",
            "Render one 1-based page of paper.pdf and inspect it visually. Use "
            "this for geometry, figures, equations, or tables that text "
            "extraction does not preserve.",
            {"page": {"type": "integer", "description":
                      "1-based PDF page number"}},
            ["page"]))
    return tools


class Sandbox:
    """Paths the agent may touch. Read: the OOFEM tree and the workdir. Write:
    the workdir only. Everything resolves before it is compared, so `..` cannot
    walk out of either root."""

    def __init__(self, work: Path, oofem_root: Path, py: str, pythonpath: str,
                 spike_timeout: float, candidate_timeout: float | None = None,
                 curve_ids: list[str] | None = None):
        self.work = work.resolve()
        self.oofem_root = oofem_root.resolve()
        self.roots = [self.work, self.oofem_root]
        self.py = py
        self.pythonpath = pythonpath
        self.spike_timeout = spike_timeout
        self.candidate_timeout = candidate_timeout or spike_timeout
        self.curve_ids = set(curve_ids or [])
        self.tool_seconds = 0.0
        self.validated_hash: str | None = None
        self.candidate_dirty = False
        self.validation_seconds = 0.0
        self.validation_total_seconds = 0.0
        self.validation_attempts = 0
        self.last_validation: str | None = None

    def _resolve(self, path: str, write: bool = False) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.work / p
        p = p.resolve()
        allowed = [self.work] if write else self.roots
        for root in allowed:
            if p == root or root in p.parents:
                return p
        raise PermissionError(
            f"{p} is outside the allowed area "
            f"({'workdir' if write else 'workdir or OOFEM source tree'})")

    def list_dir(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_dir():
            return f"not a directory: {p}"
        rows = []
        for child in sorted(p.iterdir())[:300]:
            rows.append(f"{'d' if child.is_dir() else '-'} {child.name}")
        return "\n".join(rows) or "(empty)"

    def read_file(self, path: str, start_line: int = 1) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"not a file: {p}"
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError as e:
            return f"cannot read: {e}"
        start = max(1, int(start_line or 1))
        chunk = lines[start - 1:start - 1 + MAX_READ_LINES]
        head = f"{p} lines {start}-{start + len(chunk) - 1} of {len(lines)}\n"
        return head + "\n".join(f"{start + i:6d}  {ln}"
                                for i, ln in enumerate(chunk))

    def _paper_page(self, page: int):
        """Return (document, page object) for a validated 1-based page."""
        try:
            import pymupdf
        except ImportError as e:
            raise RuntimeError("PyMuPDF is unavailable") from e
        pdf = self.work / "paper.pdf"
        if not pdf.is_file():
            raise FileNotFoundError("paper.pdf is not available for this case")
        doc = pymupdf.open(pdf)
        if doc.needs_pass:
            doc.close()
            raise RuntimeError("paper.pdf requires a password")
        try:
            number = int(page)
        except (TypeError, ValueError) as e:
            doc.close()
            raise ValueError("page must be an integer") from e
        if number < 1 or number > len(doc):
            count = len(doc)
            doc.close()
            raise ValueError(f"page {number} is outside 1..{count}")
        return doc, doc[number - 1]

    def read_paper_page(self, page: int) -> str:
        doc, pdf_page = self._paper_page(page)
        try:
            body = pdf_page.get_text("text", sort=True).strip()
            count = len(doc)
        finally:
            doc.close()
        return (f"paper.pdf page {int(page)} of {count}, layout-aware text\n\n"
                + (body or "(page has no extractable text; use the visual page tool)"))

    def view_paper_page(self, page: int) -> tuple[str, str]:
        """Render a PDF page and return its audit path and an image data URL."""
        doc, pdf_page = self._paper_page(page)
        try:
            # An A4 page at this scale is sharp enough for equations and labels,
            # while staying below the image gates used by the endpoint.
            max_side = 1600
            dpi = max(72, min(200, int(
                72 * max_side / max(pdf_page.rect.width, pdf_page.rect.height))))
            pix = pdf_page.get_pixmap(dpi=dpi, alpha=False)
            raw = pix.tobytes("png")
            count = len(doc)
        finally:
            doc.close()
        out_dir = self.work / ".paper-pages"
        out_dir.mkdir(exist_ok=True)
        out = out_dir / f"page-{int(page)}.png"
        out.write_bytes(raw)
        url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        return (f"rendered paper.pdf page {int(page)} of {count} as "
                f"{out.relative_to(self.work)} ({pix.width}x{pix.height})", url)

    def grep(self, pattern: str, path: str) -> str:
        p = self._resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"bad regex: {e}"
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
        hits = []
        for f in files:
            try:
                for i, ln in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if rx.search(ln):
                        hits.append(f"{f}:{i}: {ln.strip()[:200]}")
                        if len(hits) >= MAX_GREP_HITS:
                            return "\n".join(hits) + f"\n[stopped at {MAX_GREP_HITS} hits]"
            except (OSError, UnicodeDecodeError):
                continue
        return "\n".join(hits) or "no matches"

    def inspect_oofem(self, symbol: str) -> str:
        """Focused, source-backed API discovery in one tool call.

        This deliberately does not encode OOFEM knowledge in the harness. It
        collects the binding's own examples and input-field definitions so the
        agent can discover the installed API without spending a dozen turns on
        broad directory walks and greps.
        """
        symbol = (symbol or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            return "symbol must be one identifier (letters, digits, underscore)"
        needle = symbol.casefold()
        normalized = re.sub(r"[^a-z0-9]", "", needle)
        sections = []

        examples = []
        tests = self.oofem_root / "bindings" / "python" / "tests"
        if tests.is_dir():
            for path in sorted(tests.rglob("*.py")):
                lines = path.read_text(errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if needle in line.casefold():
                        lo, hi = max(1, i - 2), min(len(lines), i + 2)
                        shown_path = path.relative_to(self.oofem_root)
                        snippet = "\n".join(
                            f"{shown_path}:{n}: {lines[n - 1].strip()}"
                            for n in range(lo, hi + 1))
                        examples.append(snippet)
                        if len(examples) >= 8:
                            break
                if len(examples) >= 8:
                    break
        if examples:
            sections.append("OFFICIAL PYTHON EXAMPLES\n" + "\n--\n".join(examples))

        source_hits = []
        src = self.oofem_root / "src"
        allowed_suffixes = {".h", ".c", ".cc", ".cpp", ".C"}
        if src.is_dir():
            preferred = []
            others = []
            for path in src.rglob("*"):
                if not path.is_file() or path.suffix not in allowed_suffixes:
                    continue
                stem = re.sub(r"[^a-z0-9]", "", path.stem.casefold())
                (preferred if normalized in stem or stem in normalized else others).append(path)
            for path in preferred + others:
                try:
                    lines = path.read_text(errors="replace").splitlines()
                except OSError:
                    continue
                direct = [i for i, line in enumerate(lines, 1)
                          if needle in line.casefold()]
                if not direct:
                    continue
                macros = [(i, line.strip()) for i, line in enumerate(lines, 1)
                          if "#define _IFT_" in line]
                picked = macros or [(i, lines[i - 1].strip()) for i in direct[:8]]
                shown_path = path.relative_to(self.oofem_root)
                source_hits.extend(f"{shown_path}:{i}: {line}" for i, line in picked)
                if len(source_hits) >= 60:
                    break
        if source_hits:
            sections.append("SOURCE NAMES AND ACCEPTED KEYWORDS\n"
                            + "\n".join(source_hits[:60]))

        binding_hits = []
        for path in (self.oofem_root / "bindings" / "python").glob("*.h"):
            lines = path.read_text(errors="replace").splitlines()
            shown_path = path.relative_to(self.oofem_root)
            binding_hits.extend(
                f"{shown_path}:{i}: {line.strip()}"
                for i, line in enumerate(lines, 1) if needle in line.casefold())
        if binding_hits:
            sections.append("PYTHON BINDING\n" + "\n".join(binding_hits[:20]))
        return "\n\n".join(sections) if sections else f"no source matches for {symbol}"

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if p == self.work / "candidate.py":
            # Validation belongs to the exact bytes that were executed. Any
            # edit, including rewriting identical-looking source, requires the
            # current file to be checked again before submit.
            self.validated_hash = None
            self.candidate_dirty = True
        return f"wrote {p.relative_to(self.work)} ({len(content)} chars)"

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _process_output(out: str, limit: int = 5000) -> str:
        """Keep both setup messages and the traceback tail."""
        if len(out) <= limit:
            return out or "(no output)"
        head = 1200
        tail = limit - head
        return (out[:head] + f"\n[... {len(out) - limit} chars omitted ...]\n"
                + out[-tail:])

    @staticmethod
    def _status(rc: int) -> str:
        if rc >= 0:
            return f"exit {rc}"
        try:
            name = signal.Signals(-rc).name
        except ValueError:
            name = f"signal {-rc}"
        return f"killed by {name}"

    def _check_results(self, stage: Path) -> list[str]:
        errors = []
        results = stage / "results.csv"
        plot = stage / "plot.png"
        if not results.is_file():
            errors.append("did not write results.csv")
        else:
            seen = set()
            rows = 0
            try:
                with results.open(newline="", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    required_columns = {"curve", "x", "y"}
                    if not required_columns.issubset(set(reader.fieldnames or [])):
                        errors.append("results.csv must contain curve,x,y columns")
                    else:
                        for line_no, row in enumerate(reader, 2):
                            curve = (row.get("curve") or "").strip()
                            try:
                                x = float(row.get("x", ""))
                                y = float(row.get("y", ""))
                            except (TypeError, ValueError):
                                errors.append(
                                    f"results.csv line {line_no} has non-numeric x/y")
                                break
                            if not curve or not math.isfinite(x) or not math.isfinite(y):
                                errors.append(
                                    f"results.csv line {line_no} has an empty curve or non-finite value")
                                break
                            seen.add(curve)
                            rows += 1
            except (OSError, csv.Error) as e:
                errors.append(f"cannot parse results.csv: {e}")
            if rows == 0 and not errors:
                errors.append("results.csv has no data rows")
            missing = sorted(self.curve_ids - seen)
            if missing:
                errors.append("missing required curve ids: " + ", ".join(missing))
        if not plot.is_file() or plot.stat().st_size == 0:
            errors.append("did not write a non-empty plot.png")
        return errors

    def run_candidate(self) -> str:
        """Execute candidate.py from a clean, reproducible staging directory."""
        candidate = self.work / "candidate.py"
        self.validation_attempts += 1
        self.validated_hash = None
        if not candidate.is_file():
            self.last_validation = "candidate.py does not exist"
            return "[validation failed]\ncandidate.py does not exist"
        # This exact revision is about to be checked. A failed run is a current
        # validation failure, not a dirty/unvalidated edit; a later write flips
        # candidate_dirty back to True while preserving this diagnostic.
        self.candidate_dirty = False
        try:
            ast.parse(candidate.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as e:
            self.last_validation = f"candidate.py is not valid Python: {e}"
            return f"[validation failed]\n{self.last_validation}"

        # This directory is outside the agent's writable root. Each validation
        # starts clean, so a candidate cannot pass using files left by a spike.
        stage = self.work.parent / ".candidate-validation"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        shutil.copy2(candidate, stage / "candidate.py")
        for name in ("paper.txt", "scope.md"):
            src = self.work / name
            if src.is_file():
                shutil.copy2(src, stage / name)
        reference = self.work / "reference"
        if reference.is_dir():
            shutil.copytree(reference, stage / "reference")

        env = dict(os.environ)
        env["PYTHONPATH"] = self.pythonpath
        env["MPLBACKEND"] = "Agg"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONFAULTHANDLER"] = "1"
        t0 = time.time()
        timed_out = False
        try:
            r = subprocess.run(
                [self.py, "-X", "faulthandler", str(stage / "candidate.py")],
                cwd=str(stage), env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=self.candidate_timeout,
                start_new_session=True)
            out = (r.stdout or b"").decode(errors="replace")
            rc = r.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            raw = e.stdout or b""
            out = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
            rc = -1
        elapsed = time.time() - t0
        self.tool_seconds += elapsed
        self.validation_seconds = round(elapsed, 1)
        self.validation_total_seconds = round(
            self.validation_total_seconds + elapsed, 1)

        # Keep the full final validation log for audit; the tool response below
        # is shortened to avoid filling the next model turn with solver chatter.
        log = self.work.parent / "run.log"
        log.write_text(out + ("\n[killed: timeout]\n" if timed_out else ""),
                       encoding="utf-8")
        shown = self._process_output(out)
        if timed_out:
            self.last_validation = (
                f"timed out after {self.candidate_timeout:.0f}s")
            return f"[validation failed: {self.last_validation}]\n{shown}"
        if rc != 0:
            self.last_validation = self._status(rc)
            return f"[validation failed: {self.last_validation}]\n{shown}"

        errors = self._check_results(stage)
        if errors:
            self.last_validation = "; ".join(errors)
            return ("[validation failed: process exited 0]\n- "
                    + "\n- ".join(errors) + f"\n\n{shown}")

        # Preserve only contract outputs from the clean run. These exact files
        # are what run_oofem.py scores after submit succeeds.
        shutil.copy2(stage / "results.csv", self.work / "results.csv")
        shutil.copy2(stage / "plot.png", self.work / "plot.png")
        self.validated_hash = self._hash(candidate)
        self.candidate_dirty = False
        self.last_validation = "ok"
        return (f"[validation successful: exit 0, {self.validation_seconds:.1f}s]\n"
                f"results.csv and plot.png are present; all "
                f"{len(self.curve_ids)} required curve ids were found.\n{shown}")

    def is_validated(self) -> bool:
        candidate = self.work / "candidate.py"
        return (candidate.is_file() and self.validated_hash is not None
                and self._hash(candidate) == self.validated_hash)

    def submit(self) -> tuple[bool, str]:
        if not (self.work / "candidate.py").is_file():
            return False, "submission refused: candidate.py does not exist"
        if not self.is_validated():
            if self.candidate_dirty and self.last_validation:
                detail = ("candidate.py changed after its last validation; "
                          f"previous result: {self.last_validation}")
            elif self.candidate_dirty:
                detail = "candidate.py has not been validated after its last edit"
            else:
                detail = self.last_validation or "candidate.py has not been tested"
            return (False, "submission refused: run run_candidate successfully "
                    f"after the last edit ({detail})")
        return True, "submitted: validated candidate.py accepted"

    def run_python(self, code: str) -> str:
        spike = self.work / ".spike.py"
        spike.write_text(code, encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = self.pythonpath
        env["MPLBACKEND"] = "Agg"
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONFAULTHANDLER"] = "1"
        t0 = time.time()
        try:
            r = subprocess.run([self.py, "-X", "faulthandler", str(spike)],
                               cwd=str(self.work), env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=self.spike_timeout, start_new_session=True)
            out = (r.stdout or b"").decode(errors="replace")
            rc = r.returncode
        except subprocess.TimeoutExpired:
            self.tool_seconds += time.time() - t0
            return f"[timed out after {self.spike_timeout:.0f}s]"
        self.tool_seconds += time.time() - t0
        status = ("exit 0" if rc == 0 else
                  f"exit {rc}" if rc > 0 else f"killed by signal {-rc}")
        return f"[{status}]\n{out or '(no output)'}"


def dispatch(sb: Sandbox, name: str, args: dict) -> str:
    try:
        if name == "list_dir":
            return sb.list_dir(args["path"])
        if name == "read_file":
            return sb.read_file(args["path"], args.get("start_line", 1))
        if name == "read_paper_page":
            return sb.read_paper_page(args["page"])
        if name == "grep":
            return sb.grep(args["pattern"], args["path"])
        if name == "inspect_oofem":
            return sb.inspect_oofem(args["symbol"])
        if name == "write_file":
            return sb.write_file(args["path"], args["content"])
        if name == "run_python":
            return sb.run_python(args["code"])
        if name == "run_candidate":
            return sb.run_candidate()
        return f"unknown tool: {name}"
    except PermissionError as e:
        return f"refused: {e}"
    except KeyError as e:
        return f"missing argument: {e}"
    except Exception as e:                      # a bad call must not end the run
        return f"{type(e).__name__}: {e}"


def is_degenerate(text: str) -> bool:
    """A long message that keeps saying the same thing. Cheap to check, and the
    alternative is spending the whole budget on it."""
    if len(text) < DEGENERATE_CHARS:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 20:
        return False
    return len(set(lines)) / len(lines) < DEGENERATE_UNIQUE_RATIO


def truncate(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... truncated, {len(text) - limit} more chars]"


def is_transient_api_failure(resp: dict) -> bool:
    """Whether repeating the same turn can plausibly recover."""
    if "error" in resp:
        return True
    if "http_error" not in resp:
        return False
    try:
        status = int(resp["http_error"])
    except (TypeError, ValueError):
        status = 0
    detail = str(resp.get("detail", "")).casefold()
    return (status in {408, 429, 500, 502, 503, 504}
            or "server connection error" in detail
            or "temporarily unavailable" in detail)


def compact_consumed_images(messages: list) -> None:
    """Do not resend rendered PDF bytes after the model has seen them once.

    Images are useful for one inspection turn, but retaining their base64 data
    in a forty-turn chat would repeatedly charge context for the same page.
    """
    for message in messages:
        content = message.get("content")
        if (message.get("role") == "user" and isinstance(content, list)
                and any(item.get("type") == "image_url" for item in content
                        if isinstance(item, dict))):
            message["content"] = (
                "[Rendered paper page image supplied and inspected in the "
                "previous model turn. Request the page again if needed.]")


def run_agent(call, system: str, sb: Sandbox, transcript: Path,
              max_turns: int, price: dict | None,
              finalization_turns: int = 4, api_retries: int = 2,
              vision: bool = False) -> dict:
    """Drive the tool loop. `call(messages, tools)` performs one completion and
    returns the raw response dict."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content":
                 "Begin. Explore what you need, then write candidate.py, call "
                 "run_candidate until it succeeds, and call submit."}]
    tools = _tools_spec(vision=vision)
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "turns": 0,
              "tool_calls": 0, "submitted": False, "validated": False,
              "stop": None, "nudges": 0, "api_retries": 0,
              "finalization_turns": 0}
    idle = 0        # consecutive turns that called no tool
    finalization_turns = max(0, int(finalization_turns))
    api_retries = max(0, int(api_retries))
    total_turns = max_turns + finalization_turns
    draft_turn = max(3, (max_turns + 2) // 3)
    hard_draft_turn = max(draft_turn + 2, max_turns // 2)

    with transcript.open("w", encoding="utf-8") as log:
        for turn in range(total_turns):
            if turn >= max_turns and not (sb.work / "candidate.py").exists():
                totals["stop"] = (f"turn budget exhausted ({max_turns}); "
                                  "no candidate for finalization")
                break

            resp = {}
            for retry in range(api_retries + 1):
                resp = call(messages, tools)
                if not ("error" in resp or "http_error" in resp):
                    break
                if retry >= api_retries or not is_transient_api_failure(resp):
                    break
                totals["api_retries"] += 1
                log.write(json.dumps({"turn": turn, "api_retry": retry + 1,
                                      "error": resp}) + "\n")
                time.sleep(min(2 ** retry, 4))
            if "error" in resp or "http_error" in resp:
                totals["stop"] = f"api: {resp.get('error') or resp.get('detail')}"[:200]
                log.write(json.dumps({"turn": turn, "error": resp}) + "\n")
                break

            # A successful response has consumed any page images currently in
            # context. Keep only a textual marker for later turns.
            compact_consumed_images(messages)

            usage = resp.get("usage") or {}
            totals["prompt_tokens"] += usage.get("prompt_tokens") or 0
            totals["completion_tokens"] += usage.get("completion_tokens") or 0
            totals["turns"] = turn + 1
            if turn >= max_turns:
                totals["finalization_turns"] += 1

            choice = (resp.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            calls = msg.get("tool_calls") or []
            messages.append({"role": "assistant",
                             "content": msg.get("content"),
                             "tool_calls": calls} if calls else
                            {"role": "assistant", "content": msg.get("content") or ""})
            log.write(json.dumps({"turn": turn, "assistant": msg.get("content"),
                                  "tool_calls": [c.get("function", {}).get("name")
                                                 for c in calls],
                                  "usage": usage}) + "\n")

            if not calls:
                # A turn of pure reasoning is not a stop. Models narrate their
                # plan and expect to continue; ending the run there threw away
                # sixteen turns of successful exploration in the first real run.
                # Nudge, and only give up if it keeps happening.
                content = msg.get("content") or ""
                if is_degenerate(content):
                    totals["stop"] = (f"degenerate output: {len(content)} chars, "
                                      f"mostly repeated lines")
                    totals["degenerate"] = True
                    break
                idle += 1
                totals["nudges"] += 1
                if idle > MAX_IDLE_TURNS:
                    totals["stop"] = (f"no tool call for {idle} consecutive "
                                      f"turns")
                    break
                messages.append({"role": "user", "content":
                                 "Continue. Call a tool, or write candidate.py, "
                                 "validate it with run_candidate, and submit."})
                log.write(json.dumps({"turn": turn, "nudge": idle}) + "\n")
                continue
            idle = 0

            wrote_candidate = False
            viewed_pages = []
            for c in calls:
                fname = (c.get("function") or {}).get("name") or ""
                raw = (c.get("function") or {}).get("arguments") or "{}"
                try:
                    fargs = json.loads(raw)
                except json.JSONDecodeError:
                    fargs = {}
                totals["tool_calls"] += 1
                if fname == "write_file" and fargs.get("path") == "candidate.py":
                    wrote_candidate = True

                if fname == "submit":
                    accepted, result = sb.submit()
                    totals["submitted"] = accepted
                    totals["validated"] = accepted
                elif fname == "view_paper_page" and vision:
                    try:
                        result, image_url = sb.view_paper_page(fargs["page"])
                        viewed_pages.append((int(fargs["page"]), image_url))
                    except Exception as e:
                        result = f"{type(e).__name__}: {e}"
                else:
                    result = truncate(dispatch(sb, fname, fargs))

                messages.append({"role": "tool", "tool_call_id": c.get("id"),
                                 "content": result})
                log.write(json.dumps({"turn": turn, "tool": fname,
                                      "args": {k: truncate(str(v), 400)
                                               for k, v in fargs.items()},
                                      "result": truncate(result, 2000)}) + "\n")
            if viewed_pages:
                content = [{"type": "text", "text":
                            "Rendered paper page(s) requested by the previous "
                            "tool call. Inspect the images directly; use "
                            "read_paper_page for searchable text."}]
                content.extend({"type": "image_url", "image_url": {"url": url}}
                               for _, url in viewed_pages)
                messages.append({"role": "user", "content": content})
                log.write(json.dumps({"turn": turn, "paper_page_images":
                                      [page for page, _ in viewed_pages]}) + "\n")
            # Tool calls in one assistant message are processed in order. If a
            # model edits candidate.py after an accepted submit in that same
            # batch, the edit makes the submission dirty again.
            if totals["submitted"] and not sb.is_validated():
                totals["submitted"] = False
                totals["validated"] = False
            if totals["submitted"]:
                totals["stop"] = "submitted"
                break

            # Turn pressure. The first version is due after one third of the
            # budget has ELAPSED, leaving the remaining two thirds for integrated
            # execution and repair. The old condition fired when one third was
            # LEFT, which is why most candidates first appeared around turn 27/40.
            elapsed = turn + 1
            left = max(0, max_turns - elapsed)
            grace_left = max(0, total_turns - elapsed)
            candidate_exists = (sb.work / "candidate.py").exists()

            if elapsed < max_turns and not candidate_exists:
                if elapsed >= hard_draft_turn:
                    messages.append({"role": "user", "content":
                        f"Turn {elapsed}/{max_turns}: candidate.py still does not "
                        f"exist. Stop broad exploration and write a runnable first "
                        f"version now. Then use the remaining {left} turns for "
                        f"run_candidate and repairs."})
                elif elapsed >= draft_turn:
                    messages.append({"role": "user", "content":
                        f"Turn {elapsed}/{max_turns}: the first candidate.py is due "
                        f"now. Write a minimal integrated version, run it, and "
                        f"improve it with the remaining {left} turns."})
            elif (elapsed < max_turns and candidate_exists
                  and not sb.is_validated()
                  and (wrote_candidate or elapsed >= hard_draft_turn)):
                messages.append({"role": "user", "content":
                    f"candidate.py is dirty and {left} normal turn(s) remain. "
                    f"Run the complete candidate next; repair its concrete "
                    f"failure instead of returning to broad exploration."})
            elif elapsed == max_turns and candidate_exists:
                messages.append({"role": "user", "content":
                    f"The {max_turns}-turn exploration budget is exhausted. You "
                    f"have {grace_left} finalization turn(s). Only repair "
                    f"candidate.py, call run_candidate, and submit. Do not start "
                    f"new research."})
            elif elapsed > max_turns and not sb.is_validated() and grace_left:
                messages.append({"role": "user", "content":
                    f"Finalization only: {grace_left} turn(s) remain. Fix the "
                    f"last validation failure, run_candidate, then submit."})
        else:
            totals["stop"] = (f"turn budget exhausted ({max_turns} + "
                              f"{finalization_turns} finalization)")

    if price:
        totals["cost_usd"] = round(
            totals["prompt_tokens"] / 1e6 * price.get("in_per_mtok", 0.0)
            + totals["completion_tokens"] / 1e6 * price.get("out_per_mtok", 0.0), 6)
    else:
        totals["cost_usd"] = None
    totals["tool_seconds"] = round(sb.tool_seconds, 1)
    totals["validated"] = bool(totals["submitted"] and sb.is_validated())
    totals["validation_seconds"] = sb.validation_seconds
    totals["validation_total_seconds"] = sb.validation_total_seconds
    totals["validation_attempts"] = sb.validation_attempts
    totals["last_validation"] = sb.last_validation
    totals["candidate_dirty"] = sb.candidate_dirty
    return totals
