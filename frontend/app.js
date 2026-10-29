/* AeroPredict — Frontend Application Logic */
// Use the current host for the API (handles both local and cloud deployment)
const API = window.location.origin + "/api";

/* ── Load a library only when it's first needed (keeps the first page load small) ── */
const loaded = {};
function loadScript(src) {
  return loaded[src] ??= new Promise((resolve, reject) => {
    const tag = Object.assign(document.createElement("script"), { src, async: true, onload: resolve, onerror: reject });
    document.head.appendChild(tag);
  });
}
function loadStyle(href) {
  if (!document.querySelector(`link[href="${href}"]`)) {
    document.head.appendChild(Object.assign(document.createElement("link"), { rel: "stylesheet", href }));
  }
}
const CHART_JS = "https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js";
const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min";

/* ── Brand colours for charts (match style.css) ── */
const C = { text: "#C3CCD6", muted: "#8B97A6", grid: "rgba(139,151,166,0.12)",
            good: "#3DB07A", warn: "#E8893A", bad: "#E25555", accent: "#F2C230", info: "#6FA8DC" };

/* ── Smooth scroll for nav links ── */
document.querySelectorAll(".nav-link").forEach(link => {
  link.addEventListener("click", e => {
    const href = link.getAttribute("href");
    if (href && href.startsWith("#")) {
      e.preventDefault();
      document.querySelector(href)?.scrollIntoView({ behavior: "smooth" });
    }
  });
});

/* ── Active nav link on scroll ── */
const sections = document.querySelectorAll("section[id]");
const observer = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      document.querySelectorAll(".nav-link").forEach(l => l.classList.remove("active"));
      document.querySelector(`.nav-link[data-section="${e.target.id}"]`)?.classList.add("active");
    }
  });
}, { rootMargin: "-45% 0px -50% 0px" });  // the section crossing the middle of the screen
sections.forEach(s => observer.observe(s));

/* ── Headline numbers, live from the data ── */
async function loadCoverage() {
  try {
    const stats = await (await fetch(`${API}/analytics/global-stats`)).json();
    const c = stats.coverage || {};
    const set = (id, text) => { if (text) document.getElementById(id).textContent = text; };
    set("statCountries", c.countries && String(c.countries));
    set("statAirports", c.airports && `${Math.floor(c.airports / 100) * 100}+`);
    set("statAirlines", c.airlines && `${Math.floor(c.airlines / 10) * 10}+`);
    set("statFlights", stats.total_flights_analyzed && `${Math.floor(stats.total_flights_analyzed / 1e6)}M+`);
  } catch { /* keep the defaults written in the page */ }
}
loadCoverage();

/* ── Search fields: codes are always upper case ── */
["flightNumber", "depAirport", "arrAirport"].forEach(id => {
  document.getElementById(id).addEventListener("input", function () {
    const pos = this.selectionStart;
    this.value = this.value.toUpperCase();
    this.setSelectionRange(pos, pos);
  });
});

/* ── Swap From and To ── */
document.getElementById("swapAirports").addEventListener("click", () => {
  const dep = document.getElementById("depAirport"), arr = document.getElementById("arrAirport");
  [dep.value, arr.value] = [arr.value, dep.value];
});

/* ── Default date/time ── */
(function setDefaults() {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  document.getElementById("flightDate").value = tomorrow.toISOString().split("T")[0];
  document.getElementById("depTime").value = "09:00";
})();

/* ── Example flights ── */
window.fillDemo = function (flight, dep, arr, time) {
  document.getElementById("flightNumber").value = flight;
  document.getElementById("depAirport").value = dep;
  document.getElementById("arrAirport").value = arr;
  document.getElementById("depTime").value = time;
  document.getElementById("flightForm").requestSubmit();
};

