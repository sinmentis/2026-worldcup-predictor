"use strict";

const GROUPS = "ABCDEFGHIJKL".split("");

// Canonical English -> Chinese display names for the 48 finalists.
const ZH = {
  "Mexico": "墨西哥", "South Africa": "南非", "South Korea": "韩国", "Czech Republic": "捷克",
  "Canada": "加拿大", "Switzerland": "瑞士", "Bosnia and Herzegovina": "波黑", "Qatar": "卡塔尔",
  "Brazil": "巴西", "Morocco": "摩洛哥", "Scotland": "苏格兰", "Haiti": "海地",
  "United States": "美国", "Australia": "澳大利亚", "Paraguay": "巴拉圭", "Turkey": "土耳其",
  "Germany": "德国", "Ecuador": "厄瓜多尔", "Ivory Coast": "科特迪瓦", "Curacao": "库拉索",
  "Netherlands": "荷兰", "Japan": "日本", "Sweden": "瑞典", "Tunisia": "突尼斯",
  "Belgium": "比利时", "Egypt": "埃及", "Iran": "伊朗", "New Zealand": "新西兰",
  "Spain": "西班牙", "Uruguay": "乌拉圭", "Saudi Arabia": "沙特阿拉伯", "Cape Verde": "佛得角",
  "France": "法国", "Senegal": "塞内加尔", "Norway": "挪威", "Iraq": "伊拉克",
  "Argentina": "阿根廷", "Algeria": "阿尔及利亚", "Austria": "奥地利", "Jordan": "约旦",
  "Portugal": "葡萄牙", "Colombia": "哥伦比亚", "DR Congo": "刚果（金）", "Uzbekistan": "乌兹别克斯坦",
  "England": "英格兰", "Croatia": "克罗地亚", "Ghana": "加纳", "Panama": "巴拿马",
};

// Flag emoji per team. England/Scotland use subdivision emoji; others ISO regional indicators.
const FLAG = {
  "Mexico": "🇲🇽", "South Africa": "🇿🇦", "South Korea": "🇰🇷", "Czech Republic": "🇨🇿",
  "Canada": "🇨🇦", "Switzerland": "🇨🇭", "Bosnia and Herzegovina": "🇧🇦", "Qatar": "🇶🇦",
  "Brazil": "🇧🇷", "Morocco": "🇲🇦", "Scotland": "🏴\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}", "Haiti": "🇭🇹",
  "United States": "🇺🇸", "Australia": "🇦🇺", "Paraguay": "🇵🇾", "Turkey": "🇹🇷",
  "Germany": "🇩🇪", "Ecuador": "🇪🇨", "Ivory Coast": "🇨🇮", "Curacao": "🇨🇼",
  "Netherlands": "🇳🇱", "Japan": "🇯🇵", "Sweden": "🇸🇪", "Tunisia": "🇹🇳",
  "Belgium": "🇧🇪", "Egypt": "🇪🇬", "Iran": "🇮🇷", "New Zealand": "🇳🇿",
  "Spain": "🇪🇸", "Uruguay": "🇺🇾", "Saudi Arabia": "🇸🇦", "Cape Verde": "🇨🇻",
  "France": "🇫🇷", "Senegal": "🇸🇳", "Norway": "🇳🇴", "Iraq": "🇮🇶",
  "Argentina": "🇦🇷", "Algeria": "🇩🇿", "Austria": "🇦🇹", "Jordan": "🇯🇴",
  "Portugal": "🇵🇹", "Colombia": "🇨🇴", "DR Congo": "🇨🇩", "Uzbekistan": "🇺🇿",
  "England": "🏴\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}", "Croatia": "🇭🇷", "Ghana": "🇬🇭", "Panama": "🇵🇦",
};

const STAGES = { group: "小组赛", R32: "32强", R16: "16强", QF: "八强", SF: "四强", "3RD": "季军赛", FINAL: "决赛" };

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function zh(name) {
  if (name == null || name === "") return "待定";
  return esc(ZH[name] || name);
}
function flag(name) { return FLAG[name] || "🏳️"; }
function pct(x) { return (x * 100).toFixed(1) + "%"; }
function pct0(x) { return Math.round(x * 100) + "%"; }

// team cell: flag + zh name (+ small english). `away` reverses direction.
function teamCell(name, side) {
  return `<div class="side ${side || ""}"><span class="flag">${flag(name)}</span>
    <span class="tname">${zh(name)}<span class="en">${esc(name)}</span></span></div>`;
}

function kickDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}
function dayKey(d) {
  return d ? d.toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "short" }) : "时间待定";
}
function timeStr(d) {
  return d ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "待定";
}

// Live kickoff status windows.
const FUTURE_WINDOW_MS = 12 * 3600 * 1000; // show a countdown when kickoff is within 12h
const LIVE_WINDOW_MS = 2.5 * 3600 * 1000; // treat a match as in-progress for ~2.5h after kickoff
function countdownText(ms) {
  if (ms <= 0) return "进行中";
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

// Per-second refresh of every upcoming card's status: countdown -> 进行中 (live) -> 待出结果.
function refreshStatuses() {
  const cards = document.querySelectorAll(".match[data-kickoff]");
  const now = Date.now();
  cards.forEach((card) => {
    const ms = new Date(card.dataset.kickoff).getTime() - now;
    let txt = "";
    let cls = "";
    let live = false;
    if (ms > 0) {
      if (ms < FUTURE_WINDOW_MS) {
        txt = "⏱ " + countdownText(ms);
        cls = ms < 3600 * 1000 ? "soon" : "";
      }
    } else if (ms > -LIVE_WINDOW_MS) {
      txt = "🔴 进行中";
      cls = "live";
      live = true;
    } else {
      txt = "⏳ 待出结果";
      cls = "awaiting";
    }
    const st = card.querySelector(".matchstatus");
    if (st) {
      st.textContent = txt;
      st.className = "matchstatus" + (cls ? " " + cls : "");
      st.style.display = txt ? "" : "none";
    }
    card.classList.toggle("live", live);
  });
}

let countdownTimer = null;
function startCountdowns() {
  if (countdownTimer) clearInterval(countdownTimer);
  refreshStatuses();
  countdownTimer = setInterval(refreshStatuses, 1000);
}

/* ---------------- upcoming ---------------- */
let UPCOMING = [];
let UP_REMAINING = 0;
const upFilter = { group: "", team: "", onlyIntel: false };

async function loadUpcoming() {
  const data = await (await fetch("/api/upcoming-predictions?limit=60")).json();
  UPCOMING = data.matches || [];
  UP_REMAINING = data.remaining || 0;
  renderUpcoming();
}

function matchCardHtml(m) {
  const d = kickDate(m.kickoff);
  const fac = (m.factors || []).map((f) => {
    const dir = f.lambda_delta >= 0 ? "up" : "down";
    const arrow = f.lambda_delta >= 0 ? "▲" : "▼";
    return `<span class="chip ${dir}">${arrow} ${zh(f.team)} ${esc(f.description)}</span>`;
  }).join("");
  return `<div class="match card" ${d ? `data-kickoff="${esc(m.kickoff)}"` : ""} onclick="showDetail(${m.match_id})">
    ${teamCell(m.home_team, "")}
    <div class="mid">
      <div class="kick">${m.group ? esc(m.group) + "组 · " : ""}${esc(timeStr(d))}</div>
      <div class="matchstatus" style="display:none"></div>
      <span class="scoreline">${esc(m.ml_home)}-${esc(m.ml_away)}</span>
      <div class="xg">预期 ${m.exp_home_goals.toFixed(2)} : ${m.exp_away_goals.toFixed(2)}</div>
    </div>
    ${teamCell(m.away_team, "away")}
    <div class="probbar">
      <span class="h" style="flex-basis:${m.p_home * 100}%" title="主胜">${pct0(m.p_home)}</span>
      <span class="d" style="flex-basis:${m.p_draw * 100}%" title="平">${pct0(m.p_draw)}</span>
      <span class="a" style="flex-basis:${m.p_away * 100}%" title="客胜">${pct0(m.p_away)}</span>
    </div>
    ${fac ? `<div class="factors">${fac}</div>` : ""}
  </div>`;
}

function renderUpcoming() {
  const el = document.getElementById("upcoming");
  const teams = [...new Set(UPCOMING.flatMap((m) => [m.home_team, m.away_team]))]
    .sort((a, b) => zh(a).localeCompare(zh(b), "zh"));
  if (upFilter.team && !teams.includes(upFilter.team)) upFilter.team = "";

  const ms = UPCOMING.filter((m) =>
    (!upFilter.group || m.group === upFilter.group) &&
    (!upFilter.team || m.home_team === upFilter.team || m.away_team === upFilter.team) &&
    (!upFilter.onlyIntel || (m.factors && m.factors.length)));

  const groupOpts = ["", ..."ABCDEFGHIJKL".split("")]
    .map((g) => `<option value="${g}"${g === upFilter.group ? " selected" : ""}>${g ? g + "组" : "全部组"}</option>`).join("");
  const teamOpts = ["", ...teams]
    .map((t) => `<option value="${esc(t)}"${t === upFilter.team ? " selected" : ""}>${t ? zh(t) : "全部球队"}</option>`).join("");
  const active = upFilter.group || upFilter.team || upFilter.onlyIntel;

  let html = `<div class="section-head">
    <span class="pill">剩余 ${UP_REMAINING} 场比赛</span>
    <span class="muted">🟢主胜 · 🟡平 · 🔴客胜</span>
  </div>
  <div class="filterbar card">
    <select id="f-group" aria-label="按组筛选">${groupOpts}</select>
    <select id="f-team" aria-label="按球队筛选">${teamOpts}</select>
    <label class="f-check"><input type="checkbox" id="f-intel"${upFilter.onlyIntel ? " checked" : ""}/> 只看有情报</label>
    <span class="f-count">${ms.length} 场匹配</span>
    ${active ? `<button class="f-clear" id="f-clear">清除筛选</button>` : ""}
  </div>`;

  if (!ms.length) {
    html += `<div class="empty">${UPCOMING.length ? "没有符合筛选条件的比赛。" : "暂无已排程的比赛。请先执行 <code>worldcup fetch-fixtures</code> 同步赛程。"}</div>`;
  } else {
    let lastDay = null;
    for (const m of ms) {
      const dk = dayKey(kickDate(m.kickoff));
      if (dk !== lastDay) { html += `<div class="day-label">${esc(dk)}</div>`; lastDay = dk; }
      html += matchCardHtml(m);
    }
  }
  el.innerHTML = html;

  const g = document.getElementById("f-group");
  if (g) g.onchange = (e) => { upFilter.group = e.target.value; renderUpcoming(); };
  const t = document.getElementById("f-team");
  if (t) t.onchange = (e) => { upFilter.team = e.target.value; renderUpcoming(); };
  const ic = document.getElementById("f-intel");
  if (ic) ic.onchange = (e) => { upFilter.onlyIntel = e.target.checked; renderUpcoming(); };
  const c = document.getElementById("f-clear");
  if (c) c.onclick = () => { upFilter.group = ""; upFilter.team = ""; upFilter.onlyIntel = false; renderUpcoming(); };

  startCountdowns();
}

/* ---------------- forecast ---------------- */
async function loadForecast() {
  const el = document.getElementById("forecast");
  const rows = await (await fetch("/api/forecast")).json();
  if (!rows.length) {
    el.innerHTML = `<div class="empty">尚未运行模拟。请执行 <code>worldcup simulate</code> 生成夺冠预测。</div>`;
    return;
  }
  const n = rows[0].n_iter;
  const max = rows[0].title_prob || 1;
  const body = rows.map((r, i) => {
    const cls = i === 0 ? "top1" : i === 1 ? "top2" : i === 2 ? "top3" : "";
    return `<div class="lb-row ${cls}">
      <div class="rank">${i + 1}</div>
      <div class="lb-team"><span class="flag">${flag(r.team)}</span>${zh(r.team)}</div>
      <div class="track"><i style="width:${Math.max(3, (r.title_prob / max) * 100)}%"></i></div>
      <div class="lb-pct">${pct(r.title_prob)}</div>
    </div>`;
  }).join("");
  el.innerHTML = `<h2>夺冠预测 <small>（${n} 次蒙特卡洛模拟 · 进度条为相对夺冠概率）</small></h2>
    <div class="card lb">${body}</div>`;
}

/* ---------------- accuracy ---------------- */
async function loadAccuracy() {
  const el = document.getElementById("accuracy");
  const data = await (await fetch("/api/accuracy")).json();
  const a = data.aggregate || { n: 0 };
  if (!a.n) {
    el.innerHTML = `<div class="empty">还没有已结束的比赛可对比。等比赛打完、结果同步后即可显示。</div>`;
    return;
  }
  const PICK = ["主胜", "平局", "客胜"];
  const beats = a.beats_baseline;
  const tiles = `<div class="scoreboard">
    <div class="card stat"><div class="v">${a.n}</div><div class="k">已对比场次</div></div>
    <div class="card stat ${a.pick_hit_rate >= 0.5 ? "good" : "bad"}"><div class="v">${pct0(a.pick_hit_rate)}</div><div class="k">胜平负命中率</div></div>
    <div class="card stat ${beats ? "good" : "bad"}"><div class="v">${a.model_rps.toFixed(3)}</div><div class="k">模型 RPS（越低越好）</div></div>
    <div class="card stat"><div class="v">${a.baseline_rps.toFixed(3)}</div><div class="k">基准 RPS</div></div>
    <div class="card stat"><div class="v">${pct0(a.exact_rate)}</div><div class="k">比分精确命中</div></div>
  </div>`;
  const verdict = `<div class="verdict ${beats ? "good" : "bad"}">
    ${beats ? "✓ 我们的模型优于基准（RPS 更低）" : "✗ 模型暂未跑赢基准"} ·
    平均每场比基准 ${(Math.abs(a.baseline_rps - a.model_rps)).toFixed(3)} ${beats ? "更准" : "更差"}
  </div>`;
  let list = "";
  let lastDay = null;
  // newest match first (backend returns oldest-first)
  const ordered = (data.matches || []).slice().reverse();
  for (const m of ordered) {
    const dk = dayKey(kickDate(m.kickoff));
    if (dk !== lastDay) { list += `<div class="day-label">${esc(dk)}</div>`; lastDay = dk; }
    const correct = m.pick_correct;
    const stage = STAGES[m.stage] || m.stage || "";
    const meta = `<div class="res-meta">
      <span class="tag">${esc(stage)}</span>
      ${m.group ? `<span>${esc(m.group)}组</span>` : ""}
      <span class="when">${esc(timeStr(kickDate(m.kickoff)))}</span></div>`;
    list += `<div class="res card" onclick="showDetail(${m.match_id})">
      ${meta}
      ${teamCell(m.home_team, "")}
      <div class="vs">
        <span class="final">${m.home_score} - ${m.away_score}</span>
        <span class="pred">预测 ${esc(m.ml_home)}-${esc(m.ml_away)} · 押 ${PICK[m.pred_pick]}</span>
      </div>
      ${teamCell(m.away_team, "away")}
      <div class="verdict-mark ${correct ? "ok" : "no"}">${correct ? "✓" : "✗"}</div>
    </div>`;
  }
  el.innerHTML = `<h2>预测 vs 实际 <small>（与我们最初的预测对比）</small></h2>${tiles}${verdict}${list}`;
}

/* ---------------- groups ---------------- */
async function loadGroups() {
  const grid = document.getElementById("group-grid");
  const cards = await Promise.all(GROUPS.map(async (g) => {
    const rows = await (await fetch(`/api/groups/${g}/standings`)).json();
    const body = rows.map((r) => `<tr>
      <td class="team"><span class="flag" style="font-size:18px">${flag(r.team)}</span>${zh(r.team)}</td>
      <td>${r.played}</td><td>${r.won}</td><td>${r.drawn}</td><td>${r.lost}</td>
      <td>${r.gd > 0 ? "+" + r.gd : r.gd}</td><td class="pts">${r.pts}</td></tr>`).join("");
    return `<div class="card group-card"><h3><span class="badge">${g}</span> ${g} 组</h3>
      <table><thead><tr><th>队伍</th><th>赛</th><th>胜</th><th>平</th><th>负</th><th>净</th><th>分</th></tr></thead>
      <tbody>${body}</tbody></table></div>`;
  }));
  grid.innerHTML = cards.join("");
}

/* ---------------- bracket / knockout projection ---------------- */
async function loadBracket() {
  const el = document.getElementById("bracket");
  const data = await (await fetch("/api/bracket-projection")).json();
  const teams = data.teams || [];
  if (!teams.length) {
    el.innerHTML = `<div class="empty">尚未运行模拟。请执行 <code>worldcup simulate</code> 生成晋级概率。</div>`;
    return;
  }
  const ROUNDS = [["advance_prob", "出线"], ["r16_prob", "16强"], ["qf_prob", "8强"], ["sf_prob", "4强"], ["final_prob", "决赛"], ["title_prob", "夺冠"]];
  const heat = (p) => `background:rgba(52,211,153,${(0.06 + 0.82 * p).toFixed(3)})`;

  let gq = `<h3 class="bk-sub">各组出线预测 <small>（绿底=最可能出线的两支）</small></h3><div class="bk-groups">`;
  for (const g of "ABCDEFGHIJKL".split("")) {
    const gt = data.groups[g] || [];
    gq += `<div class="card bk-group"><h4><span class="badge">${g}</span> ${g} 组</h4>` +
      gt.map((t, i) => `<div class="bk-grow ${i < 2 ? "qual" : ""}"><span>${flag(t.team)} ${zh(t.team)}</span><span class="muted">${pct0(t.advance_prob)}</span></div>`).join("") +
      `</div>`;
  }
  gq += `</div>`;

  let hm = `<h3 class="bk-sub">晋级热图 <small>（${data.n_iter} 次模拟 · 每支队走到各轮的概率）</small></h3>
    <div class="bk-heat-wrap"><table class="bk-heat"><thead><tr><th>队伍</th>${ROUNDS.map((r) => `<th>${r[1]}</th>`).join("")}</tr></thead><tbody>`;
  for (const t of teams) {
    hm += `<tr><td class="team">${flag(t.team)} ${zh(t.team)}</td>` +
      ROUNDS.map((r) => `<td style="${heat(t[r[0]])}">${pct0(t[r[0]])}</td>`).join("") + `</tr>`;
  }
  hm += `</tbody></table></div>`;
  el.innerHTML = gq + hm;
}

/* ---------------- match modal ---------------- */
async function showDetail(id) {
  if (!id) return;
  const res = await fetch(`/api/matches/${id}`);
  if (!res.ok) return;
  const d = await res.json();
  if (!d.match) return;
  const m = d.match;
  const p = d.prediction;
  let html = `<h3>${flag(m.home_team)} ${zh(m.home_team)} <span class="muted">vs</span> ${zh(m.away_team)} ${flag(m.away_team)}</h3>
    <p class="muted">阶段：${esc(STAGES[m.stage] || m.stage)}${m.kickoff ? " · " + esc(new Date(m.kickoff).toLocaleString("zh-CN")) : ""}</p>`;
  if (m.status === "FINISHED") html += `<p>最终比分：<b>${m.home_score} - ${m.away_score}</b></p>`;
  if (p) {
    html += `<div class="probbar" style="margin:10px 0">
      <span class="h" style="flex-basis:${p.p_home * 100}%">${pct0(p.p_home)}</span>
      <span class="d" style="flex-basis:${p.p_draw * 100}%">${pct0(p.p_draw)}</span>
      <span class="a" style="flex-basis:${p.p_away * 100}%">${pct0(p.p_away)}</span></div>`;
  }
  if (d.over25 != null) {
    html += `<p class="muted">大于 2.5 球 <b>${pct0(d.over25)}</b> · 双方均进球 <b>${pct0(d.btts)}</b></p>`;
  }
  if (d.scorelines && d.scorelines.length) {
    html += `<div class="md-sec"><div class="md-h">最可能比分</div><div class="md-scores">` +
      d.scorelines.map((s) => `<span class="md-score">${s.home}-${s.away} <small>${pct0(s.prob)}</small></span>`).join("") + `</div></div>`;
  }
  if (d.h2h && d.h2h.meetings.length) {
    const h = d.h2h;
    html += `<div class="md-sec"><div class="md-h">历史交锋（${zh(m.home_team)}视角）</div>
      <p class="muted">${h.home_wins} 胜 ${h.draws} 平 ${h.away_wins} 负（近 ${h.meetings.length} 场）</p>` +
      h.meetings.slice(0, 5).map((g) => `<div class="md-h2h"><span class="muted">${esc((g.date || "").slice(0, 10))}</span> ${zh(g.home_team)} <b>${g.home_score ?? "-"}-${g.away_score ?? "-"}</b> ${zh(g.away_team)}</div>`).join("") + `</div>`;
  }
  if (d.odds && d.odds.consensus) {
    const o = d.odds;
    const bp = (k) => (o.best[k].price ? `${o.best[k].price.toFixed(2)}@${esc(o.best[k].book || "")}` : "-");
    html += `<div class="md-sec"><div class="md-h">盘口（${o.n_books} 家）</div>
      <p class="muted">主胜 ${pct0(o.consensus.home)} (最佳 ${bp("home")}) · 平 ${pct0(o.consensus.draw)} (${bp("draw")}) · 客胜 ${pct0(o.consensus.away)} (${bp("away")})</p></div>`;
  }
  if (p && p.reasoning) html += `<p class="muted">关键因素：${esc(p.reasoning)}</p>`;
  if (!p) html += `<p class="muted">暂无预测。</p>`;
  document.getElementById("match-detail").innerHTML = html;
  document.getElementById("match-modal").showModal();
}

/* ---------------- value bets ---------------- */
async function loadValue() {
  const el = document.getElementById("value");
  const data = await (await fetch("/api/value-bets")).json();
  const bets = (data.bets || []).slice().sort((a, b) => {
    const ka = a.kickoff || "9999", kb = b.kickoff || "9999";
    if (ka !== kb) return ka < kb ? -1 : 1;
    return b.edge - a.edge;  // within a day, best edge first
  });
  if (!bets.length) {
    el.innerHTML = `<div class="empty">暂无价值投注。先在 <code>.env</code> 配置 <code>ODDS_API_KEY</code> 并运行 <code>worldcup fetch-odds</code> 拉取赔率；或当前没有超过阈值的 edge。</div>`;
    return;
  }
  const OUT = { home: "主胜", draw: "平局", away: "客胜" };
  const betLabel = (b) =>
    b.market === "totals" ? `${b.outcome === "over" ? "大" : "小"}${b.line}` : (OUT[b.outcome] || esc(b.outcome));
  const tag = (b) => (b.market === "totals" ? `<span class="vbet-mkt">大小球</span>` : `<span class="vbet-mkt h2h">胜平负</span>`);
  let list = "";
  let lastDay = null;
  for (const b of bets) {
    const d = kickDate(b.kickoff);
    const dk = dayKey(d);
    if (dk !== lastDay) { list += `<div class="day-label">${esc(dk)}</div>`; lastDay = dk; }
    list += `<div class="vbet card">
      <div class="vbet-match">${flag(b.home_team)} ${zh(b.home_team)} <span class="muted">vs</span> ${zh(b.away_team)} ${flag(b.away_team)} <span class="muted vbet-time">${esc(timeStr(d))}</span></div>
      <div class="vbet-pick">${tag(b)} 押 <b>${betLabel(b)}</b>${b.best_price ? ` @ <b>${b.best_price.toFixed(2)}</b> <span class="muted">(${esc(b.bookmaker || "")})</span>` : ""}</div>
      <div class="vbet-stats">
        <span>我们 <b>${pct0(b.our_prob)}</b></span>
        <span class="muted">市场 ${pct0(b.market_prob)}</span>
        <span class="vbet-edge">领先市场 +${(b.edge * 100).toFixed(1)}%</span>
        ${b.ev != null ? `<span>最佳价 EV ${(b.ev * 100).toFixed(0)}%</span>` : ""}
        <span class="vbet-kelly">建议仓位 ${(b.kelly * 100).toFixed(1)}%</span>
      </div>
    </div>`;
  }
  el.innerHTML = `<h2>价值投注 <small>（我们 vs 市场共识 · 市场通常更准，非稳赚）</small></h2>
    <div class="vbet-note">⚠️「建议仓位」= 该注占你<b>总资金</b>的比例（1/4 Kelly，已保守）。只有当我们的概率确实比市场更准时才有正收益，而这<b>并无保证</b>——请当作研究候选、先纸面跟单，别无脑下注。</div>${list}`;
}

/* ---------------- tabs + live refresh ---------------- */
const LOADERS = {
  upcoming: loadUpcoming, forecast: loadForecast, value: loadValue,
  accuracy: loadAccuracy, groups: loadGroups, knockout: loadBracket,
};
let current = "upcoming";

function showTab(tab) {
  current = tab;
  document.querySelectorAll("nav button[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  Object.keys(LOADERS).forEach((t) => { document.getElementById(t + "-tab").hidden = t !== tab; });
  LOADERS[tab]().catch((e) => console.error(e));
}

document.querySelectorAll("nav button[data-tab]").forEach((b) => {
  b.addEventListener("click", () => showTab(b.dataset.tab));
});

const es = new EventSource("/api/events");
es.addEventListener("update", () => {
  document.getElementById("status").classList.remove("stale");
  document.getElementById("status").textContent = "实时";
  LOADERS[current]().catch((e) => console.error(e));
});
es.onerror = () => {
  const s = document.getElementById("status");
  s.textContent = "重连中"; s.classList.add("stale");
};

showTab("upcoming");
