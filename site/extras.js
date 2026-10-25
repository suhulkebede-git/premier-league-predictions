"use strict";

/* v3 pages: odds, this week, recaps and head-to-head. Loaded before app.js;
   uses its helpers (el, state, person, short, sheetName, ...) at call time. */

const pct = (p) => (p >= 0.995 ? ">99%" : p > 0 && p < 0.005 ? "<1%" : `${Math.round(p * 100)}%`);

// ---------- gameweek maths (from published history) ----------
function finals() {
  return state.data.gameweeks;
}
function totalsOf(entry) {
  return Object.fromEntries(entry.scores.map((s) => [s.person, s.total]));
}
function ranksOf(entry) {
  return Object.fromEntries(entry.scores.map((s) => [s.person, s.rank]));
}
// Points gained in each finished gameweek: {gw: {person: change}}
function gameweekChanges() {
  const out = {};
  let before = null;
  for (const g of finals()) {
    const now = totalsOf(g);
    out[g.gameweek] = Object.fromEntries(Object.keys(now).map((p) => [p, now[p] - (before ? before[p] : 0)]));
    before = now;
  }
  return out;
}
function gameweekWinners(changes) {
  const out = {};
  for (const [gw, ch] of Object.entries(changes)) {
    const best = Math.max(...Object.values(ch));
    out[gw] = Object.keys(ch).filter((p) => ch[p] === best);
  }
  return out;
}
function winTally() {
  const tally = Object.fromEntries(Object.keys(state.data.predictions).map((p) => [p, 0]));
  for (const names of Object.values(gameweekWinners(gameweekChanges()))) {
    for (const n of names) tally[n] += 1;
  }
  return tally;
}

// ---------- leaderboard: odds view ----------
function oddsList() {
  const odds = state.data.odds || {};
  const people = Object.keys(odds).sort((a, b) => odds[b].win - odds[a].win || odds[b].top3 - odds[a].top3);
  const top = Math.max(...people.map((p) => odds[p].win), 0.0001);
  return el("ol", { class: "rows odds", "aria-label": "Chance of winning the league" },
    people.map((name, i) => {
      const o = odds[name], who = person(name);
      return el("li", { class: i === 0 ? "row leader" : "row" },
        el("span", { class: "rank" }, i + 1),
        el("span", { class: "avatar", style: `background:${who.colour}`, "aria-hidden": "true" }, who.initials),
        el("span", { class: "who" }, el("span", { class: "odds-name" }, who.display),
          el("span", { class: "bar", "aria-hidden": "true" },
            el("span", { style: `width:${(o.win / top) * 100}%;background:${who.colour}` })),
          el("span", { class: "sub" },
            `Top 3: ${pct(o.top3)}. Likely ${o.points_p10}–${o.points_p90} pts, most often ${ordinalOf(o.likely_rank)}`)),
        el("span", { class: "pts" }, el("strong", {}, pct(o.win)), el("span", {}, "to win")));
    }));
}

// ---------- this week ----------
function swingList(deltas) {
  const items = Object.entries(deltas || {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]) || b[1] - a[1]);
  if (!items.length) return el("p", { class: "swing-none" }, "No points change");
  return el("ul", { class: "swings" }, items.map(([p, d]) =>
    el("li", { class: d > 0 ? "up" : "down" }, el("span", {}, sheetName(p)), el("b", {}, signed(d)))));
}

function weekPage() {
  const d = state.data;
  const fixtures = d.next_fixtures || [];
  const head = [el("h2", {}, d.next_gameweek ? `Gameweek ${d.next_gameweek}: what it means` : "This week"),
    el("p", { class: "section-note" },
      "Points each result would add or take away from today's standings, one match at a time. Chances are the model's estimate.")];
  if (!fixtures.length) return [...head, el("p", { class: "muted" }, "No upcoming fixtures right now.")];
  const cards = fixtures.map((f) => {
    const p = [f.p_home, f.p_draw, f.p_away];
    return el("article", { class: f.match_of_the_week ? "fixture motw" : "fixture" },
      el("header", {},
        el("div", { class: "teams" }, `${short(f.home)} v ${short(f.away)}`),
        el("div", { class: "kick" }, fmtTime(f.kickoff)),
        f.match_of_the_week ? el("span", { class: "badge live" }, "Match of the week for us") : null),
      p.every((x) => typeof x === "number") ? el("div", { class: "prob", "aria-label":
        `${short(f.home)} ${pct(f.p_home)}, draw ${pct(f.p_draw)}, ${short(f.away)} ${pct(f.p_away)}` },
        el("span", { class: "ph", style: `flex:${f.p_home}` }, pct(f.p_home)),
        el("span", { class: "pd", style: `flex:${f.p_draw}` }, pct(f.p_draw)),
        el("span", { class: "pa", style: `flex:${f.p_away}` }, pct(f.p_away))) : null,
      el("div", { class: "outcomes" },
        [["home", `${short(f.home)} win`], ["draw", "Draw"], ["away", `${short(f.away)} win`]].map(([k, label]) =>
          el("div", { class: "outcome" }, el("div", { class: "outcome-title" }, label), swingList(f.swings[k])))));
  });
  return [...head, el("div", { class: "fixtures" }, cards)];
}

