"use strict";

/* Predictions League: renders standings.json (written by `plpredict run`).
   Every view follows the selected gameweek: a finished one from data.gameweeks,
   or "live" (data.live, the API's current table). */

// One colour per person, validated as a set on the dark surface (adjacent
// pairs clear the colour-blind and normal-vision floors). Assigned by sheet
// order so a person keeps their colour whatever their rank.
const PERSON_COLOURS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
  "#008300", "#9085e9", "#e66767", "#2b9fb2"];

const PAGES = ["leaderboard", "awards", "table", "rules"];
const GW_PAGES = new Set(["leaderboard", "awards", "table"]);

const SHORT = {
  "AFC Bournemouth": "Bournemouth", "Brighton & Hove Albion": "Brighton",
  "Manchester City": "Man City", "Manchester United": "Man Utd",
  "Newcastle United": "Newcastle", "Nottingham Forest": "Forest",
  "Tottenham Hotspur": "Spurs", "Leeds United": "Leeds", "Coventry City": "Coventry",
  "Ipswich Town": "Ipswich", "Hull City": "Hull", "Crystal Palace": "Palace",
};

const BREAKDOWN = [
  ["champion", "Champion"], ["exact", "Exact positions"], ["top5", "Top 5"],
  ["relegation", "Relegation"], ["sixth_seventh", "6th and 7th"],
  ["one_off", "One off (8th to 17th)"], ["most_exact", "Most exact positions"],
  ["awards", "Awards"],
];

const state = { data: null, page: "leaderboard", gw: "live", open: null, focus: null };
const screen = document.getElementById("screen");

// ---------- helpers ----------
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
const short = (club) => SHORT[club] || club;
const sheetName = (name) => name.charAt(0) + name.slice(1).toLowerCase();

function person(name) {
  const p = (state.data.people || {})[name] || {};
  const initials = p.initials || name.slice(0, 2);
  return { key: name, display: sheetName(name), full: p.name || sheetName(name), initials,
    colour: colourOf(name) };
}
function colourOf(name) {
  const order = Object.keys(state.data.predictions);
  const i = order.indexOf(name);
  return PERSON_COLOURS.at(i % PERSON_COLOURS.length);
}

function fmtTime(iso, withDay = true) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (!withDay) return time;
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  return `${day}, ${time}`;
}

// ---------- gameweek entries ----------
function entries() {
  const finals = state.data.gameweeks.map((g) => ({ ...g, key: g.gameweek, final: true }));
  return [...finals, { ...state.data.live, key: "live", final: false }];
}
function entryFor(key) {
  return entries().find((e) => e.key === key) || entries().at(-1);
}
function previousEntry(entry) {
  // The finished gameweek just before this one (live: before the live gameweek).
  const target = entry.gameweek;
  if (target == null) return null;
  return [...state.data.gameweeks].reverse().find((g) => g.gameweek < target) || null;
}
function entryLabel(entry) {
  if (entry.final) return `GW ${entry.gameweek}`;
  if (entry.gameweek == null) return "Live";
  return entry.in_progress ? `Live, GW ${entry.gameweek} in progress` : `Live, after GW ${entry.gameweek}`;
}

function positionsOf(entry) {
  return Object.fromEntries(entry.table.map((r) => [r.club, r.position]));
}
function missesFor(name, entry) {
  const pos = positionsOf(entry);
  return state.data.predictions[name].order.map((club, i) => ({
    club, predicted: i + 1, now: pos[club], diff: Math.abs(i + 1 - pos[club]),
  }));
}

// ---------- masthead ----------
function renderTimes() {
  const d = state.data;
  const updated = document.getElementById("table-updated");
  const label = updated.previousElementSibling;
  if (d.table_updated_at) {
    updated.textContent = fmtTime(d.table_updated_at);
    label.textContent = d.table_updated_source === "last_result_kickoff" ? "Last result" : "Table updated";
  } else {
    updated.textContent = "No results yet";
  }
  const checkedAt = d.checked_at || d.updated_at;
  const checked = document.getElementById("checked");
  checked.textContent = fmtTime(checkedAt);
  const ageMin = (Date.now() - new Date(checkedAt).getTime()) / 60000;
  checked.classList.toggle("stale", ageMin > 60);
  checked.title = ageMin > 60 ? "The tracker hasn't checked for over an hour" : "";
}

