"use strict";
/* workflows.js -- the worker jobs as diagrams (#/workflows, #/workflows/<job>).

   Each job describes itself in worker/jobs/<job>/dataflow.json (schema:
   worker/schemas/dataflow.schema.json). build_index.py copies those files into
   the index; this file lays each one out top to bottom and turns its flags into
   controls, so a reader can switch a flag and watch which steps and loops it
   changes -- and copy the command that does it. */

const KIND = {
  input: "Input",
  llm: "Model call",
  tool: "Agent tool",
  check: "Check",
  step: "Step",
  solver: "Solver run",
  artifact: "Output",
  human: "Person",
};

const EDGE_KIND = {
  data: "Data",
  control: "Control / gate",
  tool: "Tool call",
  feedback: "Feedback loop",
};

/* Geometry, in CSS pixels at 100 %. */
const L = {
  NW: 152, NH: 62,     // node box: a kind line and two label lines
  CG: 24,              // gap between neighbours in a row
  RG: 52,              // free gap between rows, where edges bend
  LW: 8,               // an edge lane
  LG: 20,              // gap beside a lane
  PAD: 28,             // canvas margin
  GPX: 18,             // group side padding
  GL: 28,              // group label strip (top)
  GB: 14,              // group bottom padding
};

/* ================================================================ layout */

/* Rows follow the longest path over forward edges. Feedback edges, and any edge
   that closes a cycle, are drawn as loops beside the boxes. An edge that skips
   rows gets a vertical lane of its own, so it never runs behind a box and looks
   like part of a chain it is not in. Siblings are packed left to right so no two
   boxes -- nodes, groups or lanes -- ever overlap. */
