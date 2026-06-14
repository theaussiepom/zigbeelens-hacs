# ZigbeeLens Home Assistant integration (HACS)

Read-only Home Assistant bridge to [ZigbeeLens Core](https://github.com/theaussiepom/zigbeelens).

## Prerequisites

**ZigbeeLens Core must already be running.** This integration does not collect Zigbee2MQTT data directly.

Run Core using one of:

- **Docker** — `theaussiepom/zigbeelens` image from GHCR (see main repo [docs/docker.md](https://github.com/theaussiepom/zigbeelens/blob/main/docs/docker.md))
- **Home Assistant OS add-on** — from [zigbeelens-addons](https://github.com/theaussiepom/zigbeelens-addons) (optional)

## Install via HACS

1. **HACS → Integrations → Custom repositories**
2. Add `https://github.com/theaussiepom/zigbeelens-hacs`
3. Category: **Integration**
4. Install **ZigbeeLens** and restart Home Assistant
5. **Settings → Devices & services → Add integration → ZigbeeLens**

## Core URL examples

| Deployment | Typical URL |
|------------|-------------|
| Docker on LAN | `http://<docker-host-ip>:8377` |
| Docker Compose service | `http://zigbeelens:8377` |
| HAOS add-on | `http://localhost:8377` |

## What this integration does

- Summary sensors and binary sensors
- A native companion panel with an **Open Full Dashboard** button (opens Core in a new tab)
- Redacted diagnostics and repairs

The full ZigbeeLens dashboard is hosted by Core and opens separately. This works for normal Docker installs without a reverse proxy and avoids browser iframe restrictions when Home Assistant uses HTTPS and Core uses HTTP.

## What it does not do

- Does **not** mutate Zigbee devices
- Does **not** publish MQTT or Zigbee2MQTT request topics
- Entities are summaries only — Core dashboard remains canonical

## Safety

Read-only bridge to Core.

Issues: https://github.com/theaussiepom/zigbeelens/issues