// ---------- gameweek control ----------
function gwControl() {
  const all = entries();
  const idx = all.findIndex((e) => e.key === state.gw);
  const go = (key) => navigate(state.page, key);
  const chips = el("div", { class: "chips", role: "group", "aria-label": "Gameweek" },
    all.map((e) => el("button", {
      type: "button", class: e.final ? "chip" : "chip live", "aria-pressed": String(e.key === state.gw),
      title: entryLabel(e), onclick: () => go(e.key),
    }, e.final ? `GW ${e.gameweek}` : "Live")));
  const prev = el("button", { type: "button", class: "step", "aria-label": "Previous gameweek",
    disabled: idx <= 0, onclick: () => go(all.at(idx - 1).key) }, "‹");
  const next = el("button", { type: "button", class: "step", "aria-label": "Next gameweek",
    disabled: idx >= all.length - 1, onclick: () => go(all.at(idx + 1).key) }, "›");
  requestAnimationFrame(() => {
    // Scroll only the chip row (scrollIntoView would also scroll the page).
    const on = chips.querySelector('[aria-pressed="true"]');
    if (on) chips.scrollLeft = on.offsetLeft - chips.clientWidth / 2 + on.offsetWidth / 2;
  });
  return el("div", { class: "gw-control" }, prev, chips, next);
}

// ---------- leaderboard ----------
function moveBadge(name, entry) {
  const prev = previousEntry(entry);
  if (!prev) return null;
  const before = prev.scores.find((s) => s.person === name);
  const now = entry.scores.find((s) => s.person === name);
  if (!before || !now) return null;
  const change = before.rank - now.rank;
  if (change > 0) return el("span", { class: "move up", title: `Up ${change} since GW ${prev.gameweek}` }, `▲${change}`);
  if (change < 0) return el("span", { class: "move down", title: `Down ${-change} since GW ${prev.gameweek}` }, `▼${-change}`);
  return el("span", { class: "move same", title: `No change since GW ${prev.gameweek}` }, "–");
}

function tieNote(score, above) {
  if (!above || above.total !== score.total) return null;
  if (above.rank === score.rank) return "Level on points and every miss, so the rank is shared.";
  const tbA = above.hits.tiebreak.length, tbB = score.hits.tiebreak.length;
  if (tbA !== tbB) return `Level on ${score.total}: ${sheetName(above.person)} goes ahead on the Brighton half-pick.`;
  const i = above.misses_desc.findIndex((m, k) => m !== score.misses_desc.at(k));
  const nth = ["worst", "2nd worst", "3rd worst"].at(i) || `${i + 1}th worst`;
  return `Level on ${score.total}: ${nth} miss of ${above.misses_desc.at(i)} beats ${score.misses_desc.at(i)}.`;
}

function pickText(pick) {
  if (!pick) return "no pick";
  if (typeof pick.value === "number") return `${pick.value} points`;
  return pick.raw;
}

function personPanel(score, entry) {
  const who = person(score.person);
  const pred = state.data.predictions[score.person];
  const all = missesFor(score.person, entry);
  const stinkers = new Set([...all].sort((a, b) => b.diff - a.diff).filter((m) => m.diff > 0)
    .slice(0, 3).map((m) => m.club));
  const oneOff = new Set(score.hits.one_off);

  const points = el("dl", { class: "kv" }, BREAKDOWN.flatMap(([key, label]) => [
    el("dt", {}, label),
    el("dd", { class: score.breakdown[key] ? "hit" : "zero" }, score.breakdown[key]),
  ]));

  const rows = all.map((m) => {
    const cls = m.diff === 0 ? "exact" : oneOff.has(m.club) ? "oneoff" : stinkers.has(m.club) ? "stinker" : "";
    return el("tr", { class: cls }, el("td", {}, m.predicted), el("td", {}, short(m.club)),
      el("td", { class: "r" }, m.now), el("td", { class: "r" }, m.diff === 0 ? "=" : `±${m.diff}`));
  });

  const picks = entry.awards.map((a) => {
    const pick = pred.awards[a.key];
    const hit = score.hits.awards.includes(a.key);
    const half = score.hits.tiebreak.includes(a.key);
    const known = a.leaders.length > 0;
    const [mark, cls, said] = hit ? ["✓", "mark-hit", "scored"] : half ? ["~", "mark-half", "tiebreak only"]
      : known ? ["✕", "mark-miss", "missed"] : ["·", "mark-pending", "not decided"];
    return el("li", {}, el("span", { class: cls, "aria-label": said }, mark),
      el("span", {}, `${a.label}: `, el("span", { class: "pick" }, pickText(pick))));
  });

  return el("li", { class: "person", id: `person-${score.person}` },
    el("h3", {}, `${who.full}: ${score.total} points`),
    points,
    el("h3", {}, "Predicted vs now"),
    el("p", { class: "legend-line" }, el("b", { class: "mark-hit" }, "Green"), " exact, ",
      el("b", { class: "mark-half" }, "blue"), " one off and scoring, ",
      el("b", { class: "mark-miss" }, "red"), " the three biggest stinkers."),
    el("table", { class: "compare" },
      el("thead", {}, el("tr", {}, el("th", {}, "Pred"), el("th", {}, "Club"),
        el("th", { class: "r" }, "Now"), el("th", { class: "r" }, "Off"))),
      el("tbody", {}, rows)),
    el("h3", {}, "Award picks"),
    el("ul", { class: "picks" }, picks));
}

