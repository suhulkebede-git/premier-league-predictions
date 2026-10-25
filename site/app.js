"use strict";

const PAGES = {
  leaderboard: { no: "P302", title: "Leaderboard" },
  awards: { no: "P303", title: "Awards" },
  table: { no: "P304", title: "Premier League table" },
  rules: { no: "P305", title: "How points work" },
};

const SHORT = {
  "AFC Bournemouth": "Bournemouth",
  "Brighton & Hove Albion": "Brighton",
  "Manchester City": "Man City",
  "Manchester United": "Man Utd",
  "Newcastle United": "Newcastle",
  "Nottingham Forest": "Forest",
  "Tottenham Hotspur": "Spurs",
  "Leeds United": "Leeds",
  "Coventry City": "Coventry",
  "Ipswich Town": "Ipswich",
  "Hull City": "Hull",
  "Crystal Palace": "Palace",
};

const BREAKDOWN = [
  ["champion", "Champion"],
  ["exact", "Exact positions"],
  ["top5", "Top 5"],
  ["relegation", "Relegation"],
  ["sixth_seventh", "6th and 7th"],
  ["one_off", "One off (8th to 17th)"],
  ["most_exact", "Most exact positions"],
  ["awards", "Awards"],
];

let data = null;

const screen = document.getElementById("screen");
const short = (club) => SHORT[club] || club;
const displayName = (name) => name.charAt(0) + name.slice(1).toLowerCase();

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function draw(...nodes) {
  // Number every line so the page draws top to bottom, teletext style.
  screen.replaceChildren(...nodes);
  screen.classList.remove("draw");
  void screen.offsetWidth;
  let i = 0;
  for (const child of screen.children) child.style.setProperty("--i", i++);
  for (const list of screen.querySelectorAll(".rows")) {
    for (const row of list.children) row.style.setProperty("--i", i++);
  }
  screen.classList.add("draw");
}

function formatTime(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return `${day} ${time}`;
}

function positions() {
  return Object.fromEntries(data.table.map((r) => [r.club, r.position]));
}

function misses(person) {
  const pos = positions();
  return data.predictions[person].order.map((club, i) => ({
    club, predicted: i + 1, now: pos[club], diff: Math.abs(i + 1 - pos[club]),
  }));
}

function worstMiss(person) {
  return misses(person).reduce((a, b) => (b.diff > a.diff ? b : a));
}

function tieNote(score, above) {
  if (!above || above.total !== score.total) return null;
  const tbA = above.hits.tiebreak.length, tbB = score.hits.tiebreak.length;
  if (above.rank === score.rank) return "Level on points and every miss: rank shared.";
  if (tbA !== tbB) return `Level on ${score.total}: ${displayName(above.person)} wins on the Brighton half-pick.`;
  const i = above.misses_desc.findIndex((m, k) => m !== score.misses_desc[k]);
  const nth = ["worst", "2nd worst", "3rd worst"][i] || `${i + 1}th worst`;
  return `Level on ${score.total}: ${nth} miss ${above.misses_desc[i]} beats ${score.misses_desc[i]}.`;
}

