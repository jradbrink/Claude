/* Shared rendering for the Black Box dashboard (index.html) and the
   read-only share page (share.html). No dependencies. */

export const MONTHS = ["jan","feb","mar","apr","maj","jun","jul","aug","sep","okt","nov","dec"];
const $ = (id) => document.getElementById(id);

export function fmtDur(s) {
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}
export const fmtDate = (d) => d.toLocaleDateString("sv-SE");
export const fmtTime = (d) => d.toLocaleTimeString("sv-SE", { hour: "2-digit", minute: "2-digit" });
export const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b), m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

export function computeStats(trips) {
  const n = trips.length;
  const km = trips.reduce((a, t) => a + (+t.distance_km_est || 0), 0);
  const h = trips.reduce((a, t) => a + (t.duration_s || 0), 0) / 3600;
  const clean = trips.filter((t) => !t.cold_violation_count).length;
  const cw = trips.map((t) => t.coolant_warmup_s ?? t.warmup_s).filter((x) => x != null);
  const ow = trips.map((t) => t.oil_warmup_s).filter((x) => x != null);
  const last = n ? new Date(trips[0].started_at) : null; // trips sorted desc
  return { n, km, h, clean, medCool: median(cw), medOil: median(ow), last };
}

export function renderStats(s) {
  const warm = s.medCool != null
    ? `${Math.round(s.medCool / 60)}${s.medOil != null ? " / " + Math.round(s.medOil / 60) : ""} min`
    : "–";
  const tiles = [
    [s.n, "Antal körningar"],
    [`${Math.round(s.km).toLocaleString("sv-SE")} km`, "Total sträcka (est.)"],
    [`${s.h.toFixed(1)} h`, "Total körtid"],
    [s.n ? `${Math.round((s.clean / s.n) * 100)} %` : "–", "Utan överträdelse"],
    [s.last ? fmtDate(s.last) : "–", "Senast körd"],
    [warm, "Medianuppvärmning kylv./olja"],
  ];
  $("stats").innerHTML = tiles
    .map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`)
    .join("");
}

export function monthlyBuckets(trips) {
  const now = new Date();
  const keys = [];
  for (let i = 11; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    keys.push({ y: d.getFullYear(), m: d.getMonth(), count: 0 });
  }
  for (const t of trips) {
    const d = new Date(t.started_at);
    const k = keys.find((k) => k.y === d.getFullYear() && k.m === d.getMonth());
    if (k) k.count++;
  }
  return keys;
}

export function renderChart(buckets) {
  const W = 920, H = 220, L = 34, R = 6, T = 12, B = 26;
  const pw = W - L - R, ph = H - T - B;
  const maxC = Math.max(4, ...buckets.map((b) => b.count));
  const yMax = Math.ceil(maxC / 4) * 4;
  const slot = pw / buckets.length, bw = slot * 0.55;
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const y = T + ph - (ph * i) / 4;
    if (i > 0) g += `<line x1="${L}" y1="${y}" x2="${L + pw}" y2="${y}" stroke="var(--grid)" stroke-width="1"/>`;
    g += `<text x="${L - 7}" y="${y + 3.5}" text-anchor="end" font-size="10.5" fill="var(--ink-muted)">${(yMax * i) / 4}</text>`;
  }
  let bars = "";
  buckets.forEach((b, i) => {
    const x = L + i * slot + (slot - bw) / 2;
    const label = MONTHS[b.m] + (b.m === 0 || i === 0 ? ` ${String(b.y).slice(2)}` : "");
    if (b.count > 0) {
      const bh = (ph * b.count) / yMax, y = T + ph - bh, r = Math.min(3, bh);
      bars += `<path d="M${x},${T + ph} v${-(bh - r)} q0,${-r} ${r},${-r} h${bw - 2 * r} q${r},0 ${r},${r} v${bh - r} z" fill="var(--series)"/>`;
    }
    bars += `<rect x="${L + i * slot}" y="${T}" width="${slot}" height="${ph}" fill="transparent"
              data-tip="${MONTHS[b.m]} ${b.y}: ${b.count} körning${b.count === 1 ? "" : "ar"}"/>`;
    bars += `<text x="${L + i * slot + slot / 2}" y="${H - 8}" text-anchor="middle" font-size="10.5" fill="var(--ink-soft)">${label}</text>`;
  });
  g += `<line x1="${L}" y1="${T + ph}" x2="${L + pw}" y2="${T + ph}" stroke="var(--rule)" stroke-width="1.2"/>`;
  $("chart").innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Körningar per månad">${g}${bars}</svg>`;

  const tip = $("tooltip");
  $("chart").querySelectorAll("rect[data-tip]").forEach((r) => {
    r.addEventListener("mousemove", (e) => {
      tip.textContent = r.dataset.tip;
      tip.style.display = "block";
      tip.style.left = e.clientX + 12 + "px";
      tip.style.top = e.clientY - 30 + "px";
    });
    r.addEventListener("mouseleave", () => (tip.style.display = "none"));
  });
}