/* ── PREDICTION FORM SUBMIT ── */
document.getElementById("flightForm").addEventListener("submit", async function (e) {
  e.preventDefault();
  const btn = document.getElementById("predictBtn");
  const btnText = document.getElementById("predictBtnText");
  const loader = document.getElementById("btnLoader");
  const errBox = document.getElementById("formError");
  errBox.hidden = true;

  const flightNumber = document.getElementById("flightNumber").value.replace(/\s+/g, "").toUpperCase();
  const payload = {
    flight_number: flightNumber,
    airline:       flightNumber.slice(0, 2),  // the airline code starts every flight number
    dep_airport:   document.getElementById("depAirport").value.trim().toUpperCase(),
    arr_airport:   document.getElementById("arrAirport").value.trim().toUpperCase(),
    date:          document.getElementById("flightDate").value,
    dep_time:      document.getElementById("depTime").value,
  };

  if (!payload.flight_number || !payload.dep_airport || !payload.arr_airport || !payload.date || !payload.dep_time) {
    errBox.textContent = "Please fill in the flight number, both airports, the date and the departure time.";
    errBox.hidden = false;
    return;
  }
  if (!/^[A-Z0-9]{2}\d{1,4}[A-Z]?$/.test(payload.flight_number)) {
    errBox.textContent = "Flight numbers look like EK384 or BA117: the airline's 2-character code, then the number.";
    errBox.hidden = false;
    return;
  }

  btnText.textContent = "Checking…";
  loader.hidden = false;
  btn.disabled = true;

  try {
    const res = await fetch(`${API}/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.error || "Server error");
    renderResult(json.data);
  } catch (err) {
    errBox.textContent = err.message && err.message !== "Failed to fetch"
      ? err.message
      : "We couldn't reach the server. Please try again in a moment.";
    errBox.hidden = false;
  } finally {
    btnText.textContent = "Check my flight";
    loader.hidden = true;
    btn.disabled = false;
  }
});

/* ── Render Result ── */
let lastDeparture = null;
const departsText = departure => {
  const when = new Date(departure.replace(" ", "T"));
  return isNaN(when) ? departure
    : `Departs ${when.toLocaleDateString(window.i18n?.locale, { weekday: "short", day: "numeric", month: "short" })} · ` +
      `${departure.slice(11, 16)} local time`;
};
// Dates are written in the page language, so redo them when it changes
document.addEventListener("languagechange", () => {
  if (lastDeparture) document.getElementById("rTime").textContent = departsText(lastDeparture);
});

function renderResult(data) {
  const { flight_number, dep_airport, arr_airport, departure, probabilities, risk_tier, factors } = data;
  const p = probabilities;
  const $ = id => document.getElementById(id);

  lastDeparture = departure;
  $("rFlightNum").textContent = flight_number;
  $("rDepCode").textContent = dep_airport;
  $("rArrCode").textContent = arr_airport;
  $("rDepName").textContent = data.dep_name || "";
  $("rArrName").textContent = data.arr_name || "";
  $("rRoute").setAttribute("aria-label", `Flight from ${data.dep_name || dep_airport} to ${data.arr_name || arr_airport}`);
  $("rTime").textContent = departsText(departure);

  // Without data for this flight, its route or its airports, don't present a confident risk level
  const generic = data.data_basis && data.data_basis.confidence === "general";
  const badge = $("riskBadge");
  badge.textContent = generic ? "Estimate only" : { LOW: "Low risk", MODERATE: "Moderate risk", MEDIUM: "Medium risk", HIGH: "High risk" }[risk_tier] || `${risk_tier} risk`;
  badge.dataset.tier = generic ? "ESTIMATE" : risk_tier;

  $("pOnTime").textContent = `${Math.round(p.on_time)}%`;
  $("lOnTime").textContent = `${p.on_time}%`;
  $("pDelayed").textContent = `${p.delayed}%`;
  $("pCancelled").textContent = `${p.cancelled}%`;
  $("outcomeBar").setAttribute("aria-label",
    `On time ${p.on_time}%, delayed ${p.delayed}%, cancelled ${p.cancelled}%`);
  for (const [id, v] of [["barOnTime", p.on_time], ["barDelayed", p.delayed], ["barCancelled", p.cancelled]]) {
    $(id).style.width = "0";
    requestAnimationFrame(() => { $(id).style.width = `${v}%`; });
  }

  // Risk factors
  const list = document.getElementById("factorsList");
  list.replaceChildren(...factors.map(f => {
    const item = document.createElement("div");
    item.className = `factor-item ${["high", "medium", "low"].includes(f.level) ? f.level : "medium"}`;
    for (const [cls, text] of [["factor-label", f.label], ["factor-value", f.value]]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      item.appendChild(span);
    }
    return item;
  }));

  const typical = document.getElementById("rTypicalDelay");
  if (typical && data.expected_delay_minutes != null) {
    typical.textContent = `If it's delayed, it's usually about ${data.expected_delay_minutes} minutes late.`;
  }
  const basis = document.getElementById("rBasis");
  if (basis && data.data_basis) {
    const labels = { high: "High confidence", medium: "Medium confidence", airport: "Airport-level estimate",
                     low: "Low confidence", general: "Estimate only" };
    basis.dataset.confidence = data.data_basis.confidence;
    basis.textContent = `${labels[data.data_basis.confidence] || ""}: ${data.data_basis.note}`;
  }

  const rc = document.getElementById("resultCard");
  document.getElementById("predict").hidden = false;
  rc.hidden = false;
  flightRights.show(data);
  rc.scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ── Load Analytics ── */
async function loadAnalytics() {
  try {
    await loadScript(CHART_JS);
    Chart.defaults.font.family = '"IBM Plex Sans", system-ui, sans-serif';
    Chart.defaults.color = C.muted;
    const [statsRes, carrierRes, airportRes] = await Promise.all([
      fetch(`${API}/analytics/global-stats`),
      fetch(`${API}/analytics/carriers`),
      fetch(`${API}/analytics/airports`),
    ]);
    const stats   = await statsRes.json();
    const carriers = (await carrierRes.json()).data;
    const airports = (await airportRes.json()).data;

    // KPIs
    const millions = stats.total_flights_analyzed ? (stats.total_flights_analyzed / 1e6).toFixed(1) + "M" : "–";
    document.getElementById("kpiFlights").textContent = millions;
    document.getElementById("kpiOTP").textContent = `${stats.avg_otp_pct}%`;
    document.getElementById("kpiDelay").textContent = `${stats.avg_delay_pct}%`;
    document.getElementById("kpiAccuracy").textContent = `${stats.avg_cancel_pct}%`;
    const report = document.getElementById("modelReport");
    if (report && stats.test_flights) {
      const months = stats.data_months || [];
      // One sentence per element, so each can be translated as a whole
      const pct = x => Math.round(x * 100);
      const bucket = (stats.calibration || []).find(c => c.predicted === "20-30%");
      const t = stats.international_test;
      const sentences = [
        `Real flights from ${months[0]} to ${months[months.length - 1]} (${stats.data_source || "US Bureau of Transportation Statistics"}).`,
        stats.model_auc_with_forecast
          ? `We tested the model on ${stats.test_flights.toLocaleString()} flights from ${stats.test_months.join(", ")} that it never saw during training: it ranks risky flights above safe ones ${pct(stats.model_auc)}% of the time (50% would be guessing), rising to ${pct(stats.model_auc_with_forecast)}% for flights in the next day, when the airport weather forecast is known.`
          : `We tested the model on ${stats.test_flights.toLocaleString()} flights from ${stats.test_months.join(", ")} that it never saw during training: it ranks risky flights above safe ones ${pct(stats.model_auc)}% of the time (50% would be guessing).`,
        bucket && `Of the test flights it gave a 20–30% chance of disruption, ${bucket.actual_disrupted_pct}% were delayed or cancelled.`,
        t && (t.riskiest_predicted_pct
          ? `Outside the US we tested the same way on ${t.test_flights.toLocaleString()} ${t.country} flights from ${t.test_months}: ${pct(t.auc)}% (up from ${pct(t.auc_before)}% before route history was added), and of the riskiest tenth, predicted at ${t.riskiest_predicted_pct}%, ${t.riskiest_actual_pct}% were delayed or cancelled.`
          : `Outside the US we tested the same way on ${t.test_flights.toLocaleString()} ${t.country} flights from ${t.test_months}: ${pct(t.auc)}% (up from ${pct(t.auc_before)}% before route history was added).`),
        "Delays also depend on things no schedule can show, so treat predictions as odds, not certainties.",
      ].filter(Boolean);
      report.replaceChildren(...sentences.flatMap((text, i) =>
        [i ? " " : "", Object.assign(document.createElement("span"), { textContent: text })].filter(Boolean)));
    }

    // Charts draw text on a canvas, so the page translator can't reach it: label them here
    const tr = s => window.i18n ? window.i18n.t(s) : s;
    const monthNames = () => Array.from({ length: 12 }, (_, i) =>
      new Date(2024, i, 1).toLocaleDateString(window.i18n?.locale, { month: "short" }));
    const charts = [];
    document.addEventListener("languagechange", () => charts.forEach(({ chart, labels, months }) => {
      chart.data.datasets.forEach((d, i) => { d.label = tr(labels[i]); });
      if (months) chart.data.labels = monthNames();
      chart.update();
    }));

    // Carrier chart (top 10)
    const top10 = carriers.slice(0, 10);
    charts.push({ labels: ["On time %", "Delayed %"], chart: new Chart(document.getElementById("carrierChart"), {
      type: "bar",
      data: {
        labels: top10.map(c => c.carrier),
        datasets: [
          { label: tr("On time %"), data: top10.map(c => c.otp), backgroundColor: C.good, borderRadius: 3 },
          { label: tr("Delayed %"), data: top10.map(c => c.delay), backgroundColor: C.warn, borderRadius: 3 },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: C.muted } } },
        scales: {
          x: { ticks: { color: C.muted }, grid: { color: C.grid } },
          y: { ticks: { color: C.muted }, grid: { color: C.grid }, min: 0, max: 100 },
        },
      },
    }) });

    // Airport chart (top 12)
    const top12 = airports.slice(0, 12);
    charts.push({ labels: ["Departures delayed or cancelled %"], chart: new Chart(document.getElementById("airportChart"), {
      type: "bar",
      data: {
        labels: top12.map(a => a.airport),
        datasets: [{
          label: tr("Departures delayed or cancelled %"),
          data: top12.map(a => a.delay_pct),
          backgroundColor: C.warn,
          borderRadius: 3,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        plugins: { legend: { labels: { color: C.muted } } },
        scales: {
          x: { ticks: { color: C.muted }, grid: { color: C.grid }, min: 0 },
          y: { ticks: { color: C.muted }, grid: { color: C.grid } },
        },
      },
    }) });

    // Seasonal chart
    const byMonth = Object.fromEntries((stats.monthly || []).map(m => [m.month, m.delayed_pct + m.cancelled_pct]));
    const seasonal = Array.from({ length: 12 }, (_, i) => byMonth[i + 1] ?? null);
    charts.push({ labels: ["Flights delayed or cancelled %"], months: true, chart: new Chart(document.getElementById("seasonChart"), {
      type: "line",
      data: {
        labels: monthNames(),
        datasets: [{
          label: tr("Flights delayed or cancelled %"),
          data: seasonal,
          borderColor: C.accent,
          backgroundColor: "rgba(242,194,48,0.08)",
          fill: true,
          tension: 0.3,
          pointBackgroundColor: C.accent,
          pointRadius: 3,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: C.muted } } },
        scales: {
          x: { ticks: { color: C.muted }, grid: { color: C.grid } },
          y: { ticks: { color: C.muted }, grid: { color: C.grid }, min: 0 },
        },
      },
    }) });

  } catch (err) {
    console.warn("Analytics load failed (backend may be offline):", err);
    for (const id of ["kpiFlights", "kpiOTP", "kpiDelay", "kpiAccuracy"]) {
      document.getElementById(id).textContent = "–";
    }
  }
}

/* Load analytics when section visible */
const analyticsObserver = new IntersectionObserver(entries => {
  if (entries[0].isIntersecting) {
    loadAnalytics();
    analyticsObserver.disconnect();
  }
}, { threshold: 0.1 });
analyticsObserver.observe(document.getElementById("analytics"));

/* ── Mobile menu ── */
const menuButton = document.getElementById("mobileToggle");
const menu = document.getElementById("navLinks");
menuButton?.addEventListener("click", () => {
  const open = menu.classList.toggle("open");
  menuButton.setAttribute("aria-expanded", String(open));
});
document.querySelectorAll(".nav-link").forEach(link => link.addEventListener("click", () => {
  menu.classList.remove("open");
  menuButton?.setAttribute("aria-expanded", "false");
}));

/* "Check a flight" goes to the search and puts the cursor in it */
document.querySelector(".nav-cta")?.addEventListener("click", e => {
  e.preventDefault();
  document.getElementById("hero").scrollIntoView({ behavior: "smooth" });
  setTimeout(() => document.getElementById("flightNumber").focus({ preventScroll: true }), 400);
});

/* ═══════════════════════════════════════════
   Journey Planner (AI Concierge)
═══════════════════════════════════════════ */
(() => {
  const form = document.getElementById("journeyForm");
  if (!form) return;
  const $ = id => document.getElementById(id);

  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  $("jpDate").value = tomorrow.toISOString().split("T")[0];

  // The plan is written in the page language unless the passenger picks another one
  let langPicked = false;
  $("jpLang").addEventListener("change", () => { langPicked = true; });
  const followPageLang = () => {
    const l = window.i18n?.lang;
    if (!langPicked && l && $("jpLang").querySelector(`option[value="${l}"]`)) $("jpLang").value = l;
  };
  followPageLang();
  document.addEventListener("languagechange", followPageLang);

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("jpError");
    err.hidden = true;

    const payload = {
      flight_number:   $("jpFlight").value.trim(),
      date:            $("jpDate").value,
      dep_airport:     $("jpDep").value.trim().toUpperCase() || undefined,
      arr_airport:     $("jpArr").value.trim().toUpperCase() || undefined,
      dep_time:        $("jpTime").value || undefined,
      starting_point:  $("jpStart").value.trim() || undefined,
      transport_mode:  $("jpTransport").value,
      cabin:           $("jpCabin").value,
      checked_bags:    Number($("jpBags").value),
      loyalty_tier:    $("jpTier").value,
      language:        $("jpLang").value,
      online_check_in: $("jpOnline").checked,
      fast_track:      $("jpFast").checked,
      preferences:     [...$("jpPrefs").querySelectorAll("input:checked")].map(i => i.value),
    };
    if (!payload.flight_number || !payload.date) {
      err.textContent = "Please enter your flight number and date.";
      err.hidden = false;
      return;
    }

    $("jpBtnText").textContent = "Planning your journey…";
    $("jpLoader").hidden = false;
    $("jpBtn").disabled = true;
    try {
      const res = await fetch(`${API}/concierge/journey-plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || !json.success) throw new Error(json.error || `Server error (${res.status})`);
      renderJourney(json.data);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      $("jpBtnText").textContent = "Build my journey plan";
      $("jpLoader").hidden = true;
      $("jpBtn").disabled = false;
    }
  });

  function renderJourney(data) {
    const { flight, plan } = data;
    const dep = flight.departure;

    $("jpTitle").textContent = `${flight.number} · ${dep.airport}${flight.arrival.airport ? " → " + flight.arrival.airport : ""}`;
    $("jpAiBadge").textContent = data.ai_generated ? "Personalised plan" : "Standard plan";
    $("jpSummary").textContent = plan.summary;
    $("jpLeave").textContent = plan.leave_by;
    $("jpArrive").textContent = plan.arrive_airport_by;
    $("jpDeparts").textContent = dep.revised_time || dep.scheduled_time;

    const facts = $("jpFacts");
    facts.replaceChildren();
    const addFact = (label, value) => {
      if (!value) return;
      const f = el("span", "jp-fact");
      f.append(el("span", null, label), ": ", el("strong", null, value));
      facts.appendChild(f);
    };
    addFact("Status", flight.status);
    addFact("Terminal", dep.terminal);
    addFact("Gate", dep.gate);
    addFact("Check-in", dep.check_in_desk);
    addFact("Aircraft", flight.aircraft);
    if (data.delay_prediction) addFact("Delay risk", data.delay_prediction.risk_tier);
    const wx = data.weather?.departure;
    if (wx) addFact("Weather", `${wx.temperature_c ?? "–"}°C · ${wx.flight_category || "n/a"}`);
    if (!flight.live_data) facts.appendChild(el("span", "jp-fact", "Using the times you entered"));

    const warnings = $("jpWarnings");
    warnings.replaceChildren(...plan.warnings.map(w => el("li", null, w)));

    const timeline = $("jpTimeline");
    timeline.replaceChildren(...plan.timeline.map(step => {
      const li = el("li", "jp-step");
      li.dataset.cat = step.category;
      li.appendChild(el("span", "jp-time", step.time));
      li.appendChild(el("span", "jp-dot"));
      const body = el("div");
      const title = el("div", "jp-step-title", step.title);
      if (step.is_estimate) title.appendChild(el("span", "jp-est", "est."));
      body.appendChild(title);
      const meta = [step.location, step.duration_minutes ? `${step.duration_minutes} min` : ""].filter(Boolean).join(" · ");
      if (meta) body.appendChild(el("div", "jp-step-meta", meta));
      if (step.details) body.appendChild(el("div", "jp-step-details", step.details));
      li.appendChild(body);
      return li;
    }));

    $("jpTipsWrap").hidden = !plan.tips.length;
    $("jpTips").replaceChildren(...plan.tips.map(t => el("li", null, t)));

    $("jpSources").replaceChildren(...data.data_sources.map(s => {
      const chip = el("span", "jp-source", s.name);
      chip.dataset.type = s.type;
      return chip;
    }));
    $("jpDisclaimer").textContent = data.disclaimer;

    const nearby = $("jpNearby");
    nearby.hidden = false;
    nearby.onclick = () => window.Wayfinding?.open(dep.airport, dep.gate);

    const card = $("jpResult");
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();

/* ═══════════════════════════════════════════
   Wayfinding — amenities near your gate
═══════════════════════════════════════════ */
window.Wayfinding = (() => {
  const form = document.getElementById("wfForm");
  if (!form) return null;
  const $ = id => document.getElementById(id);
  const LABELS = {
    coffee: "Coffee", dining: "Food", toilets: "Toilets", atm: "ATM & exchange",
    prayer_room: "Prayer room", lounge: "Lounge", pharmacy: "Pharmacy",
    water: "Drinking water", charging: "Charging", shopping: "Shops",
  };
  const COLORS = {
    coffee: "#F59E0B", dining: "#22C55E", toilets: "#0EA5E9", atm: "#8B5CF6",
    prayer_room: "#14B8A6", lounge: "#EC4899", pharmacy: "#EF4444",
    water: "#38BDF8", charging: "#FACC15", shopping: "#A3A3A3",
  };
  let map = null, layer = null;

  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  function placeTitle(p) {
    if (p.name) return p.name;
    return p.category === "toilets" ? "Toilets" : `${LABELS[p.category]} (no name on map)`;
  }

  function placeMeta(p) {
    return [
      p.level != null ? `Level ${p.level}` : "",
      p.cuisine ? p.cuisine.replace(/_/g, " ").replace(/;/g, ", ") : "",
      p.opening_hours ? `Hours: ${p.opening_hours}` : "",
      p.wheelchair === "yes" ? "Wheelchair accessible" : "",
    ].filter(Boolean).join(" · ");
  }

  function popupContent(p) {
    const box = el("div");
    box.appendChild(el("strong", null, placeTitle(p)));
    const meta = placeMeta(p);
    if (meta) box.appendChild(el("div", null, meta));
    if (p.distance_m != null) box.appendChild(el("div", null, `≈ ${p.distance_m} m · ${p.walk_minutes} min walk`));
    return box;
  }

  async function ensureMap() {
    if (map) return map;
    loadStyle(`${LEAFLET}.css`);
    await loadScript(`${LEAFLET}.js`);
    map = L.map($("wfMap"), { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
    return map;
  }

  async function search(airport, gate) {
    const err = $("wfError");
    err.hidden = true;
    const categories = [...$("wfCats").querySelectorAll("input:checked")].map(i => i.value);
    if (!/^[A-Za-z]{3}$/.test(airport)) {
      err.textContent = "Please enter a 3-letter airport code, e.g. IST.";
      err.hidden = false;
      return;
    }
    if (!categories.length) {
      err.textContent = "Choose at least one thing to look for.";
      err.hidden = false;
      return;
    }
    const params = new URLSearchParams({ airport: airport.toUpperCase(), categories: categories.join(",") });
    if (gate) params.set("gate", gate);

    $("wfBtnText").textContent = "Searching…";
    $("wfLoader").hidden = false;
    $("wfBtn").disabled = true;
    try {
      // The first lookup of an airport downloads its map; the server answers 202 until it's ready
      for (let attempt = 0; ; attempt++) {
        const res = await fetch(`${API}/concierge/wayfinding?${params}`);
        const json = await res.json().catch(() => ({}));
        if (res.status === 202 && attempt < 48) {
          $("wfBtnText").textContent = "Loading airport map…";
          $("wfStatus").textContent = json.message || "Loading the airport map…";
          $("wfStatus").hidden = false;
          $("wfResult").hidden = true;
          await new Promise(r => setTimeout(r, (json.retry_after_seconds || 5) * 1000));
          continue;
        }
        if (res.status === 202) throw new Error("The airport map is taking longer than usual. Please try again in a minute.");
        if (!res.ok || !json.success) throw new Error(json.error || `Server error (${res.status})`);
        await render(json.data);
        break;
      }
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      $("wfStatus").hidden = true;
      $("wfBtnText").textContent = "Show nearby";
      $("wfLoader").hidden = true;
      $("wfBtn").disabled = false;
    }
  }

  async function render(data) {
    const name = data.airport.name || data.airport.iata;
    let note;
    if (data.gate_found) note = `Nearest places to Gate ${data.gate.ref} at ${name}.`;
    else if (data.gate_requested) note = `We couldn't find Gate ${data.gate_requested} on the ${name} map, so here is everything we know at the airport.`;
    else note = `Places at ${name}. Add your gate to sort them by walking distance.`;
    if (data.distance_note) note += " " + data.distance_note;
    $("wfNote").textContent = note;
    $("wfAttribution").textContent = data.attribution +
      (data.map_updated ? ` · Map updated ${data.map_updated.slice(0, 10)}` : "");
    $("wfResult").hidden = false;

    const markers = {};
    const bounds = [];
    await ensureMap();
    if (map) {
      layer.clearLayers();
      if (data.gate_found) {
        const icon = L.divIcon({ className: "", html: el("span", "wf-gate-marker", `Gate ${data.gate.ref}`), iconSize: null });
        L.marker([data.gate.lat, data.gate.lon], { icon, zIndexOffset: 1000 }).addTo(layer);
        bounds.push([data.gate.lat, data.gate.lon]);
      }
    }

    const lists = $("wfLists");
    lists.replaceChildren();
    // Show categories in the same order as the filter chips
    for (const cat of Object.keys(LABELS).filter(c => c in data.categories)) {
      const info = data.categories[cat];
      const section = el("div", "wf-cat");
      const heading = el("h4", null, LABELS[cat]);
      heading.appendChild(el("span", "wf-count", ` (${info.count})`));
      section.appendChild(heading);
      if (!info.nearest.length) {
        section.appendChild(el("p", "wf-empty", "Not on the map for this airport yet. Ask airport staff or check the airport signs."));
        lists.appendChild(section);
        continue;
      }
      const ul = el("ul");
      for (const p of info.nearest) {
        if (map) {
          const m = L.circleMarker([p.lat, p.lon], {
            radius: 7, color: "#fff", weight: 2, fillColor: COLORS[cat], fillOpacity: 0.95,
          }).bindPopup(popupContent(p)).addTo(layer);
          markers[p.id] = m;
          bounds.push([p.lat, p.lon]);
        }
        const li = el("li", "wf-item");
        li.tabIndex = 0;
        const left = el("div");
        left.appendChild(el("div", "wf-item-name", placeTitle(p)));
        const meta = placeMeta(p);
        if (meta) left.appendChild(el("div", "wf-item-meta", meta));
        li.appendChild(left);
        if (p.distance_m != null) li.appendChild(el("span", "wf-item-dist", `${p.walk_minutes} min · ${p.distance_m} m`));
        const focus = () => {
          const m = markers[p.id];
          if (!m) return;
          map.setView(m.getLatLng(), Math.max(map.getZoom(), 18));
          m.openPopup();
        };
        li.addEventListener("click", focus);
        li.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); focus(); } });
        ul.appendChild(li);
      }
      section.appendChild(ul);
      lists.appendChild(section);
    }

    if (map) {
      map.invalidateSize();
      if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 18 });
    } else {
      $("wfMap").hidden = true;
    }
  }

  form.addEventListener("submit", e => {
    e.preventDefault();
    search($("wfAirport").value.trim(), $("wfGate").value.trim());
  });

  return {
    open(airport, gate) {
      $("wfAirport").value = airport || "";
      $("wfGate").value = gate || "";
      document.getElementById("wayfinding").scrollIntoView({ behavior: "smooth" });
      if (airport) search(airport, gate);
    },
  };
})();

