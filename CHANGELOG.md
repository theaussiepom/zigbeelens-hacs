# Changelog

All notable changes to ZigbeeLens are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Phase 7C2 current visual evidence:** nine synthetic, privacy-reviewed release-candidate screenshots now document the current Core and locally staged Home Assistant companion surfaces, with manifest-backed provenance and mechanical validation. This is documentation-only and does not change runtime or package behavior.

## [0.1.14] - 2026-07-23

### Added

- **Phase 7B test architecture (merged in PR #101):** canonical oracle contract fixtures (`oracle_contract_version: 2` with Core-owned vocabulary manifest), Core/UI contract lanes, self-contained `scripts/validate-contracts.sh` (no `uv` requirement), Core→UI→ReportDetailV3 exact parity, unknown-not-zero and primary-copy guardrails, `/api`↔`/api/v1` decision/report matrix, OpenAPI structural checks; see `docs/test-architecture.md`
- **Phase 7C1 documentation truth (merged):** current Decision-led product identity and navigation, exact-v3 report ownership, configuration source-of-truth links, deployment/companion boundaries, and release-phase status are aligned. Screenshot capture remains deferred to Phase 7C2; live Beast validation remains deferred to Phase 7D.
- **Home Assistant enrichment contract v1:** strict bounded Core request/result models, exact network+IEEE matching, atomic full replacement, accepted complete-empty clearing, failure retention, HA registry name/area projection alongside preserved Zigbee2MQTT names, and report-profile redaction
- **HACS enrichment manager:** official device/entity/area registry snapshots, fail-closed IEEE/network ambiguity, deterministic representative entities, debounced registry events, bounded retries, forced 15-minute reconciliation, complete lifecycle cleanup, and exact Core-local POST/optional explicit-removal clear ownership
- **HACS release-readiness contracts:** durable OptionsFlow settings with one effective reload; fail-closed Core/capabilities/Decision/enrichment states and distinct repairs; `single_config_entry` metadata plus runtime guards; exact Home Assistant 2025.1.0/Python 3.12 and 2026.7.3/Python 3.14 lanes; generated pinned hassfest/HACS validation with release publication dependent on CI

### Changed

- **Pre-release report reset:** migration `014` deletes all stored development reports once (schema 13 → 14); after schema 14 only exact `ReportDetailV3` is supported for list/detail/download — no v1/v2 compatibility branch remains
- **Phase 7A query bounds (merged in PR #100):** additive incident `order=recent` closes Overview PR #83; latest topology uses per-network indexed seeks (not history-wide `ROW_NUMBER`); device snapshot-history endpoint is target-row/link-bounded (no complete device inventory; network tracking via existence probe; `UNION ALL` + `idx_topology_links_snapshot_target`) with deep topology_facts parity; metric windows use deterministic `id` tie-break; shared-availability instability reads offline transitions in SQL; cursor versions accept only exact `int` `{1,2}`; History full/network report ops measured; migration `013` index-only and runtime-smoked on SQLite 3.34.1; Track 5 baselines frozen
- **Phase 6A navigation:** primary workflows are Overview, Mesh / Investigate, Devices, Incidents, Reports, Settings; supporting Networks / Timeline / Topology snapshots / How it works live under Advanced & support; canonical investigation routes are `/investigate` and `/investigate/:networkId` with legacy `/topology/:networkId/graph` redirect compatibility
- **Phase 6B router UX:** observed router areas are investigated in Mesh / Investigate; standalone Router diagnostics page removed; `/routers` remains a compatibility redirect; Core/HACS/report router facts retained; router-area cards can focus graph evidence and open the existing device drawer without changing layout or presets
- **Phase 6B corrective:** Overview/Network Detail no longer render RouterRisk DiagnosticConclusion cards; Mesh remains the router-area authority via `investigation_priorities` / `router_neighbourhood_review`; manual topology capture encodes the network path segment; investigation actions use contextual accessible names
- **Phase 6B accessibility:** investigation Focus/Clear/View/Open names use title/summary/evidence context and fail soft with accessible-only `item N of M` suffixes when sibling cards still collide — never throws, never exposes IEEE/card ids, and never duplicates router-area action-group wording
- **Phase 6C snapshot UX:** Device Detail owns primary Snapshot history after Device Story; NodeDrawer links to full device details without fetching history; `/topology` is an Advanced/support landing; `/topology/:networkId` is exact raw detail with collapsed contents; Overview no longer promotes raw snapshots; whole-network compare remains API/debug-only
- **Phase 6C corrections:** route params are consumed exactly once; Device Detail path/API segments encode once; retained topology remains readable when capture is disabled; landing cards present truthful latest-snapshot status; Device Detail history loading/failure stays section-local
- **Phase 6C refresh resilience:** loaded snapshot history and raw detail remain visible when a background refresh fails, with section-local retry; raw detail presentation is status-aware; capture actions require both topology enabled and manual capture
- **Phase 6D contextual reports:** Create device/incident/network reports from their detail pages and Mesh; Reports is Saved reports history with Create full report; shared dialog uses Core `ReportDetailV3`; client-only Mesh evidence export removed from production *(superseded for storage: migration 014 / exact-v3-only reads)*
- **Phase 6D corrections:** stable semantic request identity; separate preview/mutation ownership; one-report post-save download; modal focus trap and launcher return; radiogroup format/profile selection; collision-safe saved-row action names; retained Saved reports list on refresh failure
- **Phase 6D seal:** per-report Saved row operation ownership; refresh warning for accepted empty lists; dialog focus trap excludes closed Advanced controls; format/profile use `aria-pressed` button groups
- **Add-on publication status:** source-runner Ingress/token support is implemented, but the generated add-on repository selects the standalone image entrypoint, which omits optional token-file propagation; publication remains blocked on packaged `/data`/Ingress/bearer smokes, reporting schema/default behavior, and HACS reachability
- **Companion publication boundary:** public HACS installation remains unavailable until the satellite tree/version is synchronized, generated exact-matrix and official checks pass remotely, and publication is explicitly authorized; the add-on is deferred from this release

### Added

- **Track 6 retention policy v2:** telemetry-only `retention_days`, separate resolved-incident retention (default 90 days), reports retained until manual delete by default, periodic maintenance, integrity gates, online SQLite backup CLI, `/api/storage/status`
- **Track 6 corrective pass (docs/ops):** `check` / `--dry-run` truly non-mutating; `--apply` never migrates (Core startup owns migrations including `012`); active topology capture exclusion; null resolved retention = keep indefinitely; reports until manual delete by default; symlink-safe backup; no automatic `VACUUM`; invalid/future timestamps retained; SSE `storage_maintenance_completed` plus category invalidations (`incidents_updated` / `reports_updated` / `timeline_updated` / `topology_updated`); `/api/storage/status` null totals before first success
- **Decision contract v2:** public diagnostic DTOs are decision-only (`DecisionBadge`, `DecisionCountSummary`); capabilities advertise `decision_only_diagnostic_payloads`, `report_contract_v3`, `decision_mqtt_summary`
- **Reports v3:** new reports use a single canonical decision-led body (`domain_details`, `collector_status`, `events_or_timeline`); *(stored-report v1/v2 readability superseded by migration 014 exact-v3-only reset)*
- **HACS:** requires exact decision contract v2; repair `core_decision_contract_incompatible`; new decision entity unique IDs (`overall_decision`, `review_first_devices`, …)
- **MQTT Discovery:** decision-contract summary entities (`decision_status`, `review_first`, `worth_reviewing`, `coverage_warnings`, `active_incidents`, `unavailable`)
- **Security:** Home Assistant ingress identity — exact ASGI peer trust, Supervisor `X-Remote-User-Id`, request-local identity, proxy-only add-on boundary
- **Security:** `ingress_trusted_proxies` / `ingress_proxy_only` on `SecurityConfig`; optional `api_token` bearer fallback in ingress mode
- **Add-on:** generates `security.mode=home_assistant_ingress` with Supervisor peer `172.30.32.2`, `ingress_proxy_only`, `panel_admin`, `ingress_stream`
- **Add-on:** optional `security.api_token` via secret file + `ZIGBEELENS_SECURITY_API_TOKEN_FILE` (never in generated YAML)
- **UI:** Home Assistant ingress auth method unlocks without token/CSRF/Sign out; ingress-required guidance when opened outside ingress
- **Capabilities / status:** `home_assistant_ingress_identity`, trusted-peer enforcement, ingress browser authentication; secret-free ingress posture fields
- **HACS:** optional Core API token on the config entry; server-side `Authorization: Bearer` on protected Core reads after public `/api/version` product proof
- **HACS:** linked reauthentication and reconfigure flows for token replace/clear; `ConfigEntryAuthFailed` instead of unreachable-repair loops on HTTP 401
- **HACS:** diagnostics expose only `api_token_configured`; token never enters panel config, websocket summary, or Open Full Dashboard / iframe URLs
- **UI:** standalone browser authentication gate — session status before protected data, API-token unlock for HttpOnly sessions, in-memory CSRF on mutations, credentialed SSE and report downloads
- **Security:** exact `cors_allowed_origins` / `frame_ancestor_origins` allowlists (canonical HTTP/HTTPS origins only)
- **Security:** credentialed CORS without wildcards; pre-body Origin checks for cookie-authenticated mutations
- **Security:** Content-Security-Policy on HTML documents, default same-origin framing, and general browser-safety headers
- **Security:** HACS Core URL validation as canonical HTTP/HTTPS origins (reject userinfo/path/query/fragment)
- **Capabilities:** `exact_cors_allowlist`, `content_security_policy`, `frame_ancestor_allowlist`, `browser_origin_validation`
- **Security:** HTTP Bearer authentication (`Authorization: Bearer`) for protected API reads, mutations, SSE, and report downloads
- **Security:** signed HttpOnly browser sessions (`zigbeelens_session`) with CSRF header protection for cookie-authenticated mutations
- **Security:** `POST/GET/DELETE /api/auth/session` (and `/api/v1`) for session login, status, and logout
- **Security:** `security.session_ttl_seconds` and `security.session_cookie_secure` with automatic Secure-cookie resolution
- **Security:** explicit public/read/mutation route dependencies (no path-prefix middleware)
- **Security:** public `GET /healthz` minimal readiness probe; Docker HEALTHCHECK uses `/healthz`
- **Security:** typed `security.mode` (`local` / `authenticated` / `home_assistant_ingress`) and `SecurityConfig` with `SecretStr` API token and session secret
- **Security:** allowlisted environment and `*_FILE` resolution for security and MQTT secrets (plus temporary `ZIGBEELENS_API_KEY` config-source alias)
- **Security:** secret-free `security` block on `/api/config/status` and startup posture logging
- **Capabilities:** `bearer_authentication`, `browser_session_authentication`, `csrf_protection`, `home_assistant_ingress_identity` advertisement flags
- **Capabilities:** `shared_decisions`, `companion_decision_summary`, and decision-surface advertisement for companion consumers
- **Validation:** release-surface version synchronisation helper; stronger HACS/add-on packaging checks for Phase 5E artefacts

### Changed

- **Public API:** removed Health/Lens presentation fields from current Dashboard, Networks, Devices, Incidents, and new reports (`lens_bucket*`, `health` diagnostic authority, health-derived Dashboard collections)
- **HACS:** no Health/Lens diagnostic fallback when the decision contract is missing/older/newer/malformed; factual operational entities retained
- **MQTT Discovery:** superseded Lens nested discovery configs are tombstoned on start; `unavailable` retained as a factual count
- **Security:** when an API token is configured, all protected routes require Bearer (no mutation-only semantics; `X-ZigbeeLens-Api-Key` removed)
- **Security:** `home_assistant_ingress` no longer requires `api_token`; ingress identity is the primary add-on UI auth method
- **Security:** capabilities advertise ingress identity support as implemented
- **Security:** source/default `server.host` is loopback (`127.0.0.1`); Docker/add-on configs keep explicit `0.0.0.0`
- **Security:** canonical `zigbeelens` launcher owns the Uvicorn bind from one effective AppConfig (including `ZIGBEELENS_PORT`)
- **Docker:** HEALTHCHECK probes `ZIGBEELENS_PORT` (default 8377) via `/healthz` without loading AppConfig or secrets
- **Redaction:** MQTT/connection URI query and fragment parameters use the same `is_secret_key()` policy as nested secret keys
- **HACS:** companion decision mode requires exact contract v2 and valid Core Dashboard decision surfaces; missing/unsupported/malformed contracts disable decision mode without restoring Health/Lens diagnostic fallback; Core compatibility is Compatible / Incompatible / Unknown
- **Topology:** enabled by default with a single startup network map scan after MQTT collector and bridge readiness (`startup_stable_delay_seconds`, default 60); passive MQTT updates thereafter; periodic active scans disabled unless `refresh_interval_seconds` > 0
- **Docs:** deployment live-state and alignment status refreshed; BenBeast uses rolling `:edge`, not pinned semver; HACS/add-on/release-test docs updated for Decision Engine companion behaviour

### Fixed

- **Mesh drawer refresh resilience:** Device Story and device coverage retain the last accepted evidence after a background refresh failure, with contextual retry notices; initial no-data failures remain unavailable and accepted-empty coverage remains distinct.
- **Home Assistant enrichment event-loop scheduling:** debounce, retry, and periodic reconciliation callbacks are explicitly classified to remain on the Home Assistant event loop; both exact HA minimum/current matrix lanes now execute the production scheduler paths.

## [0.1.13] - 2026-06-16

Lens family alignment release — clean MQTT summary entities, API v1 surface, and presentation health buckets.

### Added

- **API v1:** `/api/v1` route aliases, `/api/v1/capabilities`, and `/api/v1/status` (Lens-family parity with ThreadLens)
- **Presentation:** `lens_bucket` and related fields on dashboard/API summary payloads
- **MQTT Discovery:** clean Lens-family global summary entities (health, issues, bucket counts — six entities on one HA device)
- **Docs:** [lens-family.md](docs/lens-family.md) stub, [lens-alignment-status.md](docs/lens-alignment-status.md), [deployments/lens-alignment-live-state.md](docs/deployments/lens-alignment-live-state.md)
- Browser favicon for Core UI (`favicon.svg`, `favicon.ico`, `apple-touch-icon.png`)
- Storage retention: purge collected telemetry older than `storage.retention_days` on Core startup
- `scripts/run-release-checks.sh` — runs all automated pre-release validation steps

### Changed

- **MQTT Discovery:** new topic layout under `homeassistant/sensor/zigbeelens/<entity_key>/config` and `zigbeelens/summary/<entity_key>/state`
- **MQTT Discovery:** backward compatibility intentionally not preserved; migration docs for clearing old retained discovery configs
- OpenAPI docs (`/docs`, `/openapi.json`) disabled by default; enable with `ZIGBEELENS_OPENAPI_ENABLED=true`
- Documented v0.1.0 security posture: ZigbeeLens Core has no built-in authentication; Zigbee control remains read-only
- HACS manifest version aligned with Core/add-on packages
- Settings/docs: `retention_days` enforced on startup purge (default 7 days)
- CI: version alignment check on every run; packaging job waits for HA integration tests
- HACS packaging: drop root `icon.png` / `logo.png`; keep inline `custom_components/zigbeelens/brand/`
- Remove home-assistant/brands CDN sync script and docs

### Fixed

- **MQTT Discovery:** flat `device.identifiers` (`["zigbeelens_core"]`) — Home Assistant rejects nested identifier arrays
- Static UI: block path traversal outside the bundled static directory
- SQLite: expose `LockedCursor.rowcount` and release locks after DML/iteration
- Topology: clear pending capture after handler errors and stale timeouts
- Topology API: require strict boolean confirmation (reject `"false"` strings)
- HA enrichment: fix IEEE-only device lookup SQL join
- UI API client: retry GET only, not POST/DELETE
- HACS integration: unregister companion panel when `panel_enabled` is disabled
- API health/SSE collector status: redact `last_error` (parity with HA diagnostics)
- CORS: disable credentials with wildcard origins (invalid browser combination)

### Notes

- MQTT Discovery remains **opt-in** (`features.mqtt_discovery: false` by default)
- HACS companion entities are separate from MQTT summary entities and were preserved during Ben's deployment migration
- HACS UI was source-validated; full browser visual smoke was not part of this release
- Report/export alignment (PR #10) remains open — not included in this tag

## [0.1.12] - 2026-06-15

### Changed

- HA panel sidebar control: use Home Assistant's built-in `ha-menu-button` in the panel header (same pattern as HACS and Scrypted) instead of a custom toggle; remove **Open in new tab** from the embedded toolbar

## [0.1.11] - 2026-06-15

### Changed

- HA panel default: embed full Core dashboard when HA and Core use the same protocol (HTTP+HTTP or HTTPS+HTTPS)
- Keep ☰ menu button and **Open in new tab** link on the embedded toolbar; mixed content still falls back to summary

## [0.1.10] - 2026-06-15

### Fixed

- **Home Assistant main sidebar sliding away** on the ZigbeeLens panel page: add menu (☰) button that fires `hass-toggle-menu` so you can reopen HA navigation
- Re-register panel when a stale registration used `embed_iframe=True` or lacked `core_url`
- Stop using full-viewport `100vh` layout that could collapse HA's drawer on custom panels

### Changed

- Panel listens for HA's `narrow` property (responsive layout)

## [0.1.9] - 2026-06-15

### Fixed

- Sidebar stability: stop auto-embedding Core on HTTPS+HTTPS (iframe not required for the companion panel)
- Revert force panel remove/re-register on every setup (ThreadLens pattern — update `core_url` in place)

### Changed

- Default sidebar view is always the native summary panel (HA websocket only); Try Embedded View is manual optional only

## [0.1.8] - 2026-06-15

### Fixed

- HACS sidebar disappearing with HTTPS Core URL: force panel re-registration on setup so `embed_iframe=False` and `config.core_url` apply after upgrades (mirrors ThreadLens)
- Panel state stored separately from entry runtime; unload order fixed so panel unregisters reliably

### Changed

- Embedded HTTPS view is full-screen (no “Back to Summary” toolbar)

## [0.1.7] - 2026-06-15

### Added

- HACS integration brand icons (`icon.png`, `logo.png`, `custom_components/zigbeelens/brand/`) for GitHub/HACS listing and Home Assistant integration settings (HA 2026.3+ brands proxy)
- Config flow screenshot and docs for Core URL guidance (HTTP vs HTTPS, no `:8377` on HTTPS hostnames)
- Beast Traefik example: `deploy/traefik/zigbeelens-router.yaml.example` — `/api` bypass without Authentik (mirrors ThreadLens) so HACS config flow works over HTTPS

### Changed

- HACS companion panel: **auto-embed** full Core dashboard when Home Assistant and Core use the same scheme (HTTP+HTTP or HTTPS+HTTPS); mixed content still shows calm blocked screen + Open Full Dashboard
- HACS packaging copies root icons; validation asserts brand assets exist
- Docs updated for Beast Traefik API bypass, correct Core URLs, and embedded-view setup

## [0.1.6]

Skipped — version bumped during icon work, released as 0.1.7.

## [0.1.5] - 2026-06-14

### Changed

- HACS companion panel: optional **Try Embedded View** secondary action with mixed-content safety checks; default remains native summary + Open Full Dashboard in new tab
- Phase 14 mobile/responsive polish for Core React dashboard (overflow guards, touch targets, mono text wrapping, responsive stat grids)
- Phase 14 mobile polish for HACS native companion panel (safe-area padding, stacked layouts on narrow screens, larger primary CTA)
- HACS sidebar is a native companion panel with Open Full Dashboard button — does not iframe Core
- Documentation and `RELEASE_CHECKLIST.md` updated for Docker + HACS + add-on architecture
- Add-on defaults: `mqtt_discovery.enabled: false`, explicit `privileged: false`, `armv7` removed (matches GHCR amd64/arm64 builds)

## [0.1.0] - 2026-06-14

Initial public release.

### Highlights

- Read-only Zigbee2MQTT observability over MQTT
- Multi-network support with `network_id` + `ieee_address` identity
- Live MQTT collector (subscribe-only)
- SQLite persistence with idempotent migrations
- Health classification and incident correlation
- Router and mesh risk enrichment
- Full diagnostic dashboard (Overview, Incidents, Networks, Routers, Devices, Timeline, Reports, Settings)
- Redacted JSON, YAML, and Markdown reports
- Home Assistant OS add-on with Ingress
- Docker and Compose install path
- HACS Home Assistant integration (summary entities, native companion panel, Open Full Dashboard button, diagnostics, repairs)
- Optional MQTT Discovery summary entities
- Optional topology snapshots (manual capture, feature-gated)
- Optional Home Assistant device enrichment API
- SSE live updates with polling fallback
- Fourteen mock scenarios for regression testing

### Safety

- Collector is subscribe-only — no Zigbee2MQTT request or set topics
- MQTT Discovery publishes only `homeassistant/` and `zigbeelens/` topics
- Topology allows only confirmed `{base_topic}/bridge/request/networkmap`
- Reports redacted before storage and download
- No permit join, remove, reset, bind, unbind, OTA, or channel changes

[0.1.0]: https://github.com/theaussiepom/zigbeelens/releases/tag/v0.1.0
