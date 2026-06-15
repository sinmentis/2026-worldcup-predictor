const GROUPS = "ABCDEFGHIJKL".split("");

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function loadGroups() {
  const grid = document.getElementById("group-grid");
  grid.innerHTML = "";
  await Promise.all(GROUPS.map(async (g) => {
    const rows = await (await fetch(`/api/groups/${g}/standings`)).json();
    const div = document.createElement("div");
    div.innerHTML = `<h3>Group ${g}</h3><table>
      <thead><tr><th>Team</th><th>P</th><th>W</th><th>D</th><th>L</th><th>GD</th><th>Pts</th></tr></thead>
      <tbody>${rows.map(r => `<tr><td>${r.team}</td><td>${r.played}</td><td>${r.won}</td>
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
        `<div class="${winHome ? "winner" : ""}">${m.home_team ?? "TBD"}</div>
         <div>${m.home_score ?? "–"} : ${m.away_score ?? "–"}</div>
         <div class="${winAway ? "winner" : ""}">${m.away_team ?? "TBD"}</div>`;
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
  document.getElementById("match-detail").innerHTML =
    `<h3>${esc(d.match.home_team)} vs ${esc(d.match.away_team)}</h3>
     <p>Stage: ${esc(d.match.stage)}</p>
     ${p ? `<p>Prediction: H ${(p.p_home*100).toFixed(0)}% / D ${(p.p_draw*100).toFixed(0)}% / A ${(p.p_away*100).toFixed(0)}%</p>
            <p>Most likely: ${esc(p.ml_home)}-${esc(p.ml_away)}</p>
            <p>${esc(p.reasoning || "")}</p>` : "<p>No prediction yet.</p>"}`;
  document.getElementById("match-modal").showModal();
}

function showTab(tab) {
  document.getElementById("groups-tab").hidden = tab !== "groups";
  document.getElementById("knockout-tab").hidden = tab !== "knockout";
}

function refresh() { loadGroups(); loadBracket(); }
const es = new EventSource("/api/events");
es.addEventListener("update", refresh);
es.onerror = () => { document.getElementById("status").textContent = "reconnecting"; };
refresh();
