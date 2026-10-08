"""Constants for the Quartermaster integration."""

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "quartermaster"
LOGGER = logging.getLogger(__package__)

DEFAULT_NAME: Final = "Quartermaster"
MANUFACTURER: Final = "Quartermaster"

# The Home Assistant API version (`ha_api` in /api/status) this integration speaks.
# Servers bump it only on breaking changes.
SUPPORTED_HA_API: Final = 1

# Polling only runs while the event stream is down.
FALLBACK_POLL_INTERVAL: Final = timedelta(seconds=60)

STREAM_BACKOFF_MIN: Final = 1.0
STREAM_BACKOFF_MAX: Final = 60.0

# Repair issues are raised only after a problem has lasted this long, so a
# server restart or a short outage never shows up in Repairs.
SERVER_UNREACHABLE_AFTER: Final = timedelta(minutes=30)
STREAM_UNAVAILABLE_AFTER: Final = timedelta(minutes=15)

ISSUE_SERVER_UNREACHABLE: Final = "server_unreachable"
ISSUE_STREAM_UNAVAILABLE: Final = "stream_unavailable"
ISSUE_UNSUPPORTED_VERSION: Final = "unsupported_server_version"
ISSUE_INCOMPATIBLE_SERVER: Final = "incompatible_server"

# Server events re-fired on the Home Assistant bus as quartermaster_<name>.
FORWARDED_EVENTS: Final = ("request_added", "trip_started", "trip_ended")
EVENT_PREFIX: Final = f"{DOMAIN}_"
