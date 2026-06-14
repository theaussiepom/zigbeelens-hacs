# Changelog

All notable changes to ZigbeeLens are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- HACS Home Assistant integration (summary entities, panel, diagnostics, repairs)
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
