"use strict";
/* home.js -- the landing page (#/): one card per registered section. */

function renderHome() {
  view.innerHTML = `
    <section class="hero">
      <h1>THCM Benchmarks</h1>
      <p class="lede">Thermo-hydro-chemo-mechanical benchmarks from the literature,
        and the worker jobs that build and test them.</p>
    </section>
    <div class="home-grid">
      ${SECTIONS.map((s) => `
        <a class="home-card" href="${link(s.id)}">
          <span class="home-icon">${s.icon}</span>
          <h2>${esc(s.title)}</h2>
          <p>${esc(s.blurb)}</p>
          <ul class="home-stats">${s.stats().map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
          <span class="home-go">Open ${esc(s.title.toLowerCase())} ${ICON.arrow}</span>
        </a>`).join("")}
    </div>`;
}
