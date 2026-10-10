"use strict";
/* benchmarks.js -- the curated corpus: a filterable overview and one page per
   benchmark (#/benchmarks and #/benchmarks/<path>). */

let filter = { q: "", physics: new Set(), status: "all" };
let current = null;              // case on screen, for ←/→ keys and swipes
let onOverview = false;          // the delegated overview handler acts only here
let overviewBound = false;       // ...and binds once

const CASES = () => DATA.cases;
const byPath = (p) => CASES().find((c) => c.path === p);

/* -------------------------------------------------------------- overview */

/* Physics tags carry the field's own shorthand; the full string stays in the
   tooltip and on the detail page. */
const PHYSICS_LABEL = {
  "hydro-mechanical": "HM",
  "thermo-mechanical": "TM",
  "thermo-hydro-mechanical": "THM",
};
const physicsLabel = (t) => PHYSICS_LABEL[t] || t;

const STATUS = [
  ["all", "All", () => true],
  ["results", "Has results", (c) => c.n_figures > 0],
  ["correct", "Correct", (c) => c.correct === true],
  ["incorrect", "Incorrect", (c) => c.correct === false],
  ["unreviewed", "Unreviewed", (c) => c.correct == null],
];
const statusPred = (key) => (STATUS.find((s) => s[0] === key) || STATUS[0])[2];

const passPhysics = (c) => !filter.physics.size || filter.physics.has(c.physics);
const passStatus = (c) => statusPred(filter.status)(c);

function passQuery(c) {
  if (!filter.q) return true;
  const hay = [c.title, c.id, c.path, c.source, c.physics_raw, c.notes,
    c.validation_target].join(" ").toLowerCase();
  return filter.q.toLowerCase().split(/\s+/).every((t) => hay.includes(t));
}

/* `skip` leaves one dimension out, so each facet can be counted as "how many
   would I get if I clicked this" rather than against its own filter. */
function matches(c, skip) {
  return (skip === "physics" || passPhysics(c))
    && (skip === "status" || passStatus(c))
    && passQuery(c);
}

const isFiltered = () =>
  filter.q !== "" || filter.physics.size > 0 || filter.status !== "all";

function verdictBadge(c) {
  if (c.correct === true) return `<span class="badge ok">Correct</span>`;
  if (c.correct === false) return `<span class="badge bad">Incorrect</span>`;
  return `<span class="badge muted">Unreviewed</span>`;
}

const caseHref = (c) => link("benchmarks", c.path);

function card(c) {
  const meta = [physicsLabel(c.physics)];
  if (c.dimension) meta.push(`${c.dimension}D`);
  for (const s of c.solvers) meta.push(s.name);

  return `<a class="card" href="${caseHref(c)}">
    <p class="eyebrow">${esc(c.path)}</p>
    <h2>${esc(c.title)}</h2>
    <p class="source">${esc(c.source || "—")}</p>
    <div class="card-foot">
      <span class="meta">${meta.map(esc).join(" · ")}</span>
      <span class="badges">
        ${c.n_figures ? `<span class="badge plain">${plural(c.n_figures, "figure")}</span>`
                      : `<span class="badge plain">No output</span>`}
        ${verdictBadge(c)}
      </span>
    </div>
  </a>`;
}

/* The controls are built once; only the result list is re-rendered on input,
   so the search field keeps focus and caret position while typing. */
function chip({ dim, val, label, title, n, active }) {
  const dead = n === 0 && !active;
  return `<button class="chip" data-dim="${dim}" data-val="${esc(val)}"
    aria-pressed="${active}" ${dead ? "disabled" : ""}
    ${title ? `title="${esc(title)}"` : ""}>${esc(label)}<span class="n">${n}</span></button>`;
}

