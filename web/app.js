"use strict";

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NET_LABEL = {
  in_network: "In network", out_of_network: "Out of network", not_listed: "Not in directory",
  unknown: "Network unknown", unchecked: "Not checked",
};
const NET_COLOR = { in_network: "--in", out_of_network: "--out", not_listed: "--out", unknown: "--unk", unchecked: "--unk" };
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

const state = { tab: "picks", data: null, markers: new Map(), selected: null };

// ---- map -----------------------------------------------------------------
const map = L.map("map", { zoomControl: true, zoomSnap: 0.25 }).setView([39.8, -98.6], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
}).addTo(map);
const layer = L.layerGroup().addTo(map);

function pinIcon(p, label) {
  const color = css(NET_COLOR[p.network] || "--unk");
  const size = label ? 24 : 14;
  return L.divIcon({
    className: "", iconSize: [size, size],
    html: `<div class="pin" style="background:${color};width:${size}px;height:${size}px">${label ?? ""}</div>`,
  });
}

function drawMap(rows, picks) {
  layer.clearLayers();
  state.markers.clear();
  const d = state.data;
  if (!d) return;
  const home = [d.origin.lat, d.origin.lon];
  L.circle(home, { radius: d.radius * 1609.34, color: css("--accent"), weight: 1, fillOpacity: 0.04 }).addTo(layer);
  L.circleMarker(home, { radius: 6, color: css("--ink"), fillOpacity: 1 }).bindTooltip("You").addTo(layer);
  const pickRank = new Map(picks.map((p, i) => [p.id, i + 1]));
  for (const p of rows) {
    if (p.lat == null) continue;
    const m = L.marker([p.lat, p.lon], {
      icon: pinIcon(p, pickRank.get(p.id)),
      zIndexOffset: pickRank.has(p.id) ? 1000 : 0,
    }).bindTooltip(`${esc(p.name)}${p.score != null ? ` · ${Math.round(p.score)}` : ""}`);
    m.on("click", () => openDetail(p.id));
    m.addTo(layer);
    state.markers.set(p.id, m);
  }
  if (state.needFit) {
    map.fitBounds(L.latLng(home).toBounds(d.radius * 2 * 1609.34), { padding: [10, 10] });
    state.needFit = false;
  }
}

// ---- filtering / ranking ---------------------------------------------------
function visible() {
  const d = state.data;
  if (!d) return [];
  const nf = $("#netfilter").value;
  let rows = d.providers.filter((p) =>
    nf === "all" ? true : nf === "in" ? p.network === "in_network" : !["out_of_network", "not_listed"].includes(p.network));
  const q = $("#filter").value.trim().toLowerCase();
  if (q && state.tab === "browse") rows = rows.filter((p) => `${p.name} ${p.facts.group ?? ""}`.toLowerCase().includes(q));
  return rows;
}

function topPicks(rows) {
  // Prefer verified in-network providers; fall back to unknown ones if too few.
  const inNet = rows.filter((p) => p.network === "in_network");
  const pool = inNet.length >= 3 ? inNet : rows;
  return [...pool].filter((p) => p.score != null).sort((a, b) => b.score - a.score).slice(0, 5);
}

function sortRows(rows) {
  const k = $("#sort").value;
  const cmp = {
    score: (a, b) => (b.score ?? -1) - (a.score ?? -1),
    distance: (a, b) => (a.distance ?? 1e9) - (b.distance ?? 1e9),
    name: (a, b) => a.name.localeCompare(b.name),
  }[k];
  return [...rows].sort(cmp);
}

function why(p) {
  const good = p.breakdown.filter((c) => c.value != null && c.quality).sort((a, b) => b.value * b.weight - a.value * a.weight);
  return good.slice(0, 2).map((c) => `${c.label}: ${c.detail}`).join(" · ") || "No quality data. Ranked by distance.";
}

