# OOFEM manuals (sources)

Copied by `manual.py sync` from `doc/` of an OOFEM checkout, so the
oofem-native agent can look records up in them (`inspect_record`,
`search_manual`). Text sources only; figures are left out. The same
manuals are published at https://www.oofem.org/documentation/.

- checkout: `/home/imb-user/Documents/Gereon/oofem/oofem-gw`
- commit: 4573cc1e2aa125ba3035bffe9b5240f0180a351e 2025-10-15 Remove debug output from Rebar3d and BondCEBMaterial cross-section reduction calculations
- files: 12

OOFEM and its documentation are licensed under the GNU LGPL 2.1; see `COPYING.LGPLv2.1`.

Do not edit these files by hand; re-run

    .venv/bin/python worker/jobs/oofem-native/manual.py sync "$OOFEM_ROOT"
