/* AeroPredict — shared header and footer for every page */
(function () {
  const onHome = location.pathname === "/" || location.pathname.endsWith("/index.html");
  const home = onHome ? "" : "/";

  const mark = `
    <span class="brand-mark"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.5 13.5 3 11l1.5-1.5 8 1L17 6c1-1 2.7-1.2 3.3-.6s.4 2.3-.6 3.3l-4.5 4.5 1 8L14.7 22l-2.5-7.5-4 4 .3 2.5-1.5 1.5-1.5-3.5L2 17.5 3.5 16l2.5.3 4-4"/></svg></span>`;

  // Pages other than the homepage get the same top menu, pointing back to the homepage tools
  const nav = document.getElementById("siteNav");
  if (nav) {
    const here = location.pathname.replace(/\.html$/, "");
    const link = (href, text) =>
      `<li><a href="${href}" class="nav-link${here === href ? " active" : ""}">${text}</a></li>`;
    nav.className = "navbar";
    nav.innerHTML = `
      <a class="brand" href="/" aria-label="AeroPredict home">${mark}<span class="brand-name">AeroPredict</span></a>
      <ul class="nav-links" id="navLinks">
        <li class="menu-only"><a href="/#hero" class="nav-link menu-cta">Check a flight</a></li>
        ${link("/#journey", "Journey plan")}
        ${link("/#wayfinding", "Near my gate")}
        ${link("/#baggage", "Bag check")}
        ${link("/#rights", "My rights")}
        ${link("/#analytics", "Analytics")}
        ${link("/how-it-works", "How it works")}
      </ul>
      <a href="/#hero" class="btn btn-primary btn-sm nav-cta">Check a flight</a>
      <button class="nav-toggle" aria-label="Open menu" aria-expanded="false" aria-controls="navLinks">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75"
          stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16"/></svg>
      </button>`;
    const toggle = nav.querySelector(".nav-toggle");
    toggle.addEventListener("click", () => {
      const open = nav.querySelector(".nav-links").classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
    });
  }

  const footer = document.getElementById("siteFooter");
  if (footer) {
    footer.innerHTML = `
      <div class="container">
        <div class="footer-grid">
          <div class="footer-brand">
            <a href="/" class="brand">${mark}<span class="brand-name">AeroPredict</span></a>
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
