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
  list.innerHTML = factors.map(f => `
    <div class="factor-item ${f.level}">
      <span class="factor-icon">${f.icon}</span>
      <span class="factor-label">${f.label}</span>
      <span class="factor-value">${f.value}</span>
    </div>`).join("");

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
    document.getElementById("kpiFlights").textContent = "120K+";
    document.getElementById("kpiOTP").textContent = `${stats.avg_otp_pct}%`;
    document.getElementById("kpiDelay").textContent = `${stats.avg_delay_pct}%`;
    document.getElementById("kpiAccuracy").textContent = `${stats.model_accuracy_pct}%`;

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
          label: "Congestion Index %",
          data: top12.map(a => a.congestion),
          backgroundColor: top12.map(a => a.congestion > 85 ? "#EF4444" : a.congestion > 70 ? "#F59E0B" : "#22C55E"),
          borderRadius: 6,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        plugins: { legend: { labels: { color: "#7A8FA6" } } },
        scales: {
          x: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" }, min: 0, max: 100 },
          y: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" } },
        },
      },
    });

    // Seasonal chart
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const seasonal = [55,50,45,30,25,25,20,20,30,40,50,60];
    new Chart(document.getElementById("seasonChart"), {
      type: "line",
      data: {
        labels: months,
        datasets: [{
          label: "NAS Delay Risk Index",
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
          y: { ticks: { color: "#7A8FA6" }, grid: { color: "rgba(255,255,255,0.04)" }, min: 0, max: 80 },
        },
      },
    });

  } catch (err) {
    console.warn("Analytics load failed (backend may be offline):", err);
    // Show fallback static data for KPIs
    document.getElementById("kpiFlights").textContent = "120K+";
    document.getElementById("kpiOTP").textContent = "80.4%";
    document.getElementById("kpiDelay").textContent = "18.2%";
    document.getElementById("kpiAccuracy").textContent = "84.7%";
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

    const card = $("jpResult");
    card.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();
