"""
manual.py -- OOFEM's own manuals, as an index the agent can query.

The manuals that oofem.org publishes, as their sources, copied from an OOFEM
checkout's doc/ into manuals/ next to this file (LGPL 2.1, like OOFEM):

    manuals/oofemInput/*.rst             input data format specification
                                         (analysis records, domain records,
                                         export modules, time functions, ...)
    manuals/elementlibmanual/*.tex       element library: DOFs, side numbering,
                                         integration, cross-section needs
    manuals/matlibmanual/*.tex           material library: record format,
                                         meaning and typical values/units of
                                         every parameter
    manuals/usermanual/*.rst             how an input file and output read

Refresh the copy when the build moves to a new checkout:

    .venv/bin/python worker/jobs/oofem-native/manual.py sync "$OOFEM_ROOT"

They explain what the C++ `_IFT_` defines only name: what a parameter MEANS,
its unit, which modes a material supports, how element sides are numbered.
They can also be stale -- the source is what this build accepts -- which is
why inspect_record shows both and flags documented parameters the source
does not define.

Nothing here encodes OOFEM knowledge in the harness: it splits OOFEM's
own documents into sections and remembers which record keywords each section
documents.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MANUAL_DIR = Path(__file__).resolve().parent / "manuals"
MANUALS = (                        # (label, folder under doc/ and manuals/, files)
    ("input manual", "oofemInput", "*.rst"),
    ("element manual", "elementlibmanual", "*.tex"),
    ("material manual", "matlibmanual", "*.tex"),
    ("user manual", "usermanual", "*.rst"),
)
SKIP = {"conf.py", "index.rst", "about.rst", "installation.rst"}
LICENSE = "COPYING.LGPLv2.1"

_TEX_HEAD = re.compile(r"^\s*\\(section|subsection|subsubsection|paragraph)\*?\s*\{(.*)")
_RST_UNDER = re.compile(r"^([=~\-^\"*+#`'])\1{3,}\s*$")
_DESCITEM = re.compile(r"\\descitem\{([^}]+)\}")
_ELEMSUMMARY = re.compile(r"\\begin\{elementsummary\}\{([^}]+)\}")
# A record-format line: `` ``Keyword`` ``attr #(rn)`` [...]``, possibly inside a
# line block (`| `) or a list item (`-  | `); or a bold `| **Keyword**` title.
_RST_RECORD = re.compile(r"^[\s\-|]*(?:``(\w[\w\-]*)``\s*(?:``|\*|\[|\||<)"
                         r"|\*\*(\w[\w\-]*)\*\*\s*$)")
_TEX_PARAM = re.compile(r"\\(?:opt)?(?:elemparam|[Pp]?(?:opt)?[Ff]ield(?:notype)?)\{([^}]+)\}")
# A parameter on a record-format line: ``name #(type)`` or an optional [``flag``].
_RST_PARAM = re.compile(r"``(\w+)\s*#[^`]*``|\[``(\w+)``\]")


@dataclass
class Section:
    manual: str
    path: str               # absolute, so read_file can open it
    line: int               # 1-based first line
    end: int                # 1-based last line
    title: str
    body: str
    keywords: list = field(default_factory=list)   # record keywords documented

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}-{self.end}"


_CACHE: dict = {}


def _tex_title(rest: str) -> str:
    depth, out = 1, []
    for ch in rest:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        out.append(ch)
    return clean("".join(out)).strip()


def _split(text: str, tex: bool) -> list:
    """(line, title) of every heading, 1-based."""
    lines = text.splitlines()
    heads = []
    for i, ln in enumerate(lines):
        if tex:
            m = _TEX_HEAD.match(ln)
            if m:
                heads.append((i + 1, _tex_title(m.group(2))))
        elif (i > 0 and _RST_UNDER.match(ln) and lines[i - 1].strip()
              and not _RST_UNDER.match(lines[i - 1])
              and len(ln.rstrip()) >= len(lines[i - 1].rstrip())):
            heads.append((i, lines[i - 1].strip()))
    return heads


def load(root: Path | None = None) -> list:
    """Every section of every manual under `root` (default manuals/); cached."""
    root = Path(root or MANUAL_DIR).resolve()
    key = str(root)
    if key in _CACHE:
        return _CACHE[key]
    sections = []
    for manual, sub, pattern in MANUALS:
        folder = root / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob(pattern)):
            if path.name in SKIP:
                continue
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            tex = path.suffix == ".tex"
            lines = text.splitlines()
            heads = _split(text, tex) or [(1, path.stem)]
            if heads[0][0] > 1:
                heads.insert(0, (1, path.stem))
            rel = str(path)
            for n, (start, title) in enumerate(heads):
                end = heads[n + 1][0] - 1 if n + 1 < len(heads) else len(lines)
                body = "\n".join(lines[start - 1:end])
                if tex:
                    kws = _DESCITEM.findall(body) + _ELEMSUMMARY.findall(body)
                else:
                    kws = [m.group(1) or m.group(2)
                           for m in map(_RST_RECORD.match, body.splitlines()) if m]
                kws = [clean(k).strip() for k in kws]
                kws = [k for k in dict.fromkeys(kws)
                       if k and k.lower() not in {"keyword", "parameters"}]
                sections.append(Section(manual, rel, start, end, title, body, kws))
    _CACHE[key] = sections
    return sections


# --- LaTeX / rst to readable text -------------------------------------------

_SUBS = [
    (re.compile(r"(?<!\\)%.*"), ""),                                   # comments
    (re.compile(r"\\(?:opt)?elemparam\{([^}]*)\}\{([^}]*)\}"), r"\1 #(\2)"),
    (re.compile(r"\\(?:P)?optField\{([^}]*)\}\{([^}]*)\}"), r"[\1 #(\2)]"),
    (re.compile(r"\\field\{([^}]*)\}\{([^}]*)\}"), r"\1 #(\2)"),
    (re.compile(r"\\(?:P)?optFieldnotype\{([^}]*)\}"), r"[\1]"),
    (re.compile(r"\\elementDescription\{([^}]*)\}"), r"\1:"),
    (re.compile(r"\\begin\{elementsummary\}\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}\{[^}]*\}\{[^}]*\}"),
     r"Keyword: \1 -- \2. Specific parameters: \3"),
    (re.compile(r"\\(?:ref|refeq|reffig|eqref|cite)\{([^}]*)\}"), r"<\1>"),
    (re.compile(r"\\(?:label|input|includegraphics|caption)(?:\[[^]]*\])?\{[^}]*\}"), ""),
    (re.compile(r"\\(?:begin|end)\{[^}]*\}(?:\[[^]]*\])?(?:\{[^}]*\})*"), ""),
    (re.compile(r"\\optelemstring\{([^}]*)\}"), r"[\1]"),
    (re.compile(r"\\(?:caption|centerline)\b"), ""),
    (re.compile(r"^\.\. _[^:]+:\s*$", re.M), ""),                  # rst labels
    (re.compile(r"\\(?:hline|newpage|noindent|centering|small|tiny|bf|it|tt)\b"), ""),
    (re.compile(r"\\(?:param|descitem|textbf|textit|texttt|emph|mathrm|rm|elementParam|Pparam)\{"), "{"),
    (re.compile(r"\\\\[ \t]*\n?"), "\n"),        # table row ends
    (re.compile(r"(?<!\\)&"), " | "),
    (re.compile(r"\\([%&_#$])"), r"\1"),
    (re.compile(r"~"), " "),
    (re.compile(r"(?<!\\)[{}]"), ""),
    (re.compile(r"[ \t]+"), " "),
    (re.compile(r"\n[ \t|]*(?=\n)"), ""),          # blank and empty-cell lines
    (re.compile(r"\n\s*\n\s*\n+"), "\n\n"),
]


def clean(text: str) -> str:
    for rx, rep in _SUBS:
        text = rx.sub(rep, text)
    return text.strip()


def params_of(section: Section, keyword: str) -> list:
    """Parameter names in the manual's record-format line(s) for `keyword`,
    lower-cased and without the record number."""
    lines = section.body.splitlines()
    out = []
    tex = section.path.endswith(".tex")
    for i, ln in enumerate(lines):
        if tex:
            hit = (re.search(r"\\descitem\{%s\}" % re.escape(keyword), ln, re.I)
                   or re.search(r"\\begin\{elementsummary\}\{%s\}" % re.escape(keyword),
                                ln, re.I))
        else:
            m = _RST_RECORD.match(ln)
            hit = m and (m.group(1) or m.group(2)).lower() == keyword.lower()
        if not hit:
            continue
        block = [ln]
        for nxt in lines[i + 1:i + 8]:          # the record line may wrap
            if not nxt.strip() or (not tex and "``" not in nxt) or (tex and ("Parameters" in nxt
                                            or "elementDescription" in nxt
                                            or "elementParam" in nxt)):
                break
            block.append(nxt)
        text = " ".join(block)
        names = (_TEX_PARAM.findall(text) if tex
                 else [a or b for a, b in _RST_PARAM.findall(text)])
        out.extend(n.replace("\\_", "_").lower() for n in names)
    skip = {"num", "#", "keyword", keyword.lower()}
    return [n for n in dict.fromkeys(out) if n not in skip]


# --- queries ------------------------------------------------------------------

def sync(oofem_root: Path, dest: Path = MANUAL_DIR) -> str:
    """Copy the manual sources (text only, no figures) from a checkout's doc/,
    with OOFEM's licence and a note of the commit they came from."""
    doc = Path(oofem_root) / "doc"
    if not doc.is_dir():
        raise SystemExit(f"no doc/ in {oofem_root}")
    if dest.exists():
        shutil.rmtree(dest)
    count = 0
    for _, sub, pattern in MANUALS:
        (dest / sub).mkdir(parents=True)
        for path in sorted((doc / sub).glob(pattern)):
            if path.name not in SKIP:
                shutil.copy2(path, dest / sub / path.name)
                count += 1
    if (Path(oofem_root) / LICENSE).is_file():
        shutil.copy2(Path(oofem_root) / LICENSE, dest / LICENSE)
    try:
        commit = subprocess.run(
            ["git", "-C", str(oofem_root), "log", "-1", "--format=%H %cs %s"],
            capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown (not a git checkout)"
    (dest / "SOURCE.md").write_text(
        "# OOFEM manuals (sources)\n\n"
        "Copied by `manual.py sync` from `doc/` of an OOFEM checkout, so the\n"
        "oofem-native agent can look records up in them (`inspect_record`,\n"
        "`search_manual`). Text sources only; figures are left out. The same\n"
        "manuals are published at https://www.oofem.org/documentation/.\n\n"
        f"- checkout: `{oofem_root}`\n- commit: {commit}\n"
        f"- files: {count}\n\n"
        f"OOFEM and its documentation are licensed under the GNU LGPL 2.1; "
        f"see `{LICENSE}`.\n\n"
        "Do not edit these files by hand; re-run\n\n"
        "    .venv/bin/python worker/jobs/oofem-native/manual.py sync \"$OOFEM_ROOT\"\n")
    _CACHE.pop(str(dest.resolve()), None)
    return f"copied {count} manual files from {doc} to {dest} ({commit})"


def sections_for(*names: str, root: Path | None = None) -> list:
    """Sections that document a record under any of `names` (keyword or
    class name), record-format documentation first."""
    wanted = {n.lower() for n in names if n}
    hits = [s for s in load(root)
            if wanted & {k.lower() for k in s.keywords}]
    if hits:
        return hits
    # Fall back to a section titled after the record, e.g. "Tr1mt element".
    rx = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, wanted)), re.I) if wanted else None
    return [s for s in load(root) if rx and rx.search(s.title)][:3]


