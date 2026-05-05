/* =============================================================
 * Transit Load Viewer — frontend
 *
 * Filter chain (each one re-populates the next):
 *   Signup → Route → Direction → Pattern
 *
 * Pattern dropdown is disabled when Direction is "All directions",
 * because patterns are direction-specific.
 * ============================================================= */

const els = {
  signup:    document.getElementById("signup-select"),
  route:     document.getElementById("route-select"),
  direction: document.getElementById("direction-select"),
  pattern:   document.getElementById("pattern-select"),
  status:    document.getElementById("status"),
  legend:    document.getElementById("legend"),
  subtitle:  document.getElementById("chart-subtitle"),
};

let map;
let segmentsLayer;
let stopsLayer;
let chart;
let meta;

// ------------------------------------------------------------------
// Bootstrap
// ------------------------------------------------------------------

async function init() {
  setStatus("Loading…");
  try {
    meta = await fetchJSON("/api/meta");
  } catch (err) {
    return setStatus("Error: " + err.message, true);
  }

  populateSignups();
  populateRoutes();
  populateDirections();
  populatePatterns();
  renderLegend();
  initMap();
  initChart();

  // Wire filter cascade. Each change repopulates downstream dropdowns.
  els.signup.addEventListener("change", () => {
    populatePatterns();   // pattern letters depend on signup
    refresh();
  });
  els.route.addEventListener("change", () => {
    populateDirections();
    populatePatterns();
    refresh();
  });
  els.direction.addEventListener("change", () => {
    populatePatterns();
    refresh();
  });
  els.pattern.addEventListener("change", refresh);

  await refresh();
}

// ------------------------------------------------------------------
// Dropdowns
// ------------------------------------------------------------------

function populateSignups() {
  els.signup.innerHTML = meta.signups
    .map(s => `<option value="${s}">${s}</option>`).join("");
  els.signup.value = meta.signups[meta.signups.length - 1];  // newest by default
}

function populateRoutes() {
  els.route.innerHTML = meta.routes
    .map(r => `<option value="${r}">Route ${r}</option>`).join("");
  els.route.value = meta.routes[0];
}

function populateDirections() {
  const route = els.route.value;
  const dirs = (meta.directions[route] || []);
  const opts = [
    `<option value="">All directions</option>`,
    ...dirs.map(d =>
      `<option value="${d.direction_id}">${d.label} (dir ${d.direction_id})</option>`
    ),
  ];
  els.direction.innerHTML = opts.join("");
  // Default to the first specific direction (not "All") so pattern can populate
  els.direction.value = dirs.length ? dirs[0].direction_id : "";
}

function populatePatterns() {
  const signup = els.signup.value;
  const route = els.route.value;
  const direction = els.direction.value;

  // Patterns are direction-specific. Disable when "All directions" selected.
  if (direction === "") {
    els.pattern.innerHTML = `<option value="">All patterns</option>`;
    els.pattern.value = "";
    els.pattern.disabled = true;
    return;
  }

  els.pattern.disabled = false;
  const patterns = (meta.patterns?.[signup]?.[route]?.[direction]) || [];

  if (patterns.length === 0) {
    els.pattern.innerHTML = `<option value="">(no patterns)</option>`;
    els.pattern.value = "";
    return;
  }

  els.pattern.innerHTML = patterns
    .map(p => `<option value="${p.id}">${p.label}</option>`).join("");
  // Dominant (first, since they're sorted by trip count desc) selected by default
  els.pattern.value = patterns[0].id;
}

// ------------------------------------------------------------------
// Legend
// ------------------------------------------------------------------

function renderLegend() {
  const items = meta.color_legend.map(b =>
    `<li><span class="swatch" style="background:${b.color}"></span>${b.label}</li>`
  ).join("");
  els.legend.innerHTML = `<h3>Avg Load</h3><ul>${items}</ul>`;
}

// ------------------------------------------------------------------
// Map
// ------------------------------------------------------------------

function initMap() {
  map = L.map("map", {
    center: meta.map_center,
    zoom: 12,
    zoomControl: true,
  });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap contributors",
  }).addTo(map);
}

function styleSegment(feature) {
  return {
    color: feature.properties.color,
    weight: 6,
    opacity: 0.9,
    lineCap: "round",
  };
}

function onEachSegment(feature, layer) {
  const p = feature.properties;
  layer.bindPopup(`
    <div class="segment-popup">
      <div><span class="label">Route</span> <span class="value">${p.route}</span></div>
      <div><span class="label">Direction</span> <span class="value">${p.direction_label}</span></div>
      <div><span class="label">Pattern</span> <span class="value">${p.pattern_label || p.pattern}</span></div>
      <div><span class="label">From → To</span> <span class="value">${p.from_stop} → ${p.to_stop}</span></div>
      <div><span class="label">Signup</span> <span class="value">${p.signup}</span></div>
      <div><span class="label">Avg Load</span> <span class="value">${p.avg_load}</span></div>
    </div>
  `);
}