function layoutFlow(doc) {
  const nodes = doc.nodes;
  const index = new Map(nodes.map((n, i) => [n.id, i]));
  const node = new Map(nodes.map((n) => [n.id, n]));
  const groups = new Map((doc.groups || []).map((g) => [g.id, g]));
  const edges = doc.edges.map((e, i) => ({ ...e, i, kind: e.kind || "data" }));

  /* 1. Which edges point back. */
  const out = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) {
    if (e.kind === "feedback" || e.from === e.to) e.back = true;
    else out.get(e.from).push(e);
  }
  const seen = new Map();
  const visit = (id) => {
    seen.set(id, 1);
    for (const e of out.get(id)) {
      const s = seen.get(e.to);
      if (s === 1) e.back = true;
      else if (!s) visit(e.to);
    }
    seen.set(id, 2);
  };
  nodes.forEach((n) => { if (!seen.get(n.id)) visit(n.id); });

  /* 2. Rows. */
  const preds = new Map(nodes.map((n) => [n.id, []]));
  const succs = new Map(nodes.map((n) => [n.id, []]));
  for (const e of edges) if (!e.back) { preds.get(e.to).push(e.from); succs.get(e.from).push(e.to); }
  const row = new Map();
  const rowOf = (id) => {
    if (!row.has(id)) row.set(id, Math.max(0, ...preds.get(id).map((p) => rowOf(p) + 1)));
    return row.get(id);
  };
  nodes.forEach((n) => rowOf(n.id));
  // A source inside a group (a budget, a gate) sits just above what it feeds,
  // not up with the inputs.
  for (const n of nodes) {
    if (n.group && !preds.get(n.id).length && succs.get(n.id).length) {
      row.set(n.id, Math.max(0, Math.min(...succs.get(n.id).map((s) => row.get(s))) - 1));
    }
  }

  /* 3. Containment tree. */
  const chain = (gid) => {
    const c = [];
    while (groups.has(gid)) { c.unshift(gid); gid = groups.get(gid).parent; }
    return c;
  };
  const commonGroup = (a, b) => {
    const ca = chain(node.get(a).group), cb = chain(node.get(b).group);
    let g = "";
    for (let k = 0; k < Math.min(ca.length, cb.length) && ca[k] === cb[k]; k++) g = ca[k];
    return g;
  };
  // An unknown parent or group falls back to the top level rather than failing.
  const kids = new Map([["", []]]);
  groups.forEach((_, id) => kids.set(id, []));
  groups.forEach((g) => kids.get(groups.has(g.parent) ? g.parent : "").push({ type: "group", id: g.id }));
  nodes.forEach((n) => kids.get(groups.has(n.group) ? n.group : "").push({ type: "node", id: n.id }));

  /* 4. Lanes. A forward lane runs inside the deepest group around either end
     that already spans its rows -- so an input feeding a step deep in a group
     drops straight in beside it instead of detouring round the whole box.
     Forward edges from one source at one level share a lane. */
  const rowsOf = new Map();
  const groupRows = (gid) => {
    if (!rowsOf.has(gid)) {
      const rs = [...nodes.filter((n) => n.group === gid).map((n) => row.get(n.id)),
        ...[...groups.values()].filter((g) => g.parent === gid).flatMap((g) => groupRows(g.id))];
      rowsOf.set(gid, rs.length ? [Math.min(...rs), Math.max(...rs)] : []);
    }
    return rowsOf.get(gid);
  };
  const laneLevel = (e, r0, r1) => {
    const depth = (g) => chain(g).length;
    const around = [...chain(node.get(e.to).group), ...chain(node.get(e.from).group)]
      .filter((g) => { const s = groupRows(g); return s.length && s[0] <= r0 && s[1] >= r1; })
      .sort((a, b) => depth(b) - depth(a));
    return around[0] || "";
  };
  const shared = new Map();
  for (const e of edges) {
    const r0 = row.get(e.from), r1 = row.get(e.to);
    const level = e.back || r1 - r0 <= 1 ? commonGroup(e.from, e.to) : laneLevel(e, r0 + 1, r1 - 1);
    if (e.back) {
      e.lane = { type: "lane", back: true, r0: Math.min(r0, r1), r1: Math.max(r0, r1) };
      kids.get(level).push(e.lane);
    } else if (r1 - r0 > 1) {
      const key = `${e.from}|${level}`;
      let lane = shared.get(key);
      if (!lane) {
        lane = { type: "lane", r0: r0 + 1, r1: r1 - 1 };
        shared.set(key, lane);
        kids.get(level).push(lane);
      }
      lane.r1 = Math.max(lane.r1, r1 - 1);
      e.lane = lane;
    }
  }

  /* 5. Pack. */
  const extraTop = [], extraBottom = [];
  const spanOf = (it) => {
    if (it.type === "node") { const r = row.get(it.id); return [r, r, index.get(it.id)]; }
    if (it.type === "lane") return [it.r0, it.r1, Infinity];
    const s = kids.get(it.id).filter((k) => k.type !== "lane").map(spanOf).filter(Boolean);
    if (!s.length) return null;
    return [Math.min(...s.map((x) => x[0])), Math.max(...s.map((x) => x[1])),
      Math.min(...s.map((x) => x[2]))];
  };
  const gapBetween = (a, b) => (a.type === "lane" || b.type === "lane" ? L.LG : L.CG);

  function pack(list) {
    const items = list
      .map((it) => { const s = spanOf(it); return s && Object.assign(it, { r0: s[0], r1: s[1], k: s[2] }); })
      .filter(Boolean)
      // Lanes last, so they take whatever room the boxes leave.
      .sort((a, b) => (a.type === "lane") - (b.type === "lane") || a.r0 - b.r0 || a.k - b.k);
    const placed = [];
    let width = 0;
    for (const it of items) {
      if (it.type === "node") it.w = L.NW;
      else if (it.type === "lane") it.w = L.LW;
      else {
        it.inner = pack(kids.get(it.id));
        it.w = Math.max(it.inner.w + 2 * L.GPX, labelWidth(groups.get(it.id)));
        const sub = it.inner.items.filter((c) => c.type === "group");
        it.dStart = 1 + Math.max(0, ...sub.filter((c) => c.r0 === it.r0).map((c) => c.dStart));
        it.dEnd = 1 + Math.max(0, ...sub.filter((c) => c.r1 === it.r1).map((c) => c.dEnd));
        extraTop[it.r0] = Math.max(extraTop[it.r0] || 0, it.dStart);
        extraBottom[it.r1] = Math.max(extraBottom[it.r1] || 0, it.dEnd);
      }
      let x = 0, moved = true;
      while (moved) {
        moved = false;
        for (const p of placed) {
          const g = gapBetween(p, it);
          if (p.r0 <= it.r1 && it.r0 <= p.r1 && x < p.x + p.w + g && p.x < x + it.w + g) {
            x = p.x + p.w + g;
            moved = true;
          }
        }
      }
      it.x = x;
      placed.push(it);
      width = Math.max(width, x + it.w);
    }
    return { items: placed, w: width };
  }
  const root = pack(kids.get(""));

  /* 6. Rows to y. */
  const nRows = Math.max(0, ...row.values()) + 1;
  const top = [];
  // The free band above each row, clear of group borders and title strips.
  // Edges bend only in there and cross the strips straight down.
  const gapLo = [], gapHi = [];
  let y = L.PAD;
  for (let r = 0; r < nRows; r++) {
    gapLo[r] = y - L.RG;
    gapHi[r] = y;
    y += (extraTop[r] || 0) * L.GL;
    top[r] = y;
    y += L.NH + (extraBottom[r] || 0) * L.GB + L.RG;
  }
  const height = y - L.RG + L.PAD;
  const width = root.w + 2 * L.PAD;

  /* 7. Absolute positions. */
  const pos = new Map();
  const boxes = [];
  (function place(items, x0, depth) {
    for (const it of items) {
      const x = x0 + it.x;
      if (it.type === "node") pos.set(it.id, { x, y: top[it.r0], w: L.NW, h: L.NH });
      else if (it.type === "lane") it.cx = x + L.LW / 2;
      else {
        const y0 = top[it.r0] - it.dStart * L.GL;
        const y1 = top[it.r1] + L.NH + it.dEnd * L.GB;
        boxes.push({ id: it.id, group: groups.get(it.id), depth, x, y: y0, w: it.w, h: y1 - y0 });
        place(it.inner.items, x + L.GPX, depth + 1);
      }
    }
  })(root.items, L.PAD, 0);

  /* 8. Edge paths. */
  const bend = (xa, xb, r) => {
    const m = (gapLo[r] + gapHi[r]) / 2;
    return `L${xa},${gapLo[r]} C${xa},${m} ${xb},${m} ${xb},${gapHi[r]}`;
  };
  for (const e of edges) {
    const a = pos.get(e.from), b = pos.get(e.to);
    if (e.back) {
      const lx = e.lane.cx;
      const side = (n) => (lx >= n.x + n.w / 2 ? n.x + n.w : n.x);
      let y1 = a.y + a.h / 2, y2 = b.y + b.h / 2;
      if (Math.abs(y1 - y2) < 1) { y1 -= 10; y2 += 10; }
      e.d = roundedPath([[side(a), y1], [lx, y1], [lx, y2], [side(b), y2]], 10);
      e.mid = { x: lx, y: (y1 + y2) / 2 };
    } else {
      const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y;
      const ra = row.get(e.from), rb = row.get(e.to);
      e.d = e.lane
        ? `M${x1},${y1} ${bend(x1, e.lane.cx, ra + 1)} ${bend(e.lane.cx, x2, rb)} L${x2},${y2}`
        : `M${x1},${y1} ${bend(x1, x2, rb)} L${x2},${y2}`;
    }
  }

  /* 9. Loop badges sit halfway along their lane; lanes side by side over the
     same rows would stack them, so push each one clear of those before it. */
  const badges = edges.filter((e) => e.back).sort((a, b) => a.mid.y - b.mid.y || a.mid.x - b.mid.x);
  badges.forEach((e, k) => {
    for (let j = 0; j < k; j++) {
      const o = badges[j].mid;
      if (Math.abs(o.x - e.mid.x) < 64 && Math.abs(o.y - e.mid.y) < 26) e.mid.y = o.y + 28;
    }
  });

  return { width, height, pos, boxes, edges };
}