function leaderboardList(entry) {
  const list = el("ol", { class: "rows board", "aria-label": `Leaderboard, ${entryLabel(entry)}` });
  list.append(el("li", { class: "row head", "aria-hidden": "true" },
    el("span", {}, "Pos"), el("span", {}), el("span", {}, "Name"), el("span", { class: "pts" }, "Points")));
  entry.scores.forEach((s, i) => {
    const who = person(s.person);
    const worst = missesFor(s.person, entry).reduce((a, b) => (b.diff > a.diff ? b : a));
    const open = state.open === s.person;
    const name = el("button", { type: "button", "aria-expanded": String(open),
      "aria-controls": `person-${s.person}`,
      onclick: () => { state.open = open ? null : s.person; state.focus = state.open; render(); },
      onmouseenter: () => setFocus(s.person), onmouseleave: () => setFocus(state.open),
    }, who.display);
    list.append(el("li", { class: s.rank === 1 ? "row leader" : "row" },
      el("span", { class: "rank" }, s.rank),
      el("span", { class: "avatar", style: `background:${who.colour}`, "aria-hidden": "true" }, who.initials),
      el("span", { class: "who" }, name, moveBadge(s.person, entry),
        el("span", { class: "sub" }, `Stinker: ${short(worst.club)}, ${worst.diff} off`)),
      el("span", { class: "pts" }, el("strong", {}, s.total), el("span", {}, "pts"))));
    const note = tieNote(s, entry.scores.at(i - 1) && i > 0 ? entry.scores.at(i - 1) : null);
    if (note) list.append(el("li", { class: "row tie-row" }, el("span", { class: "tie-note" }, note)));
    if (open) list.append(personPanel(s, entry));
  });
  return list;
}

function leaderboardPage() {
  const entry = entryFor(state.gw);
  const chartPanel = el("section", { class: "panel", "aria-labelledby": "chart-title" },
    el("h2", { id: "chart-title" }, "Rank by gameweek"),
    el("p", { class: "section-note" }, "Tap a line or a name to pick someone out; tap a gameweek to jump to it."),
    el("div", { class: "chart-scroll", id: "chart" }));
  const board = el("section", { "aria-labelledby": "board-title" },
    el("h2", { id: "board-title" }, entry.final ? `After gameweek ${entry.gameweek}` : "Live standings"),
    el("p", { class: "section-note" }, entry.final
      ? "Points as they stood when this gameweek finished."
      : "Points if the season ended now."),
    gwControl(),
    leaderboardList(entry));
  requestAnimationFrame(drawChart);
  return [el("div", { class: "board-grid" }, board, chartPanel)];
}

function drawChart() {
  const host = document.getElementById("chart");
  if (!host || !window.BumpChart) return;
  const names = Object.keys(state.data.predictions);
  window.BumpChart.render(host, {
    entries: entries(),
    people: names.map(person),
    selectedKey: state.gw,
    focus: state.focus,
    onSelect: (key) => navigate("leaderboard", key),
    onFocus: (name) => { state.focus = state.focus === name ? null : name; drawChart(); },
  });
}
function setFocus(name) {
  if (state.focus === name) return;
  state.focus = name;
  drawChart();
}

// ---------- awards, PL table, rules ----------
// How each award's numbers read. `unit(n)` labels a race value; `lead(a)` the
// headline under the award name when it has a leader.
const ordinalOf = (n) => {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  return n + ({ 1: "st", 2: "nd", 3: "rd" }[n % 10] || "th");
};
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const signed = (n) => (n > 0 ? `+${n}` : n < 0 ? `\u2212${-n}` : "0");
const AWARD_UNITS = {
  golden_boot: (n) => plural(n, "goal", "goals"),
  top_assister: (n) => plural(n, "assist", "assists"),
  most_goals: (n) => plural(n, "goal", "goals"),
  fewest_goals: (n) => plural(n, "goal", "goals"),
  best_defense: (n) => `${n} conceded`,
  worst_defense: (n) => `${n} conceded`,
  most_draws: (n) => plural(n, "draw", "draws"),
  most_clean_sheets: (n) => plural(n, "clean sheet", "clean sheets"),
  highest_promoted: (n) => ordinalOf(n),
  title_margin: (n) => `${n} pts`,
  biggest_increase: (n) => `${signed(n)} pts on pace`,
  biggest_dropoff: (n) => `${signed(n)} pts on pace`,
  top_at_christmas: (n) => `${n} pts`,
  bottom_at_christmas: (n) => `${n} pts`,
  any_over_90: (n) => `${n} pts`,
  any_under_25: (n) => `${n} pts`,
};
const RACE_TITLES = {
  title_margin: "Top of the table", any_over_90: "Closest to 91", any_under_25: "Lowest points",
};

