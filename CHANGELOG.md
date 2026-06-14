# Changelog

All notable changes to ZigbeeLens are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.12] - 2026-06-15

### Changed

- HA panel sidebar control: use Home Assistant's built-in `ha-menu-button` in the panel header (same pattern as HACS and Scrypted) instead of a custom toggle; remove **Open in new tab** from the embedded toolbar

### Fixed

- HACS store icon: submit brand assets to [home-assistant/brands](https://github.com/home-assistant/brands) (`custom_integrations/zigbeelens/`) so HACS can resolve the icon from the CDN (same path as Scrypted/HACS; inline `brand/` assets only cover HA Settings)

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