/* Roughly how wide a group's title strip is, so a narrow group is widened to
   show it whole instead of cutting it off. */
function labelWidth(g) {
  const cond = g.enabled_by ? conditionText(g.enabled_by) : "";
  return 26 + g.label.length * 6.7 + (cond ? 18 + cond.length * 6.5 : 0);
}
const conditionText = (sw) => (sw.startsWith("!") ? `without ${sw.slice(1)}` : sw);

/* An orthogonal polyline with its corners rounded off. */
function roundedPath(pts, r) {
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1], [x, y] = pts[i], [nx, ny] = pts[i + 1];
    const d1 = Math.hypot(x - px, y - py), d2 = Math.hypot(nx - x, ny - y);
    const k = Math.min(r, d1 / 2, d2 / 2);
    const ax = x - ((x - px) / (d1 || 1)) * k, ay = y - ((y - py) / (d1 || 1)) * k;
    const bx = x + ((nx - x) / (d2 || 1)) * k, by = y + ((ny - y) / (d2 || 1)) * k;
    d += ` L${ax},${ay} Q${x},${y} ${bx},${by}`;
  }
  const last = pts[pts.length - 1];
  return d + ` L${last[0]},${last[1]}`;
}

/* ================================================================= state */

/* One workflow page at a time. */
let wf = null;

const paramName = (p) => p.flag || p.env;
const optionalArg = (p) => !!p.arg && p.arg.startsWith("[");
/* A flag is controlled by a switch (on/off), a text field for its argument, or
   both: a toggle that takes an argument, or an argument that may be left out. */
const hasSwitch = (p) => !p.arg || optionalArg(p) || p.kind === "toggle";
const hasText = (p) => !!p.arg;

function freshState(doc) {
  const on = {}, text = {}, num = {};
  for (const p of doc.params) {
    if (!p.flag || p.kind === "mode" || p.kind === "info") continue;
    if (p.kind === "loop-limit") { num[p.flag] = p.default ?? 0; continue; }
    if (hasSwitch(p)) on[p.flag] = p.default === true;
    if (hasText(p)) text[p.flag] = "";
  }
  return { mode: "", modeArg: "", on, text, num, selected: null, scale: null };
}

function paramActive(p, st) {
  if (p.kind === "loop-limit") {
    const v = Number(st.num[p.flag]);
    return v > 0 && v !== Number(p.default ?? 0);
  }
  return hasSwitch(p) ? st.on[p.flag] === true : !!(st.text[p.flag] || "").trim();
}

/* Toggles that are on: set directly, or switched on by another flag. */
function flagsOn(doc, st) {
  const on = new Set(), impliedBy = new Map();
  if (st.mode) return { on, impliedBy };
  for (const p of doc.params) {
    if (!p.flag || p.kind === "mode" || p.kind === "info") continue;
    if (!paramActive(p, st)) continue;
    if (p.kind === "toggle") on.add(p.flag);
    for (const f of p.implies || []) {
      on.add(f);
      impliedBy.set(f, p.flag);
    }
  }
  return { on, impliedBy };
}

function switchOn(doc, sw, on) {
  if (!sw) return true;
  const negate = sw.startsWith("!");
  const s = negate ? sw.slice(1) : sw;
  let v;
  if (s.startsWith("--")) v = on.has(s);
  else {
    const [name, want] = s.split("=");
    const p = doc.params.find((x) => x.env === name);
    v = p ? String(p.default) === want : false;
  }
  return negate ? !v : v;
}

function limitValue(doc, st, limit) {
  if (!limit) return null;
  if (limit.flag) return Number(st.num[limit.flag] ?? 0);
  if (limit.env) return doc.params.find((p) => p.env === limit.env)?.default ?? null;
  return limit.value ?? null;
}

