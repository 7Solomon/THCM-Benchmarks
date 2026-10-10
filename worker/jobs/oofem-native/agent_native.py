#!/usr/bin/env python3
"""
agent_native.py -- the agentic arm, for input decks instead of oofempy.

A duplicate of worker/jobs/oofem/agent.py, deliberately: the two jobs are meant
to be compared, and a change to one arm's loop must not silently move the other
one's numbers. The loop mechanics (turn pressure, finalization turns, retries,
image compaction, degenerate-output stop, accounting) are the same; what
differs is the sandbox:

    candidate:  model.in and/or make_deck.py, plus postprocess.py
                (see pipeline.py) -- not one candidate.py
    tools:      list_dir, read_file, read_paper_page, view_paper_page, grep,
                inspect_record, search_manual, run_oofem, run_python,
                write_file, run_candidate, submit

`inspect_record` replaces `inspect_oofem`. For a deck the question is not "what
does this factory function take" but "what attributes does this RECORD accept,
including the ones inherited from its base classes, and how do OOFEM's own test
decks write it". It answers that from the source and tests/, in one call,
together with what OOFEM's own manuals say about the record (manual.py):
parameter meanings, units, typical values, supported modes, side numbering.
`search_manual` finds manual sections by topic when there is no record name
to look up yet ("convection", "time step function", "side numbering").

`run_oofem` is the deck spike: the executable on any deck in the workdir, with
its error and unread-keyword lines pulled to the top. `run_python` stays, for
the mesh generator and the parser -- but refuses oofempy / oofem imports, so a
spike cannot quietly go back to the bindings.

THE PAPER IS NOT IN CONTEXT, and CHEATING is auditable rather than prevented --
both exactly as in the oofem job. `solver_evidence` here is stronger than a
string match: the validated run records whether oofem exited 0 and which files
it wrote.
"""
from __future__ import annotations

import base64
import csv
import json
import math
import re
import shutil
import time
from pathlib import Path

import manual as MANUAL
import pipeline as PIPE

MAX_TOOL_CHARS = 6000        # per tool result, before truncation
MAX_GREP_HITS = 80
MAX_READ_LINES = 400
MAX_IDLE_TURNS = 3           # consecutive no-tool-call turns before giving up
# A degenerate turn: a very long message whose lines mostly repeat. See the
# oofem job's agent.py for the kit.deepseek-v4-flash observation behind it.
DEGENERATE_CHARS = 20000
DEGENERATE_UNIQUE_RATIO = 0.5

CANDIDATE_NAMES = (PIPE.GENERATOR, PIPE.DECK, PIPE.POST)
CANDIDATE_DESC = "model.in (or make_deck.py) + postprocess.py"


