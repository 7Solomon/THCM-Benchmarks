"use strict";
/* core.js -- shared by every section: the data, the router, the top navigation,
   the lightbox and small helpers. Loaded first; the section files register
   themselves with registerSection(), and main.js starts the router. */

const view = document.getElementById("view");

/* In the standalone export the builder inlines the index and base64 images into
   the page, so there is nothing to fetch and no sibling files to link to. */
const EMBEDDED = window.__BENCHMARK_DATA__ || null;
const STANDALONE = EMBEDDED !== null;
const ASSET = (p) => (STANDALONE ? p : "../" + p);

const DATA = { cases: [], dataflows: [] };

/* ------------------------------------------------------------------ utils */

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;

function fmtDuration(t) {
  if (t == null) return null;
  if (t >= 86400) return `${(t / 86400).toFixed(t % 86400 ? 1 : 0)} d`;
  if (t >= 3600) return `${(t / 3600).toFixed(t % 3600 ? 1 : 0)} h`;
  if (t >= 60) return `${(t / 60).toFixed(0)} min`;
  return `${t} s`;
}

/* A section route: #/<section>/<rest>. `rest` is encoded as one segment, so a
   benchmark path like HAMSTAD/B2 survives as a single argument. */
const link = (section, rest) =>
  "#/" + section + (rest ? "/" + encodeURIComponent(rest) : "");

function crumbs(trail) {
  return `<nav class="crumbs" aria-label="Breadcrumb">${trail.map(([label, href], i) =>
    (href ? `<a href="${href}">${esc(label)}</a>` : `<span aria-current="page">${esc(label)}</span>`)
    + (i < trail.length - 1 ? `<span class="sep" aria-hidden="true">/</span>` : "")).join("")}</nav>`;
}

/* -------------------------------------------------------------- markdown */
/* Small block-level renderer: headings, fences, tables, lists, quotes, rules. */

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2">$1</a>');
}

function markdown(src) {
  const lines = src.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0, para = [], list = null;

  const flushPara = () => {
    if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
  };
  const flushList = () => {
    if (list) { out.push(`</${list}>`); list = null; }
  };
  const flush = () => { flushPara(); flushList(); };

  while (i < lines.length) {
    const line = lines[i];

    if (/^```/.test(line)) {                                   // fenced code
      flush();
      const body = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) body.push(lines[i++]);
      i++;
      out.push(`<pre><code>${esc(body.join("\n"))}</code></pre>`);
      continue;
    }

    if (!line.trim()) { flush(); i++; continue; }

    if (/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {        // rule
      flush(); out.push("<hr>"); i++; continue;
    }

    const h = line.match(/^(#{1,6})\s+(.*)$/);                  // heading
    if (h) {
      flush();
      const lvl = Math.min(h[1].length, 3);
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      i++; continue;
    }

    if (/^\s*\|/.test(line) && /^\s*\|[\s:|-]+\|?\s*$/.test(lines[i + 1] || "")) {
      flush();                                                  // table
      const row = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = row(line);
      i += 2;
      const body = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) body.push(row(lines[i++]));
      out.push(
        "<table><thead><tr>" + head.map((c) => `<th>${inline(c)}</th>`).join("") +
        "</tr></thead><tbody>" +
        body.map((r) => "<tr>" + r.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>").join("") +
        "</tbody></table>"
      );
      continue;
    }

    const li = line.match(/^\s*([-*+]|\d+\.)\s+(.*)$/);         // list item
    if (li) {
      flushPara();
      const want = /^\d/.test(li[1]) ? "ol" : "ul";
      if (list !== want) { flushList(); out.push(`<${want}>`); list = want; }
      out.push(`<li>${inline(li[2])}</li>`);
      i++; continue;
    }

    const bq = line.match(/^>\s?(.*)$/);                        // blockquote
    if (bq) {
      flush();
      const body = [];
      while (i < lines.length && /^>/.test(lines[i])) body.push(lines[i++].replace(/^>\s?/, ""));
      out.push(`<blockquote>${markdown(body.join("\n"))}</blockquote>`);
      continue;
    }

    flushList();
    para.push(line.trim());
    i++;
  }
  flush();
  return out.join("\n");
}

/* --------------------------------------------------------------- icons */

const ICON = {
  benchmarks: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V5M4 19h16"/>
    <path d="M7.5 15.5c2-4.5 3.5-7 5.5-7s3 3 6 3.5"/><circle cx="13" cy="8.5" r="1.2"/></svg>`,
  workflows: `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="7" height="5" rx="1.5"/>
    <rect x="13.5" y="15.5" width="7" height="5" rx="1.5"/><rect x="13.5" y="3.5" width="7" height="5" rx="1.5"/>
    <path d="M10.5 6h3M17 8.5v7"/><path d="M13.5 18H7a2 2 0 0 1-2-2v-7.5"/></svg>`,
  arrow: `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9.5M9 4.5 12.5 8 9 11.5"/></svg>`,
};

/* ------------------------------------------------------------- sections */
/* Every top-level section registers itself here. The home page cards, the top
   navigation and the router are all built from this one list, so a new section
   is one registerSection() call in its own file. */

const SECTIONS = [];

function registerSection(section) {
  SECTIONS.push(section);
}

function renderTopnav(activeId) {
  document.getElementById("topnav").innerHTML = SECTIONS.map((s) =>
    `<a href="${link(s.id)}"${s.id === activeId ? ' aria-current="page"' : ""}>${esc(s.title)}</a>`
  ).join("");
}

/* -------------------------------------------------------------- lightbox */

const lb = document.getElementById("lightbox");
const lbImg = document.getElementById("lightbox-img");
const lbCap = document.getElementById("lightbox-caption");

function openLightbox(src, caption) {
  lbImg.src = src;
  lbCap.textContent = caption || "";
  lb.hidden = false;
}
function closeLightbox() { lb.hidden = true; lbImg.src = ""; }
lb.addEventListener("click", closeLightbox);
const lightboxOpen = () => !lb.hidden;

/* ---------------------------------------------------------------- router */

function route() {
  const raw = location.hash.replace(/^#/, "");
  // Links from before the home page existed: #HAMSTAD%2FB2 meant a benchmark.
  if (raw && !raw.startsWith("/")) {
    location.replace(link("benchmarks", decodeURIComponent(raw)));
    return;
  }
  const [id = "", ...rest] = raw.replace(/^\//, "").split("/");
  const arg = rest.map(decodeURIComponent).join("/");
  const section = SECTIONS.find((s) => s.id === id);

  for (const s of SECTIONS) s.leave?.();
  renderTopnav(section?.id);
  view.dataset.width = "normal";
  if (section) section.render(arg);
  else renderHome();
  window.scrollTo(0, 0);
}

window.addEventListener("hashchange", route);
