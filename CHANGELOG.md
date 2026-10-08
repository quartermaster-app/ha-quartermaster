# Changelog

All notable changes to this integration. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.1] - 2026-10-08

### Changed

- Servers are identified by Quartermaster's stable `server_id` instead of their address, so the same server under two addresses can't be added twice. Existing entries switch over automatically (config entry 1.2); servers without a `server_id` keep using the address.
- Reconfigure and re-authenticate check that the address still points at the same Quartermaster server, and refuse a different one.
- Server compatibility is checked with `ha_api` from `/api/status`: a server that doesn't report it (or reports an older one) raises the "server too old" repair, and a newer one raises a new "server is newer than this integration" repair.
- Changing or removing an item that was already removed, cleared or merged in Quartermaster refreshes the list instead of failing.
- Diagnostics show the Home Assistant API version and whether the server ID is in use.

## [0.2.0] - 2026-10-08

### Added

- Reconfigure: change the server address, token or certificate checking without removing the integration.
- "Verify SSL certificate" option, for servers with a self-signed certificate.
- Shoppers sensor: how many household members are on a shopping trip.
- Live updates binary sensor (diagnostic, disabled by default): whether the event stream is connected.
- Repairs for a server that has been unreachable for 30 minutes, for live updates blocked by a reverse proxy, and for a server older than this integration supports.
- The device shows the Quartermaster version, and follows server upgrades and household renames after a reconnect.
- Translated error messages for every failed action and setup problem; refused changes (empty item, unknown item) are validation errors.
- Diagnostics now include the server version, presence counts, wrapped intents and open repairs, and redact the server address and household name.
- Brand images (icon and logo) shipped with the integration.
- `quality_scale.yaml` documenting how the integration meets the Home Assistant Integration Quality Scale (Platinum).

### Changed

- The API client is a standalone, fully typed async package (`api/`) on Home Assistant's shared HTTP session.
- Setup checks that the address is a Quartermaster server and that the server has been set up.
- 100% test coverage, strict mypy, and CI on the oldest supported and the current Home Assistant.

## [0.1.0] - 2026-10-08

### Added

- First release: the household list as a to-do entity, live updates over the event stream with polling fallback, voice attribution for satellites and users, `quartermaster_*` events, reauthentication and diagnostics.

[Unreleased]: https://github.com/quartermaster-app/ha-quartermaster/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/quartermaster-app/ha-quartermaster/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/quartermaster-app/ha-quartermaster/releases/tag/v0.2.0
