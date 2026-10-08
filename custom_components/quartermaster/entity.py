"""Base entity for Quartermaster."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import QuartermasterCoordinator, device_name


class QuartermasterEntity(CoordinatorEntity[QuartermasterCoordinator]):
    """An entity on the household's device (one device per Quartermaster server)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: QuartermasterCoordinator, key: str | None) -> None:
        """Initialize the entity. `key` is None for the to-do list, which predates the others."""
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = entry_id if key is None else f"{entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=device_name(coordinator.status),
            manufacturer=MANUFACTURER,
            model="Household list",
            sw_version=coordinator.status.version,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=coordinator.client.url,
        )
