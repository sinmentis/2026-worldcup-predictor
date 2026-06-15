const GROUPS = "ABCDEFGHIJKL".split("");

// Chinese display names for the 48 finalists (canonical English -> Chinese).
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

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Show a team name in Chinese when known, else the (escaped) original; null -> 待定.
function zh(name) {
  if (name == null || name === "") return "待定";
  return esc(ZH[name] || name);
}

function pct(x) { return (x * 100).toFixed(1) + "%"; }

async function loadForecast() {
  const el = document.getElementById("forecast");
  const rows = await (await fetch("/api/forecast")).json();
  if (!rows.length) {
    el.innerHTML = "<p>尚未运行模拟。请先执行 <code>worldcup simulate</code> 生成夺冠预测。</p>";
    return;
  }
  const n = rows[0].n_iter;
  el.innerHTML = `<h2>夺冠预测 <small>（${n} 次蒙特卡洛模拟）</small></h2>
    <table class="forecast">
      <thead><tr><th>#</th><th>队伍</th><th>夺冠</th><th>进决赛</th><th>四强</th><th>出线</th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr>
        <td>${i + 1}</td><td>${zh(r.team)}</td>
        <td><b>${pct(r.title_prob)}</b></td><td>${pct(r.final_prob)}</td>
        <td>${pct(r.sf_prob)}</td><td>${pct(r.advance_prob)}</td></tr>`).join("")}
      </tbody></table>`;
}

async function loadGroups() {
  const grid = document.getElementById("group-grid");
  grid.innerHTML = "";
  await Promise.all(GROUPS.map(async (g) => {
    const rows = await (await fetch(`/api/groups/${g}/standings`)).json();
    const div = document.createElement("div");
    div.innerHTML = `<h3>${g} 组</h3><table>
      <thead><tr><th>队伍</th><th>赛</th><th>胜</th><th>平</th><th>负</th><th>净胜</th><th>积分</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td>${zh(r.team)}</td><td>${r.played}</td><td>${r.won}</td>
        <td>${r.drawn}</td><td>${r.lost}</td><td>${r.gd}</td><td><b>${r.pts}</b></td></tr>`).join("")}
      </tbody></table>`;
    grid.appendChild(div);
  }));
}

async function loadBracket() {
  const data = await (await fetch("/api/knockout/bracket")).json();
  for (const stage of ["R32", "R16", "QF", "SF", "FINAL"]) {
    const el = document.getElementById(stage);
    el.querySelectorAll(".match-card").forEach(n => n.remove());
    (data[stage] || []).forEach(m => {
      const card = document.createElement("div");
      card.className = "match-card";
      const winHome = m.home_score > m.away_score;
      const winAway = m.away_score > m.home_score;
      card.innerHTML =
        `<div class="${winHome ? "winner" : ""}">${zh(m.home_team)}</div>
         <div>${m.home_score ?? "–"} : ${m.away_score ?? "–"}</div>
         <div class="${winAway ? "winner" : ""}">${zh(m.away_team)}</div>`;
      card.onclick = () => showDetail(m.id);
      el.appendChild(card);
    });
  }
}

async function showDetail(id) {
  if (!id) return;
  const res = await fetch(`/api/matches/${id}`);
  if (!res.ok) return;
  const d = await res.json();
  if (!d.match) return;
  const p = d.prediction;
  const stages = { group: "小组赛", R32: "32强", R16: "16强", QF: "八强", SF: "四强", "3RD": "季军赛", FINAL: "决赛" };
  document.getElementById("match-detail").innerHTML =
    `<h3>${zh(d.match.home_team)} vs ${zh(d.match.away_team)}</h3>
     <p>阶段：${esc(stages[d.match.stage] || d.match.stage)}</p>
     ${p ? `<p>预测：胜 ${(p.p_home*100).toFixed(0)}% / 平 ${(p.p_draw*100).toFixed(0)}% / 负 ${(p.p_away*100).toFixed(0)}%</p>
            <p>最可能比分：${esc(p.ml_home)}-${esc(p.ml_away)}</p>
            ${p.reasoning ? `<p>关键因素：${esc(p.reasoning)}</p>` : ""}` : "<p>暂无预测。</p>"}`;
  document.getElementById("match-modal").showModal();
}

function showTab(tab) {
  document.getElementById("forecast-tab").hidden = tab !== "forecast";
  document.getElementById("groups-tab").hidden = tab !== "groups";
  document.getElementById("knockout-tab").hidden = tab !== "knockout";
}

function refresh() { loadForecast(); loadGroups(); loadBracket(); }
const es = new EventSource("/api/events");
es.addEventListener("update", refresh);
es.onerror = () => { document.getElementById("status").textContent = "重连中"; };
refresh();