// ---------- recaps ----------
function recapFor(i) {
  const gws = finals();
  const g = gws.at(i), prev = i > 0 ? gws.at(i - 1) : null;
  const changes = gameweekChanges()[g.gameweek];
  const best = Math.max(...Object.values(changes));
  const winners = Object.keys(changes).filter((p) => changes[p] === best);
  const ranked = Object.entries(changes).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const now = ranksOf(g), before = prev ? ranksOf(prev) : null;
  let climber = null, faller = null;
  if (before) {
    for (const p of Object.keys(now)) {
      const moved = before[p] - now[p];
      if (moved > 0 && (!climber || moved > climber.moved)) climber = { p, moved, rank: now[p] };
      if (moved < 0 && (!faller || moved < faller.moved)) faller = { p, moved, rank: now[p] };
    }
  }
  // Stinker of the week: the prediction that got furthest off this gameweek.
  let stinker = null;
  for (const name of Object.keys(state.data.predictions)) {
    const after = missesFor(name, g);
    const was = prev ? Object.fromEntries(missesFor(name, prev).map((m) => [m.club, m.diff])) : {};
    for (const m of after) {
      const worse = m.diff - (was[m.club] ?? 0);
      if (!stinker || worse > stinker.worse || (worse === stinker.worse && m.diff > stinker.diff)) {
        stinker = { name, club: m.club, diff: m.diff, worse, predicted: m.predicted, now: m.now };
      }
    }
  }
  // Award leaders that changed this gameweek.
  const awardChanges = [];
  if (prev) {
    const before = Object.fromEntries(prev.awards.map((a) => [a.key, a.leaders]));
    for (const a of g.awards) {
      const was = before[a.key] || [];
      if (a.leaders.length && JSON.stringify(was) !== JSON.stringify(a.leaders)) {
        awardChanges.push({ label: a.label, now: a.leaders.map(pickLabel).join(", "),
          was: was.length ? was.map(pickLabel).join(", ") : null });
      }
    }
  }
  const top3 = g.scores.filter((s) => s.rank <= 3);
  return { g, winners, best, ranked, climber, faller, stinker, awardChanges, top3 };
}

function recapText(r) {
  const link = `${location.href.split("#")[0]}#leaderboard/gw${r.g.gameweek}`;
  const lines = [`⚽ Predictions League: GW ${r.g.gameweek} recap`,
    `🏆 GW winner: ${r.winners.map(sheetName).join(" & ")} (${signed(r.best)} pts)`];
  if (r.climber) lines.push(`📈 Climber: ${sheetName(r.climber.p)}, up ${r.climber.moved} to ${ordinalOf(r.climber.rank)}`);
  if (r.faller) lines.push(`📉 Faller: ${sheetName(r.faller.p)}, down ${-r.faller.moved} to ${ordinalOf(r.faller.rank)}`);
  if (r.stinker) lines.push(`🦨 Stinker: ${sheetName(r.stinker.name)}'s ${short(r.stinker.club)} (said ${ordinalOf(r.stinker.predicted)}, now ${ordinalOf(r.stinker.now)})`);
  for (const c of r.awardChanges.slice(0, 2)) lines.push(`🔄 ${c.label}: ${c.now}${c.was ? ` (was ${c.was})` : ""}`);
  lines.push(`Top 3: ${r.top3.map((s) => `${s.rank}. ${sheetName(s.person)} ${s.total}`).join(", ")}`);
  lines.push(link);
  return lines.join("\n");
}

