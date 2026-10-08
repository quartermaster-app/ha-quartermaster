<img src="custom_components/quartermaster/brand/icon.png" alt="Quartermaster" width="80" align="right">

# Quartermaster for Home Assistant

[![Validate](https://github.com/quartermaster-app/ha-quartermaster/actions/workflows/validate.yml/badge.svg)](https://github.com/quartermaster-app/ha-quartermaster/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/quartermaster-app/ha-quartermaster)](https://github.com/quartermaster-app/ha-quartermaster/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Puts your [Quartermaster](https://github.com/quartermaster-app) household shopping list into Home Assistant as a to-do list, so anyone in the house can say "add milk to the shopping list" to a voice satellite and have it land in Quartermaster.

Quartermaster is a self-hosted shopping list: one shared household list that it turns into sorted lists per store. It never depends on Home Assistant. This integration is a way in for voice and automations; store views, sorting and caveats stay in the Quartermaster app.

## Contents

- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Voice: "add milk to the shopping list"](#voice-add-milk-to-the-shopping-list)
- [How items map](#how-items-map)
- [Attribution](#attribution)
- [Events](#events)
- [Use cases and examples](#use-cases-and-examples)
- [How data is updated](#how-data-is-updated)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Removal](#removal)
- [Development](#development)

## What you get

Each Quartermaster server you connect becomes one device, named after your household (or "Quartermaster" if the household has no name), with:

| Entity | Example ID | What it is |
| --- | --- | --- |
| To-do list | `todo.maple_street` | The household list. Add, rename, check off and remove items from Home Assistant, Assist, or automations. Its state is the number of open items. |
| Shoppers sensor | `sensor.maple_street_shoppers` | How many household members are on a shopping trip right now. |
| Live updates (diagnostic, disabled by default) | `binary_sensor.maple_street_live_updates` | On while the integration is connected to Quartermaster's event stream; off while it falls back to polling. |

And:

- Adds from a voice satellite are credited to the satellite's area ("Kitchen"), and adds by a logged-in Home Assistant user to that user, so the Quartermaster app shows who added what (see [Attribution](#attribution)).
- Events for automations: `quartermaster_request_added`, `quartermaster_trip_started`, `quartermaster_trip_ended` (see [Events](#events)).
- Repairs when something needs your attention: the server has been unreachable for 30 minutes, live updates are blocked (usually by a reverse proxy), or the server is too old or too new for this version of the integration.
- Diagnostics you can attach to an issue, with the token, server address, household name and item text removed.

## Requirements

- Home Assistant 2026.9 or later. Tested on 2026.9.4 and 2026.10.0.
- A Quartermaster server that speaks Home Assistant API 1 (`ha_api` in `/api/status`; any server from October 2026 on), reachable from Home Assistant. Older servers still work, but show a repair asking you to update.
- An admin account in Quartermaster, to create the Home Assistant token.

## Installation

### HACS (recommended)

The integration isn't in the HACS default list yet, so add it as a custom repository:

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=quartermaster-app&repository=ha-quartermaster&category=integration)

Or by hand:

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Repository: `https://github.com/quartermaster-app/ha-quartermaster`, type **Integration**. Choose **Add**.
3. Search HACS for **Quartermaster**, open it, choose **Download**, and restart Home Assistant.

HACS offers new releases as updates.

### Manual

1. Download `quartermaster.zip` from the [latest release](https://github.com/quartermaster-app/ha-quartermaster/releases/latest).
2. Unzip it into `config/custom_components/quartermaster/`, so you end up with `config/custom_components/quartermaster/manifest.json`.
3. Restart Home Assistant.

## Configuration

1. In Quartermaster, open **Settings** and choose **New Home Assistant token** (admins only). Copy the token: it starts with `qm_` and is shown once.
2. In Home Assistant, go to **Settings → Devices & services → Add integration** and choose **Quartermaster**, or use this button:

   [![Open your Home Assistant instance and start setting up Quartermaster.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=quartermaster)

3. Fill in the form:

| Field | What to enter |
| --- | --- |
| Server address | The address you open Quartermaster at in a browser, starting with `http://` or `https://`, for example `https://shopping.example.com`. |
| Home Assistant token | The `qm_...` token from step 1. It must be a Home Assistant token; device tokens are refused. |
| Verify SSL certificate | Leave on. Turn off only for an `https://` server with a self-signed certificate. |

Home Assistant checks that it can reach the server and read the list with the token before it saves anything. Each server can be added once: Quartermaster reports a stable server ID, so the same server under a second address (say, a LAN IP and a public name) is recognized and refused. There are no further options.

**Moving the server or rotating the token**: open **Settings → Devices & services → Quartermaster**, choose the three-dot menu on the entry, and choose **Reconfigure**. Leave the token empty to keep the current one. The new address must be the same Quartermaster server (same server ID); a different server is refused, so add it as a new entry instead. A server restored from a backup keeps its ID; a freshly set-up one gets a new ID.

**Revoked token**: if Quartermaster stops accepting the token, the integration asks for a new one (a **Reconfigure** or **Re-authenticate** prompt on the integration card). Create a new token in Quartermaster and paste it.

## Voice: "add milk to the shopping list"

Assist has two ways to add something to a list, and they behave differently:

| Sentence | Intent | Where it goes |
| --- | --- | --- |
| "add milk to the **Maple Street** list", "put milk on my **Maple Street** list" | `HassListAddItem` | The to-do entity whose name (or alias) matches |
| "add milk to the list", "add milk to my shopping list" | `HassShoppingListAddItem` | Home Assistant's built-in **Shopping list** integration, always |

When a to-do entity is named "Shopping List", "add milk to my shopping list" matches `HassListAddItem` by name, and that wins. Bare "add milk to the list" still means the built-in list.

To send "add milk to the shopping list" to Quartermaster:

1. **Rename the Quartermaster list.** Settings → Devices & services → Entities → open the Quartermaster to-do entity (`todo.<household>`) → cog icon → set **Name** to `Shopping List` → **Update**. Only the name changes; the entity ID can stay. Alternatively, add `Shopping List` as an alias under **Voice assistants** on the same dialog.
2. **Deal with the built-in Shopping list**, if you have it. Two lists with the same name make Assist answer "Sorry, there are multiple devices called Shopping List". Either:
   - **Remove it** (recommended): Settings → Devices & services → Shopping list → three-dot menu → **Delete**. Afterwards "add milk to the list" (without "shopping") gets "Unknown intent", so get used to saying "shopping list".
   - **Or rename it**: open its entity (`todo.shopping_list`) and rename it, for example to `Notes`. Then "add milk to the shopping list" goes to Quartermaster and "add milk to the list" goes to the built-in one, which is easy to mix up.
3. **Check it's exposed**: Settings → Voice assistants → Expose. To-do lists are exposed by default; if you turned off "expose new entities", expose the Quartermaster list.
4. **Test it**: Settings → Voice assistants → Assist → type `add milk to the shopping list`. Assist answers "Added milk" and the item shows up in Quartermaster.

Checked against a real Home Assistant with this integration and a real Quartermaster server:

| Setup | "add bread to my shopping list" | "add tea to the list" |
| --- | --- | --- |
| Quartermaster list named after the household, no built-in list | Unknown intent | Unknown intent |
| Quartermaster list renamed to Shopping List, no built-in list | Quartermaster | Unknown intent |
| Quartermaster list renamed, built-in list also called Shopping list | "multiple devices called Shopping List" | built-in list |
| Quartermaster list renamed, built-in list renamed to Notes | Quartermaster | built-in list |

LLM conversation agents (OpenAI, Anthropic, Ollama and others with Assist control) call the same `HassListAddItem` intent with a list name, so the rename helps them too.

Quartermaster parses what you say like typing in the app: "add 2 dozen eggs to the shopping list" adds Eggs with a quantity of 2 dozen, and "milk, eggs and bread" becomes three items when each one is known.

## How items map

| Home Assistant | Quartermaster |
| --- | --- |
| Item | One request. The item's `uid` is the request ID |
| Summary | The request text |
| Description | Quantity, what's left, store limits, notes ("2 of 4 left · Costco only"). Read only |
| Needs action | Open or partly bought |
| Completed | Bought, not yet cleared |
| Add item (`todo.add_item`, "add ... to the list") | Adds by free text, credited to the satellite or user when known |
| Rename (`todo.update_item` with `rename`) | Changes this request's wording, never the catalog item |
| Check off (`todo.update_item` with `status: completed`, "check off ...") | Marks it handled (a purchase with no store) |
| Uncheck (`status: needs_action`) | Voids the purchases Home Assistant made |
| Remove (`todo.remove_item`) | Cancels an open item, clears a completed one |
| `todo.remove_completed_items` | Clears every bought item |

Due dates and descriptions set in Home Assistant aren't stored. Changing or removing an item that someone already removed, cleared or merged in the app isn't an error: the list just refreshes. If an action fails, Home Assistant shows why: a change Quartermaster refuses (for example an empty item) as a validation error, a server that can't be reached or answers with an error as a failure.

## Attribution

Quartermaster shows who added each item. The integration sends `via: {kind: "satellite", name}` or `via: {kind: "user", name}` when it can tell, and nothing otherwise (the add is then credited to the Home Assistant token).

Home Assistant doesn't tell a to-do entity who called it: `async_create_todo_item` only receives the item. What's possible:

| How the item was added | Credited to |
| --- | --- |
| Voice satellite (Voice PE, ESPHome, Wyoming) through Assist | The satellite's area ("Kitchen"), or its device name if it has no area |
| Assist typed or spoken in the Home Assistant app or web UI by a logged-in user | That user's name |
| LLM agent tool call from a satellite | The satellite device's area or name (LLM tool calls pass the device but not the satellite entity) |
| `todo.add_item` from the UI or the to-do card | The logged-in user |
| `todo.add_item` from an automation or script | Nothing (automations have no user) |

How it works: Assist runs the `HassListAddItem` and `HassListCompleteItem` intents, and the intent carries `satellite_id`, `device_id` and the user's context. The integration wraps those two intent handlers so the to-do entity can see the intent while it handles the call. The wrapper stores the intent in a context variable and then calls Home Assistant's own handler unchanged: other to-do lists behave exactly as before, and unloading the last Quartermaster entry restores the original handlers. For actions, Home Assistant sets the caller's context on the entity just before the call, and the integration reads the user from it. Diagnostics list which intents are currently wrapped.

## Events

Each event carries the same data as Quartermaster's event stream:

| Event | Data |
| --- | --- |
| `quartermaster_request_added` | `request_id`, `text`, `actor` (`id`, `kind`, `name`, `user_id`), `possible_duplicate`, `duplicate_of`, `shoppers`: people on a trip at a store that carries the item (`user_id`, `name`, `store_id`, `store`, `last_sync_at`) |
| `quartermaster_trip_started`, `quartermaster_trip_ended` | `trip_id`, `user_id`, `user`, `store_id`, `store`, `open_count` |

`shoppers` never includes the person who added the item. `last_sync_at` is when that shopper's phone last synced; if it's old, they may not see the new item until they open the app. Events only fire while live updates are connected.

## Use cases and examples

- Add to the list by voice from any satellite, credited to the room it heard you in.
- Tell whoever is at the store that something was just added, or warn that their phone hasn't synced.
- Announce when someone starts shopping, so the house can shout out last-minute items.
- Show the list on a dashboard with the to-do list card, or show who's shopping with the Shoppers sensor.

### Tell the kitchen who's at the store

When someone adds an item while another household member is shopping, announce it on the kitchen satellite. Shoppers whose phone hasn't synced in five minutes "may not see this".

```yaml
alias: Quartermaster - announce shoppers on new items
mode: queued
triggers:
  - trigger: event
    event_type: quartermaster_request_added
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.shoppers | count > 0 }}"
actions:
  - variables:
      message: >-
        {% set ns = namespace(lines=[]) %}
        {% for s in trigger.event.data.shoppers %}
          {% set synced = s.last_sync_at and as_datetime(s.last_sync_at) %}
          {% set stale = not synced or (now() - synced).total_seconds() > 300 %}
          {% set ns.lines = ns.lines + [s.name ~ ' is at ' ~ s.store ~ (' and may not see this' if stale else ' and will see it')] %}
        {% endfor %}
        {{ ns.lines | join('. ') }}.
  - action: assist_satellite.announce
    target:
      entity_id: assist_satellite.kitchen
    data:
      message: "{{ message }}"
```

Result: "Sam is at Costco and may not see this."

### Possible duplicate

Quartermaster flags an add as a possible duplicate when the item is already on the list, and asks the household to resolve it in the app.

```yaml
alias: Quartermaster - possible duplicate
triggers:
  - trigger: event
    event_type: quartermaster_request_added
conditions:
  - condition: template
    value_template: "{{ trigger.event.data.possible_duplicate }}"
actions:
  - action: assist_satellite.announce
    target:
      entity_id: assist_satellite.kitchen
    data:
      message: >-
        {{ trigger.event.data.text | capitalize }} was already on the list.
        Check the Quartermaster app.
```

### Someone started shopping

```yaml
alias: Quartermaster - trip started
triggers:
  - trigger: event
    event_type: quartermaster_trip_started
actions:
  - action: notify.notify
    data:
      message: >-
        {{ trigger.event.data.user }} is at {{ trigger.event.data.store }},
        {{ trigger.event.data.open_count }} items on the list there.
```

### Add from an automation

```yaml
actions:
  - action: todo.add_item
    target:
      entity_id: todo.maple_street
    data:
      item: 2 dozen eggs
```

## How data is updated

The integration keeps a long-lived connection to Quartermaster's event stream (`/api/events`). Every change in Quartermaster sends a `changed` event and the integration re-reads the list right away, so changes show up within a second. Presence (who is shopping) and the forwarded events arrive the same way.

If the stream drops, the integration polls the list (and presence) every 60 seconds and keeps reconnecting with backoff (1 second up to about a minute and a half). As soon as the stream is back, polling stops. While the server can't be reached at all, the list and the Shoppers sensor are unavailable.

After each reconnect the integration also re-reads the server's version and household name, so an upgraded or renamed server shows up on the device.

## Known limitations

- Descriptions and due dates set in Home Assistant aren't saved; Quartermaster computes the description itself.
- Removing items isn't attributed (Quartermaster's delete takes no attribution).
- A satellite's identity is its area or device, never the person speaking. Home Assistant has no speaker recognition.
- Attribution relies on wrapping two of Home Assistant's intent handlers. If Home Assistant changes how those are registered, adds keep working but stop being attributed.
- "add milk to the list" (without a list name) always goes to Home Assistant's built-in Shopping list; see [Voice](#voice-add-milk-to-the-shopping-list).
- Quartermaster servers aren't discovered automatically; you enter the address.
- Servers older than October 2026 don't report a server ID; for them the address identifies the server, and reconfigure can't confirm a move. Entries switch to the server ID automatically once the server is updated.
- Store views, store placement, caveats and trips are managed in the Quartermaster app, not in Home Assistant.

## Troubleshooting

- **Repairs**: Settings → System → Repairs shows:
  - *Quartermaster server unreachable*: Home Assistant couldn't reach the server for 30 minutes. Check that it's running; if it moved, use **Reconfigure**.
  - *Quartermaster live updates aren't working*: the server answers, but the event stream hasn't connected for 15 minutes. This is almost always a reverse proxy buffering or closing `text/event-stream` responses on `/api/events`. Turn off response buffering for that path (for nginx, `proxy_buffering off;`; Traefik and Caddy usually stream it already) and allow idle connections for at least 60 seconds. Quartermaster sends a comment every 25 seconds and the integration reconnects if it hears nothing for 60.
  - *Quartermaster server is too old*: the server doesn't speak this integration's Home Assistant API (or predates API versioning). Update Quartermaster.
  - *Quartermaster server is newer than this integration*: the server speaks a newer Home Assistant API. Update this integration in HACS.

  Each one clears itself when the problem is fixed.
- **"Couldn't reach Quartermaster" during setup**: use the address you'd type in a browser on the Home Assistant machine. From a Home Assistant container, `localhost` is the container itself, not the host.
- **"Quartermaster rejected the token"**: make sure it's a Home Assistant token (Settings → New Home Assistant token in Quartermaster), not a device token, and that it wasn't revoked.
- **"That address answers, but it isn't a Quartermaster server"**: the address points at something else (a proxy error page, another app). Open it in a browser and check you see Quartermaster.
- **Adds aren't credited to the satellite**: download diagnostics and check `attribution.wrapped_intents` lists `HassListAddItem` and `HassListCompleteItem`. With debug logs on, each add logs who it was credited to (`Adding 'Milk' via satellite:Kitchen`).
- **Diagnostics**: Settings → Devices & services → Quartermaster → three-dot menu → **Download diagnostics**. Shows server version, stream state, item counts, polling, attribution and open repairs. No token, address, household name or item text.
- **Debug logs**: Settings → Devices & services → Quartermaster → **Enable debug logging**, or:

  ```yaml
  logger:
    logs:
      custom_components.quartermaster: debug
  ```

## Removal

1. Settings → Devices & services → Quartermaster → three-dot menu on the entry → **Delete**. This removes the device and entities; nothing in Quartermaster changes.
2. If you installed with HACS: HACS → Quartermaster → three-dot menu → **Remove**, then restart Home Assistant. If you installed manually, delete `config/custom_components/quartermaster` and restart.
3. Optionally revoke the token in Quartermaster under **Settings**.

## Development

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and need Python 3.14:

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
.venv/bin/python -m pytest --cov            # 100% line and branch coverage required
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy                              # strict
```

The API client in `custom_components/quartermaster/api/` has no Home Assistant imports. `scripts/e2e_client.py` exercises it against a real, freshly set-up Quartermaster server (needs only `aiohttp`):

```sh
# in the quartermaster repository
QM_DATA_DIR=$(mktemp -d) PORT=8098 QM_FETCH_DICTIONARY=0 node packages/server/src/main.ts
# here
python scripts/e2e_client.py http://localhost:8098
```

CI runs hassfest, HACS validation, ruff, mypy, and the tests on the oldest supported and the current Home Assistant. See [CONTRIBUTING.md](CONTRIBUTING.md) for releases and [quality_scale.yaml](custom_components/quartermaster/quality_scale.yaml) for how the integration meets Home Assistant's Integration Quality Scale.

## License

MIT. See [LICENSE](LICENSE).