function card(p, rank) {
  const sub = [p.credential, p.specialty].filter(Boolean).join(" · ");
  return `<li class="card${state.selected === p.id ? " sel" : ""}" data-id="${esc(p.id)}">
    <div class="nm">${rank ? `<span class="rank">${rank}</span>` : ""}${esc(p.name)}</div>
    <div class="sub">${esc(sub)}${p.distance != null ? ` · ${p.distance.toFixed(1)} mi` : ""}</div>
    <div class="sub"><span class="pill net-${p.network}">${NET_LABEL[p.network]}</span>${p.facts.telehealth ? '<span class="pill">Telehealth</span>' : ""}</div>
    ${rank ? `<div class="why">${esc(why(p))}</div>` : ""}
    <div class="score">${p.score != null ? `<b>${Math.round(p.score)}</b><small>${p.kind === "organization" ? "distance only" : `${Math.round(p.confidence * 100)}% data`}</small>` : "<small>n/a</small>"}</div>
  </li>`;
}

function render() {
  const d = state.data;
  const rows = visible();
  const picks = topPicks(rows);
  $(".panel").dataset.tab = state.tab;
  $("#count").textContent = d ? `(${rows.length})` : "";
  const list = $("#list");
  if (!d) { list.innerHTML = ""; return; }
  if (!rows.length) {
    list.innerHTML = `<li class="empty">Nothing matched. Try a bigger radius, or set the network filter to "Show everything".</li>`;
  } else if (state.tab === "picks") {
    const tip = d.service.kind === "organization"
      ? `<li class="empty note">No public quality data exists for ${esc(d.service.label.toLowerCase())}, so these are ranked by distance only.</li>` : "";
    list.innerHTML = tip + picks.map((p, i) => card(p, i + 1)).join("");
  } else {
    list.innerHTML = sortRows(rows).map((p) => card(p)).join("");
  }
  drawMap(rows, picks);
}

// ---- detail ------------------------------------------------------------------
function fmtPhone(s) { const m = String(s).replace(/\D/g, "").match(/^(\d{3})(\d{3})(\d{4})$/); return m ? `(${m[1]}) ${m[2]}-${m[3]}` : s; }

function openDetail(id) {
  const p = state.data.providers.find((x) => x.id === id);
  if (!p) return;
  state.selected = id;
  document.querySelectorAll(".card").forEach((c) => c.classList.toggle("sel", c.dataset.id === id));
  const f = p.facts;
  const addr = `${p.street}, ${p.city}, ${p.state} ${p.zip}`;
  const isHosp = p.kind === "hospital";
  const npiLink = isHosp ? "" : `<a target="_blank" rel="noopener" href="https://npiregistry.cms.hhs.gov/provider-view/${esc(p.id)}">NPI registry record</a>`;
  const ccLink = isHosp ? `<a target="_blank" rel="noopener" href="https://www.medicare.gov/care-compare/details/hospital/${esc(f.ccn)}">Medicare Care Compare</a>` : "";
  const facts = [
    ["Credential", p.credential], ["Specialty", p.specialty], ["Group", f.group], ["Medical school", f.medical_school],
    ["Graduated", f.grad_year], ["Telehealth", f.telehealth ? "Yes" : null], ["Emergency dept", isHosp ? (f.emergency ? "Yes" : "No") : null],
    ["Ownership", f.ownership], ["NPI", isHosp ? null : p.id],
  ].filter(([, v]) => v != null && v !== "");
  const hosps = (f.hospitals || []).map((h) => `<li>${esc(h.name)}${h.stars ? ` (${h.stars}★)` : ""}</li>`).join("");
  const bk = p.breakdown.map((c) => `<div class="bk"><span class="lab">${esc(c.label)} <small class="note">×${c.weight}</small></span>
      <span class="meter"><i style="width:${c.value == null ? 0 : Math.round(c.value * 100)}%"></i></span>
      <span class="det">${esc(c.detail)}</span></div>`).join("");
  const el = $("#detail");
  el.innerHTML = `<button class="close" aria-label="Close">Close</button>
    <div class="note">${esc(state.data.service.label)}</div>
    <h2>${esc(p.name)}</h2>
    <div>${esc(addr)}${p.located === "zip" ? ' <span class="note">(map pin at ZIP center)</span>' : ""}</div>
    <div>${p.phone ? `<a href="tel:${esc(p.phone)}">${esc(fmtPhone(p.phone))}</a> · ` : ""}
      <a target="_blank" rel="noopener" href="https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(addr)}">Directions</a>
      ${p.distance != null ? ` · ${p.distance.toFixed(1)} mi` : ""}</div>
    <h3>Network</h3>
    <div><span class="pill net-${p.network}">${NET_LABEL[p.network]}</span> <span class="note">${esc(p.network_detail)}</span></div>
    ${p.network === "unchecked" || p.network === "unknown" ? `<p><button class="btn" id="chk">Check network now</button></p>` : ""}
    <p class="note">Directories are often stale. Call the office and confirm they take your specific plan before booking.</p>
    <h3>Score ${p.score != null ? Math.round(p.score) : "n/a"} <span class="note">(${Math.round(p.confidence * 100)}% of quality data available)</span></h3>
    ${bk}
    <p class="note">Missing data pulls the score toward 50 rather than counting as zero. MIPS mostly measures Medicare reporting, not outcomes, so use the score as a tie-breaker.</p>
    <h3>Details</h3>
    <dl>${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
    ${hosps ? `<h3>Hospital affiliations</h3><ul>${hosps}</ul>` : ""}
    <p>${npiLink} ${ccLink}</p>`;
  el.hidden = false;
  el.querySelector(".close").onclick = closeDetail;
  const chk = el.querySelector("#chk");
  if (chk) chk.onclick = async () => {
    chk.disabled = true; chk.textContent = "Checking…";
    const r = await fetch(`api/network/${encodeURIComponent(id)}`, { method: "POST" });
    if (r.ok) Object.assign(p, await r.json());
    render(); openDetail(id);
  };
  const m = state.markers.get(id);
  if (m) { map.panTo(m.getLatLng()); m.openTooltip(); }
}
function closeDetail() { $("#detail").hidden = true; state.selected = null; render(); }