def excerpt(section: Section, keyword: str, limit: int) -> str:
    """The part of a section a deck author needs: the record format and its
    parameter list, plus the section's opening prose if there is room."""
    lines = section.body.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if keyword.lower() in ln.lower() and ("descitem" in ln or "elementsummary" in ln
                                               or _RST_RECORD.match(ln)):
            start = i
            break
    record = clean("\n".join(lines[start:start + 60]))
    intro = clean("\n".join(lines[1:start])) if start > 1 else ""
    text = record
    if intro and len(intro) + len(record) < limit:
        text = intro + "\n\n" + record
    elif intro:
        room = max(0, limit - len(record) - 40)
        if room > 200:
            text = intro[:room] + " [...]\n\n" + record
    if len(text) > limit:
        text = text[:limit] + " [...]"
    return text


def search(query: str, root: Path | None = None, limit: int = 12) -> str:
    """Rank sections by the query's words (all words must occur; falls back to
    any word). Titles and documented record keywords weigh more than body text."""
    terms = [t for t in re.findall(r"[\w\-]+", (query or "").lower()) if len(t) > 1]
    if not terms:
        return "give one or more words to search for"
    sections = load(root)
    if not sections:
        return f"no manuals found under {root or MANUAL_DIR}"

    def score(s: Section, need_all: bool) -> float:
        title, body = s.title.lower(), s.body.lower()
        kws = " ".join(s.keywords).lower()
        total, present = 0.0, 0
        for t in terms:
            n = body.count(t)
            if n or t in title or t in kws:
                present += 1
            total += 6 * (t in title) + 4 * (t in kws.split()) + min(n, 6)
        if need_all and present < len(terms):
            return 0.0
        return total / (1 + len(s.body) / 20000)

    ranked = sorted(((score(s, True), s) for s in sections), key=lambda x: -x[0])
    ranked = [r for r in ranked if r[0] > 0]
    note = ""
    if not ranked:
        ranked = sorted(((score(s, False), s) for s in sections), key=lambda x: -x[0])
        ranked = [r for r in ranked if r[0] > 0]
        note = "(no section contains every word; showing partial matches)\n"
    if not ranked:
        return f"no manual section mentions {query!r}"
    rows = []
    for _, s in ranked[:limit]:
        best, snippet = 0, ""
        for ln in s.body.splitlines()[1:]:
            n = sum(t in ln.lower() for t in terms)
            if n > best and clean(ln):
                best, snippet = n, clean(ln)[:160]
        kw = f"  records: {', '.join(s.keywords[:8])}" if s.keywords else ""
        rows.append(f"{s.where}  [{s.manual}] {s.title}{kw}\n    {snippet}")
    return (note + "\n".join(rows)
            + "\n\nRead a section with read_file(path, start_line); "
              "inspect_record NAME shows a record's documentation directly.")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "sync":
        print(sync(Path(sys.argv[2])))
    else:
        raise SystemExit("usage: manual.py sync OOFEM_ROOT")