function personPage(score) {
  const pred = data.predictions[score.person];
  const all = misses(score.person);
  const stinkers = new Set(
    [...all].sort((a, b) => b.diff - a.diff).filter((m) => m.diff > 0).slice(0, 3).map((m) => m.club));
  const oneOff = new Set(score.hits.one_off);
  const awardsByKey = Object.fromEntries(data.awards.map((a) => [a.key, a]));

  const points = BREAKDOWN.map(([key, label]) =>
    el("li", { class: "row points-row" }, el("span", {}, label),
      el("span", { class: `num ${score.breakdown[key] ? "green" : "dim"}` }, score.breakdown[key])));

  const order = all.map((m) => {
    const cls = m.diff === 0 ? "green" : oneOff.has(m.club) ? "cyan" : stinkers.has(m.club) ? "red" : "";
    return el("li", { class: `row order-row ${cls}` },
      el("span", { class: "num" }, m.predicted), el("span", {}, short(m.club)),
      el("span", { class: "num" }, m.now), el("span", { class: "num" }, m.diff === 0 ? "=" : `±${m.diff}`));
  });

  const awards = data.awards.map((a) => {
    const pick = pred.awards[a.key];
    const hit = score.hits.awards.includes(a.key);
    const half = score.hits.tiebreak.includes(a.key);
    const known = a.leaders.length > 0;
    const [mark, cls] = hit ? ["+", "green"] : half ? ["~", "magenta"] : known ? ["x", "red"] : ["-", "dim"];
    return el("li", { class: "row award-row" },
      el("span", { class: cls, "aria-hidden": "true" }, mark),
      el("span", {}, `${a.label}: `, el("span", { class: "pick" }, pick ? pick.raw : "no pick"),
        el("span", { class: "sr-only" }, hit ? " (scored)" : known ? " (missed)" : " (not decided)")));
  });

  return el("li", { class: "person", id: `person-${score.person}` },
    el("h2", {}, `${displayName(score.person)} ${score.total}`),
    el("ul", { class: "rows" }, points),
    el("h2", {}, "Predicted vs now"),
    el("p", { class: "dim" }, "Green exact, cyan one off, red the three biggest stinkers."),
    el("ul", { class: "rows" },
      el("li", { class: "row order-row head" }, el("span", { class: "num" }, "Pr"),
        el("span", {}, "Club"), el("span", { class: "num" }, "Now"), el("span", { class: "num" }, "Off")),
      order),
    el("h2", {}, "Awards"),
    el("ul", { class: "rows" }, awards));
}

function leaderboard() {
  const rows = [el("li", { class: "row leader-row head" },
    el("span", { class: "num" }, "Pos"), el("span", {}, "Name"),
    el("span", { class: "num" }, "Pts"), el("span", {}, "Stinker"))];
  data.scores.forEach((s, i) => {
    const worst = worstMiss(s.person);
    const button = el("button", { type: "button", "aria-expanded": "false",
      "aria-controls": `person-${s.person}` }, displayName(s.person));
    const row = el("li", { class: "row leader-row" },
      el("span", { class: "num" }, s.rank), button,
      el("span", { class: "num pts" }, s.total),
      el("span", { class: "red" }, `${short(worst.club)} ${worst.diff}`));
    button.addEventListener("click", () => {
      const open = document.getElementById(`person-${s.person}`);
      if (open) { open.remove(); button.setAttribute("aria-expanded", "false"); return; }
      row.after(personPage(s));
      button.setAttribute("aria-expanded", "true");
    });
    rows.push(row);
    const note = tieNote(s, data.scores[i - 1]);
    if (note) rows.push(el("li", { class: "row leader-row" }, el("span", { class: "tie-note" }, note)));
  });
  const games = data.games_played;
  return [
    el("h1", {}, data.season_over ? "Final table" : "Points table"),
    el("p", { class: "dim" }, data.season_over ? "Season over. Tap a name for their page." : `If the season ended now, after ${games} game${games === 1 ? "" : "s"} each. Tap a name for their page.`),
    el("ul", { class: "rows" }, rows),
  ];
}

function awardsPage() {
  const rows = data.awards.map((a) => {
    const right = Object.entries(data.predictions)
      .filter(([, p]) => p.awards[a.key] && a.leaders.includes(p.awards[a.key].value))
      .map(([name]) => displayName(name));
    const leaders = a.leaders.map((v) => (v === true ? "Yes" : v === false ? "No" : short(String(v))));
    const state = a.leaders.length ? (a.settled ? "Final" : "Live") : "";
    return el("li", { class: "row awards-row" },
      el("span", {}, a.label),
      a.leaders.length
        ? el("span", { class: "yellow" }, `${state}: ${leaders.join(", ")}${a.key === "title_margin" ? " pts" : ""}`)
        : el("span", { class: "dim" }, a.note ? a.note.charAt(0).toUpperCase() + a.note.slice(1) : "No data yet"),
      a.leaders.length
        ? el("span", { class: "who" }, right.length ? `Scoring: ${right.join(", ")}` : "Nobody has it")
        : null);
  });
  return [el("h1", {}, "Awards"), el("p", { class: "dim" }, "10 points each."), el("ul", { class: "rows" }, rows)];
}

