"use strict";

/* Bump chart: everyone's rank after each gameweek.
   One line per person in their colour, a dot per gameweek, and at the latest
   column a circle with their initials plus their name (the direct label that
   means colour is never the only way to tell people apart). Tap a line to
   make it bold; tap a gameweek label to jump the leaderboard there. */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const COL = 44;      // minimum px per gameweek column
  const ROW = 40;      // px per rank
  const LEFT = 40;     // rank labels
  const RIGHT = 150;   // end circles + names
  const TOP = 24;
  const BOTTOM = 44;   // gameweek labels

  function svg(tag, attrs = {}, ...children) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  const ordinal = (n) => {
    const v = n % 100;
    if (v >= 11 && v <= 13) return `${n}th`;
    return n + ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
  };

  function render(host, { entries, people, selectedKey, focus, onSelect, onFocus }) {
    if (!entries.length || !people.length) {
      host.replaceChildren();
      return;
    }
    const n = people.length;
    const width = Math.max(host.clientWidth, LEFT + COL * Math.max(entries.length - 1, 1) + RIGHT + 24);
    const plotRight = width - RIGHT;
    const step = entries.length > 1 ? (plotRight - LEFT - 24) / (entries.length - 1) : 0;
    const x = (i) => LEFT + 24 + i * step;
    const y = (rank) => TOP + (rank - 1) * ROW + ROW / 2;
    const height = TOP + n * ROW + BOTTOM;

    const rankOf = (entry, name) => {
      const s = entry.scores.find((sc) => sc.person === name);
      return s ? s : null;
    };

    const root = svg("svg", {
      class: focus ? "bump has-focus" : "bump", width, height, viewBox: `0 0 ${width} ${height}`,
      role: "img", "aria-label": "Each person's rank after every gameweek",
    });

    // Selected gameweek: a faint band behind its column.
    const sel = entries.findIndex((e) => e.key === selectedKey);
    if (sel >= 0) {
      root.append(svg("rect", { x: x(sel) - 22, y: TOP - 8, width: 44, height: n * ROW + 16, rx: 10,
        fill: "rgba(46,197,255,0.10)", stroke: "rgba(46,197,255,0.35)" }));
    }

    // Rank axis + quiet guides.
    const axis = svg("g", { "aria-hidden": "true" });
    for (let r = 1; r <= n; r++) {
      axis.append(svg("line", { x1: LEFT + 8, x2: plotRight, y1: y(r), y2: y(r),
        stroke: "rgba(138,155,181,0.12)" }));
      axis.append(svg("text", { x: LEFT - 6, y: y(r) + 4, "text-anchor": "end", fill: "#8a9bb5",
        "font-size": 12, "font-weight": 700 }, r));
    }
    root.append(axis);

    // Gameweek labels: also buttons that switch the leaderboard.
    entries.forEach((e, i) => {
      const label = e.final ? `GW ${e.gameweek}` : "Live";
      const t = svg("text", {
        class: "gw-label", x: x(i), y: height - 16, "text-anchor": "middle",
        fill: i === sel ? "#eaf2ff" : "#8a9bb5", "font-size": 12, "font-weight": i === sel ? 800 : 500,
        tabindex: 0, role: "button", "aria-label": `Show ${e.final ? `gameweek ${e.gameweek}` : "live standings"}`,
        onclick: () => onSelect(e.key),
        onkeydown: (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); onSelect(e.key); } },
      }, label);
      root.append(t);
    });

    // End-label placement: people sharing a rank get their names stacked.
    const last = entries.at(-1);
    const tied = {};
    for (const p of people) {
      const s = rankOf(last, p.key);
      if (s) (tied[s.rank] = tied[s.rank] || []).push(p.key);
    }

    // Draw focused person last so their line sits on top.
    const order = [...people].sort((a, b) => (a.key === focus) - (b.key === focus));
    for (const p of order) {
      const pts = entries.map((e, i) => {
        const s = rankOf(e, p.key);
        return s ? { i, e, s } : null;
      }).filter(Boolean);
      if (!pts.length) continue;
      const isFocus = p.key === focus;
      const g = svg("g", { class: isFocus ? "series focus" : "series", "data-person": p.key,
        onclick: () => onFocus(p.key) });
      const d = pts.map((pt) => `${x(pt.i)},${y(pt.s.rank)}`).join(" ");
      g.append(svg("polyline", { points: d, fill: "none", stroke: "transparent", "stroke-width": 16 }));
      g.append(svg("polyline", { points: d, fill: "none", stroke: p.colour,
        "stroke-width": isFocus ? 4 : 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      for (const pt of pts.slice(0, -1)) {
        g.append(svg("circle", { cx: x(pt.i), cy: y(pt.s.rank), r: isFocus ? 5 : 4, fill: p.colour,
          stroke: "#0f1b2d", "stroke-width": 2 },
          svg("title", {}, `${p.display}, ${pt.e.final ? `GW ${pt.e.gameweek}` : "live"}: ${ordinal(pt.s.rank)}, ${pt.s.total} pts`)));
      }
      const end = pts.at(-1);
      const peers = tied[end.s.rank] || [p.key];
      const k = peers.indexOf(p.key);
      const offset = (k - (peers.length - 1) / 2) * 18;
      const cx = x(end.i) + k * 10, cy = y(end.s.rank); // tied: fan out
      g.append(svg("circle", { cx, cy, r: 13, fill: p.colour, stroke: isFocus ? "#eaf2ff" : "#0f1b2d",
        "stroke-width": 2 },
        svg("title", {}, `${p.display}: ${ordinal(end.s.rank)}, ${end.s.total} pts`)));
      g.append(svg("text", { x: cx, y: cy + 4, "text-anchor": "middle", fill: "#08111f",
        "font-size": 10.5, "font-weight": 800, "pointer-events": "none" }, p.initials));
      g.append(svg("text", { x: cx + 22, y: cy + offset + 4, fill: isFocus ? "#eaf2ff" : "#c9d5e8",
        "font-size": 13, "font-weight": isFocus ? 800 : 500 }, p.display));
      root.append(g);
    }

    host.replaceChildren(root);
    // Narrow screens scroll: keep the selected gameweek (normally the latest) in view.
    if (host.scrollWidth > host.clientWidth) {
      const target = sel >= 0 ? x(sel) : width;
      host.scrollLeft = Math.max(0, target - host.clientWidth / 2);
    }
  }

  window.BumpChart = { render };
})();
