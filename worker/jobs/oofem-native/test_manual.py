#!/usr/bin/env python3
"""Tests for the manual lookup behind inspect_record and search_manual.

    .venv/bin/python -m unittest discover -s worker/jobs/oofem-native -p 'test_*.py'
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import agent_native as A  # noqa: E402
import manual as M        # noqa: E402

HEADER = """\
#define _IFT_HeatMat_Name "heatmat"
#define _IFT_HeatMat_k "k"
#define _IFT_Material_d "d"
class HeatMat : public Material {
};
class Material : public FEMComponent {
};
"""
SOURCE = """\
ParamKey HeatMat :: IPK_HeatMat_cap("c");

void
HeatMat :: initializeFrom(InputRecord &ir)
{
    IR_GIVE_OPTIONAL_FIELD(ir, scale, "scaling");
}
"""
MATLIB = r"""
\section{Material Models for Transport Problems}
\subsection{Isotropic heat material -- HeatMat}
Linear heat conduction.
\begin{table}
\begin{mmt}
Record Format & \descitem{HeatMat} \elemparam{num}{in} \elemparam{d}{rn}
\elemparam{k}{rn} \elemparam{c}{rn} [\optelemparam{oldname}{rn}]\\
Parameters &- \param{d} density in kg/m$^3$\\
&- \param{k} conductivity about 1.7 W/m/K\\
\hline
\end{mmt}
\end{table}
\subsection{Something else}
Nothing about convection here.
"""
INPUT = """\
Boundary loads
~~~~~~~~~~~~~~

``ConstantEdgeLoad`` ``loadType #(in)`` ``components #(ra)``

