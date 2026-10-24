/* AeroPredict — Frontend Application Logic */
// Use the current host for the API (handles both local and cloud deployment)
const API = window.location.origin + "/api";
let resultChart = null;

/* ── Airport name lookup ── */
const AIRPORTS = {
  ORD:"Chicago O'Hare",ATL:"Atlanta Hartsfield",LAX:"Los Angeles Intl",
  DFW:"Dallas/Fort Worth",JFK:"New York JFK",EWR:"Newark Liberty",
  SFO:"San Francisco Intl",BOS:"Boston Logan",LGA:"New York LaGuardia",
  MIA:"Miami Intl",SEA:"Seattle-Tacoma",DEN:"Denver Intl",
  MCO:"Orlando Intl",LAS:"Las Vegas Harry Reid",PHX:"Phoenix Sky Harbor",
  BAH:"Bahrain Intl",DXB:"Dubai Intl",DOH:"Hamad Intl Doha",
  AUH:"Abu Dhabi Intl",RUH:"King Khalid Riyadh",
  CDG:"Paris Charles de Gaulle",LHR:"London Heathrow",
  FRA:"Frankfurt Intl",AMS:"Amsterdam Schiphol",IST:"Istanbul Intl",
};

/* ── Navbar scroll effect ── */
window.addEventListener("scroll", () => {
  document.getElementById("navbar").style.background =
    window.scrollY > 50 ? "rgba(5,11,24,0.97)" : "rgba(5,11,24,0.85)";
});

/* ── Smooth scroll for nav links ── */
document.querySelectorAll(".nav-link, #heroCTA").forEach(link => {
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
}, { threshold: 0.4 });
sections.forEach(s => observer.observe(s));

/* ── Counter animation ── */
function animateCounter(el) {
  const target = parseFloat(el.dataset.target);
  const duration = 2000;
  const start = performance.now();
  const step = (now) => {
    const progress = Math.min((now - start) / duration, 1);
    const ease = 1 - Math.pow(1 - progress, 3);
    el.textContent = target % 1 === 0 ? Math.floor(ease * target) : (ease * target).toFixed(1);
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const statsObserver = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      e.target.querySelectorAll(".stat-value").forEach(animateCounter);
      statsObserver.unobserve(e.target);
    }
  });
}, { threshold: 0.5 });
statsObserver.observe(document.querySelector(".hero-stats"));