function styleStop() {
  return {
    radius: 4,
    fillColor: "#ffffff",
    color: "#1a1d22",
    weight: 2,
    opacity: 1,
    fillOpacity: 1,
  };
}

function onEachStop(feature, layer) {
  const p = feature.properties;
  layer.bindTooltip(`${p.stop_sequence}. ${p.stop_name || p.stop_id}`, {
    direction: "top",
    offset: [0, -6],
  });
}

async function reloadMap() {
  const params = mapParams();

  const [segments, stops] = await Promise.all([
    fetchJSON(`/api/segments?${params}`),
    fetchJSON(`/api/stops?${params}`),
  ]);

  if (segmentsLayer) map.removeLayer(segmentsLayer);
  if (stopsLayer)    map.removeLayer(stopsLayer);

  segmentsLayer = L.geoJSON(segments, {
    style: styleSegment,
    onEachFeature: onEachSegment,
  }).addTo(map);

  stopsLayer = L.geoJSON(stops, {
    pointToLayer: (feat, latlng) => L.circleMarker(latlng, styleStop()),
    onEachFeature: onEachStop,
  }).addTo(map);

  const bounds = segmentsLayer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
}

// ------------------------------------------------------------------
// Trend chart
// ------------------------------------------------------------------

function initChart() {
  const ctx = document.getElementById("trend-chart").getContext("2d");
  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [{
        label: "Avg load",
        data: [],
        borderColor: "#1f77b4",
        backgroundColor: "rgba(31,119,180,0.10)",
        borderWidth: 2.5,
        pointRadius: 5,
        pointHoverRadius: 7,
        pointBackgroundColor: "#1f77b4",
        tension: 0.25,
        fill: true,
        spanGaps: false,  // honest gaps when a pattern doesn't exist in some signup
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (ctx) => ctx.parsed.y === null
              ? "(pattern not in this signup)"
              : `Avg load: ${ctx.parsed.y}`,
          },
        },
      },
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: "Avg passenger load" },
          grid: { color: "rgba(0,0,0,0.06)" },
        },
        x: {
          title: { display: true, text: "Signup" },
          grid: { display: false },
        },
      },
    },
  });
}

async function reloadChart() {
  const params = trendParams();
  const data = await fetchJSON(`/api/trend?${params}`);

  chart.data.labels = data.labels;
  chart.data.datasets[0].data = data.values;

  // Highlight the currently selected signup
  const selectedIdx = data.labels.indexOf(els.signup.value);
  chart.data.datasets[0].pointRadius = data.labels.map((_, i) => i === selectedIdx ? 8 : 5);
  chart.data.datasets[0].pointBackgroundColor = data.labels.map(
    (_, i) => i === selectedIdx ? "#d62728" : "#1f77b4"
  );

  chart.update();

  // Subtitle reflects what the chart line actually represents
  const dirText = els.direction.value === ""
    ? "all directions"
    : `direction ${els.direction.value}`;
  const patText = els.pattern.value === ""
    ? "all patterns"
    : `Pattern ${els.pattern.value}`;
  els.subtitle.textContent = `Route ${els.route.value} — ${dirText} — ${patText}`;
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

function mapParams() {
  // signup + route + direction + pattern, all only if set
  const p = new URLSearchParams();
  p.set("signup", els.signup.value);
  p.set("route", els.route.value);
  if (els.direction.value !== "") p.set("direction", els.direction.value);
  if (els.pattern.value   !== "") p.set("pattern", els.pattern.value);
  return p.toString();
}

function trendParams() {
  // route + direction + pattern (no signup — chart is always cross-signup)
  const p = new URLSearchParams();
  p.set("route", els.route.value);
  if (els.direction.value !== "") p.set("direction", els.direction.value);
  if (els.pattern.value   !== "") p.set("pattern", els.pattern.value);
  return p.toString();
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({error: res.statusText}));
    throw new Error(body.error || `HTTP ${res.status}`);
  }
  return res.json();
}

async function refresh() {
  setStatus("Refreshing…");
  try {
    await Promise.all([reloadMap(), reloadChart()]);
    setStatus("");
  } catch (err) {
    setStatus("Error: " + err.message, true);
  }
}

function setStatus(msg, isError = false) {
  els.status.textContent = msg;
  els.status.classList.toggle("error", isError);
}

init();
