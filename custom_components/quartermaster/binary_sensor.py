"""Binary sensors for Quartermaster."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import QuartermasterConfigEntry, QuartermasterCoordinator
from .entity import QuartermasterEntity

# Coordinator-based: no per-entity requests.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QuartermasterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors."""
    async_add_entities([QuartermasterLiveUpdatesSensor(entry.runtime_data)])


class QuartermasterLiveUpdatesSensor(QuartermasterEntity, BinarySensorEntity):
    """On while the event stream is connected; off while the integration falls back to polling."""

    _attr_translation_key = "live_updates"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, "live_updates")

    @property
    def available(self) -> bool:
        """Always available: a server that can't be reached simply means off."""
        return True

    @property
    def is_on(self) -> bool:
        """True while the event stream is connected."""
        return self.coordinator.stream_connected