/* What is live in the diagram for the current flags. */
function liveGraph(doc, st) {
  const { on, impliedBy } = flagsOn(doc, st);
  const mode = st.mode ? doc.params.find((p) => p.flag === st.mode) : null;
  const groups = new Map((doc.groups || []).map((g) => [g.id, g]));
  const groupOn = (gid) => {
    for (let g = groups.get(gid); g; g = groups.get(g.parent)) {
      if (!switchOn(doc, g.enabled_by, on)) return false;
    }
    return true;
  };
  const nodeOn = new Map(doc.nodes.map((n) => [n.id, mode
    ? mode.runs.includes(n.id)
    : switchOn(doc, n.enabled_by, on) && groupOn(n.group)]));
  const groupLive = new Map([...groups.keys()].map((id) => [id, mode
    ? doc.nodes.some((n) => nodeOn.get(n.id) && inGroup(groups, n.group, id))
    : groupOn(id)]));
  const edgeOn = doc.edges.map((e) => {
    if (!nodeOn.get(e.from) || !nodeOn.get(e.to)) return false;
    if (mode) return true;
    if (!switchOn(doc, e.enabled_by, on)) return false;
    const lim = limitValue(doc, st, e.limit);
    return lim == null || lim > 0;
  });
  return { on, impliedBy, nodeOn, groupLive, edgeOn };
}

function inGroup(groups, gid, target) {
  for (let g = groups.get(gid); g; g = groups.get(g.parent)) if (g.id === target) return true;
  return false;
}

function commandLine(doc, st) {
  const parts = [doc.command];
  const { impliedBy } = flagsOn(doc, st);
  if (st.mode) {
    const p = doc.params.find((x) => x.flag === st.mode);
    parts.push(p.flag);
    const arg = st.modeArg.trim();
    if (arg) parts.push(arg);
    else if (p.arg && !p.arg.startsWith("[")) parts.push(p.arg);
  }
  for (const p of doc.params) {
    if (!p.flag || p.kind === "mode" || p.kind === "info") continue;
    if (st.mode && (p.kind === "toggle" || p.kind === "loop-limit")) continue;
    if (p.kind === "loop-limit") {
      const v = Number(st.num[p.flag]);
      if (v !== Number(p.default ?? 0)) parts.push(`${p.flag} ${v}`);
      continue;
    }
    if (!paramActive(p, st) || impliedBy.has(p.flag)) continue;
    const text = (st.text[p.flag] || "").trim();
    // A required argument left empty shows its placeholder, so the gap is visible.
    parts.push(p.flag + (text ? ` ${text}` : p.arg && !optionalArg(p) ? ` ${p.arg}` : ""));
  }
  return parts.join(" ");
}

/* Every element a param changes, for hover highlighting. */
function touchedBy(doc, p) {
  const name = paramName(p);
  const strip = (s) => (s || "").replace(/^!/, "");
  const nodes = new Set([...(p.affects || []), ...(p.runs || [])]);
  doc.nodes.forEach((n) => { if (strip(n.enabled_by) === name) nodes.add(n.id); });
  const groups = new Set((doc.groups || []).filter((g) => strip(g.enabled_by) === name).map((g) => g.id));
  const edges = new Set();
  doc.edges.forEach((e, i) => {
    const lim = e.limit || {};
    if (strip(e.enabled_by) === name || lim.flag === name || lim.env === name) edges.add(i);
  });
  return { nodes, groups, edges };
}

/* ================================================================ render */

const loopEdges = (lay) => lay.edges.filter((e) => e.back);

function loopText(doc, st, e) {
  const v = limitValue(doc, st, e.limit);
  return v == null ? "" : `≤ ${v}×`;
}

function renderDiagram(doc, lay) {
  const loops = loopEdges(lay);
  const loopNo = new Map(loops.map((e, k) => [e.i, k + 1]));
  const byId = new Map(doc.nodes.map((n) => [n.id, n]));

  const svg = `<svg class="flow-svg" width="${lay.width}" height="${lay.height}" aria-hidden="true">
    <defs>
      ${["data", "control", "tool", "feedback"].map((k) => `
      <marker id="ah-${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
              orient="auto-start-reverse"><path d="M1,1.5 L9,5 L1,8.5 Z" class="ah ah-${k}"/></marker>`).join("")}
    </defs>
    ${lay.boxes.map((b) => `<rect class="fg-box depth-${Math.min(b.depth, 2)}" data-group="${b.id}"
        x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="14"/>`).join("")}
    ${lay.edges.map((e) => {
      const kind = e.back && e.kind !== "control" ? "feedback" : e.kind;
      const start = e.kind === "tool" ? `marker-start="url(#ah-tool)"` : "";
      const tip = [e.label, e.when && `when ${e.when}`, e.carries].filter(Boolean).join(" — ");
      return `<g class="fe fe-${kind}${e.back ? " is-back" : ""}" data-edge="${e.i}">
        <path class="fe-hit" d="${e.d}"><title>${esc(byId.get(e.from).label)} → ${esc(byId.get(e.to).label)}${tip ? "\n" + esc(tip) : ""}</title></path>
        <path class="fe-line" d="${e.d}" marker-end="url(#ah-${kind})" ${start}/>
      </g>`;
    }).join("")}
  </svg>`;

  const labels = lay.boxes.map((b) =>
    `<div class="fg-label depth-${Math.min(b.depth, 2)}" data-group="${b.id}" style="left:${b.x + 8}px;top:${b.y + 6}px;max-width:${b.w - 16}px"
          title="${esc(b.group.detail || "")}">${esc(b.group.label)}${b.group.enabled_by
            ? ` <span class="fg-cond">${esc(conditionText(b.group.enabled_by))}</span>` : ""}</div>`).join("");

  const nodes = doc.nodes.map((n) => {
    const p = lay.pos.get(n.id);
    return `<button class="fn k-${n.kind}" data-node="${n.id}" type="button"
        style="left:${p.x}px;top:${p.y}px;width:${p.w}px;height:${p.h}px">
      <span class="fn-kind"><i></i>${esc(KIND[n.kind] || n.kind)}</span>
      <span class="fn-label">${esc(n.label)}</span>
    </button>`;
  }).join("");

  const pills = loops.map((e) =>
    `<div class="fl-pill" data-edge="${e.i}" style="left:${e.mid.x}px;top:${e.mid.y}px"
          title="${esc(e.label || "loop")}"><b>${loopNo.get(e.i)}</b><span class="fl-lim"></span></div>`).join("");

  return `<div class="flow-stage" style="width:${lay.width}px;height:${lay.height}px">
    ${svg}${labels}${nodes}${pills}</div>`;
}