/* ═══════════════════════════════════════════
   Baggage risk
═══════════════════════════════════════════ */
(() => {
  const form = document.getElementById("bagForm");
  if (!form) return;
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const DURATIONS = [["under_1h", "Under 1 hour"], ["1_2h", "1–2 hours"], ["2_4h", "2–4 hours"], ["over_4h", "Over 4 hours"]];

  const parseRoute = () => $("bagRoute").value.split(/[,\s→>-]+/).map(a => a.trim().toUpperCase()).filter(Boolean);

  // One connection box per stop between the first and last airport
  function renderConnections() {
    const route = parseRoute();
    const box = $("bagConnections");
    const stops = route.slice(1, -1);
    const existing = [...box.querySelectorAll(".bag-conn")].map(c => c.dataset.airport);
    if (stops.join() === existing.join()) return;
    box.replaceChildren(...stops.map((airport, i) => {
      const conn = el("div", "bag-conn");
      conn.dataset.airport = airport;
      conn.appendChild(el("div", "bag-conn-title", `Connection in ${airport}`));
      const sel = el("select", "form-input form-select plain");
      sel.setAttribute("aria-label", `Connection time in ${airport}`);
      for (const [v, label] of DURATIONS) {
        const o = el("option", null, label);
        o.value = v;
        if (v === "1_2h") o.selected = true;
        sel.appendChild(o);
      }
      conn.appendChild(sel);
      const row = el("div", "check-row");
      for (const [key, label] of [["airline_change", "Changing airline"], ["terminal_change", "Changing terminal"]]) {
        const lab = el("label", "check");
        const cb = el("input");
        cb.type = "checkbox";
        cb.dataset.key = key;
        lab.append(cb, " " + label);
        row.appendChild(lab);
      }
      conn.appendChild(row);
      return conn;
    }));
  }
  $("bagRoute").addEventListener("input", renderConnections);

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("bagError");
    err.hidden = true;
    const route = parseRoute();
    const connections = [...$("bagConnections").querySelectorAll(".bag-conn")].map(c => ({
      duration: c.querySelector("select").value,
      airline_change: c.querySelector('[data-key="airline_change"]').checked,
      terminal_change: c.querySelector('[data-key="terminal_change"]').checked,
    }));
    try {
      const res = await fetch(`${API}/concierge/baggage-risk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          route, connections,
          checked_bags: Number($("bagCount").value),
          date: $("bagDate").value || undefined,
        }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || !json.success) throw new Error(json.error || `Server error (${res.status})`);
      render(json.data);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    }
  });

  const fill = (id, items) => $(id).replaceChildren(...items.map(t => el("li", null, t)));

  function render(d) {
    const card = $("bagResult");
    card.dataset.level = d.risk_level;
    $("bagTitle").textContent = d.route.join(" → ");
    $("bagLevel").textContent = { low: "Low risk", medium: "Medium risk", high: "High risk" }[d.risk_level] || `${d.risk_level} risk`;
    $("bagPct").textContent = `${d.risk_percent}%`;
    $("bagOneIn").textContent = d.one_in
      ? `chance a bag is delayed or lost · about 1 in ${d.one_in} trips like yours`
      : "chance a bag is delayed or lost";
    $("bagFactors").replaceChildren(...d.factors.map(f => {
      const li = el("li");
      li.dataset.impact = f.impact;
      li.append(el("strong", null, f.factor), f.detail);
      return li;
    }));
    fill("bagBefore", d.actions.before_you_fly);
    fill("bagDuring", d.actions.during_your_trip);
    fill("bagMissing", d.if_bag_missing);
    $("bagRights").textContent = `${d.compensation.summary} ${d.compensation.note}`;
    fill("bagDeadlines", d.compensation.deadlines);
    $("bagMethod").textContent = d.method;
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();

/* ═══════════════════════════════════════════
   Delay & cancellation rights
═══════════════════════════════════════════ */
(() => {
  const form = document.getElementById("rightsForm");
  if (!form) return;
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const list = (tag, items, cls) => {
    const ul = el(tag, cls);
    ul.append(...items.map(t => el("li", null, t)));
    return ul;
  };

  const syncFields = () => {
    const type = $("rtType").value;
    $("rtNoticeGroup").hidden = type !== "cancellation";
    $("rtDelayGroup").hidden = type === "denied_boarding";
  };
  $("rtType").addEventListener("change", syncFields);
  syncFields();

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const err = $("rtError");
    err.hidden = true;
    const type = $("rtType").value;
    try {
      const res = await fetch(`${API}/concierge/rights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          origin: $("rtFrom").value.trim(),
          destination: $("rtTo").value.trim(),
          airline: $("rtAirline").value.trim(),
          disruption: type,
          cause: $("rtCause").value,
          delay_hours: type === "denied_boarding" ? 0 : Number($("rtDelay").value || 0),
          notice_days: type === "cancellation" ? Number($("rtNotice").value || 0) : undefined,
        }),
      });
      const json = await res.json().catch(() => ({}));
      if (!res.ok || !json.success) throw new Error(json.error || `Server error (${res.status})`);
      render(json.data);
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    }
  });

  function render(d) {
    const card = $("rtResult");
    const h = d.headline;
    card.dataset.eligible = h.eligible ? "yes" : "no";
    $("rtTitle").textContent = `${d.route.origin} → ${d.route.destination}` +
      (d.route.distance_km ? ` · ${d.route.distance_km.toLocaleString()} km` : "");
    $("rtBadge").textContent = d.regimes.map(r => r.code).join(" · ");
    $("rtAmount").textContent = h.eligible ? `${h.currency}${h.amount}` : "No fixed payout";
    $("rtAmountLabel").textContent = h.eligible
      ? "you may be owed in compensation, per passenger"
      : "but you still have rights to care, rebooking or a refund";

    $("rtRegimes").replaceChildren(...d.regimes.map(r => {
      const box = el("div", "rt-regime");
      const title = el("h5", null, r.name);
      title.appendChild(el("span", null, r.compensation.eligible ? `${r.compensation.currency}${r.compensation.amount}` : "No cash payout"));
      box.appendChild(title);
      box.appendChild(el("p", null, r.compensation.explanation));
      box.appendChild(el("div", "rt-sub", "While you wait"));
      box.appendChild(list("ul", r.care));
      box.appendChild(el("div", "rt-sub", "Your choices"));
      box.appendChild(list("ul", r.choices));
      box.appendChild(el("p", null, r.claim));
      return box;
    }));
    $("rtScript").replaceChildren(...d.ask_at_the_desk.map(t => el("li", null, `“${t}”`)));
    $("rtOptions").replaceChildren(...d.rebooking_options.map(o => {
      const li = el("li");
      li.dataset.impact = "low";
      li.append(el("strong", null, o.option), `${o.best_for} ${o.tip}`);
      return li;
    }));
    $("rtEvidence").replaceChildren(...d.keep_evidence.map(t => el("li", null, t)));
    $("rtNote").textContent = d.note;

    const partner = d.claim_partner;
    $("rtClaim").hidden = !partner;
    if (partner) {
      $("rtClaimBtn").href = partner.url;
      $("rtClaimBtn").textContent = `Claim it for me with ${partner.name}`;
      $("rtClaimNote").textContent = partner.disclosure;
    }
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();

/* ═══════════════════════════════════════════
   Rights for the flight just checked
═══════════════════════════════════════════ */
const flightRights = (() => {
  const $ = id => document.getElementById(id);
  let flight = null;          // {flight, airline, origin, destination}
  let type = "delay";

  const rightsFor = async body => {
    const res = await fetch(`${API}/concierge/rights`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ origin: flight.origin, destination: flight.destination, airline: flight.airline, ...body }),
    });
    const json = await res.json().catch(() => ({}));
    if (!res.ok || !json.success) throw new Error(json.error || "We couldn't check your rights just now.");
    return json.data;
  };

  // What the passenger could be owed on this route, before anything has happened
  async function show(data) {
    flight = { flight: data.flight_number, airline: data.airline, origin: data.dep_airport, destination: data.arr_airport };
    const box = $("rbBox");
    $("rbForm").hidden = true;
    $("rbResult").hidden = true;
    $("rbOpen").hidden = false;
    $("rbTitle").textContent = `If ${data.flight_number} is delayed or cancelled`;
    const p = data.probabilities;
    box.classList.toggle("rb-alert", p.delayed + p.cancelled >= 30);
    try {
      const d = await rightsFor({ disruption: "delay", delay_hours: 3, cause: "airline" });
      const h = d.headline;
      const laws = d.regimes.filter(r => r.compensation.eligible).map(r => r.code);
      $("rbSummary").textContent = h.eligible
        ? `On this route, ${laws.join(" / ")} rules could give you up to ${h.currency}${h.amount} per passenger if you arrive 3 or more hours late or your flight is cancelled at short notice, unless it's caused by something outside the airline's control, such as severe weather.`
        : `No law sets a fixed cash payout on this route, but the airline still owes you help if things go wrong.`;
      const main = d.regimes[0];
      $("rbPoints").replaceChildren(...[main.care[0], main.choices[0], main.choices[main.choices.length - 1]]
        .filter((t, i, all) => t && all.indexOf(t) === i)
        .map(t => Object.assign(document.createElement("li"), { textContent: t })));
      box.hidden = false;
    } catch {
      box.hidden = true;  // the prediction itself still shows
    }
  }

  $("rbOpen").addEventListener("click", () => {
    $("rbForm").hidden = false;
    $("rbOpen").hidden = true;
    $("rbForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  document.querySelectorAll(".rb-chip").forEach(chip => chip.addEventListener("click", () => {
    document.querySelectorAll(".rb-chip").forEach(c => c.classList.toggle("active", c === chip));
    type = chip.dataset.type;
    $("rbDelayField").hidden = type === "denied_boarding";
    $("rbNoticeField").hidden = type !== "cancellation";
    $("rbDelayField").querySelector(".search-label").textContent = type === "cancellation"
      ? "How late did you arrive on the new flight? (hours)" : "How late did you arrive? (hours)";
  }));

  $("rbForm").addEventListener("submit", async e => {
    e.preventDefault();
    $("rbError").hidden = true;
    try {
      const d = await rightsFor({
        disruption: type,
        cause: $("rbCause").value,
        delay_hours: type === "denied_boarding" ? 0 : Number($("rbDelay").value || 0),
        notice_days: type === "cancellation" ? Number($("rbNotice").value) : undefined,
      });
      const h = d.headline;
      $("rbAmount").textContent = h.eligible ? `${h.currency}${h.amount}` : "No fixed payout";
      $("rbAmount").dataset.eligible = h.eligible ? "yes" : "no";
      $("rbAmountLabel").textContent = h.eligible
        ? "you're likely owed in compensation, per passenger"
        : h.explanation;
      const partner = d.claim_partner;
      $("rbClaim").hidden = !partner;
      if (partner) {
        $("rbClaimBtn").href = partner.url;
        $("rbClaimBtn").textContent = `Claim it for me with ${partner.name}`;
        $("rbClaimNote").textContent = partner.disclosure;
      }
      $("rbSelf").textContent = h.eligible
        ? `Or claim directly from the airline in writing, for free. Keep your boarding pass and the reason they gave.`
        : `Ask the airline for meal and hotel vouchers, rebooking or a refund, and keep receipts for any costs.`;
      $("rbResult").hidden = false;
      $("rbResult").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (ex) {
      $("rbError").textContent = ex.message;
      $("rbError").hidden = false;
    }
  });

  // Full details: fill the My Rights tool with the same answers and run it
  $("rbMore").addEventListener("click", e => {
    e.preventDefault();
    $("rtFrom").value = flight.origin;
    $("rtTo").value = flight.destination;
    $("rtAirline").value = flight.airline;
    $("rtType").value = type;
    $("rtType").dispatchEvent(new Event("change"));
    $("rtCause").value = $("rbCause").value;
    $("rtDelay").value = $("rbDelay").value;
    $("rtNotice").value = $("rbNotice").value;
    $("rightsForm").requestSubmit();
  });

  return { show };
})();