function renderFacets() {
  const tags = [...new Set(CASES().map((c) => c.physics))].sort();

  const physics = [
    chip({
      dim: "physics", val: "all", label: "All",
      n: CASES().filter((c) => matches(c, "physics")).length,
      active: filter.physics.size === 0,
    }),
    ...tags.map((t) => chip({
      dim: "physics", val: t, label: physicsLabel(t), title: t,
      n: CASES().filter((c) => matches(c, "physics") && c.physics === t).length,
      active: filter.physics.has(t),
    })),
  ].join("");

  const status = STATUS.map(([key, label, pred]) => chip({
    dim: "status", val: key, label,
    n: CASES().filter((c) => matches(c, "status") && pred(c)).length,
    active: filter.status === key,
  })).join("");

  view.querySelector("#facets").innerHTML = `
    <div class="facet">
      <span class="facet-label" id="lbl-physics">Physics</span>
      <div class="chips" role="group" aria-labelledby="lbl-physics">${physics}</div>
    </div>
    <div class="facet">
      <span class="facet-label" id="lbl-status">Status</span>
      <div class="chips" role="group" aria-labelledby="lbl-status">${status}</div>
    </div>`;
}

function renderResults() {
  const shown = CASES().filter((c) => matches(c));

  view.querySelector("#results").innerHTML = shown.length
    ? `<div class="grid">${shown.map(card).join("")}</div>`
    : `<div class="empty">
         <p>No benchmark matches these filters.</p>
         <button class="linkish" data-action="reset">Clear all filters</button>
       </div>`;

  view.querySelector("#tally").innerHTML = isFiltered()
    ? `<strong>${shown.length}</strong> of ${CASES().length}`
    : `<strong>${CASES().length}</strong> benchmark${CASES().length === 1 ? "" : "s"}`;
  view.querySelector("#reset").hidden = !isFiltered();
}

function refresh() {
  renderFacets();
  renderResults();
}

function resetFilters() {
  filter = { q: "", physics: new Set(), status: "all" };
  view.querySelector("#search").value = "";
  refresh();
}

function renderOverview() {
  onOverview = true;
  view.innerHTML = `
    <header class="page-head">
      <h1>Benchmarks</h1>
      <p class="lede">Each benchmark is one experiment from the literature, written as a
        <code>benchmark.json</code>, with the solver's plots and a review verdict.</p>
    </header>
    <div class="filterbar">
      <div class="searchrow">
        <div class="searchfield">
          <svg class="search-icon" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5"/>
            <path d="M10.5 10.5 L14 14" stroke="currentColor" stroke-width="1.5"
                  stroke-linecap="round"/>
          </svg>
          <input id="search" type="text" autocomplete="off" spellcheck="false"
                 placeholder="Search title, source, notes…" value="${esc(filter.q)}"
                 aria-label="Search benchmarks">
          <kbd class="hint">/</kbd>
        </div>
        <span class="tally" id="tally" aria-live="polite"></span>
        <button class="linkish" id="reset" data-action="reset" hidden>Reset</button>
      </div>
      <div id="facets"></div>
    </div>
    <div id="results"></div>`;

  const search = view.querySelector("#search");
  search.addEventListener("input", (e) => { filter.q = e.target.value; refresh(); });
  search.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && search.value) { search.value = ""; filter.q = ""; refresh(); }
  });

  /* Delegated: the facet chips are re-rendered on every change. Bound once --
     `view` itself outlives every re-render. */
  if (!overviewBound) {
    overviewBound = true;
    view.addEventListener("click", (e) => {
      if (!onOverview) return;
      const btn = e.target.closest("button");
      if (!btn) return;
      if (btn.dataset.action === "reset") return resetFilters();
      if (btn.dataset.dim === "status") {
        filter.status = btn.dataset.val;
      } else if (btn.dataset.dim === "physics") {
        const v = btn.dataset.val;
        if (v === "all") filter.physics.clear();
        else filter.physics.has(v) ? filter.physics.delete(v) : filter.physics.add(v);
      } else return;
      refresh();
    });
  }

  refresh();
}

/* ---------------------------------------------------------------- detail */

/* Paging follows the list the user came from: with filters active the
   neighbours stay inside the filtered set, otherwise it is the whole suite. */
