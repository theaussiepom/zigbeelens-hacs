/**
 * ZigbeeLens panel inside Home Assistant.
 *
 * Native companion summary is the default. Try Embedded View optionally loads
 * the full Core dashboard in an iframe when schemes match; mixed content and
 * invalid URLs stay on the native/blocked fallback. Back to Summary always
 * returns to the native panel. Uses Home Assistant's built-in ha-menu-button
 * in the panel header (same pattern as HACS and Scrypted) to reopen the sidebar.
 */

const SEVERITY = {
  ok: { label: "Healthy", color: "var(--success-color, #2e7d32)" },
  watch: { label: "Watch", color: "var(--warning-color, #f9a825)" },
  incident: { label: "Incident", color: "var(--error-color, #c62828)" },
  unknown: { label: "No signal", color: "var(--secondary-text-color, #888)" },
};

const STRICT_IPV4 =
  /^(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$/;
const IPV4_NUMBER_LABEL = /^(?:[0-9]+|0[xX][0-9A-Fa-f]+)$/;

function endsInIpv4Number(host) {
  if (!host) return false;
  const parts = host.split(".");
  return IPV4_NUMBER_LABEL.test(parts[parts.length - 1] || "");
}

function isValidDnsAscii(host) {
  if (!host || host.length > 253 || host.endsWith(".") || host.startsWith(".") || host.includes("*")) {
    return false;
  }
  if (host.includes("..")) return false;
  if (/[\s"'`;,\\]/.test(host) || host.includes("_")) {
    return false;
  }
  if (endsInIpv4Number(host) && !STRICT_IPV4.test(host)) {
    return false;
  }
  const labels = host.split(".");
  for (const label of labels) {
    if (!label || label.length > 63) return false;
    if (label.startsWith("-") || label.endsWith("-")) return false;
    if (!/^[a-z0-9-]+$/i.test(label)) return false;
  }
  return true;
}

function splitRawAuthority(raw) {
  const m = /^https?:\/\/(\[[^\]]+\]|[^/?#]+)/i.exec(raw);
  if (!m) return null;
  const authority = m[1];
  if (authority.startsWith("[")) {
    const end = authority.indexOf("]");
    if (end < 0) return null;
    const host = authority.slice(1, end);
    const rest = authority.slice(end + 1);
    // Raw port must be plain decimal digits when present (leading zeros ok).
    if (rest && !/^:\d+$/.test(rest)) return null;
    return { host, portText: rest ? rest.slice(1) : "", bracketed: true };
  }
  if (authority.includes("@") || /[\s"'`;,]/.test(authority)) {
    return null;
  }
  const idx = authority.lastIndexOf(":");
  if (idx >= 0) {
    const portText = authority.slice(idx + 1);
    if (!/^\d+$/.test(portText)) return null;
    return {
      host: authority.slice(0, idx),
      portText,
      bracketed: false,
    };
  }
  return { host: authority, portText: "", bracketed: false };
}

/**
 * Strict canonical Core origin for iframe/anchor use.
 * Absolute http(s) only; no relative resolution against the HA page.
 * Matches Core/HACS CSP-safe host grammar (strict IPv4/IPv6 or IDNA DNS).
 * @returns {string|null}
 */
function canonicalizeCoreOrigin(coreUrl) {
  try {
    if (!coreUrl || !String(coreUrl).trim()) {
      return null;
    }
    const raw = String(coreUrl).trim();
    if (raw !== String(coreUrl) || raw.toLowerCase() === "null" || raw.startsWith("//")) {
      return null;
    }
    if (raw.includes("\\") || /[\u0000-\u001f\u007f]/.test(raw) || /\s/.test(raw)) {
      return null;
    }
    const auth = splitRawAuthority(raw);
    if (!auth || !auth.host) {
      return null;
    }
    if (auth.host.includes("%")) {
      return null;
    }
    if (auth.portText) {
      const n = Number(auth.portText);
      if (!Number.isInteger(n) || n < 1 || n > 65535) {
        return null;
      }
    }
    // Reject relative values: do not resolve against window.location.
    const core = new URL(raw);
    if (core.protocol !== "http:" && core.protocol !== "https:") {
      return null;
    }
    if (core.username || core.password) {
      return null;
    }
    if (core.search || core.hash) {
      return null;
    }
    const path = core.pathname || "";
    if (path && path !== "/") {
      return null;
    }

    const browserHost = String(core.hostname || "").replace(/^\[|\]$/g, "");
    let hostAscii;
    if (auth.bracketed) {
      // Bracketed authorities are IPv6-only.
      if (!auth.host.includes(":")) return null;
      if (!browserHost.includes(":")) return null;
      hostAscii = browserHost;
    } else if (STRICT_IPV4.test(browserHost)) {
      // Reject every browser rewrite from hex/octal/short/mixed IPv4 input.
      if (auth.host !== browserHost) return null;
      hostAscii = browserHost;
    } else if (endsInIpv4Number(auth.host) || endsInIpv4Number(browserHost)) {
      return null;
    } else {
      hostAscii = browserHost;
      if (hostAscii.includes(":") || !isValidDnsAscii(hostAscii)) {
        return null;
      }
    }

    const scheme = core.protocol === "https:" ? "https" : "http";
    const hostFmt = hostAscii.includes(":") ? `[${hostAscii}]` : hostAscii;
    // Use browser-canonical port (strips leading zeros / default ports).
    const port = core.port ? `:${core.port}` : "";
    const origin = `${scheme}://${hostFmt}${port}`;
    if (/\s/.test(origin)) return null;
    if (auth.bracketed) {
      return core.origin;
    }
    return origin;
  } catch {
    return null;
  }
}

/** @returns {{ canEmbed: boolean, reason?: string, origin?: string|null }} */
function canEmbedDashboard(haProtocol, coreUrl) {
  try {
    const ha = String(haProtocol || "").toLowerCase();
    if (!ha.endsWith(":")) {
      return { canEmbed: false, reason: "invalid_ha", origin: null };
    }
    const origin = canonicalizeCoreOrigin(coreUrl);
    if (!origin) {
      return {
        canEmbed: false,
        reason: coreUrl && String(coreUrl).trim() ? "invalid_core_url" : "missing_core_url",
        origin: null,
      };
    }
    const coreProtocol = new URL(origin).protocol;
    const isMixedContentIframe = ha === "https:" && coreProtocol === "http:";
    return { canEmbed: !isMixedContentIframe, origin };
  } catch {
    return { canEmbed: false, reason: "invalid_core_url", origin: null };
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

function formatCount(value) {
  if (value === null || value === undefined) return "—";
  return String(value);
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
    // Native summary remains the default; never auto-enter iframe mode.
    if (this.shadowRoot && this.shadowRoot.childNodes.length) {
      this._render();
    }
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
      return;
    }
    this._syncHaMenuButton();
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
    const raw = (this._summary && this._summary.core_url) || this._configCoreUrl || "";
    return canonicalizeCoreOrigin(raw) || "";
  }

  _backToSummary() {
    this._view = "summary";
    this._render();
  }

  _openDashboardButton(coreUrl, extraClass = "") {
    const safe = canonicalizeCoreOrigin(coreUrl);
    if (!safe) return "";
    return `<a class="btn primary ${extraClass}" href="${esc(safe)}" target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">
      Open full ZigbeeLens dashboard
    </a>`;
  }

  _tryEmbedButton() {
    return `<button type="button" class="btn secondary" id="try-embed">Try Embedded View</button>`;
  }

  _backToSummaryButton() {
    return `<button type="button" class="btn secondary" id="back-summary">Back to Summary</button>`;
  }

  _ctaRow(coreUrl, { includeEmbed = true } = {}) {
    const open = this._openDashboardButton(coreUrl);
    const embed = includeEmbed && coreUrl ? this._tryEmbedButton() : "";
    if (!open && !embed) return "";
    return `<div class="cta-row">${open}${embed}</div>`;
  }

  _panelHeader({ title = "" } = {}) {
    const titleHtml =
      this._narrow && title ? `<div class="panel-header-title">${esc(title)}</div>` : "";
    return `<div class="panel-header">
      <ha-menu-button id="menu-btn"></ha-menu-button>
      ${titleHtml}
    </div>`;
  }

  _syncHaMenuButton() {
    const menuBtn = this.shadowRoot && this.shadowRoot.querySelector("ha-menu-button");
    if (!menuBtn) return false;
    menuBtn.hass = this._hass;
    menuBtn.narrow = this._narrow;
    return true;
  }

  _tryEmbeddedView() {
    const coreUrl = this._coreUrl();
    const { canEmbed } = canEmbedDashboard(window.location.protocol, coreUrl);
    this._view = canEmbed ? "embedded" : "embed_blocked";
    this._render();
  }

  _render() {
    const coreUrl = this._coreUrl();

    if (this._view === "embedded") {
      this.shadowRoot.innerHTML = `
        <style>${ZigbeeLensPanel.styles}</style>
        <div class="embed-layout">
          ${this._panelHeader({ title: "ZigbeeLens" })}
          <div class="embed-toolbar">
            ${this._backToSummaryButton()}
            ${this._openDashboardButton(coreUrl)}
          </div>
          <div class="embed-body">
            ${this._embeddedView(coreUrl)}
          </div>
        </div>
      `;
      this._wire();
      return;
    }

    if (this._view === "embed_blocked") {
      this.shadowRoot.innerHTML = `
        <style>${ZigbeeLensPanel.styles}</style>
        ${this._panelHeader({ title: "ZigbeeLens" })}
        <div class="wrap">
          ${this._embedBlockedView(coreUrl)}
        </div>
      `;
      this._wire();
      return;
    }

    const s = this._summary || {};
    const connected = !!s.connected;
    const decisionMode = this._decisionMode(s);

    this.shadowRoot.innerHTML = `
      <style>${ZigbeeLensPanel.styles}</style>
      ${this._panelHeader({ title: "ZigbeeLens" })}
      <div class="wrap">
        ${this._heroCard(s, coreUrl, connected)}
        ${this._loading ? this._loadingCard() : ""}
        ${!this._loading && !connected ? this._disconnectedCard(s, coreUrl) : ""}
        ${
          !this._loading && connected && decisionMode
            ? this._decisionPrioritiesCard(s)
            : ""
        }
        ${!this._loading && connected && !decisionMode ? this._contractIncompatibleCard(s) : ""}
        ${!this._loading && connected && decisionMode ? this._statsCard(s) : ""}
        ${!this._loading && connected && decisionMode ? this._networksCard(s) : ""}
        ${!this._loading ? this._integrationCard(s, coreUrl, connected) : ""}
        <p class="note">
          The native summary is the default view. Use Try Embedded View when you want the
          full Core dashboard in this panel (requires matching Core frame-ancestor
          configuration). Use the menu button above to reopen Home Assistant navigation
          when the sidebar is hidden.
        </p>
      </div>
    `;

    this._wire();
  }

  _decisionMode(s) {
    return (
      !!s &&
      s.shared_decisions_available === true &&
      s.core_version_compatible === true
    );
  }

  _embeddedView(coreUrl) {
    const safe = canonicalizeCoreOrigin(coreUrl);
    if (!safe) {
      return `<p class="muted embed-empty">Core URL is not configured.</p>`;
    }
    return `<iframe
      class="embed-frame"
      src="${esc(safe)}"
      title="ZigbeeLens full dashboard"
      loading="lazy"
      referrerpolicy="no-referrer"
    ></iframe>`;
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
          ${this._backToSummaryButton()}
          ${this._openDashboardButton(coreUrl)}
        </div>
      </section>
    `;
  }

  _heroCard(s, coreUrl, connected) {
    const decisionMode = this._decisionMode(s);
    const connBadge = connected
      ? `<span class="badge ok">Connected to Core</span>`
      : `<span class="badge off">Not connected</span>`;
    let modeBadge = "";
    if (connected && decisionMode) {
      const contract =
        s.decision_contract_version !== null &&
        s.decision_contract_version !== undefined
        ? `Decision contract v${esc(s.decision_contract_version)}`
        : "Shared decisions";
      const status = s.overall_decision_status
        ? ` · ${esc(s.overall_decision_status)}`
        : "";
      modeBadge = `<span class="badge ok">${contract}${status}</span>`;
    } else if (connected && s.core_update_required) {
      modeBadge = `<span class="badge watch">Core update required</span>`;
    } else if (connected && s.integration_update_required) {
      modeBadge = `<span class="badge watch">Integration update required</span>`;
    } else if (connected && s.decision_payload_invalid) {
      modeBadge = `<span class="badge watch">Decision data invalid</span>`;
    } else if (connected && s.core_version_state === "unknown") {
      modeBadge = `<span class="badge watch">Core version unknown</span>`;
    } else if (connected && !decisionMode) {
      modeBadge = `<span class="badge watch">Shared decisions unavailable</span>`;
    }
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
          <div class="badges">${connBadge}${modeBadge}${mockBadge}</div>
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

  _contractIncompatibleCard(s) {
    let title = "Shared decisions unavailable";
    let message =
      "ZigbeeLens could not establish a compatible Decision contract. Review integration diagnostics and try again after Core is reachable.";

    if (s.core_version_state === "unknown") {
      title = "Core version unknown";
      message =
        "Core did not provide a valid version, so compatibility cannot be established. Shared decisions remain disabled until a valid version is observed.";
    } else if (s.core_version_state === "incompatible") {
      title = "Core version incompatible";
      message =
        "This Core version is older than the integration supports. Update Core, then reload the integration.";
    } else if (s.capabilities_state === "unavailable") {
      title = "Core capabilities unavailable";
      message =
        "The capabilities response could not be fetched. The integration will retry; no compatibility conclusion is being inferred from the outage.";
    } else if (s.capabilities_state === "malformed") {
      title = "Core capabilities malformed";
      message =
        "The capabilities response could not be validated. Shared decisions remain disabled until a valid response is observed.";
    } else if (s.integration_update_required) {
      title = "Integration update required";
      message =
        "Core exposes a newer Decision contract than this integration supports. Update the ZigbeeLens integration, then reload it.";
    } else if (s.core_update_required) {
      title = "Core update required";
      message =
        "Core does not expose the required Decision contract and capabilities. Update Core, then reload the integration.";
    } else if (s.decision_contract_state === "malformed") {
      title = "Decision contract malformed";
      message =
        "The observed Decision contract version is invalid. Compatibility cannot be established from this response.";
    } else if (s.decision_payload_invalid) {
      title = "Decision data malformed";
      message =
        "Core exposes the exact supported Decision contract, but its Dashboard decision data is missing or malformed. No upgrade remedy is inferred.";
    }
    return `
      <section class="card">
        <h2>${esc(title)}</h2>
        <p class="muted">${esc(message)}</p>
      </section>
    `;
  }

  _decisionPrioritiesCard(s) {
    const priorities = Array.isArray(s.investigation_priorities) ? s.investigation_priorities : [];
    const more = Number(s.more_investigation_priority_count || 0);
    let body;
    if (!priorities.length) {
      body = `<p class="muted">No current investigation priorities from stored evidence.</p>`;
    } else {
      const rows = priorities
        .map((item) => {
          const evidence = item.latest_supporting_evidence_at
            ? `<div class="priority-time">Evidence ${esc(relativeTime(item.latest_supporting_evidence_at))}</div>`
            : "";
          return `
            <div class="priority-row">
              <div class="priority-meta">
                <span class="badge">${esc(item.priority)}</span>
                <span class="priority-net">${esc(item.network_name || "Network")}</span>
              </div>
              <div class="priority-title">${esc(item.title)}</div>
              <div class="priority-summary">${esc(item.summary)}</div>
              ${evidence}
            </div>
          `;
        })
        .join("");
      const moreLine =
        more > 0
          ? `<p class="muted more-line">+${esc(more)} more in the full ZigbeeLens dashboard.</p>`
          : "";
      body = `<div class="priority-list">${rows}</div>${moreLine}`;
    }
    return `
      <section class="card">
        <h2>What needs attention now</h2>
        ${body}
      </section>
    `;
  }

  _stat(label, value, accent) {
    return `
      <div class="stat">
        <div class="stat-value" ${accent ? `style="color:${accent}"` : ""}>${esc(formatCount(value))}</div>
        <div class="stat-label">${esc(label)}</div>
      </div>
    `;
  }

  _statsCard(s) {
    const incidentAccent =
      typeof s.active_incident_count === "number" && s.active_incident_count > 0
        ? SEVERITY.incident.color
        : undefined;
    const unavailAccent =
      typeof s.unavailable_devices === "number" && s.unavailable_devices > 0
        ? SEVERITY.watch.color
        : undefined;
    return `
      <section class="card">
        <div class="grid">
          ${this._stat("Investigation priorities", s.investigation_priority_count)}
          ${this._stat("Data coverage warnings", s.data_coverage_warning_count)}
          ${this._stat("Active incidents", s.active_incident_count, incidentAccent)}
          ${this._stat("Networks", s.network_count)}
          ${this._stat("Devices", s.device_count)}
          ${this._stat("Unavailable", s.unavailable_devices, unavailAccent)}
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
        const online = String(n.bridge_state || "").toLowerCase() === "online";
        const offline = String(n.bridge_state || "").toLowerCase() === "offline";
        const bridgeColor = online
          ? SEVERITY.ok.color
          : offline
            ? SEVERITY.incident.color
            : SEVERITY.unknown.color;
        const priorityCount =
          typeof n.investigation_priority_count === "number"
            ? n.investigation_priority_count
            : null;
        const unavailable =
          typeof n.unavailable_devices === "number" ? n.unavailable_devices : null;
        const meta = `
              <span>${esc(formatCount(n.device_count))} devices</span>
              ${
                unavailable !== null && unavailable > 0
                  ? `<span class="warn">${esc(unavailable)} unavailable</span>`
                  : ""
              }
              ${
                priorityCount !== null && priorityCount > 0
                  ? `<span>${esc(priorityCount)} investigation priorit${priorityCount === 1 ? "y" : "ies"}</span>`
                  : ""
              }
            `;
        return `
          <div class="net-row">
            <div class="net-main">
              <span class="dot" style="background:${bridgeColor}"></span>
              <div>
                <div class="net-name">${esc(n.name)}</div>
                <div class="net-sub ${online ? "" : "warn"}">${esc(bridgeLabel(n.bridge_state))}</div>
              </div>
            </div>
            <div class="net-meta">${meta}</div>
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
      ? s.collector_connected === true
        ? `<span class="ok-text">Connected</span>`
        : s.collector_connected === false
          ? `<span class="warn">Disconnected</span>`
          : `<span class="muted">Unknown</span>`
      : `<span class="muted">Unknown</span>`;
    const lastUpdate = connected ? relativeTime(s.last_update) : "—";
    const version = s.core_version ? ` · v${esc(s.core_version)}` : "";
    const decisions =
      s.shared_decisions_available === true
        ? `<span class="ok-text">Available</span>`
        : `<span class="muted">Unavailable</span>`;
    const contract =
      s.decision_contract_version !== null &&
      s.decision_contract_version !== undefined
        ? `v${esc(s.decision_contract_version)}`
        : "unobserved";
    let compatibility;
    if (s.core_version_compatible === true) {
      compatibility = `<span class="ok-text">Compatible</span>`;
    } else if (s.core_version_compatible === false) {
      compatibility = `<span class="warn">Incompatible</span>`;
    } else {
      compatibility = `<span class="muted">Unknown</span>`;
    }
    return `
      <section class="card">
        <h2>Integration health</h2>
        <dl class="meta">
          <div><dt>Core URL</dt><dd><code>${esc(coreUrl || "not configured")}</code>${version}</dd></div>
          <div><dt>Shared decisions</dt><dd>${decisions}</dd></div>
          <div><dt>Decision contract</dt><dd>${esc(contract)}</dd></div>
          <div><dt>Contract state</dt><dd>${esc(s.decision_contract_state || "unknown")}</dd></div>
          <div><dt>Decision payload</dt><dd>${esc(s.decision_payload_state || "unknown")}</dd></div>
          <div><dt>Capabilities</dt><dd>${esc(s.capabilities_state || "unknown")}</dd></div>
          <div><dt>HA enrichment contract</dt><dd>${esc(s.enrichment_contract_state || "unknown")}</dd></div>
          <div><dt>Core compatibility</dt><dd>${compatibility}</dd></div>
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
    this._syncHaMenuButton();

    const tryEmbed = this.shadowRoot.getElementById("try-embed");
    if (tryEmbed) tryEmbed.addEventListener("click", () => this._tryEmbeddedView());

    const backSummary = this.shadowRoot.getElementById("back-summary");
    if (backSummary) backSummary.addEventListener("click", () => this._backToSummary());

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
    display: flex;
    flex-direction: column;
    background: var(--primary-background-color, #f5f5f5);
    min-height: 100%;
    height: 100%;
    color: var(--primary-text-color, #212121);
    font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
    overflow: hidden;
  }
  .embed-layout {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    height: 100%;
  }
  .embed-toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--divider-color, #ddd);
    background: var(--card-background-color, #fff);
    flex: 0 0 auto;
  }
  .embed-body {
    flex: 1;
    min-height: 0;
    position: relative;
    display: flex;
    flex-direction: column;
  }
  .panel-header {
    display: flex;
    align-items: center;
    flex-shrink: 0;
    padding-left: max(0px, env(safe-area-inset-left, 0));
    color: var(--primary-text-color, #212121);
    font-family: var(--paper-font-body1_-_font-family, Roboto, Noto, sans-serif);
    font-size: var(--paper-font-body1_-_font-size, 14px);
  }
  .panel-header-title {
    font-weight: 500;
    line-height: 1.25;
    padding: 8px 0;
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
  .embed-empty {
    padding: 16px;
  }
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
  .embed-frame {
    width: 100%;
    flex: 1;
    min-height: 0;
    border: 0;
    background: var(--card-background-color, #fff);
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
  .priority-list { display: flex; flex-direction: column; gap: 12px; }
  .priority-row {
    padding: 12px;
    border-radius: 10px;
    background: var(--secondary-background-color, #f7f7f7);
  }
  .priority-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 6px;
  }
  .priority-net {
    font-size: 0.85rem;
    color: var(--secondary-text-color, #727272);
  }
  .priority-title { font-weight: 600; word-break: break-word; }
  .priority-summary {
    margin-top: 4px;
    font-size: 0.92rem;
    line-height: 1.45;
    color: var(--secondary-text-color, #727272);
    word-break: break-word;
  }
  .priority-time {
    margin-top: 6px;
    font-size: 0.8rem;
    color: var(--secondary-text-color, #727272);
  }
  .more-line { margin: 10px 0 0; }
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
  }
`;

if (!customElements.get("zigbeelens-panel")) {
  customElements.define("zigbeelens-panel", ZigbeeLensPanel);
}

// Exported for lightweight testing in Node (see test_panel_embed.py asset checks).
if (typeof module !== "undefined" && module.exports) {
  module.exports = { canEmbedDashboard, canonicalizeCoreOrigin };
}