function tablePage() {
  const rows = [el("li", { class: "row table-row head" }, el("span", { class: "num" }, "Pos"),
    el("span", {}, "Club"), el("span", { class: "num" }, "P"), el("span", { class: "num" }, "GD"),
    el("span", { class: "num" }, "Pts"))];
  for (const r of data.table) {
    const gd = r.goals_for - r.goals_against;
    const cls = r.position <= 5 ? "cyan" : r.position >= 18 ? "red" : "";
    rows.push(el("li", { class: `row table-row ${cls}` }, el("span", { class: "num" }, r.position),
      el("span", {}, short(r.club)), el("span", { class: "num" }, r.played),
      el("span", { class: "num" }, gd > 0 ? `+${gd}` : gd), el("span", { class: "num" }, r.points)));
  }
  return [el("h1", {}, "PL table"),
    el("p", { class: "dim" }, "Cyan top 5, red relegation."), el("ul", { class: "rows" }, rows)];
}

function rulesPage() {
  const lines = [
    ["Exact position", "5 each"], ["Champion (instead of the 5)", "20"],
    ["Top 5, any order, all five", "15"], ["Relegated three, any order", "15"],
    ["6th and 7th, both right", "13"], ["One off, when 8th to 17th is involved", "3 each"],
    ["Most exact positions (all tied get it)", "20"], ["Each award", "10"],
  ];
  return [
    el("h1", {}, "Rules"),
    el("ul", { class: "rows" }, lines.map(([what, pts]) =>
      el("li", { class: "row points-row" }, el("span", {}, what), el("span", { class: "num green" }, pts)))),
    el("h2", {}, "Ties"),
    el("p", {}, "Level on points? Biggest miss loses: compare everyone's worst miss, then the next worst, until someone's is smaller. Ugwe's Brighton half-pick is checked first."),
    el("h2", {}, "Awards"),
    el("p", {}, "Live awards go to whoever leads right now. Christmas awards settle on 25 December. PFA Player, first sacking, red cards and the final-day question are entered by hand."),
  ];
}

function show(page) {
  if (!PAGES[page]) page = "leaderboard";
  document.getElementById("page-no").textContent = PAGES[page].no;
  document.title = `${PAGES[page].no} ${PAGES[page].title}`;
  for (const key of document.querySelectorAll(".key")) {
    if (key.dataset.page === page) key.setAttribute("aria-current", "page");
    else key.removeAttribute("aria-current");
  }
  if (!data) return;
  const views = { leaderboard, awards: awardsPage, table: tablePage, rules: rulesPage };
  draw(...views[page]());
}

function route() { show(location.hash.slice(1)); }

for (const key of document.querySelectorAll(".key")) {
  key.addEventListener("click", () => { location.hash = key.dataset.page; });
}
window.addEventListener("hashchange", route);

async function load() {
  try {
    const resp = await fetch("standings.json", { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    data = await resp.json();
    document.getElementById("clock").textContent = formatTime(data.updated_at);
    document.getElementById("clock").setAttribute("datetime", data.updated_at);
    route();
  } catch (err) {
    console.error("Could not load standings", err);
    draw(el("h1", {}, "Page 302 not found"),
      el("p", {}, "The standings haven't been published yet. They update every 15 minutes on matchdays; refresh in a little while."));
  }
}

route();
load();
