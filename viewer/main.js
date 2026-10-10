"use strict";
/* main.js -- loaded last: fetch the index (or take the embedded one) and route. */

function start(data) {
  DATA.cases = (data.cases || []).sort((a, b) => a.path.localeCompare(b.path));
  DATA.dataflows = (data.dataflows || []).sort((a, b) => a.title.localeCompare(b.title));
  route();
}

if (STANDALONE) {
  start(EMBEDDED);
} else {
  fetch("index.json")
    .then((r) => {
      if (!r.ok) throw new Error(`index.json → HTTP ${r.status}`);
      return r.json();
    })
    .then(start)
    .catch((err) => {
      view.innerHTML = `<p class="error">Could not load <code>index.json</code>: ${esc(err.message)}<br>
        Run <code>python3 viewer/build_index.py</code> from the repo root, then serve with
        <code>python3 -m http.server</code> and open <code>localhost:8000/viewer/</code>.</p>`;
    });
}
