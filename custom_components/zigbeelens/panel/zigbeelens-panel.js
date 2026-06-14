/**
 * ZigbeeLens companion panel (native Home Assistant custom panel).
 *
 * Default view: redacted status summary over the HA websocket (no direct Core fetch).
 * No iframe on load — the sidebar is a native companion panel, not an embedded Core UI.
 *
 * Primary action: Open Full Dashboard (new tab — always reliable).
 * Secondary action: Try Embedded View (manual optional iframe).
 */

const SEVERITY = {
  ok: { label: "Healthy", color: "var(--success-color, #2e7d32)" },
  watch: { label: "Watch", color: "var(--warning-color, #f9a825)" },
  incident: { label: "Incident", color: "var(--error-color, #c62828)" },
  unknown: { label: "No signal", color: "var(--secondary-text-color, #888)" },
};

/** @returns {{ canEmbed: boolean, reason?: string }} */
function canEmbedDashboard(haProtocol, coreUrl, baseHref) {
  try {
    const ha = String(haProtocol || "").toLowerCase();
    if (!ha.endsWith(":")) {
      return { canEmbed: false, reason: "invalid_ha" };
    }
    if (!coreUrl || !String(coreUrl).trim()) {
      return { canEmbed: false, reason: "missing_core_url" };
    }
    const core = new URL(String(coreUrl).trim(), baseHref || window.location.href);
    if (!core.protocol || !core.host) {
      return { canEmbed: false, reason: "invalid_core_url" };
    }
    const isMixedContentIframe = ha === "https:" && core.protocol === "http:";
    return { canEmbed: !isMixedContentIframe };
  } catch {
    return { canEmbed: false, reason: "invalid_core_url" };
  }
}

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function relativeTime(iso) {
  if (!iso) return "unknown";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function bridgeLabel(state) {
  const text = String(state || "unknown").toLowerCase();
  if (text === "online") return "bridge online";
  if (text === "offline") return "bridge offline";
  return "bridge state unknown";
}

class ZigbeeLensPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._summary = null;
    this._loading = true;
    this._loaded = false;
    this._copied = false;
    this._configCoreUrl = "";
    this._view = "summary";
    this._narrow = false;
  }

  set panel(panel) {
    this._configCoreUrl = (panel && panel.config && panel.config.core_url) || "";
  }

  set narrow(n) {
    this._narrow = Boolean(n);
    this.classList.toggle("narrow", this._narrow);
    if (this.shadowRoot && this.shadowRoot.childNodes.length) {
      this._render();
    }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && !this._loaded) {
      this._loadSummary();
    }
  }

  connectedCallback() {
    this._render();
  }

  async _loadSummary() {
    this._loading = true;
    this._render();
    try {
      this._summary = await this._hass.callWS({ type: "zigbeelens/panel_summary" });
    } catch (err) {
      this._summary = {
        connected: false,
        core_url: this._configCoreUrl,
        error: (err && err.message) || "Could not reach the ZigbeeLens integration.",
        networks: [],
      };
    }
    this._loading = false;
    this._loaded = true;
    this._render();
  }

  _coreUrl() {
    return (this._summary && this._summary.core_url) || this._configCoreUrl || "";
  }

  _openDashboardButton(coreUrl, extraClass = "") {
    if (!coreUrl) return "";
    return `<a class="btn primary ${extraClass}" href="${esc(coreUrl)}" target="_blank" rel="noopener noreferrer">
      Open full ZigbeeLens dashboard
    </a>`;
  }

  _tryEmbedButton() {
    return `<button type="button" class="btn secondary" id="try-embed">Try Embedded View</button>`;
  }

  _ctaRow(coreUrl, { includeEmbed = true } = {}) {
    const open = this._openDashboardButton(coreUrl);
    const embed = includeEmbed && coreUrl ? this._tryEmbedButton() : "";
    if (!open && !embed) return "";
    return `<div class="cta-row">${open}${embed}</div>`;
  }

  _haMenuButton() {
    return `<button type="button" class="menu-btn" id="menu-btn" aria-label="Open Home Assistant menu">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M3 6h18v2H3V6m0 5h18v2H3v-2m0 5h18v2H3v-2z"/></svg>
    </button>`;
  }

  _panelToolbar(extra = "") {
    return `<div class="panel-toolbar">${this._haMenuButton()}${extra}</div>`;
  }

  _toggleHaSidebar() {
    this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
  }

  _tryEmbeddedView() {
    const coreUrl = this._coreUrl();
    const { canEmbed } = canEmbedDashboard(window.location.protocol, coreUrl, window.location.href);
    this._view = canEmbed ? "embedded" : "embed_blocked";
    this._render();
  }

  _render() {
    const coreUrl = this._coreUrl();

    if (this._view === "embedded") {
      this.shadowRoot.innerHTML = `
        <style>${ZigbeeLensPanel.styles}</style>
        ${this._panelToolbar()}
        <div class="wrap embed-wrap">
          ${this._embeddedView(coreUrl)}
        </div>
      `;
      this._wire();
      return;
    }

    if (this._view === "embed_blocked") {
      this.shadowRoot.innerHTML = `
        <style>${ZigbeeLensPanel.styles}</style>
        ${this._panelToolbar()}
        <div class="wrap">
          ${this._embedBlockedView(coreUrl)}
        </div>
      `;
      this._wire();
      return;
    }

    const s = this._summary || {};
    const connected = !!s.connected;

    this.shadowRoot.innerHTML = `
      <style>${ZigbeeLensPanel.styles}</style>
      ${this._panelToolbar()}
      <div class="wrap">
        ${this._heroCard(s, coreUrl, connected)}
        ${this._loading ? this._loadingCard() : ""}
        ${!this._loading && !connected ? this._disconnectedCard(s, coreUrl) : ""}
        ${!this._loading && connected ? this._findingCard(s) : ""}
        ${!this._loading && connected ? this._statsCard(s) : ""}
        ${!this._loading && connected ? this._networksCard(s) : ""}
        ${!this._loading ? this._integrationCard(s, coreUrl, connected) : ""}
        <p class="note">
          The native companion panel works for all installs without iframing Core.
          Open Full Dashboard opens the full dashboard in a new tab. Try Embedded View
          is optional and only loads when you choose it.
        </p>
      </div>
    `;

    this._wire();
  }

  _embeddedView(coreUrl) {
    const iframe = coreUrl
      ? `<iframe
          class="embed-frame"
          src="${esc(coreUrl)}"
          title="ZigbeeLens full dashboard"
          loading="lazy"
          referrerpolicy="no-referrer"
        ></iframe>`
      : `<p class="muted">Core URL is not configured.</p>`;
    return `
      <section class="card embed-card">
        ${iframe}
      </section>
    `;
  }

  _embedBlockedView(coreUrl) {
    return `
      <section class="card">
        <h2>Embedded view is not available</h2>
        <p class="muted">
          Home Assistant is using HTTPS, but your ZigbeeLens Core URL uses HTTP.
          Browsers block HTTP dashboards from being embedded inside HTTPS pages.
        </p>
        <p class="muted">
          You can still open the full ZigbeeLens dashboard in a separate tab.
        </p>
        <p class="note-inline">
          If you want embedded view later, configure ZigbeeLens with an HTTPS dashboard address.
          This is optional and not required for normal use.
        </p>
        <div class="actions">
          ${this._openDashboardButton(coreUrl)}
        </div>
      </section>
    `;
  }

  _heroCard(s, coreUrl, connected) {
    const sev = SEVERITY[s.overall_health] || SEVERITY.unknown;
    const connBadge = connected
      ? `<span class="badge ok">Connected to Core</span>`
      : `<span class="badge off">Not connected</span>`;
    const healthBadge =
      connected && s.overall_health
        ? `<span class="badge" style="--badge:${sev.color}">Health: ${esc(sev.label)}</span>`
        : "";
    const mockBadge = s.mock_mode ? `<span class="badge watch">Mock data</span>` : "";
    return `
      <section class="card hero">
        <div class="hero-head">
          <div class="hero-title">
            <div class="logo">ZL</div>
            <div>
              <h1>ZigbeeLens</h1>
              <div class="subtitle">Home Assistant companion panel</div>
            </div>
          </div>
          <div class="badges">${connBadge}${healthBadge}${mockBadge}</div>
        </div>
        ${this._ctaRow(coreUrl)}
      </section>
    `;
  }

  _loadingCard() {
    return `<section class="card"><div class="muted">Loading ZigbeeLens status…</div></section>`;
  }

  _disconnectedCard(s, coreUrl) {
    return `
      <section class="card">
        <h2>ZigbeeLens Core is not responding</h2>
        <p class="muted">
          Home Assistant could not reach ZigbeeLens Core${coreUrl ? ` at <code>${esc(coreUrl)}</code>` : ""}.
          Check that the ZigbeeLens Core container or add-on is running and reachable
          from Home Assistant, then reload the status below.
        </p>
        <div class="actions">
          ${this._openDashboardButton(coreUrl)}
          ${coreUrl ? this._tryEmbedButton() : ""}
          <button type="button" class="btn" id="reload">Reload status</button>
        </div>
      </section>
    `;
  }

  _findingCard(s) {
    const finding = s.current_finding;
    const incidents = s.active_incident_count || 0;
    const incidentLine =
      incidents > 0
        ? `<span class="badge incident">${incidents} active incident${incidents === 1 ? "" : "s"}</span>`
        : `<span class="badge ok">No active incidents</span>`;
    return `
      <section class="card">
        <div class="card-head">
          <h2>Current finding</h2>
          ${incidentLine}
        </div>
        <p class="finding">${finding ? esc(finding) : "No active findings. ZigbeeLens is monitoring your networks."}</p>
      </section>
    `;
  }

  _stat(label, value, accent) {
    return `
      <div class="stat">
        <div class="stat-value" ${accent ? `style="color:${accent}"` : ""}>${esc(value)}</div>
        <div class="stat-label">${esc(label)}</div>
      </div>
    `;
  }

  _statsCard(s) {
    const incidentAccent =
      (s.active_incident_count || 0) > 0 ? SEVERITY.incident.color : undefined;
    const unavailAccent =
      (s.unavailable_devices || 0) > 0 ? SEVERITY.watch.color : undefined;
    const routerAccent = (s.router_risks || 0) > 0 ? SEVERITY.watch.color : undefined;
    return `
      <section class="card">
        <div class="grid">
          ${this._stat("Active incidents", s.active_incident_count || 0, incidentAccent)}
          ${this._stat("Networks", s.network_count || 0)}
          ${this._stat("Devices", s.device_count || 0)}
          ${this._stat("Unavailable", s.unavailable_devices || 0, unavailAccent)}
          ${this._stat("Router risks", s.router_risks || 0, routerAccent)}
        </div>
      </section>
    `;
  }

  _networksCard(s) {
    const networks = s.networks || [];
    if (!networks.length) {
      return `<section class="card"><h2>Networks</h2><p class="muted">No networks reported yet.</p></section>`;
    }
    const rows = networks
      .map((n) => {
        const sev = SEVERITY[n.health] || SEVERITY.unknown;
        const online = String(n.bridge_state || "").toLowerCase() === "online";
        return `
          <div class="net-row">
            <div class="net-main">
              <span class="dot" style="background:${sev.color}"></span>
              <div>
                <div class="net-name">${esc(n.name)}</div>
                <div class="net-sub ${online ? "" : "warn"}">${esc(bridgeLabel(n.bridge_state))}</div>
              </div>
            </div>
            <div class="net-meta">
              <span>${esc(n.device_count || 0)} devices</span>
              ${(n.unavailable_devices || 0) > 0 ? `<span class="warn">${esc(n.unavailable_devices)} unavailable</span>` : ""}
              ${(n.router_risks || 0) > 0 ? `<span class="warn">${esc(n.router_risks)} router risk${n.router_risks === 1 ? "" : "s"}</span>` : ""}
            </div>
          </div>
        `;
      })
      .join("");
    return `
      <section class="card">
        <h2>Networks</h2>
        <div class="net-list">${rows}</div>
      </section>
    `;
  }

  _integrationCard(s, coreUrl, connected) {
    const collector = connected
      ? s.collector_connected
        ? `<span class="ok-text">Connected</span>`
        : `<span class="warn">Disconnected</span>`
      : `<span class="muted">Unknown</span>`;
    const lastUpdate = connected ? relativeTime(s.last_update) : "—";
    const version = s.core_version ? ` · v${esc(s.core_version)}` : "";
    return `
      <section class="card">
        <h2>Integration health</h2>
        <dl class="meta">
          <div><dt>Core URL</dt><dd><code>${esc(coreUrl || "not configured")}</code>${version}</dd></div>
          <div><dt>Collector</dt><dd>${collector}</dd></div>
          <div><dt>Last update</dt><dd>${esc(lastUpdate)}</dd></div>
        </dl>
        <div class="actions">
          ${coreUrl ? `<button type="button" class="btn" id="copy">${this._copied ? "Copied!" : "Copy Core URL"}</button>` : ""}
          <button type="button" class="btn" id="reload">Reload status</button>
        </div>
      </section>
    `;
  }

  _wire() {
    const menuBtn = this.shadowRoot.getElementById("menu-btn");
    if (menuBtn) menuBtn.addEventListener("click", () => this._toggleHaSidebar());

    const tryEmbed = this.shadowRoot.getElementById("try-embed");
    if (tryEmbed) tryEmbed.addEventListener("click", () => this._tryEmbeddedView());

    const reload = this.shadowRoot.getElementById("reload");
    if (reload) reload.addEventListener("click", () => this._loadSummary());

    const copy = this.shadowRoot.getElementById("copy");
    if (copy) {
      copy.addEventListener("click", async () => {
        const url = this._coreUrl();
        try {
          await navigator.clipboard.writeText(url);
        } catch {
          /* clipboard may be unavailable; ignore */
        }
        this._copied = true;
        this._render();
        setTimeout(() => {
          this._copied = false;
          this._render();
        }, 1500);
      });
    }
  }
}