function siblings(c) {
  const all = CASES();
  const shown = all.filter((x) => matches(x));
  const scope = shown.includes(c) ? shown : all;
  const i = scope.indexOf(c);
  return {
    prev: scope[i - 1] || null,
    next: scope[i + 1] || null,
    pos: i + 1,
    total: scope.length,
    scoped: scope !== all && isFiltered(),
  };
}

const goTo = (c) => { if (c) location.hash = caseHref(c); };

function navBar(c) {
  const { prev, next, pos, total, scoped } = siblings(c);
  const arrow = (label, s, glyph) => s
    ? `<a class="pager-btn" href="${caseHref(s)}" title="${esc(s.title)}"
         aria-label="${label}: ${esc(s.title)}">${glyph}</a>`
    : `<span class="pager-btn" aria-disabled="true" aria-hidden="true">${glyph}</span>`;

  return `<div class="detail-nav">
    ${crumbs([["Benchmarks", link("benchmarks")], [c.path]])}
    <div class="pager-mini" aria-label="Benchmark navigation">
      ${arrow("Previous benchmark", prev, "←")}
      <span class="pager-count">${pos}<span class="of">/</span>${total}${
        scoped ? ` <span class="of">filtered</span>` : ""}</span>
      ${arrow("Next benchmark", next, "→")}
    </div>
  </div>`;
}

function pagerFoot(c) {
  const { prev, next } = siblings(c);
  const cell = (dir, label, s) => s
    ? `<a class="pager-card ${dir}" href="${caseHref(s)}">
         <span class="pager-dir">${label}</span>
         <span class="pager-title">${esc(s.title)}</span>
         <span class="pager-path">${esc(s.path)}</span>
       </a>`
    : `<span class="pager-card is-empty"></span>`;

  return `<nav class="pager" aria-label="Previous and next benchmark">
    ${cell("prev", "← Previous", prev)}${cell("next", "Next →", next)}
  </nav>`;
}