function renderLegend(doc, lay) {
  const kinds = [...new Set(doc.nodes.map((n) => n.kind))];
  const edgeKinds = new Set(lay.edges.map((e) => (e.back && e.kind !== "control" ? "feedback" : e.kind)));
  return `<div class="legend">
    ${kinds.map((k) => `<span class="lg-node k-${k}"><i></i>${esc(KIND[k])}</span>`).join("")}
    <span class="lg-sep"></span>
    ${["data", "control", "tool", "feedback"].filter((k) => edgeKinds.has(k)).map((k) =>
      `<span class="lg-edge"><svg width="26" height="10"><path class="lg-line fe-${k}" d="M1,5 H25"/></svg>${esc(EDGE_KIND[k])}</span>`).join("")}
  </div>`;
}

function switchCtl(p) {
  return `<label class="switch"><input type="checkbox" data-on="${esc(p.flag)}"
    aria-label="${esc(p.flag)}"><span></span></label>`;
}
function textCtl(p, placeholder) {
  return `<input class="ctl-input sub" type="text" data-text="${esc(p.flag)}"
    placeholder="${esc(placeholder)}" spellcheck="false"${hasSwitch(p) ? " hidden" : ""}>`;
}
const flagControls = (p, placeholder) =>
  paramRow(p, hasSwitch(p) ? switchCtl(p) : "") + (hasText(p) ? textCtl(p, placeholder) : "");

function paramRow(p, control) {
  return `<div class="ctl-row" data-param="${esc(paramName(p))}">
    ${control}
    <div class="ctl-text">
      <code>${esc(paramName(p))}</code>${p.arg ? ` <span class="ctl-arg">${esc(p.arg)}</span>` : ""}
      ${p.metered ? `<span class="tag-metered" title="Can spend money">metered</span>` : ""}
      <small>${esc(p.help)}</small>
    </div>
  </div>`;
}

function renderControls(doc) {
  const by = (kind) => doc.params.filter((p) => p.kind === kind && p.flag);
  const modes = by("mode"), toggles = by("toggle"), limits = by("loop-limit");
  const more = doc.params.filter((p) => p.flag && (p.kind === "selection" || p.kind === "tuning"));
  const env = doc.params.filter((p) => p.env);
  const info = by("info");

  const modeRows = modes.length ? `
    <section class="ctl-sec" id="ctl-modes">
      <h4>What to run</h4>
      <label class="ctl-radio"><input type="radio" name="wf-mode" value="" checked>
        <span><b>Full run</b><small>Every step the switches below turn on.</small></span></label>
      ${modes.map((p) => `<label class="ctl-radio" data-param="${esc(p.flag)}">
        <input type="radio" name="wf-mode" value="${esc(p.flag)}">
        <span><code>${esc(p.flag)}</code>${p.metered ? ` <span class="tag-metered">metered</span>` : ""}
          <small>${esc(p.help)}</small></span></label>`).join("")}
      <input class="ctl-input" id="wf-mode-arg" type="text" hidden spellcheck="false">
    </section>` : "";

  const toggleRows = toggles.length ? `
    <section class="ctl-sec" id="ctl-toggles">
      <h4>Switches</h4>
      ${toggles.map((p) => flagControls(p, p.arg)).join("")}
    </section>` : "";

  const limitRows = limits.length ? `
    <section class="ctl-sec" id="ctl-limits">
      <h4>Loop limits</h4>
      ${limits.map((p) => paramRow(p, `<span class="stepper">
          <button type="button" data-step="-1" data-num="${esc(p.flag)}" aria-label="Fewer">−</button>
          <input type="number" min="0" max="99" data-num="${esc(p.flag)}" value="${p.default ?? 0}" aria-label="${esc(p.flag)}">
          <button type="button" data-step="1" data-num="${esc(p.flag)}" aria-label="More">+</button>
        </span>`)).join("")}
    </section>` : "";

  const moreRows = more.length ? `
    <details class="ctl-sec ctl-more">
      <summary>More options <span class="n">${more.length}</span></summary>
      ${more.map((p) => flagControls(p, p.default != null ? `${p.arg} (default ${p.default})` : p.arg)).join("")}
    </details>` : "";

  const envRows = env.length ? `
    <details class="ctl-sec ctl-more">
      <summary>Set in config <span class="n">${env.length}</span></summary>
      <p class="ctl-note">Read from the file, not from the command line.</p>
      ${env.map((p) => paramRow(p, `<span class="ctl-value">${esc(p.default)}</span>`)
        .replace("</small>", ` · <code>${esc(p.source)}</code></small>`)).join("")}
    </details>` : "";

  const infoRows = info.length ? `
    <details class="ctl-sec ctl-more">
      <summary>Other commands <span class="n">${info.length}</span></summary>
      <p class="ctl-note">Each prints something and exits.</p>
      ${info.map((p) => paramRow(p, "")).join("")}
    </details>` : "";

  return modeRows + toggleRows + limitRows + moreRows + envRows + infoRows;
}

