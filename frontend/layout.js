/* AeroPredict — shared header and footer for every page */
(function () {
  const onHome = location.pathname === "/" || location.pathname.endsWith("/index.html");
  const home = onHome ? "" : "/";

  const logo = `
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="M16 2L28 8V20L16 30L4 20V8L16 2Z" fill="url(#hexGradL)" />
      <path d="M10 16L14 12L20 16L14 20L10 16Z" fill="white" opacity="0.9"/>
      <path d="M14 12L22 14L20 16L14 12Z" fill="white" opacity="0.6"/>
      <defs>
        <linearGradient id="hexGradL" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stop-color="#0EA5E9"/><stop offset="100%" stop-color="#6366F1"/>
        </linearGradient>
      </defs>
    </svg>`;

  // Pages other than the homepage get the same top menu, pointing back to the homepage tools
  const nav = document.getElementById("siteNav");
  if (nav) {
    const here = location.pathname.replace(/\.html$/, "");
    const link = (href, text) =>
      `<li><a href="${href}" class="nav-link${here === href ? " active" : ""}">${text}</a></li>`;
    nav.className = "navbar";
    nav.innerHTML = `
      <a class="nav-brand" href="/">${logo}<span class="nav-title">AeroPredict<span class="nav-badge">AI</span></span></a>
      <ul class="nav-links">
        ${link("/#hero", "Check a Flight")}
        ${link("/#journey", "Journey Plan")}
        ${link("/#wayfinding", "Near My Gate")}
        ${link("/#baggage", "Bag Check")}
        ${link("/#rights", "My Rights")}
        ${link("/#analytics", "Analytics")}
        ${link("/how-it-works", "How It Works")}
      </ul>
      <button class="nav-mobile-toggle" aria-label="Toggle menu">☰</button>`;
    nav.querySelector(".nav-mobile-toggle").addEventListener("click", () =>
      nav.querySelector(".nav-links").classList.toggle("open"));
  }

  const footer = document.getElementById("siteFooter");
  if (footer) {
    footer.innerHTML = `
      <div class="container">
        <div class="footer-grid">
          <div class="footer-brand">
            <a href="/" class="nav-title">AeroPredict<span class="nav-badge">AI</span></a>
            <p>Free flight delay odds and airport help for passengers, built on real flight records from official sources.</p>
          </div>
          <div class="footer-col">
            <h5>Tools</h5>
            <ul>
              <li><a href="${home}#hero">Check a flight</a></li>
              <li><a href="${home}#journey">Journey plan</a></li>
              <li><a href="${home}#wayfinding">Near my gate</a></li>
              <li><a href="${home}#baggage">Bag check</a></li>
              <li><a href="${home}#rights">Delay &amp; cancellation rights</a></li>
              <li><a href="${home}#analytics">Analytics</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h5>AeroPredict</h5>
            <ul>
              <li><a href="/how-it-works">How it works</a></li>
              <li><a href="/faq">FAQ</a></li>
              <li><a href="/contact">Contact</a></li>
            </ul>
          </div>
          <div class="footer-col">
            <h5>Legal</h5>
            <ul>
              <li><a href="/privacy">Privacy policy</a></li>
              <li><a href="/terms">Terms of use</a></li>
            </ul>
          </div>
        </div>
        <p class="footer-sources">
          Data: US Bureau of Transportation Statistics · UK Civil Aviation Authority · ANAC Brazil · BITRE Australia ·
          Airportal Korea · Airport Authority Hong Kong · MLIT Japan · Israel Airports Authority ·
          Airports Company South Africa · Taoyuan International Airport · Nigerian Civil Aviation Authority ·
          Flight data from <a href="https://www.avinor.no" target="_blank" rel="noopener">Avinor</a> ·
          © EUROCONTROL · aviationweather.gov ·
          map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors
        </p>
        <div class="footer-bottom">
          <p>© ${new Date().getFullYear()} AeroPredict</p>
          <p class="footer-disclaimer">Predictions are estimates based on past flights and forecasts, not guarantees. Always check with your airline.
            Some links to claim services are affiliate links: we may earn a commission at no cost to you.</p>
        </div>
      </div>`;
  }
})();