/* ── Particle Canvas ── */
(function initParticles() {
  const canvas = document.getElementById("particleCanvas");
  const ctx = canvas.getContext("2d");
  let W, H, particles = [];

  function resize() {
    W = canvas.width = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener("resize", resize);

  for (let i = 0; i < 80; i++) {
    particles.push({
      x: Math.random() * 2000, y: Math.random() * 1000,
      vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 2 + 0.5,
      alpha: Math.random() * 0.5 + 0.1,
    });
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);
    particles.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
      if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(14,165,233,${p.alpha})`;
      ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  draw();
})();

/* ── Airport name hints ── */
function setupAirportHint(inputId, hintId) {
  document.getElementById(inputId).addEventListener("input", function () {
    const code = this.value.toUpperCase();
    document.getElementById(hintId).textContent = AIRPORTS[code] || "";
  });
}
setupAirportHint("depAirport", "depAirportName");
setupAirportHint("arrAirport", "arrAirportName");

/* ── Default date/time ── */
(function setDefaults() {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  document.getElementById("flightDate").value = tomorrow.toISOString().split("T")[0];
  document.getElementById("depTime").value = "09:00";
})();

/* ── Demo fill ── */
window.fillDemo = function (airline, dep, arr, date, time) {
  document.getElementById("airline").value = airline;
  document.getElementById("depAirport").value = dep;
  document.getElementById("arrAirport").value = arr;
  document.getElementById("flightDate").value = date;
  document.getElementById("depTime").value = time;
  document.getElementById("depAirportName").textContent = AIRPORTS[dep] || "";
  document.getElementById("arrAirportName").textContent = AIRPORTS[arr] || "";
  const flightNum = airline + Math.floor(1000 + Math.random() * 8999);
  document.getElementById("flightNumber").value = flightNum;
  document.getElementById("predict").scrollIntoView({ behavior: "smooth" });
};

/* ── PREDICTION FORM SUBMIT ── */
document.getElementById("flightForm").addEventListener("submit", async function (e) {
  e.preventDefault();
  const btn = document.getElementById("predictBtn");
  const btnText = document.getElementById("predictBtnText");
  const loader = document.getElementById("btnLoader");
  const errBox = document.getElementById("formError");
  errBox.hidden = true;

  const payload = {
    flight_number: document.getElementById("flightNumber").value.trim(),
    airline:       document.getElementById("airline").value.trim(),
    dep_airport:   document.getElementById("depAirport").value.trim().toUpperCase(),
    arr_airport:   document.getElementById("arrAirport").value.trim().toUpperCase(),
    date:          document.getElementById("flightDate").value,
    dep_time:      document.getElementById("depTime").value,
  };

  if (!payload.flight_number || !payload.airline || !payload.dep_airport ||
      !payload.arr_airport || !payload.date || !payload.dep_time) {
    errBox.textContent = "Please fill in all fields before running analysis.";
    errBox.hidden = false;
    return;
  }

  btnText.textContent = "Analyzing…";
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
    errBox.textContent = `Prediction failed: ${err.message}. Make sure the Python backend is running on port 5000.`;
    errBox.hidden = false;
  } finally {
    btnText.textContent = "Analyze Flight Risk";
    loader.hidden = true;
    btn.disabled = false;
  }
});

/* ── Render Result ── */
function renderResult(data) {
  const { flight_number, dep_airport, arr_airport, departure, probabilities, risk_tier, risk_color, factors } = data;
  const p = probabilities;

  document.getElementById("rFlightNum").textContent = flight_number;
  document.getElementById("rRoute").textContent = `${dep_airport} → ${arr_airport}`;
  document.getElementById("rTime").textContent = departure;

  const badge = document.getElementById("riskBadge");
  badge.textContent = `${risk_tier} RISK`;
  badge.style.background = risk_color + "22";
  badge.style.color = risk_color;
  badge.style.border = `1px solid ${risk_color}44`;

  document.getElementById("pOnTime").textContent = `${p.on_time}%`;
  document.getElementById("pDelayed").textContent = `${p.delayed}%`;
  document.getElementById("pCancelled").textContent = `${p.cancelled}%`;

  setTimeout(() => {
    document.getElementById("barOnTime").style.width = `${p.on_time}%`;
    document.getElementById("barDelayed").style.width = `${p.delayed}%`;
    document.getElementById("barCancelled").style.width = `${p.cancelled}%`;
  }, 100);

  // Doughnut chart
  if (resultChart) resultChart.destroy();
  resultChart = new Chart(document.getElementById("resultChart"), {
    type: "doughnut",
    data: {
      labels: ["On-Time", "Delayed", "Cancelled"],
      datasets: [{
        data: [p.on_time, p.delayed, p.cancelled],
        backgroundColor: ["#22C55E", "#F59E0B", "#EF4444"],
        borderWidth: 0,
        hoverOffset: 6,
      }],
    },
    options: {
      cutout: "72%",
      plugins: {
        legend: { labels: { color: "#7A8FA6", font: { family: "Inter", size: 12 } } },
        tooltip: {
          callbacks: { label: ctx => ` ${ctx.label}: ${ctx.raw}%` },
        },
      },
    },
  });

  // Risk factors
  const list = document.getElementById("factorsList");
  list.replaceChildren(...factors.map(f => {
    const item = document.createElement("div");
    item.className = `factor-item ${["high", "medium", "low"].includes(f.level) ? f.level : "medium"}`;
    for (const [cls, text] of [["factor-icon", f.icon], ["factor-label", f.label], ["factor-value", f.value]]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      item.appendChild(span);
    }
    return item;
  }));

  const typical = document.getElementById("rTypicalDelay");
  if (typical && data.expected_delay_minutes != null) {
    typical.textContent = `If delayed: usually about ${data.expected_delay_minutes} min late (${data.delay_severity}).`;
  }
  const basis = document.getElementById("rBasis");
  if (basis && data.data_basis) {
    const labels = { high: "High confidence", medium: "Medium confidence", low: "Low confidence", general: "General estimate" };
    basis.dataset.confidence = data.data_basis.confidence;
    basis.textContent = `${labels[data.data_basis.confidence] || ""}: ${data.data_basis.note}`;
  }

  const rc = document.getElementById("resultCard");
  rc.hidden = false;
  rc.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ── API Status Check ── */
async function checkApiStatus() {
  const dot = document.getElementById("apiStatus");
  const txt = document.getElementById("apiStatusText");
  try {
    const res = await fetch(`${API}/model/status`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      dot.className = "status-dot online";
      txt.textContent = "AI Engine Online";
    } else { throw new Error(); }
  } catch {
    dot.className = "status-dot offline";
    txt.textContent = "Backend Offline";
  }
}
checkApiStatus();

/* ── Load Analytics ── */
async function loadAnalytics() {
  try {
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
      report.textContent =
        `Real US flights from ${months[0]} to ${months[months.length - 1]} (US Bureau of Transportation Statistics). ` +
        `We tested the model on ${stats.test_flights.toLocaleString()} flights from ${stats.test_months.join(" and ")} ` +
        `that it never saw during training: it ranks risky flights above safe ones ${Math.round(stats.model_auc * 100)}% of the time ` +
        `(50% would be guessing). Delays depend on things no schedule can show, like a storm next week, ` +
        `so treat predictions as odds, not certainties.`;
    }

    // Carrier chart (top 10)
    const top10 = carriers.slice(0, 10);
    new Chart(document.getElementById("carrierChart"), {
      type: "bar",
      data: {
        labels: top10.map(c => c.carrier),
        datasets: [
          { label: "On-Time %", data: top10.map(c => c.otp), backgroundColor: "#0EA5E9", borderRadius: 6 },
          { label: "Delay %",   data: top10.map(c => c.delay), backgroundColor: "#F59E0B", borderRadius: 6 },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: "#7A8FA6" } } },
        scales: {
          x: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" }, min: 0, max: 100 },
        },
      },
    });

    // Airport chart (top 12)
    const top12 = airports.slice(0, 12);
    new Chart(document.getElementById("airportChart"), {
      type: "bar",
      data: {
        labels: top12.map(a => a.airport),
        datasets: [{
          label: "Departures delayed or cancelled %",
          data: top12.map(a => a.delay_pct),
          backgroundColor: top12.map(a => a.delay_pct > 25 ? "#EF4444" : a.delay_pct > 20 ? "#F59E0B" : "#22C55E"),
          borderRadius: 6,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        plugins: { legend: { labels: { color: "#7A8FA6" } } },
        scales: {
          x: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" }, min: 0 },
          y: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" } },
        },
      },
    });

    // Seasonal chart
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const byMonth = Object.fromEntries((stats.monthly || []).map(m => [m.month, m.delayed_pct + m.cancelled_pct]));
    const seasonal = months.map((_, i) => byMonth[i + 1] ?? null);
    new Chart(document.getElementById("seasonChart"), {
      type: "line",
      data: {
        labels: months,
        datasets: [{
          label: "Flights delayed or cancelled %",
          data: seasonal,
          borderColor: "#6366F1",
          backgroundColor: "rgba(99,102,241,0.1)",
          fill: true,
          tension: 0.4,
          pointBackgroundColor: "#6366F1",
          pointRadius: 5,
        }],
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: "#7A8FA6" } } },
        scales: {
          x: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" } },
          y: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" }, min: 0 },
        },
      },
    });

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
document.getElementById("mobileToggle")?.addEventListener("click", () => {
  document.querySelector(".nav-links").classList.toggle("open");
});
document.querySelectorAll(".nav-link").forEach(link =>
  link.addEventListener("click", () => document.querySelector(".nav-links").classList.remove("open"))
);

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
      $("jpBtnText").textContent = "Build My Journey Plan";
      $("jpLoader").hidden = true;
      $("jpBtn").disabled = false;
    }
  });

  function renderJourney(data) {
    const { flight, plan } = data;
    const dep = flight.departure;

    $("jpTitle").textContent = `${flight.number} · ${dep.airport}${flight.arrival.airport ? " → " + flight.arrival.airport : ""}`;
    $("jpAiBadge").textContent = data.ai_generated ? "AI Personalized" : "Standard Plan";
    $("jpSummary").textContent = plan.summary;
    $("jpLeave").textContent = plan.leave_by;
    $("jpArrive").textContent = plan.arrive_airport_by;
    $("jpDeparts").textContent = dep.revised_time || dep.scheduled_time;

    const facts = $("jpFacts");
    facts.replaceChildren();
    const addFact = (label, value) => {
      if (!value) return;
      const f = el("span", "jp-fact", label + ": ");
      f.appendChild(el("strong", null, value));
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

  function ensureMap() {
    if (map || typeof L === "undefined") return map;
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
        render(json.data);
        break;
      }
    } catch (ex) {
      err.textContent = ex.message;
      err.hidden = false;
    } finally {
      $("wfStatus").hidden = true;
      $("wfBtnText").textContent = "Show Nearby";
      $("wfLoader").hidden = true;
      $("wfBtn").disabled = false;
    }
  }

  function render(data) {
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
    ensureMap();
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
      section.appendChild(el("h4", null, `${LABELS[cat]} (${info.count})`));
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
    $("bagLevel").textContent = `${d.risk_level[0].toUpperCase()}${d.risk_level.slice(1)} risk`;
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