function recapsPage() {
  const gws = finals();
  if (!gws.length) return [el("h2", {}, "Recaps"), el("p", { class: "muted" }, "Recaps appear once a gameweek finishes.")];
  const tally = winTally();
  const order = Object.keys(tally).sort((a, b) => tally[b] - tally[a] || a.localeCompare(b));
  const summary = el("section", { class: "panel tally" },
    el("h3", {}, "Gameweek wins"),
    el("ol", { class: "tally-list" }, order.map((p) => {
      const who = person(p);
      return el("li", {}, el("span", { class: "avatar sm", style: `background:${who.colour}` }, who.initials),
        el("span", {}, who.display), el("b", {}, tally[p]));
    })));
  const cards = gws.map((_, i) => recapFor(i)).reverse().map((r) => {
    const text = recapText(r);
    return el("article", { class: "recap" },
      el("header", {}, el("h3", {}, `Gameweek ${r.g.gameweek}`),
        el("a", { class: "share", href: `https://wa.me/?text=${encodeURIComponent(text)}`,
          target: "_blank", rel: "noopener" }, "Share to WhatsApp"),
        el("button", { type: "button", class: "copy", onclick: (ev) => {
          navigator.clipboard?.writeText(text).then(() => { ev.target.textContent = "Copied"; },
            () => { ev.target.textContent = "Copy failed"; });
        } }, "Copy")),
      el("dl", { class: "recap-facts" },
        el("dt", {}, "Gameweek winner"), el("dd", { class: "hit" },
          `${r.winners.map(sheetName).join(" & ")}, ${signed(r.best)} pts`),
        r.climber ? [el("dt", {}, "Biggest climber"),
          el("dd", {}, `${sheetName(r.climber.p)}, up ${r.climber.moved} to ${ordinalOf(r.climber.rank)}`)] : null,
        r.faller ? [el("dt", {}, "Biggest faller"),
          el("dd", {}, `${sheetName(r.faller.p)}, down ${-r.faller.moved} to ${ordinalOf(r.faller.rank)}`)] : null,
        r.stinker ? [el("dt", {}, "Stinker of the week"),
          el("dd", { class: "miss" }, `${sheetName(r.stinker.name)}: ${short(r.stinker.club)}, predicted ${ordinalOf(r.stinker.predicted)}, now ${ordinalOf(r.stinker.now)}${r.stinker.worse > 0 ? ` (${r.stinker.worse} worse)` : ""}`)] : null,
        r.awardChanges.length ? [el("dt", {}, "New award leaders"),
          el("dd", {}, r.awardChanges.map((c) => `${c.label}: ${c.now}${c.was ? ` (was ${c.was})` : ""}`).join("; "))] : null,
        el("dt", {}, "Top 3"),
        el("dd", {}, r.top3.map((s) => `${s.rank}. ${sheetName(s.person)} ${s.total}`).join(", "))),
      el("table", { class: "gained" },
        el("caption", {}, "Points gained or lost this gameweek"),
        el("tbody", {}, r.ranked.map(([p, d]) => el("tr", {},
          el("td", {}, sheetName(p)),
          el("td", { class: d > 0 ? "up" : d < 0 ? "down" : "" }, signed(d)))))));
  });
  return [el("h2", {}, "Recaps"),
    el("p", { class: "section-note" }, "One card per finished gameweek. Share sends it to WhatsApp ready to post; you pick the chat."),
    summary, el("div", { class: "recaps" }, cards)];
}

// ---------- head to head ----------
function clubPoints(score, club) {
  if (score.hits.champion.includes(club)) return 20;
  if (score.hits.exact.includes(club)) return 5;
  if (score.hits.one_off.includes(club)) return 3;
  return 0;
}