function pickLabel(v) {
  if (v === true) return "Yes";
  if (v === false) return "No";
  return short(String(v));
}

function awardHeadline(a) {
  const unit = AWARD_UNITS[a.key];
  if (!a.leaders.length) return null;
  if (a.key === "title_margin") return `${plural(a.value, "point", "points")} between 1st and 2nd`;
  const names = a.leaders.map(pickLabel).join(", ");
  return unit && a.value != null ? `${names}, ${unit(a.value)}` : names;
}

function thresholdLine(a) {
  // Over 90 / under 25: how far the nearest club is from the line.
  const near = a.race.at(0);
  if (!near || a.leaders.length) return null;
  if (a.key === "any_over_90") return `Highest so far: ${short(near.name)} on ${near.value}, ${91 - near.value} short of 91`;
  if (a.key === "any_under_25") return `Lowest so far: ${short(near.name)} on ${near.value}`;
  return null;
}

function awardsPage() {
  const entry = entryFor(state.gw);
  const preds = Object.entries(state.data.predictions);
  const items = entry.awards.map((a) => {
    const pickers = (value) => preds
      .filter(([, p]) => p.awards[a.key] && p.awards[a.key].value === value).map(([n]) => n);
    const scoring = a.leaders.flatMap(pickers);
    const unit = AWARD_UNITS[a.key] || String;
    const named = a.race.length && !["title_margin", "any_over_90", "any_under_25"].includes(a.key);

    // Left: name, status, headline number, who's scoring.
    const main = el("div", { class: "award-main" },
      el("div", { class: "label" }, a.label,
        a.settled ? el("span", { class: "badge" }, "Final")
          : a.leaders.length && !entry.final ? el("span", { class: "badge live" }, "Live") : null),
      a.leaders.length
        ? el("div", { class: "leaders" }, awardHeadline(a))
        : el("div", { class: "pending" },
          thresholdLine(a) || (a.note ? a.note.charAt(0).toUpperCase() + a.note.slice(1) : "No data yet")),
      a.leaders.length ? el("div", { class: "scoring" },
        scoring.length ? `Scoring 10: ${scoring.map(sheetName).join(", ")}` : "Nobody picked this") : null,
      named ? null : pickSummary(a, preds));

    // Right: the race, top 3 with values; under each entry, who picked it.
    const inRace = new Set(a.race.map((e) => e.name));
    const elsewhere = named ? preds.filter(([, p]) => p.awards[a.key] && !inRace.has(p.awards[a.key].value)) : [];
    const race = a.race.length ? el("div", { class: "race" },
      el("div", { class: "race-title" }, RACE_TITLES[a.key] || "The race"),
      el("ol", {}, a.race.map((e) => {
        const who = named ? pickers(e.name) : [];
        const leader = a.leaders.includes(e.name);
        return el("li", { class: e.rank === 1 ? "first" : "" },
          el("span", { class: "race-rank" }, e.rank),
          el("span", { class: "race-name" }, pickLabel(e.name)),
          el("span", { class: "race-value" }, unit(e.value)),
          who.length ? el("span", { class: leader ? "race-pickers scoring-pickers" : "race-pickers" },
            `${leader ? "+10 " : ""}${who.map(sheetName).join(", ")}`) : null);
      })),
      elsewhere.length ? el("div", { class: "race-else" }, "Elsewhere: ",
        elsewhere.map(([n, p]) => `${sheetName(n)} (${pickLabel(p.awards[a.key].value)})`).join(", ")) : null) : null;

    return el("li", { class: race ? "award has-race" : "award" }, main, race);
  });
  return [el("h2", {}, "Awards"),
    el("p", { class: "section-note" }, `10 points each. ${entryLabel(entry)}.`),
    gwControl(), el("ul", { class: "list awards" }, items)];
}