// ---- wiring --------------------------------------------------------------------
async function search(ev) {
  ev?.preventDefault();
  closeDetail();
  const params = new URLSearchParams({ service: $("#service").value, radius: $("#radius").value });
  if ($("#where").value.trim()) params.set("where", $("#where").value.trim());
  $("#status").textContent = "Searching… (first search in an area can take a minute; later ones are cached)";
  $("#list").innerHTML = "";
  try {
    const r = await fetch(`api/search?${params}`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    state.data = body;
    state.needFit = true;
    const n = body.providers.length;
    const inNet = body.providers.filter((p) => p.network === "in_network").length;
    $("#status").textContent = `${n} found within ${body.radius} mi of ${body.origin.query}` +
      (body.network_source === "none" ? " · no network source configured" : ` · ${inNet} in network`) +
      (body.elapsed ? ` · ${body.elapsed}s` : "");
    localStorage.setItem("hpd", JSON.stringify({ service: $("#service").value, where: $("#where").value, radius: $("#radius").value }));
  } catch (e) {
    state.data = null;
    $("#status").textContent = `Search failed: ${e.message}`;
  }
  render();
}

async function init() {
  const cfg = await (await fetch("api/config")).json();
  const groups = {};
  for (const s of cfg.services) (groups[s.group] ||= []).push(s);
  $("#service").innerHTML = Object.entries(groups).map(([g, ss]) =>
    `<optgroup label="${esc(g)}">${ss.map((s) => `<option value="${s.key}">${esc(s.label)}</option>`).join("")}</optgroup>`).join("");
  $("#where").value = cfg.home;
  $("#radius").value = String(cfg.radius);
  try {
    const saved = JSON.parse(localStorage.getItem("hpd") || "null");
    if (saved) { $("#service").value = saved.service; $("#where").value = saved.where; $("#radius").value = saved.radius; }
  } catch { /* ignore */ }
  const banner = $("#banner");
  if (cfg.demo) { banner.hidden = false; banner.textContent = "DEMO MODE: every provider shown is fictional. Run without --demo for real data."; }
  else if (cfg.network === "none") { banner.hidden = false; banner.textContent = "No network source configured, so network status shows as unknown. See README → “Connect your network”."; }
  $("#q").addEventListener("submit", search);
  $("#service").addEventListener("change", search);
  for (const id of ["#netfilter", "#sort"]) $(id).addEventListener("change", render);
  $("#filter").addEventListener("input", render);
  document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    render();
  }));
  $("#list").addEventListener("click", (e) => { const c = e.target.closest(".card"); if (c) openDetail(c.dataset.id); });
  search();
}
init();