ZigbeeLensPanel.styles = `
  :host {
    display: block;
    background: var(--primary-background-color, #f5f5f5);
    min-height: 100%;
    color: var(--primary-text-color, #212121);
    font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
    overflow-x: clip;
  }
  .wrap {
    max-width: 880px;
    margin: 0 auto;
    padding: 16px;
    padding-left: max(16px, env(safe-area-inset-left, 0));
    padding-right: max(16px, env(safe-area-inset-right, 0));
    padding-bottom: max(16px, env(safe-area-inset-bottom, 0));
    display: flex;
    flex-direction: column;
    gap: 16px;
    box-sizing: border-box;
  }
  .embed-wrap {
    max-width: none;
    height: calc(100% - 8px);
    min-height: 480px;
  }
  .panel-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px 0;
    max-width: 880px;
    margin: 0 auto;
    box-sizing: border-box;
  }
  .menu-btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 10px;
    background: var(--card-background-color, #fff);
    color: var(--primary-text-color, #212121);
    cursor: pointer;
    flex-shrink: 0;
    -webkit-tap-highlight-color: transparent;
  }
  .menu-btn svg {
    width: 22px;
    height: 22px;
  }
  .menu-btn:hover { filter: brightness(0.97); }
  .menu-btn:active { filter: brightness(0.93); }
  .card {
    background: var(--card-background-color, #fff);
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: var(--ha-card-border-radius, 12px);
    padding: 20px;
    box-shadow: var(--ha-card-box-shadow, none);
    overflow: hidden;
  }
  .embed-card {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    padding: 12px;
  }
  h1 { font-size: 1.4rem; margin: 0; line-height: 1.2; }
  h2 { font-size: 1.05rem; margin: 0 0 12px; }
  .subtitle, .muted { color: var(--secondary-text-color, #727272); }
  .muted { font-size: 0.92rem; line-height: 1.5; word-break: break-word; }
  .note-inline {
    color: var(--secondary-text-color, #727272);
    font-size: 0.82rem;
    line-height: 1.5;
    margin: 12px 0 0;
  }
  code {
    background: var(--secondary-background-color, #f0f0f0);
    padding: 2px 6px;
    border-radius: 6px;
    font-size: 0.85em;
    word-break: break-all;
  }
  .hero-head {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
  }
  .hero-title { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .logo {
    width: 44px; height: 44px;
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700;
    flex-shrink: 0;
    background: color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent);
    color: var(--primary-color, #03a9f4);
  }
  .badges { display: flex; flex-wrap: wrap; gap: 8px; }
  .badge {
    --badge: var(--secondary-text-color, #888);
    display: inline-flex; align-items: center;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.8rem;
    font-weight: 600;
    color: var(--badge);
    background: color-mix(in srgb, var(--badge) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--badge) 30%, transparent);
  }
  .badge.ok { --badge: var(--success-color, #2e7d32); }
  .badge.watch { --badge: var(--warning-color, #f9a825); }
  .badge.incident, .badge.off { --badge: var(--error-color, #c62828); }
  .btn {
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 48px;
    padding: 12px 18px;
    border-radius: 10px;
    border: 1px solid var(--divider-color, #e0e0e0);
    background: var(--secondary-background-color, #f0f0f0);
    color: var(--primary-text-color, #212121);
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    text-decoration: none;
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
  }
  .btn:hover { filter: brightness(0.97); }
  .btn:active { filter: brightness(0.93); }
  .btn.primary {
    background: var(--primary-color, #03a9f4);
    border-color: var(--primary-color, #03a9f4);
    color: var(--text-primary-color, #fff);
    font-size: 1.05rem;
    min-height: 52px;
  }
  .btn.secondary {
    background: transparent;
    border-color: var(--divider-color, #e0e0e0);
    color: var(--primary-text-color, #212121);
    font-weight: 600;
  }
  .cta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 16px;
  }
  .cta-row .btn.primary {
    flex: 2 1 220px;
    margin-top: 0;
    width: auto;
  }
  .cta-row .btn.secondary {
    flex: 1 1 160px;
  }
  .embed-toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
    flex-shrink: 0;
  }
  .embed-toolbar .btn.primary.embed-open {
    margin-left: auto;
    min-height: 44px;
    font-size: 0.95rem;
    flex: 1 1 auto;
    width: auto;
  }
  .embed-frame {
    width: 100%;
    flex: 1;
    min-height: 360px;
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 10px;
    background: var(--card-background-color, #fff);
  }
  .card-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .finding { margin: 0; line-height: 1.55; font-size: 1rem; word-break: break-word; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
    gap: 12px;
  }
  .stat {
    background: var(--secondary-background-color, #f7f7f7);
    border-radius: 10px;
    padding: 14px 12px;
    text-align: center;
    min-width: 0;
  }
  .stat-value { font-size: 1.7rem; font-weight: 700; line-height: 1; }
  .stat-label { margin-top: 6px; font-size: 0.78rem; color: var(--secondary-text-color, #727272); line-height: 1.3; }
  .net-list { display: flex; flex-direction: column; gap: 10px; }
  .net-row {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 12px; flex-wrap: wrap;
    padding: 12px;
    border-radius: 10px;
    background: var(--secondary-background-color, #f7f7f7);
  }
  .net-main { display: flex; align-items: flex-start; gap: 10px; min-width: 0; flex: 1; }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
  .net-name { font-weight: 600; word-break: break-word; }
  .net-sub { font-size: 0.82rem; color: var(--secondary-text-color, #727272); }
  .net-meta { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 0.85rem; color: var(--secondary-text-color, #727272); width: 100%; }
  .warn { color: var(--warning-color, #f9a825); font-weight: 600; }
  .ok-text { color: var(--success-color, #2e7d32); font-weight: 600; }
  .meta { margin: 0; display: flex; flex-direction: column; gap: 10px; }
  .meta div { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .meta dt { color: var(--secondary-text-color, #727272); font-size: 0.9rem; flex-shrink: 0; }
  .meta dd { margin: 0; text-align: right; min-width: 0; word-break: break-all; }
  .actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
  .actions .btn { flex: 1 1 auto; min-width: min(100%, 140px); }
  .note {
    color: var(--secondary-text-color, #727272);
    font-size: 0.82rem;
    line-height: 1.5;
    margin: 0 4px;
    text-align: center;
    word-break: break-word;
  }
  @media (max-width: 600px) {
    .wrap { padding: 12px; gap: 12px; }
    .embed-wrap { min-height: 420px; }
    .card { padding: 16px; }
    .hero-head { flex-direction: column; align-items: stretch; }
    .badges { justify-content: flex-start; }
    .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .stat-value { font-size: 1.5rem; }
    .net-row { flex-direction: column; align-items: stretch; }
    .net-meta { width: 100%; }
    .meta div { flex-direction: column; align-items: stretch; gap: 4px; }
    .meta dd { text-align: left; }
    .actions { flex-direction: column; }
    .actions .btn { width: 100%; min-width: 0; }
    .cta-row { flex-direction: column; }
    .cta-row .btn.primary,
    .cta-row .btn.secondary { width: 100%; flex: 1 1 auto; }
    .embed-toolbar { flex-direction: column; }
    .embed-toolbar .btn.primary.embed-open { margin-left: 0; width: 100%; }
  }
`;

if (!customElements.get("zigbeelens-panel")) {
  customElements.define("zigbeelens-panel", ZigbeeLensPanel);
}

// Exported for lightweight testing in Node (see test_panel_embed.py asset checks).
if (typeof module !== "undefined" && module.exports) {
  module.exports = { canEmbedDashboard };
}