function pickSummary(a, preds) {
  // Yes/no and number awards: group everyone's picks by answer.
  const groups = new Map();
  for (const [name, p] of preds) {
    const pick = p.awards[a.key];
    if (!pick) continue;
    const k = typeof pick.value === "number" ? `${pick.value} pts` : pickLabel(pick.value);
    if (!groups.has(k)) groups.set(k, { names: [], hit: a.leaders.includes(pick.value) });
    groups.get(k).names.push(sheetName(name));
  }
  if (!groups.size) return null;
  return el("div", { class: "pick-groups" }, [...groups].map(([k, g]) =>
    el("span", { class: g.hit ? "pick-group hit" : "pick-group" }, el("b", {}, k), ` ${g.names.join(", ")}`)));
}

function tablePage() {
  const entry = entryFor(state.gw);
  const rows = entry.table.map((r) => {
    const gd = r.goals_for - r.goals_against;
    return el("tr", { class: r.position <= 5 ? "zone top" : r.position >= 18 ? "zone drop" : "" },
      el("td", {}, r.position), el("td", {}, short(r.club)), el("td", {}, r.played),
      el("td", {}, signed(gd)), el("td", {}, r.points));
  });
  return [el("h2", {}, "Premier League table"),
    el("p", { class: "section-note" }, `${entryLabel(entry)}. Blue rows: top 5. Red rows: relegation.`),
    gwControl(),
    el("table", { class: "pl" },
      el("thead", {}, el("tr", {}, ["Pos", "Club", "P", "GD", "Pts"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))];
}

function rulesPage() {
  const lines = [
    ["Exact position", "5 each"], ["Champion (instead of the 5)", "20"],
    ["Top 5, any order, all five", "15"], ["Relegated three, any order", "15"],
    ["6th and 7th, both right", "13"], ["One off, when 8th to 17th is involved", "3 each"],
    ["Most exact positions (everyone tied gets it)", "20"], ["Each award", "10"],
  ];
  return [el("div", { class: "rules" },
    el("h2", {}, "How points work"),
    el("dl", { class: "kv", style: "margin:1.5rem 0 2.5rem" },
      lines.flatMap(([what, pts]) => [el("dt", {}, what), el("dd", { class: "hit" }, pts)])),
    el("h2", {}, "Ties"),
    el("p", {}, "Level on points? Ugwe's Brighton half-pick is checked first, then stinkers: everyone's worst miss is compared, then the next worst, until someone's is smaller."),
    el("h2", {}, "Gameweeks"),
    el("p", {}, "Each finished gameweek is frozen as it stood. A postponed game counts in the gameweek it's actually played; points deductions count from the gameweek they happen."),
    el("h2", {}, "Awards"),
    el("p", {}, "Live awards go to whoever leads right now. Christmas awards settle on 25 December. PFA Player, first sacking, red cards and the final-day question are entered by hand."))];
}

// ---------- routing ----------
function navigate(page, gw) {
  const suffix = gw === "live" || !GW_PAGES.has(page) ? "" : `/gw${gw}`;
  const hash = `#${page}${suffix}`;
  if (location.hash === hash) route();
  else location.hash = hash;
}

function route() {
  const [page, gwPart] = location.hash.slice(1).split("/");
  state.page = PAGES.includes(page) ? page : "leaderboard";
  const gw = gwPart && /^gw\d+$/.test(gwPart) ? Number(gwPart.slice(2)) : "live";
  if (state.data && gw !== "live" && !state.data.gameweeks.some((g) => g.gameweek === gw)) {
    state.gw = "live";
  } else {
    state.gw = gw;
  }
  render();
}

function render() {
  for (const tab of document.querySelectorAll(".tab")) {
    if (tab.dataset.page === state.page) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  if (!state.data) return;
  const views = { leaderboard: leaderboardPage, awards: awardsPage, table: tablePage, rules: rulesPage };
  screen.replaceChildren(...views[state.page]());
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", () => navigate(tab.dataset.page, state.gw));
}
window.addEventListener("hashchange", route);

async function load() {
  try {
    const resp = await fetch("standings.json", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    // Files from before v2 have no history: treat them as a live view only.
    data.gameweeks = data.gameweeks || [];
    data.live = data.live || { gameweek: null, in_progress: false, table: data.table,
      awards: data.awards, scores: data.scores };
    state.data = data;
    renderTimes();
    route();
  } catch (err) {
    console.error("Could not load standings", err);
    screen.replaceChildren(el("h2", {}, "Standings aren't available yet"),
      el("p", { class: "section-note" },
        "They update every 15 minutes on matchdays. Refresh in a little while."));
  }
}

route();
load();