function h2hPage() {
  const names = Object.keys(state.data.predictions);
  const live = state.data.live;
  const byRank = [...live.scores].sort((x, y) => x.rank - y.rank).map((s) => s.person);
  let [a, b] = state.h2h || [];
  if (!names.includes(a)) a = byRank.at(0);
  if (!names.includes(b) || b === a) b = byRank.find((p) => p !== a);
  const pick = (current, other, onPick) => el("select", { "aria-label": "Person", onchange: (ev) => onPick(ev.target.value) },
    names.filter((n) => n !== other).map((n) => el("option", { value: n, selected: n === current }, sheetName(n))));
  const go = (x, y) => { location.hash = `#h2h/${x}/${y}`; };

  const sa = live.scores.find((s) => s.person === a), sb = live.scores.find((s) => s.person === b);
  const pa = state.data.predictions[a], pb = state.data.predictions[b];
  const pos = positionsOf(live);

  // Gameweek-by-gameweek record (who gained more each finished gameweek).
  let wa = 0, wb = 0, level = 0;
  for (const ch of Object.values(gameweekChanges())) {
    if (ch[a] > ch[b]) wa += 1; else if (ch[b] > ch[a]) wb += 1; else level += 1;
  }

  const clubRows = state.data.predictions[a].order
    .filter((club) => pa.order.indexOf(club) !== pb.order.indexOf(club))
    .map((club) => ({ club, ia: pa.order.indexOf(club) + 1, ib: pb.order.indexOf(club) + 1, now: pos[club],
      xa: clubPoints(sa, club), xb: clubPoints(sb, club) }))
    .sort((x, y) => Math.abs(y.xa - y.xb) - Math.abs(x.xa - x.xb) || x.now - y.now);

  const ruleRows = [["top5", "Top 5"], ["relegation", "Relegation"], ["sixth_seventh", "6th and 7th"],
    ["most_exact", "Most exact positions"]].filter(([k]) => sa.breakdown[k] !== sb.breakdown[k]);

  const awardRows = live.awards.filter((aw) => {
    const x = pa.awards[aw.key], y = pb.awards[aw.key];
    return x && y && x.value !== y.value;
  });

  const cell = (n) => el("td", { class: n > 0 ? "r up" : "r" }, n);
  const gap = sa.total - sb.total;
  return [
    el("h2", {}, "Head to head"),
    el("p", { class: "section-note" }, "Only the predictions where the two of you differ, scored as things stand."),
    el("div", { class: "h2h-pick" }, pick(a, b, (v) => go(v, b)), el("span", {}, "vs"), pick(b, a, (v) => go(a, v))),
    el("div", { class: "h2h-score" },
      ...[[a, sa], [b, sb]].map(([n, s]) => {
        const who = person(n);
        return el("div", { class: "side" }, el("span", { class: "avatar", style: `background:${who.colour}` }, who.initials),
          el("div", {}, el("div", { class: "nm" }, who.display), el("div", { class: "big" }, s.total),
            el("div", { class: "muted" }, `${ordinalOf(s.rank)} place`)));
      })),
    el("p", { class: "h2h-gap" }, gap === 0 ? "Level on points." :
      `${sheetName(gap > 0 ? a : b)} leads by ${Math.abs(gap)}. Gameweeks won head to head: ${sheetName(a)} ${wa}, ${sheetName(b)} ${wb}${level ? `, level ${level}` : ""}.`),
    el("h3", {}, "Clubs placed differently"),
    clubRows.length ? el("div", { class: "table-scroll" }, el("table", { class: "pl h2h" },
      el("thead", {}, el("tr", {}, ["Club", sheetName(a), sheetName(b), "Now", `${sheetName(a)} pts`, `${sheetName(b)} pts`]
        .map((h) => el("th", {}, h)))),
      el("tbody", {}, clubRows.map((r) => el("tr", {}, el("td", {}, short(r.club)),
        el("td", {}, ordinalOf(r.ia)), el("td", {}, ordinalOf(r.ib)), el("td", {}, ordinalOf(r.now)),
        cell(r.xa), cell(r.xb)))))) : el("p", { class: "muted" }, "Same finishing order."),
    ruleRows.length ? [el("h3", {}, "Bonus rules"),
      el("div", { class: "table-scroll" }, el("table", { class: "pl h2h" },
        el("thead", {}, el("tr", {}, ["Rule", sheetName(a), sheetName(b)].map((h) => el("th", {}, h)))),
        el("tbody", {}, ruleRows.map(([k, label]) =>
          el("tr", {}, el("td", {}, label), cell(sa.breakdown[k]), cell(sb.breakdown[k]))))))] : null,
    el("h3", {}, "Awards picked differently"),
    awardRows.length ? el("div", { class: "table-scroll" }, el("table", { class: "pl h2h" },
      el("thead", {}, el("tr", {}, ["Award", sheetName(a), sheetName(b), "Leading"].map((h) => el("th", {}, h)))),
      el("tbody", {}, awardRows.map((aw) => {
        const x = pa.awards[aw.key], y = pb.awards[aw.key];
        const hitA = sa.hits.awards.includes(aw.key), hitB = sb.hits.awards.includes(aw.key);
        return el("tr", {}, el("td", {}, aw.label),
          el("td", { class: hitA ? "up" : "" }, `${pickText(x)}${hitA ? " +10" : ""}`),
          el("td", { class: hitB ? "up" : "" }, `${pickText(y)}${hitB ? " +10" : ""}`),
          el("td", { class: "muted" }, aw.leaders.length ? aw.leaders.map(pickLabel).join(", ") : "Not decided"));
      })))) : el("p", { class: "muted" }, "Same award picks."),
  ];
}