function detailHtml(t, events) {
  const d = (v) => (v == null ? "–" : v);
  const rows = [
    ["Slut", t.ended_at ? fmtTime(new Date(t.ended_at)) : "–"],
    ["Uppvärmd (helt)", t.warmup_s != null ? `${Math.round(t.warmup_s / 60)} min` : "nej"],
    ["Kylvätska varm", t.coolant_warmup_s != null ? `${Math.round(t.coolant_warmup_s / 60)} min` : "–"],
    ["Olja varm", t.oil_warmup_s != null ? `${Math.round(t.oil_warmup_s / 60)} min` : "–"],
    ["Kriterium", t.warm_criterion === "oil_and_coolant" ? "kylvätska + olja" : "kylvätska"],
  ];
  let html = `<div class="detail-grid">${rows
    .map(([k, v]) => `<div><b>${k}:</b> ${d(v)}</div>`)
    .join("")}</div>`;
  if (events && events.length) {
    html += `<div class="episodes"><b class="viol">Överträdelseepisoder (${events.length}):</b><ul>` +
      events.map((e) => {
        const start = e.started_at ? fmtTime(new Date(e.started_at)) : "–";
        const dur = e.duration_s != null ? `${e.duration_s} s` : "–";
        const temps = [
          e.coolant_c_at_start != null ? `kylvätska ${Math.round(e.coolant_c_at_start)} °C` : null,
          e.oil_c_at_start != null ? `olja ${Math.round(e.oil_c_at_start)} °C` : null,
        ].filter(Boolean).join(", ");
        return `<li>${start} • ${dur} • max ${e.max_rpm} r/min${temps ? " • " + temps : ""}</li>`;
      }).join("") + `</ul></div>`;
  } else {
    html += `<div class="episodes ok">Inga kallstartsöverträdelser under körningen.</div>`;
  }
  return html;
}

export function renderTable(trips, { expandable = false, eventsByTrip = {} } = {}) {
  $("tripCount").textContent = trips.length;
  const tbody = $("tripRows");
  tbody.innerHTML = "";
  trips.forEach((t) => {
    const d = new Date(t.started_at);
    const warm = t.warmup_s != null ? `${Math.round(t.warmup_s / 60)} min` : "ej varm";
    const v = t.cold_violation_count || 0;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${fmtDate(d)}</td><td>${fmtTime(d)}</td>
      <td>${fmtDur(t.duration_s || 0)}</td>
      <td>${(+t.distance_km_est || 0).toFixed(1)} km</td>
      <td>${warm}</td>
      <td>${t.max_rpm ?? "–"}</td>
      <td>${t.max_coolant_c != null ? Math.round(t.max_coolant_c) + " °C" : "–"}</td>
      <td>${t.max_oil_temp_c != null ? Math.round(t.max_oil_temp_c) + " °C" : "–"}</td>
      <td class="${v ? "viol" : "ok"}">${v || "inga"}</td>`;
    tbody.appendChild(tr);
    if (expandable) {
      tr.classList.add("clickable");
      tr.title = "Klicka för detaljer";
      tr.addEventListener("click", () => {
        const open = tr.nextElementSibling?.classList.contains("detail-row");
        if (open) { tr.nextElementSibling.remove(); return; }
        const detail = document.createElement("tr");
        detail.className = "detail-row";
        detail.innerHTML =
          `<td colspan="9">${detailHtml(t, eventsByTrip[t.id] || [])}</td>`;
        tr.after(detail);
      });
    }
  });
}

export function showApp(vehicle, trips, opts = {}) {
  $("vehicleName").textContent = vehicle?.display_name || "Fordon";
  $("vehicleMeta").textContent = [
    vehicle?.vin ? `VIN: ${vehicle.vin}` : null,
    vehicle?.model_year ? `Årsmodell: ${vehicle.model_year}` : null,
    opts.metaSuffix || `Uppdaterad: ${new Date().toLocaleString("sv-SE")}`,
  ].filter(Boolean).join("  •  ");
  renderStats(computeStats(trips));
  renderChart(monthlyBuckets(trips));
  renderTable(trips, opts);
  $("app").classList.remove("hidden");
}

/* Demo data so both pages render without any Supabase config. */
export function demoData() {
  const trips = [], eventsByTrip = {};
  let seed = 42;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const now = Date.now();
  for (let day = 335; day >= 1; day--) {
    const dt = new Date(now - day * 864e5);
    const p = [0.08,0.08,0.25,0.25,0.45,0.45,0.45,0.45,0.45,0.25,0.25,0.08][dt.getMonth()];
    if (rnd() < p) {
      const dur = 900 + Math.floor(rnd() * 6300);
      const viol = rnd() < 0.06 ? 1 : 0;
      const id = `demo-${day}`;
      const start = new Date(dt.setHours(8 + Math.floor(rnd() * 11), Math.floor(rnd() * 60)));
      trips.push({
        id,
        started_at: start.toISOString(),
        ended_at: new Date(start.getTime() + dur * 1000).toISOString(),
        duration_s: dur,
        distance_km_est: (dur / 3600) * (45 + rnd() * 40),
        warmup_s: 900 + Math.floor(rnd() * 500),
        coolant_warmup_s: 400 + Math.floor(rnd() * 300),
        oil_warmup_s: 900 + Math.floor(rnd() * 500),
        warmed_up: true,
        warm_criterion: "oil_and_coolant",
        max_rpm: 3200 + Math.floor(rnd() * 3200),
        max_coolant_c: 88 + rnd() * 8,
        max_oil_temp_c: 85 + rnd() * 15,
        cold_violation_count: viol,
      });
      if (viol) {
        eventsByTrip[id] = [{
          started_at: new Date(start.getTime() + 120000).toISOString(),
          duration_s: (2 + rnd() * 8).toFixed(1),
          max_rpm: 3400 + Math.floor(rnd() * 1500),
          coolant_c_at_start: 25 + rnd() * 40,
          oil_c_at_start: 20 + rnd() * 30,
        }];
      }
    }
  }
  trips.reverse(); // newest first
  return {
    vehicle: { display_name: "Porsche 911 Carrera 4S (996.2)", vin: "WP0ZZZ99Z2S600000", model_year: 2003 },
    trips, eventsByTrip,
  };
}