function renderLoops(doc, lay) {
  const loops = loopEdges(lay);
  if (!loops.length) {
    return `<section class="loops"><h3 class="section-title">Loops</h3>
      <p class="muted">This workflow has no loop: each step runs once.</p></section>`;
  }
  const byId = new Map(doc.nodes.map((n) => [n.id, n]));
  return `<section class="loops"><h3 class="section-title">Loops</h3>
    <div class="loop-grid">${loops.map((e, k) => `
      <article class="loop-card" data-edge="${e.i}">
        <header><span class="loop-no">${k + 1}</span>
          <h4>${esc(e.label || `${byId.get(e.from).label} → ${byId.get(e.to).label}`)}</h4>
          <span class="loop-lim"></span></header>
        <p class="loop-path">${esc(byId.get(e.from).label)} <span>→</span> ${esc(byId.get(e.to).label)}</p>
        <dl>
          ${e.when ? `<dt>When</dt><dd>${esc(e.when)}</dd>` : ""}
          ${e.carries ? `<dt>${e.kind === "feedback" ? "Sends back" : "Carries"}</dt><dd>${esc(e.carries)}</dd>` : ""}
          ${e.limit ? `<dt>Limit</dt><dd>${e.limit.flag ? `<code>${esc(e.limit.flag)}</code>`
            : e.limit.env ? `<code>${esc(e.limit.env)}</code>`
            : `${esc(e.limit.value)} (${esc(e.limit.source || "fixed")})`}</dd>` : ""}
        </dl>
      </article>`).join("")}</div></section>`;
}

function drawerHtml(doc, id) {
  const n = doc.nodes.find((x) => x.id === id);
  const byId = new Map(doc.nodes.map((x) => [x.id, x]));
  const inc = doc.edges.filter((e) => e.to === id), outg = doc.edges.filter((e) => e.from === id);
  const edgeLi = (e, other) => `<li><button type="button" class="linkish" data-goto="${other.id}">${esc(other.label)}</button>
    ${e.kind && e.kind !== "data" ? `<span class="badge plain">${esc(EDGE_KIND[e.kind])}</span>` : ""}
    ${e.when || e.carries ? `<small>${esc([e.when && `when ${e.when}`, e.carries].filter(Boolean).join(" — "))}</small>` : ""}</li>`;
  const params = doc.params.filter((p) => touchedBy(doc, p).nodes.has(id));
  const group = (doc.groups || []).find((g) => g.id === n.group);

  return `<header class="drawer-head">
      <span class="fn-kind k-${n.kind}"><i></i>${esc(KIND[n.kind])}</span>
      <button class="drawer-close" type="button" aria-label="Close">×</button>
    </header>
    <h3>${esc(n.label)}</h3>
    ${group ? `<p class="drawer-group">in <b>${esc(group.label)}</b></p>` : ""}
    ${n.detail ? `<p class="drawer-detail">${esc(n.detail)}</p>` : ""}
    <dl class="drawer-facts">
      ${n.path ? `<dt>Where</dt><dd><code>${esc(n.path)}</code></dd>` : ""}
      ${n.code ? `<dt>Code</dt><dd><code>${esc(n.code)}</code></dd>` : ""}
      ${n.enabled_by ? `<dt>Runs when</dt><dd><code>${esc(n.enabled_by)}</code></dd>` : ""}
    </dl>
    ${inc.length ? `<h4>Comes from</h4><ul class="drawer-edges">${inc.map((e) => edgeLi(e, byId.get(e.from))).join("")}</ul>` : ""}
    ${outg.length ? `<h4>Goes to</h4><ul class="drawer-edges">${outg.map((e) => edgeLi(e, byId.get(e.to))).join("")}</ul>` : ""}
    ${params.length ? `<h4>Changed by</h4><ul class="drawer-params">${params.map((p) =>
      `<li><code>${esc(paramName(p))}</code><small>${esc(p.help)}</small></li>`).join("")}</ul>` : ""}`;
}

/* ============================================================ the pages */