function factsList(c) {
  const rows = [
    ["Physics", c.physics_raw],
    ["Dimension", c.dimension ? `${c.dimension}D` : null],
    ["Fields", c.fields.length ? c.fields.join(", ") : null],
    ["Materials", c.materials.length ? c.materials.join(", ") : null],
    ["Boundary conditions", c.n_bcs || null],
    ["Analysis", c.analysis_type],
    ["Simulated time", fmtDuration(c.t_end)],
    ["Time step", fmtDuration(c.dt)],
    ["Validation", c.validation_method],
    ["Target", c.validation_target],
    ["Reviewed", c.correct === true ? "correct"
      : c.correct === false ? "incorrect"
      : "not yet reviewed"],
    ["Review note", c.verdict_comment],
    ["Solvers", c.solvers.length ? c.solvers.map((s) => s.name).join(", ") : "not run"],
    ["Reference data", c.reference_files.length
      ? plural(c.reference_files.length, "CSV file") : null],
    ["Specification", STANDALONE
      ? `<code>${esc(c.spec_path)}</code>`
      : `<a href="${esc(ASSET(c.spec_path))}"><code>${esc(c.spec_path)}</code></a>`],
    ["Paper", !c.paper_pdf ? null : STANDALONE
      ? `<code>${esc(c.paper_pdf)}</code>`
      : `<a href="${esc(ASSET(c.paper_pdf))}">${esc(c.paper_pdf.split("/").pop())}</a>`],
  ].filter(([, v]) => v != null && v !== "");

  return `<dl class="facts">${rows.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${/^<(a|code)[\s>]/.test(String(v)) ? v : esc(v)}</dd>`).join("")}</dl>`;
}

/* No curve list here -- the plot image carries its own legend. */
function figure(f) {
  const axes = [f.y_label, f.x_label].filter(Boolean).join(" vs. ");
  return `<figure class="plot">
    <img src="${esc(ASSET(f.src))}" alt="${esc(f.title)}" loading="lazy"
         data-caption="${esc(f.title)}">
    <figcaption>
      <span class="fig-title">${esc(f.title)}</span>
      ${axes ? `<span class="axes">${esc(axes)}</span>` : ""}
    </figcaption>
  </figure>`;
}

function renderDetail(c) {
  const results = c.solvers.length
    ? c.solvers.map((s) => `
        <p class="solver-label">Solver <strong>${esc(s.name)}</strong> &middot;
          ${plural(s.figures.length, "figure")}${s.has_vtu ? " · VTU field output" : ""}${s.has_logs ? " · logs" : ""}</p>
        <div class="figures">${s.figures.map(figure).join("")}</div>`).join("")
    : `<p class="empty">No output plots on disk for this benchmark.</p>`;

  view.innerHTML = `
    ${navBar(c)}
    <header class="detail-head">
      <h1>${esc(c.title)}</h1>
      <p class="source">${esc(c.source || c.path)}</p>
      <div class="badges">
        <span class="badge" title="${esc(c.physics_raw)}">${esc(c.physics)}</span>
        ${c.dimension ? `<span class="badge plain">${c.dimension}D</span>` : ""}
        ${verdictBadge(c)}
      </div>
    </header>

    ${c.notes ? `<p class="abstract">${esc(c.notes)}</p>` : ""}

    <section class="block">
      <h3 class="section-title">Setup</h3>
      ${factsList(c)}
    </section>

    <section class="block">
      <h3 class="section-title">Results</h3>
      ${results}
    </section>

    ${c.analysis_md ? `<section class="block">
      <h3 class="section-title">Notes</h3>
      <details class="notes">
        <summary>${esc(c.analysis_name)}</summary>
        <div class="md">${markdown(c.analysis_md)}</div>
      </details>
    </section>` : ""}

    ${pagerFoot(c)}`;

  view.querySelectorAll("figure.plot img").forEach((img) =>
    img.addEventListener("click", () => openLightbox(img.src, img.dataset.caption)));
}

/* ------------------------------------------------------- keys and swipes */

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && lightboxOpen()) { closeLightbox(); return; }
  // ← / → page through the benchmarks while a detail page is open.
  const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || "");
  if (current && !lightboxOpen() && !inField && !e.metaKey && !e.ctrlKey && !e.altKey) {
    if (e.key === "ArrowLeft") { e.preventDefault(); return goTo(siblings(current).prev); }
    if (e.key === "ArrowRight") { e.preventDefault(); return goTo(siblings(current).next); }
  }
  // "/" jumps to the search box from anywhere on the overview.
  const search = document.getElementById("search");
  if (e.key === "/" && search && document.activeElement !== search) {
    e.preventDefault();
    search.focus();
    search.select();
  }
});

/* Horizontal drags page between benchmarks; anything that leans vertical is
   left to the scroller. */
let touchStart = null;

document.addEventListener("touchstart", (e) => {
  touchStart = e.touches.length === 1
    ? { x: e.touches[0].clientX, y: e.touches[0].clientY }
    : null;
}, { passive: true });

document.addEventListener("touchend", (e) => {
  const from = touchStart;
  touchStart = null;
  if (!from || !current || lightboxOpen()) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - from.x, dy = t.clientY - from.y;
  if (Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.8) return;
  goTo(dx < 0 ? siblings(current).next : siblings(current).prev);
}, { passive: true });

/* --------------------------------------------------------------- section */

registerSection({
  id: "benchmarks",
  title: "Benchmarks",
  icon: ICON.benchmarks,
  blurb: "The curated corpus: each paper's setup, the solver's plots, and whether " +
         "the result has been checked against the paper.",
  stats() {
    const all = CASES();
    const withPlots = all.filter((c) => c.n_figures > 0).length;
    const correct = all.filter((c) => c.correct === true).length;
    return [plural(all.length, "benchmark"), `${withPlots} with results`, `${correct} reviewed correct`];
  },
  render(path) {
    const c = path ? byPath(path) : null;
    current = c;
    if (c) renderDetail(c);
    else renderOverview();
  },
  leave() { current = null; onOverview = false; },
});