Convection on an edge: loadType 3, components hold the film coefficient
and the ambient temperature.
"""


def fake_checkout(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "heatmat.h").write_text(HEADER)
    (root / "src" / "heatmat.C").write_text(SOURCE)
    (root / "doc" / "matlibmanual").mkdir(parents=True)
    (root / "doc" / "matlibmanual" / "matlibmanual.tex").write_text(MATLIB)
    (root / "doc" / "oofemInput").mkdir(parents=True)
    (root / "doc" / "oofemInput" / "domainrecords.rst").write_text(INPUT)
    (root / "doc" / "oofemInput" / "conf.py").write_text("# sphinx\n")
    (root / "COPYING.LGPLv2.1").write_text("LGPL\n")
    return root


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.oofem = fake_checkout(root / "oofem")
        self.manuals = root / "manuals"
        M.sync(self.oofem, self.manuals)
        (root / "work").mkdir()
        self.sb = A.Sandbox(root / "work", self.oofem, None, spike_timeout=5,
                            manual_root=self.manuals)

    def tearDown(self):
        A._INDEX.clear()
        M._CACHE.clear()
        self.td.cleanup()

    def test_sync_copies_text_sources_and_licence(self):
        self.assertTrue((self.manuals / "matlibmanual" / "matlibmanual.tex").is_file())
        self.assertFalse((self.manuals / "oofemInput" / "conf.py").exists())
        self.assertTrue((self.manuals / "COPYING.LGPLv2.1").is_file())
        self.assertIn("LGPL 2.1", (self.manuals / "SOURCE.md").read_text())

    def test_sections_carry_the_records_they_document(self):
        sections = M.load(self.manuals)
        by_title = {s.title: s for s in sections}
        self.assertEqual(by_title["Isotropic heat material -- HeatMat"].keywords,
                         ["HeatMat"])
        self.assertEqual(by_title["Boundary loads"].keywords, ["ConstantEdgeLoad"])
        heat = M.sections_for("heatmat", root=self.manuals)[0]
        self.assertEqual(M.params_of(heat, "HeatMat"), ["d", "k", "c", "oldname"])

    def test_inspect_record_shows_the_manual_and_all_keyword_forms(self):
        out = self.sb.inspect_record("heatmat")
        self.assertIn("MANUAL: Isotropic heat material -- HeatMat", out)
        self.assertIn("conductivity about 1.7 W/m/K", out)
        path = str(self.manuals.resolve() / "matlibmanual" / "matlibmanual.tex")
        self.assertIn(path + ":", out)
        # the location it prints is one read_file accepts
        line = int(out.split(path + ":")[1].split("-")[0])
        self.assertIn("HeatMat", self.sb.read_file(path, line))
        # _IFT_ define, ParamKey and a literal read in the class's own method
        for kw in ("k", "c", "scaling"):
            self.assertRegex(out, r"OWN ATTRIBUTES[^\n]*\n  .*\b%s\b" % kw)
        self.assertIn("INHERITED FROM Material", out)
        # documented, but no source defines it anywhere
        self.assertIn("DEFINED NOWHERE IN THIS BUILD'S SOURCE: oldname", out)
        self.assertLess(out.index("MANUAL"), out.index("OWN ATTRIBUTES"))

    def test_manual_only_record_is_labelled(self):
        out = self.sb.inspect_record("ConstantEdgeLoad")
        self.assertIn("NOT a record keyword or class in this build", out)
        self.assertIn("loadType", out)

    def test_unknown_record_points_at_search(self):
        out = self.sb.inspect_record("nosuchrecord")
        self.assertIn("Try search_manual", out)

    def test_search_ranks_by_topic(self):
        out = A.dispatch(self.sb, "search_manual", {"query": "convection edge"})
        first = out.splitlines()[0]
        self.assertIn("Boundary loads", first)
        self.assertIn("records: ConstantEdgeLoad", first)
        # no section has both words -> partial matches, labelled
        out = self.sb.search_manual("convection conductivity")
        self.assertIn("partial matches", out)
        self.assertIn("no manual section", self.sb.search_manual("zzzz"))

    def test_tool_is_offered(self):
        names = [t["function"]["name"] for t in A._tools_spec()]
        self.assertIn("search_manual", names)

    def test_clean_turns_latex_into_text(self):
        self.assertEqual(M.clean(r"\elemparam{E}{rn} \param{k} 1.7~W/m/K\\"),
                         "E #(rn) k 1.7 W/m/K")


REAL = Path(os.environ.get("OOFEM_ROOT",
                           Path.home() / "Documents/Gereon/oofem/oofem-gw"))


class CommittedManualTests(unittest.TestCase):
    """The copy in manuals/ is what the agent reads; it needs no checkout."""

    def test_copy_is_present_and_indexed(self):
        sections = M.load()
        self.assertGreater(len(sections), 250)
        records = {k.lower() for s in sections for k in s.keywords}
        for name in ("hydratingconcretemat", "mps", "tr1ht", "transienttransport",
                     "nonlinearstatic", "simplecs", "set", "boundarycondition"):
            self.assertIn(name, records)
        self.assertIn("commit:", (M.MANUAL_DIR / "SOURCE.md").read_text())


@unittest.skipUnless((REAL / "src").is_dir(), "no OOFEM checkout")
class RealCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.sb = A.Sandbox(Path(self.td.name), REAL, None, spike_timeout=5)

    def tearDown(self):
        self.td.cleanup()

    def test_benchmark_records_are_documented(self):
        out = self.sb.inspect_record("hydratingconcretemat")
        self.assertIn("kJ/kg", out)           # Qpot's unit, from the manual
        self.assertIn("side numbering", self.sb.inspect_record("tr1ht"))
        self.assertIn("nip", self.sb.inspect_record("planestress2d"))  # ParamKey
        for name in ("mps", "transienttransport", "nonlinearstatic",
                     "simplecs", "set", "boundarycondition"):
            self.assertIn("MANUAL:", self.sb.inspect_record(name), name)

    def test_every_record_fits_one_tool_result(self):
        idx = A.record_index(REAL)
        over = [(k, n) for k in idx["by_name"]
                if (n := len(self.sb.inspect_record(k))) > A.MAX_TOOL_CHARS]
        self.assertEqual(over, [])


if __name__ == "__main__":
    unittest.main()