def _tools_spec(vision: bool = False) -> list:
    def fn(name, desc, props, required):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": required}}}
    tools = [
        fn("list_dir", "List a directory. Use it to explore the OOFEM source "
           "tree and its tests/ decks.",
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
           "Search files for a regular expression. Record attributes are "
           "defined in the OOFEM source as `#define _IFT_<Class>_<attr> "
           "\"<keyword>\"`; tests/**/*.in shows how decks use them.",
           {"pattern": {"type": "string"},
            "path": {"type": "string", "description": "file or directory to search"}},
           ["pattern", "path"]),
        fn("inspect_record",
           "Look up one OOFEM input record by its deck keyword (e.g. isole, "
           "transienttransport, tr1ht, simplecs, gnuplot) or C++ class name. "
           "Returns the class, every attribute keyword it accepts INCLUDING "
           "those inherited from base classes, the record's documentation from "
           "OOFEM's manuals (parameter meanings, units, typical values, "
           "supported modes, element side numbering), and example lines from "
           "OOFEM's own test decks. Use this before guessing attribute names.",
           {"name": {"type": "string", "description":
                     "record keyword or class name"}},
           ["name"]),
        fn("search_manual",
           "Search OOFEM's manuals (input format, element library, material "
           "library, user manual) by topic, e.g. 'convection edge load', "
           "'time step function', 'export gauss point', 'hydration heat'. "
           "Returns matching sections with their file:line, the record "
           "keywords they document, and a matching line; read one with "
           "read_file.",
           {"query": {"type": "string", "description": "words to search for"}},
           ["query"]),
        fn("run_oofem",
           "Run the oofem executable on a deck in the working directory "
           "(`oofem -f <deck>`). Returns the exit status, OOFEM's error lines, "
           "unread-keyword warnings, the files it wrote, and the tail of its "
           "output. Use small decks here to test records before building the "
           "full model.",
           {"deck": {"type": "string", "description":
                     "deck file in the working directory, default model.in"}},
           []),
        fn("run_python",
           "Run a Python snippet in the working directory (numpy, scipy, "
           "matplotlib, gmsh, meshio available). Use it to test a mesh "
           "generator or a parser for OOFEM's output files. Importing oofempy "
           "or oofem is refused. Returns stdout and stderr.",
           {"code": {"type": "string"}}, ["code"]),
        fn("write_file",
           "Write a file in the working directory. The candidate is model.in "
           "(or make_deck.py, which writes it) plus postprocess.py.",
           {"path": {"type": "string"}, "content": {"type": "string"}},
           ["path", "content"]),
        fn("run_candidate",
           "Run the candidate exactly as the harness will, in a clean "
           "directory: make_deck.py (if present), then oofem -f model.in, then "
           "postprocess.py. Validates results.csv, plot.png, and all required "
           "curve ids. You must get a successful result after the last edit "
           "to any candidate file before submit will be accepted.",
           {}, []),
        fn("submit",
           "Submit the exact candidate files most recently validated "
           "successfully by run_candidate. Submission is refused if they are "
           "untested, failed, or have changed since validation.",
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


# --- the record index behind inspect_record ---------------------------------

_INDEX: dict = {}
_NAME_DEF = re.compile(r'#define\s+_IFT_(\w+?)_Name\s+"([^"]+)"')
_IFT_DEF = re.compile(r'#define\s+_IFT_(\w+)\s+"([^"]*)"')
# Newer code declares keywords as `ParamKey Element::IPK_Element_nip("nip")`,
# and some records read a literal: `IR_GIVE_OPTIONAL_FIELD(ir, n, "npoints")`.
_PARAMKEY = re.compile(r'ParamKey\s+(\w+)\s*::\s*IPK_(\w+)\s*\(\s*"([^"]+)"')
_LITERAL = re.compile(r'IR_GIVE_(?:OPTIONAL_)?FIELD\s*\(\s*\w+\s*,\s*[^,]+,\s*"([^"]+)"')
_METHOD = re.compile(r"^[\w:<>\*&\s]*?\b(\w+)\s*::\s*~?\w+\s*\(")
_CLASS = re.compile(
    r"^\s*class\s+(?:OOFEM_EXPORT\s+)?(\w+)\s*(?:final\s*)?:\s*([^{;]+)\{", re.M)


def record_index(oofem_root: Path) -> dict:
    """Keyword -> class, class -> header, class -> base classes, and every
    attribute keyword: `_IFT_` defines and `ParamKey`s, plus string literals
    read in a class's own methods (filed as `<Class>_<keyword>`). Built once
    per OOFEM checkout."""
    key = str(oofem_root)
    if key in _INDEX:
        return _INDEX[key]
    idx = {"by_name": {}, "header": {}, "parents": {}, "defines": []}
    src = oofem_root / "src"
    if src.is_dir():
        for path in sorted(src.rglob("*.h")):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            rel = str(path.relative_to(oofem_root))
            for cls, name in _NAME_DEF.findall(text):
                idx["by_name"].setdefault(name.lower(), cls)
                idx["header"].setdefault(cls, rel)
            for i, ln in enumerate(text.splitlines(), 1):
                m = _IFT_DEF.search(ln)
                if m:
                    idx["defines"].append((m.group(1), m.group(2), f"{rel}:{i}"))
            for cls, bases in _CLASS.findall(text):
                parents = re.findall(r"(?:public|protected|private)\s+(\w+)", bases)
                if parents:
                    idx["parents"].setdefault(cls, parents)
                    idx["header"].setdefault(cls, rel)
        for path in sorted(src.rglob("*")):
            if path.suffix not in (".C", ".h"):
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if "ParamKey" not in text and "IR_GIVE" not in text:
                continue
            rel = str(path.relative_to(oofem_root))
            owner = None
            for i, ln in enumerate(text.splitlines(), 1):
                m = _METHOD.match(ln)
                if m and not ln.startswith((" ", "\t")):
                    owner = m.group(1)
                for cls, attr, kw in _PARAMKEY.findall(ln):
                    idx["defines"].append((attr, kw, f"{rel}:{i}"))
                if owner and "IR_GIVE" in ln:
                    for kw in _LITERAL.findall(ln):
                        idx["defines"].append((f"{owner}_{kw}", kw, f"{rel}:{i}"))
    _INDEX[key] = idx
    return idx


def _attrs_of(idx: dict, cls: str) -> list:
    """(attr, keyword, where) for `_IFT_<cls>_<attr>`. The trailing underscore
    keeps `Material_` from claiming `MaterialStatus_...`."""
    prefix = cls + "_"
    return [(macro[len(prefix):], kw, where)
            for macro, kw, where in idx["defines"]
            if macro.startswith(prefix) and macro != prefix + "Name"]


class Sandbox:
    """Paths the agent may touch. Read: the OOFEM tree and the workdir. Write:
    the workdir only. Everything resolves before it is compared, so `..` cannot
    walk out of either root."""

    def __init__(self, work: Path, oofem_root: Path, toolchain: PIPE.Toolchain,
                 spike_timeout: float, candidate_timeout: float | None = None,
                 curve_ids: list[str] | None = None,
                 manual_root: Path | None = None):
        self.work = work.resolve()
        self.oofem_root = oofem_root.resolve()
        self.manual_root = Path(manual_root or MANUAL.MANUAL_DIR).resolve()
        self.roots = [self.work, self.oofem_root, self.manual_root]
        self.tc = toolchain
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
        self.last_result: PIPE.Result | None = None
        self.oofem_spikes = 0

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
            f"({'workdir' if write else 'workdir, OOFEM source tree or manuals'})")

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

    def inspect_record(self, name: str) -> str:
        """Source-backed record lookup in one tool call: class, own and
        inherited attribute keywords, and usage in OOFEM's own test decks.

        Like inspect_oofem, this encodes no OOFEM knowledge in the harness; it
        only gathers what the checkout already says, so the agent does not
        spend a dozen turns on grep to learn what one record accepts.
        """
        name = (name or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\-]*", name):
            return "name must be one identifier (letters, digits, underscore)"
        idx = record_index(self.oofem_root)
        cls = idx["by_name"].get(name.lower())
        if cls is None:
            cls = next((c for c in idx["header"] if c.lower() == name.lower()), None)
        sections = []
        deck_word = name.lower()

        if cls:
            keyword = next((k for k, c in idx["by_name"].items() if c == cls), None)
            deck_word = (keyword or name).lower()
            sections.append(f"RECORD `{keyword or '?'}` = class {cls} "
                            f"({idx['header'].get(cls, '?')})")
            # Depth-first, primary base first: elements derive from half a
            # dozen *Interface mix-ins, and a breadth-first walk used up its
            # budget on them before reaching Element (nodes, mat, nip).
            seen, chain, stack = set(), [], [cls]
            while stack and len(chain) < 24:
                c = stack.pop()
                if c in seen:
                    continue
                seen.add(c)
                chain.append(c)
                stack.extend(reversed(idx["parents"].get(c, [])))
            shown, taken = 0, set()
            for c in chain:
                # Grouped per header, keywords only: the macro names add
                # nothing a deck needs, and the room goes to the manual.
                by_file: dict = {}
                for _, kw, where in _attrs_of(idx, c):
                    if kw.lower() in taken:
                        continue
                    taken.add(kw.lower())
                    path, line = where.rsplit(":", 1)
                    by_file.setdefault(path, []).append((int(line), kw))
                if not by_file:
                    continue
                label = "OWN ATTRIBUTES" if c == cls else f"INHERITED FROM {c}"
                for path, rows in by_file.items():
                    first = min(ln for ln, _ in rows)
                    sections.append(f"{label}  ({path}:{first})\n  "
                                    + " ".join(kw for _, kw in rows[:80]))
                    shown += len(rows)
            if not shown:
                sections.append("(no _IFT_ attribute definitions found for this "
                                "class or its bases)")
            sections.append("CLASS CHAIN: " + " -> ".join(
                c for c in chain
                if c == cls or not c.endswith("Interface") or _attrs_of(idx, c)))

        examples, files = [], set()
        tests = self.oofem_root / "tests"
        if tests.is_dir():
            for path in sorted(tests.rglob("*.in")):
                try:
                    lines = path.read_text(errors="replace").splitlines()
                except OSError:
                    continue
                for i, line in enumerate(lines, 1):
                    toks = line.split()
                    if toks and toks[0].lower() == deck_word and path not in files:
                        files.add(path)
                        examples.append(f"{path.relative_to(self.oofem_root)}:{i}: "
                                        f"{line.strip()[:240]}")
                        break
                if len(examples) >= 8:
                    break
        if examples:
            sections.append("USED IN OOFEM TEST DECKS\n" + "\n".join(examples))

        # The manual goes right after the RECORD line, sized to what the tool
        # result has left: attributes and examples are never cut for it.
        docs = MANUAL.sections_for(deck_word, name, cls or "",
                                   root=self.manual_root)
        if docs:
            # Any keyword the source defines anywhere counts as known: records
            # also read attributes through members (a material's elastic
            # part reads `E`), which the class chain does not show.
            known = {kw.lower() for _, kw, _ in idx["defines"]} if cls else set()
            room = MAX_TOOL_CHARS - 200 - sum(len(x) + 2 for x in sections)
            sections.insert(1 if cls else 0,
                            self._manual_block(docs, deck_word, known,
                                               min(2600, max(700, room))))
        if not sections:
            return (f"no record named {name}: not a registered keyword or "
                    f"class, no manual section documents it, and no test deck "
                    f"starts a line with it. Try search_manual.")
        if not cls:
            sections.insert(0, f"`{name}` is NOT a record keyword or class in "
                            f"this build's source; what follows is only what "
                            f"the manuals or test decks say about it.")
        return "\n\n".join(sections)

    def _manual_block(self, docs: list, keyword: str, known: set,
                      limit: int) -> str:
        """The documenting sections, excerpted in order while `limit` lasts
        (a record's intro and its syntax are often separate sections); where
        the rest are; and documented keywords the source defines nowhere."""
        lines, left, shown = [], limit, 0
        for d in docs:
            if left < 400:
                break
            head = (f"MANUAL: {d.title}  ({d.where}, {d.manual}; "
                    f"read_file for the full section)")
            body = MANUAL.excerpt(d, keyword, left - len(head))
            lines += [head, body]
            left -= len(head) + len(body) + 2
            shown += 1
        more = [f"{d.where} {d.title}" for d in docs[shown:shown + 4]]
        if more:
            lines.append("Also documented in: " + "; ".join(more))
        if known:
            documented = []
            for d in docs:
                documented += MANUAL.params_of(d, keyword)
            stale = [p for p in dict.fromkeys(documented) if p not in known]
            if stale:
                lines.append(
                    "IN THE MANUAL BUT DEFINED NOWHERE IN THIS BUILD'S SOURCE: "
                    + ", ".join(stale) + " -- stale documentation or a "
                    "placeholder. OOFEM would ignore such a keyword (Unread "
                    "token warning); check the attribute list and test decks.")
        return "\n".join(lines)

    def search_manual(self, query: str) -> str:
        return MANUAL.search(query, root=self.manual_root)

    def write_file(self, path: str, content: str) -> str:
        p = self._resolve(path, write=True)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if p in {f.resolve() for f in PIPE.candidate_files(self.work)}:
            # Validation belongs to the exact bytes that were executed. Any
            # edit to a candidate file requires a new run_candidate.
            self.validated_hash = None
            self.candidate_dirty = True
        return f"wrote {p.relative_to(self.work)} ({len(content)} chars)"

    @staticmethod
    def _process_output(out: str, limit: int = 5000) -> str:
        """Keep both setup messages and the traceback tail."""
        if len(out) <= limit:
            return out or "(no output)"
        head = 1200
        tail = limit - head
        return (out[:head] + f"\n[... {len(out) - limit} chars omitted ...]\n"
                + out[-tail:])

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

    def _stage_report(self, r: PIPE.Result) -> str:
        """What the agent sees of a pipeline run: per stage, errors first."""
        parts = []
        if r.warnings:
            parts.append("oofem warnings (unknown attributes are IGNORED):\n  "
                         + "\n  ".join(r.warnings))
        if r.oofem_outputs:
            shown = r.oofem_outputs[:25]
            more = len(r.oofem_outputs) - len(shown)
            parts.append("oofem wrote: " + ", ".join(shown)
                         + (f" (+{more} more)" if more > 0 else ""))
        for name in ("generate", "solve", "postprocess"):
            if name in r.logs:
                body = r.logs[name]
                if name == "solve":
                    errs = PIPE.oofem_errors(body)
                    if errs:
                        parts.append("oofem errors:\n  " + "\n  ".join(errs))
                parts.append(f"--- {name} ({r.seconds.get(name, 0):.1f}s) ---\n"
                             + self._process_output(body, 2500))
        return "\n".join(parts)

    def run_candidate(self) -> str:
        """Run the candidate pipeline from a clean, reproducible staging dir."""
        self.validation_attempts += 1
        self.validated_hash = None
        # This exact revision is about to be checked. A failed run is a current
        # validation failure, not a dirty/unvalidated edit; a later write flips
        # candidate_dirty back to True while preserving this diagnostic.
        self.candidate_dirty = False
        problems = PIPE.static_problems(self.work)
        if problems:
            self.last_validation = "; ".join(problems)
            return "[validation failed]\n- " + "\n- ".join(problems)

        files = PIPE.candidate_files(self.work)
        digest = PIPE.candidate_hash(self.work)
        # Outside the agent's writable root. Each validation starts clean, so a
        # candidate cannot pass using files left by a spike.
        stage = self.work.parent / ".candidate-validation"
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        for f in files:
            shutil.copy2(f, stage / f.name)
        for name in ("paper.txt", "scope.md"):
            src = self.work / name
            if src.is_file():
                shutil.copy2(src, stage / name)
        reference = self.work / "reference"
        if reference.is_dir():
            shutil.copytree(reference, stage / "reference")

        t0 = time.time()
        r = PIPE.run_pipeline(stage, self.tc, self.candidate_timeout)
        elapsed = time.time() - t0
        self.last_result = r
        self.tool_seconds += elapsed
        self.validation_seconds = round(elapsed, 1)
        self.validation_total_seconds = round(
            self.validation_total_seconds + elapsed, 1)

        # Keep the full final validation log, and the deck that was actually
        # solved, for audit. The tool response is shortened.
        (self.work.parent / "run.log").write_text(PIPE.render_log(r),
                                                  encoding="utf-8")
        if (stage / PIPE.DECK).is_file():
            shutil.copy2(stage / PIPE.DECK, self.work.parent / "solved.in")
        report = self._stage_report(r)
        if r.outcome != PIPE.OK:
            self.last_validation = f"{r.outcome}: {r.message}"
            return f"[validation failed: {self.last_validation}]\n{report}"

        errors = self._check_results(stage)
        if errors:
            self.last_validation = "; ".join(errors)
            return ("[validation failed: all stages exited 0]\n- "
                    + "\n- ".join(errors) + f"\n\n{report}")

        # Preserve only contract outputs from the clean run. These exact files
        # are what run_native.py scores after submit succeeds.
        shutil.copy2(stage / "results.csv", self.work / "results.csv")
        shutil.copy2(stage / "plot.png", self.work / "plot.png")
        self.validated_hash = digest
        self.candidate_dirty = False
        self.last_validation = "ok"
        return (f"[validation successful: {self.validation_seconds:.1f}s]\n"
                f"results.csv and plot.png are present; all "
                f"{len(self.curve_ids)} required curve ids were found.\n{report}")

    def is_validated(self) -> bool:
        return (PIPE.has_candidate(self.work) and self.validated_hash is not None
                and PIPE.candidate_hash(self.work) == self.validated_hash)

    def submit(self) -> tuple[bool, str]:
        if not PIPE.has_candidate(self.work):
            return False, f"submission refused: no candidate ({CANDIDATE_DESC})"
        if not self.is_validated():
            if self.candidate_dirty and self.last_validation:
                detail = ("candidate files changed after their last validation; "
                          f"previous result: {self.last_validation}")
            elif self.candidate_dirty:
                detail = "candidate has not been validated after its last edit"
            else:
                detail = self.last_validation or "candidate has not been tested"
            return (False, "submission refused: run run_candidate successfully "
                    f"after the last edit ({detail})")
        return True, "submitted: validated candidate accepted"

    def run_oofem(self, deck: str = PIPE.DECK) -> str:
        p = self._resolve(deck or PIPE.DECK, write=True)
        if not p.is_file():
            return f"no such deck: {p.relative_to(self.work)}"
        self.oofem_spikes += 1
        before = PIPE._snapshot(p.parent)
        try:
            rc, out, s = PIPE.run_process([self.tc.oofem_bin, "-f", p.name],
                                          p.parent, self.tc.solver_env(),
                                          self.spike_timeout)
        except FileNotFoundError:
            return f"oofem executable not found: {self.tc.oofem_bin}"
        self.tool_seconds += s
        written = [n for n in PIPE.new_files(p.parent, before) if n != p.name]
        head = (f"[timed out after {self.spike_timeout:.0f}s]" if rc is None
                else f"[{PIPE.status(rc)}, {s:.1f}s]")
        parts = [head]
        errs = PIPE.oofem_errors(out)
        if errs:
            parts.append("oofem errors:\n  " + "\n  ".join(errs))
        warns = PIPE.oofem_warnings(out)
        if warns:
            parts.append("oofem warnings (unknown attributes are IGNORED):\n  "
                         + "\n  ".join(warns))
        if written:
            parts.append("wrote: " + ", ".join(written[:25])
                         + (f" (+{len(written) - 25} more)" if len(written) > 25 else ""))
        parts.append(self._process_output(out[-3000:], 3000))
        return "\n".join(parts)

    def run_python(self, code: str) -> str:
        try:
            bound = PIPE.imports_of(code) & PIPE.BINDINGS
        except SyntaxError:
            bound = set()           # let the interpreter report it
        if bound:
            return (f"refused: imports {', '.join(sorted(bound))}. This job "
                    f"uses the oofem executable on a deck (run_oofem), not "
                    f"the Python bindings.")
        spike = self.work / ".spike.py"
        spike.write_text(code, encoding="utf-8")
        rc, out, s = PIPE.run_process([self.tc.python, "-X", "faulthandler",
                                       str(spike)], self.work,
                                      self.tc.python_env(), self.spike_timeout)
        self.tool_seconds += s
        if rc is None:
            return f"[timed out after {self.spike_timeout:.0f}s]"
        return f"[{PIPE.status(rc)}]\n{out or '(no output)'}"


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
        if name == "inspect_record":
            return sb.inspect_record(args["name"])
        if name == "search_manual":
            return sb.search_manual(args["query"])
        if name == "write_file":
            return sb.write_file(args["path"], args["content"])
        if name == "run_oofem":
            return sb.run_oofem(args.get("deck") or PIPE.DECK)
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
    """Do not resend rendered PDF bytes after the model has seen them once."""
    for message in messages:
        content = message.get("content")
        if (message.get("role") == "user" and isinstance(content, list)
                and any(item.get("type") == "image_url" for item in content
                        if isinstance(item, dict))):
            message["content"] = (
                "[Rendered paper page image supplied and inspected in the "
                "previous model turn. Request the page again if needed.]")


def _is_candidate_path(path) -> bool:
    return Path(str(path or "")).name in CANDIDATE_NAMES


def run_agent(call, system: str, sb: Sandbox, transcript: Path,
              max_turns: int, price: dict | None,
              finalization_turns: int = 4, api_retries: int = 2,
              vision: bool = False) -> dict:
    """Drive the tool loop. `call(messages, tools)` performs one completion and
    returns the raw response dict."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content":
                 "Begin. Explore what you need, then write the candidate "
                 f"({CANDIDATE_DESC}), call run_candidate until it succeeds, "
                 "and call submit."}]
    tools = _tools_spec(vision=vision)
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "turns": 0,
              "tool_calls": 0, "submitted": False, "validated": False,
              "stop": None, "nudges": 0, "api_retries": 0,
              "finalization_turns": 0, "auto_submitted": False}
    idle = 0        # consecutive turns that called no tool
    finalization_turns = max(0, int(finalization_turns))
    api_retries = max(0, int(api_retries))
    total_turns = max_turns + finalization_turns
    draft_turn = max(3, (max_turns + 2) // 3)
    hard_draft_turn = max(draft_turn + 2, max_turns // 2)

    with transcript.open("w", encoding="utf-8") as log:
        for turn in range(total_turns):
            if turn >= max_turns and not PIPE.has_candidate(sb.work):
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
                # A turn of pure reasoning is not a stop -- nudge, and only
                # give up if it keeps happening.
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
                                 "Continue. Call a tool, or write the candidate "
                                 f"({CANDIDATE_DESC}), validate it with "
                                 "run_candidate, and submit."})
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
                if fname == "write_file" and _is_candidate_path(fargs.get("path")):
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
            # An edit after an accepted submit in the same batch makes the
            # submission dirty again.
            if totals["submitted"] and not sb.is_validated():
                totals["submitted"] = False
                totals["validated"] = False
            if totals["submitted"]:
                totals["stop"] = "submitted"
                break

            # Turn pressure: the first candidate is due after one third of the
            # budget has ELAPSED, leaving two thirds for integrated repair.
            elapsed = turn + 1
            left = max(0, max_turns - elapsed)
            grace_left = max(0, total_turns - elapsed)
            candidate_exists = PIPE.has_candidate(sb.work)

            if elapsed < max_turns and not candidate_exists:
                if elapsed >= hard_draft_turn:
                    messages.append({"role": "user", "content":
                        f"Turn {elapsed}/{max_turns}: the candidate "
                        f"({CANDIDATE_DESC}) still does not exist. Stop broad "
                        f"exploration and write a runnable first version now. "
                        f"Then use the remaining {left} turns for "
                        f"run_candidate and repairs."})
                elif elapsed >= draft_turn:
                    messages.append({"role": "user", "content":
                        f"Turn {elapsed}/{max_turns}: the first candidate "
                        f"({CANDIDATE_DESC}) is due now. Write a minimal "
                        f"integrated version, run it, and improve it with the "
                        f"remaining {left} turns."})
            elif (elapsed < max_turns and candidate_exists
                  and not sb.is_validated()
                  and (wrote_candidate or elapsed >= hard_draft_turn)):
                messages.append({"role": "user", "content":
                    f"The candidate is dirty and {left} normal turn(s) remain. "
                    f"Run the complete candidate next; repair its concrete "
                    f"failure instead of returning to broad exploration."})
            elif elapsed == max_turns and candidate_exists:
                messages.append({"role": "user", "content":
                    f"The {max_turns}-turn exploration budget is exhausted. You "
                    f"have {grace_left} finalization turn(s). Only repair the "
                    f"candidate, call run_candidate, and submit. Do not start "
                    f"new research."})
            elif elapsed > max_turns and not sb.is_validated() and grace_left:
                messages.append({"role": "user", "content":
                    f"Finalization only: {grace_left} turn(s) remain. Fix the "
                    f"last validation failure, run_candidate, then submit."})
            elif sb.is_validated() and grace_left:
                # A validated candidate that is never submitted is lost work:
                # glm-5.3-flash validated twice on 2026-10-09 and ran out of
                # turns still refining it.
                messages.append({"role": "user", "content":
                    f"The current candidate passed run_candidate. Call submit "
                    f"now unless you are certain an improvement fits in the "
                    f"{grace_left} turn(s) left; any edit must be validated "
                    f"again before it can be submitted."})
        else:
            totals["stop"] = (f"turn budget exhausted ({max_turns} + "
                              f"{finalization_turns} finalization)")

        # Out of turns (or stopped) with files that are byte-for-byte the last
        # validated candidate: submit them on the model's behalf. The outputs
        # in work/ came from that exact validation, so nothing is re-run.
        # Flagged, so a report can tell this apart from a deliberate submit.
        if not totals["submitted"] and sb.is_validated():
            accepted, result = sb.submit()
            totals["submitted"] = accepted
            totals["auto_submitted"] = accepted
            log.write(json.dumps({"auto_submit": accepted,
                                  "result": result}) + "\n")

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
    totals["oofem_spikes"] = sb.oofem_spikes
    return totals
