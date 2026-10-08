"""Sensors for Quartermaster."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
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
    """Add the sensors."""
    async_add_entities([QuartermasterShoppersSensor(entry.runtime_data)])


class QuartermasterShoppersSensor(QuartermasterEntity, SensorEntity):
    """How many household members are on a shopping trip right now."""

    _attr_translation_key = "shoppers"

    def __init__(self, coordinator: QuartermasterCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "shoppers")

    @property
    def native_value(self) -> int:
        """Members currently shopping."""
        return self.coordinator.data.shoppers