function renderWorkflowList() {
  const flows = DATA.dataflows;
  view.innerHTML = `
    <header class="page-head">
      <h1>Workflows</h1>
      <p class="lede">What each worker job does, step by step: where a model is called,
        where the solver runs, which loops send results back, and which flag changes what.</p>
    </header>
    ${flows.length ? `<div class="grid wf-grid">${flows.map((d) => {
      const lay = layoutFlow(d);
      const n = loopEdges(lay).filter((e) => e.kind === "feedback").length;
      return `<a class="card wf-card" href="${link("workflows", d.job)}">
        <p class="eyebrow">${esc(d.command)}</p>
        <h2>${esc(d.title)}</h2>
        <p class="source clamp">${esc(d.summary)}</p>
        <div class="card-foot"><span class="meta">${[plural(d.nodes.length, "step"),
          plural(n, "feedback loop"), plural(d.params.length, "option")].join(" · ")}</span>
          ${d.problems?.length ? `<span class="badge bad" title="check_dataflow.py found problems">out of date?</span>` : ""}</div>
      </a>`;
    }).join("")}</div>`
    : `<div class="empty"><p>No <code>worker/jobs/*/dataflow.json</code> found.</p></div>`}`;
}

function renderWorkflow(doc) {
  view.dataset.width = "wide";
  const lay = layoutFlow(doc);
  wf = { doc, lay, st: freshState(doc) };

  view.innerHTML = `
    ${crumbs([["Workflows", link("workflows")], [doc.title]])}
    <header class="page-head">
      <h1>${esc(doc.title)}</h1>
      <p class="lede">${esc(doc.summary)}</p>
    </header>
    ${doc.problems?.length ? `<div class="notice">
      <b>This diagram may be out of date.</b> <code>worker/tools/check_dataflow.py</code> reports:
      <ul>${doc.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></div>` : ""}
    <div class="cmdbar">
      <span class="cmd-prompt">$</span>
      <code id="wf-cmd"></code>
      <button type="button" class="btn" id="wf-copy">Copy</button>
    </div>
    <div class="wf-layout">
      <aside class="wf-controls" aria-label="Flags">${renderControls(doc)}</aside>
      <div class="wf-main">
        <div class="wf-toolbar">
          ${renderLegend(doc, lay)}
          <div class="zoom" role="group" aria-label="Zoom">
            <button type="button" data-zoom="-1" aria-label="Zoom out">−</button>
            <button type="button" data-zoom="0" id="wf-zoom" title="Fit to width">100%</button>
            <button type="button" data-zoom="1" aria-label="Zoom in">+</button>
          </div>
        </div>
        <div class="flow-canvas" id="wf-canvas">
          <div class="flow-zoom" id="wf-zoomer">${renderDiagram(doc, lay)}</div>
        </div>
        <p class="wf-hint">Click a step for details. Hover a flag to see what it touches.</p>
        ${renderLoops(doc, lay)}
      </div>
    </div>
    <aside class="drawer" id="wf-drawer" hidden aria-label="Step details"></aside>`;

  bindWorkflow();
  fitZoom();
  applyState();
}

/* ------------------------------------------------------------ behaviour */

function applyState() {
  const { doc, lay, st } = wf;
  const live = liveGraph(doc, st);
  const q = (sel) => view.querySelectorAll(sel);

  q(".fn").forEach((el) => el.classList.toggle("off", !live.nodeOn.get(el.dataset.node)));
  q("[data-group]").forEach((el) => el.classList.toggle("off", !live.groupLive.get(el.dataset.group)));
  q(".fe").forEach((el) => el.classList.toggle("off", !live.edgeOn[+el.dataset.edge]));
  for (const e of loopEdges(lay)) {
    const text = loopText(doc, st, e);
    q(`.fl-pill[data-edge="${e.i}"]`).forEach((el) => {
      el.classList.toggle("off", !live.edgeOn[e.i]);
      el.querySelector(".fl-lim").textContent = text;
    });
    q(`.loop-card[data-edge="${e.i}"]`).forEach((el) => {
      el.classList.toggle("off", !live.edgeOn[e.i]);
      el.querySelector(".loop-lim").textContent = live.edgeOn[e.i] ? text || "on" : "off";
    });
  }

  // Controls: implied switches show as on and locked; a mode greys the rest.
  q("input[data-on]").forEach((el) => {
    const f = el.dataset.on, by = live.impliedBy.get(f);
    const inToggles = !!el.closest("#ctl-toggles");
    el.checked = st.on[f] === true || !!by;
    el.disabled = (inToggles && !!st.mode) || !!by;
    const row = el.closest(".ctl-row");
    row.classList.toggle("implied", !!by);
    row.title = by ? `On because of ${by}` : "";
    const text = view.querySelector(`[data-text="${f}"]`);
    if (text) text.hidden = !el.checked || (inToggles && !!st.mode);
  });
  q("#ctl-limits input, #ctl-limits button").forEach((el) => { el.disabled = !!st.mode; });
  q("#ctl-toggles, #ctl-limits").forEach((el) => el.classList.toggle("dimmed", !!st.mode));

  const modeArg = view.querySelector("#wf-mode-arg");
  if (modeArg) {
    const p = doc.params.find((x) => x.flag === st.mode);
    modeArg.hidden = !p?.arg;
    modeArg.placeholder = p?.arg || "";
  }
  view.querySelector("#wf-cmd").textContent = commandLine(doc, st);
}

function setScale(s) {
  const { lay } = wf;
  wf.st.scale = Math.max(0.4, Math.min(1.6, s));
  const stage = view.querySelector(".flow-stage");
  const zoomer = view.querySelector("#wf-zoomer");
  stage.style.transform = `scale(${wf.st.scale})`;
  zoomer.style.width = `${lay.width * wf.st.scale}px`;
  zoomer.style.height = `${lay.height * wf.st.scale}px`;
  view.querySelector("#wf-zoom").textContent = `${Math.round(wf.st.scale * 100)}%`;
}

function fitZoom() {
  const canvas = view.querySelector("#wf-canvas");
  const fit = (canvas.clientWidth - 2) / wf.lay.width;
  // Past a point, shrinking to fit makes the labels unreadable; scroll instead.
  setScale(Math.min(1, Math.max(0.72, fit)));
}

function highlight(p) {
  const stage = view.querySelector(".flow-stage");
  view.querySelectorAll(".hl").forEach((el) => el.classList.remove("hl"));
  stage.classList.toggle("focusing", !!p);
  if (!p) return;
  const t = touchedBy(wf.doc, p);
  t.nodes.forEach((id) => view.querySelector(`.fn[data-node="${id}"]`)?.classList.add("hl"));
  t.groups.forEach((id) => view.querySelectorAll(`[data-group="${id}"]`).forEach((el) => el.classList.add("hl")));
  t.edges.forEach((i) => view.querySelectorAll(`.fe[data-edge="${i}"], .fl-pill[data-edge="${i}"]`)
    .forEach((el) => el.classList.add("hl")));
}

function highlightEdge(i) {
  const stage = view.querySelector(".flow-stage");
  view.querySelectorAll(".hl").forEach((el) => el.classList.remove("hl"));
  stage.classList.toggle("focusing", i != null);
  if (i == null) return;
  const e = wf.doc.edges[i];
  view.querySelectorAll(`.fe[data-edge="${i}"], .fl-pill[data-edge="${i}"], .fn[data-node="${e.from}"], .fn[data-node="${e.to}"]`)
    .forEach((el) => el.classList.add("hl"));
}

function selectNode(id) {
  const drawer = view.querySelector("#wf-drawer");
  wf.st.selected = id;
  view.querySelectorAll(".fn.selected, .fe.sel").forEach((el) => el.classList.remove("selected", "sel"));
  if (!id) { drawer.hidden = true; return; }
  view.querySelector(`.fn[data-node="${id}"]`)?.classList.add("selected");
  wf.doc.edges.forEach((e, i) => {
    if (e.from === id || e.to === id) view.querySelector(`.fe[data-edge="${i}"]`)?.classList.add("sel");
  });
  drawer.innerHTML = drawerHtml(wf.doc, id);
  drawer.hidden = false;
}

function bindWorkflow() {
  const { doc, st } = wf;

  view.querySelector(".wf-controls").addEventListener("input", (e) => {
    const el = e.target;
    if (el.name === "wf-mode") { st.mode = el.value; st.modeArg = ""; view.querySelector("#wf-mode-arg").value = ""; }
    else if (el.id === "wf-mode-arg") st.modeArg = el.value;
    else if (el.dataset.text) st.text[el.dataset.text] = el.value;
    else if (el.dataset.on) st.on[el.dataset.on] = el.checked;
    else if (el.dataset.num) st.num[el.dataset.num] = Math.max(0, Math.min(99, Number(el.value) || 0));
    applyState();
  });

  view.querySelector(".wf-controls").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-step]");
    if (!b) return;
    const f = b.dataset.num;
    st.num[f] = Math.max(0, Math.min(99, Number(st.num[f] || 0) + Number(b.dataset.step)));
    view.querySelector(`#ctl-limits input[data-num="${f}"]`).value = st.num[f];
    applyState();
  });

  // Hovering an option shows what it touches.
  view.querySelector(".wf-controls").addEventListener("mouseover", (e) => {
    const row = e.target.closest("[data-param]");
    highlight(row ? doc.params.find((p) => paramName(p) === row.dataset.param) : null);
  });
  view.querySelector(".wf-controls").addEventListener("mouseleave", () => highlight(null));

  const loopsEl = view.querySelector(".loops");
  loopsEl.addEventListener("mouseover", (e) => {
    const card = e.target.closest(".loop-card");
    highlightEdge(card ? +card.dataset.edge : null);
  });
  loopsEl.addEventListener("mouseleave", () => highlightEdge(null));

  view.querySelector("#wf-canvas").addEventListener("click", (e) => {
    const n = e.target.closest(".fn");
    selectNode(n && n.dataset.node !== st.selected ? n.dataset.node : null);
  });

  view.querySelector("#wf-drawer").addEventListener("click", (e) => {
    if (e.target.closest(".drawer-close")) return selectNode(null);
    const go = e.target.closest("[data-goto]");
    if (go) {
      selectNode(go.dataset.goto);
      view.querySelector(`.fn[data-node="${go.dataset.goto}"]`)
        ?.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    }
  });

  view.querySelector(".zoom").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    const dir = Number(b.dataset.zoom);
    if (dir === 0) fitZoom(); else setScale(wf.st.scale + dir * 0.1);
  });

  view.querySelector("#wf-copy").addEventListener("click", async (e) => {
    const text = view.querySelector("#wf-cmd").textContent;
    const btn = e.currentTarget;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "Copied";
    } catch {
      // file:// and plain http have no clipboard API: select it for Ctrl+C.
      const r = document.createRange();
      r.selectNodeContents(view.querySelector("#wf-cmd"));
      getSelection().removeAllRanges();
      getSelection().addRange(r);
      btn.textContent = "Press Ctrl+C";
    }
    setTimeout(() => { btn.textContent = "Copy"; }, 1600);
  });
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && wf?.st.selected && !lightboxOpen()) selectNode(null);
});

let resizeTimer = null;
window.addEventListener("resize", () => {
  if (!wf) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(fitZoom, 120);
});

/* --------------------------------------------------------------- section */

registerSection({
  id: "workflows",
  title: "Workflows",
  icon: ICON.workflows,
  blurb: "How the worker jobs run: every step, the loops that send results back " +
         "to a model, and what each command-line flag changes.",
  stats() {
    return [plural(DATA.dataflows.length, "job"),
      DATA.dataflows.map((d) => d.job).join(" · ")];
  },
  render(job) {
    const doc = job && DATA.dataflows.find((d) => d.job === job);
    if (doc) renderWorkflow(doc);
    else renderWorkflowList();
  },
  leave() { wf = null; },
});
