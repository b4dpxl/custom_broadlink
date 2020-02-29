"""Support for Broadlink RM devices."""
import binascii
from datetime import timedelta
import logging
import socket

from . import broadlink
import voluptuous as vol

from homeassistant.components.switch import DOMAIN, PLATFORM_SCHEMA, SwitchDevice
from homeassistant.const import (
    CONF_COMMAND_OFF,
    CONF_COMMAND_ON,
    CONF_FRIENDLY_NAME,
    CONF_HOST,
    CONF_MAC,
    CONF_SWITCHES,
    CONF_TIMEOUT,
    CONF_TYPE,
    STATE_ON,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import Throttle, slugify

from . import async_setup_service, data_packet

_LOGGER = logging.getLogger(__name__)

TIME_BETWEEN_UPDATES = timedelta(seconds=5)

DEFAULT_NAME = "Broadlink switch"
DEFAULT_TIMEOUT = 10
DEFAULT_RETRY = 2
CONF_SLOTS = "slots"
CONF_RETRY = "retry"

RM_TYPES = [
    "rm",
    "rm2",
    "rm_mini",
    "rm_pro_phicomm",
    "rm2_home_plus",
    "rm2_home_plus_gdt",
    "rm2_pro_plus",
    "rm2_pro_plus2",
    "rm2_pro_plus_bl",
    "rm_mini_shate",
]
SP1_TYPES = ["sp1"]
SP2_TYPES = ["sp2", "honeywell_sp2", "sp3", "spmini2", "spminiplus"]
MP1_TYPES = ["mp1"]
BG1_TYPES = ["bg1"]

SWITCH_TYPES = RM_TYPES + SP1_TYPES + SP2_TYPES + MP1_TYPES + BG1_TYPES

SWITCH_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_COMMAND_OFF): data_packet,
        vol.Optional(CONF_COMMAND_ON): data_packet,
        vol.Optional(CONF_FRIENDLY_NAME): cv.string,
    }
)

MP1_SWITCH_SLOT_SCHEMA = vol.Schema(
    {
        vol.Optional("slot_1"): cv.string,
        vol.Optional("slot_2"): cv.string,
        vol.Optional("slot_3"): cv.string,
        vol.Optional("slot_4"): cv.string,
    }
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_SWITCHES, default={}): cv.schema_with_slug_keys(
            SWITCH_SCHEMA
        ),
        vol.Optional(CONF_SLOTS, default={}): MP1_SWITCH_SLOT_SCHEMA,
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_MAC): cv.string,
        vol.Optional(CONF_FRIENDLY_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_TYPE, default=SWITCH_TYPES[0]): vol.In(SWITCH_TYPES),
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
        vol.Optional(CONF_RETRY, default=DEFAULT_RETRY): cv.positive_int,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Broadlink switches."""

    devices = config.get(CONF_SWITCHES)
    slots = config.get("slots", {})
    ip_addr = config.get(CONF_HOST)
    friendly_name = config.get(CONF_FRIENDLY_NAME)
    mac_addr = binascii.unhexlify(config.get(CONF_MAC).encode().replace(b":", b""))
    switch_type = config.get(CONF_TYPE)
    retry_times = config.get(CONF_RETRY)

    def _get_mp1_slot_name(switch_friendly_name, slot):
        """Get slot name."""
        if not slots[f"slot_{slot}"]:
            return f"{switch_friendly_name} slot {slot}"
        return slots[f"slot_{slot}"]

    if switch_type in RM_TYPES:
        broadlink_device = broadlink.rm((ip_addr, 80), mac_addr, None)
        hass.add_job(async_setup_service, hass, ip_addr, broadlink_device)

        switches = []
        for object_id, device_config in devices.items():
            switches.append(
                BroadlinkRMSwitch(
                    object_id,
                    device_config.get(CONF_FRIENDLY_NAME, object_id),
                    broadlink_device,
                    device_config.get(CONF_COMMAND_ON),
                    device_config.get(CONF_COMMAND_OFF),
                    retry_times,
                )
            )
    elif switch_type in SP1_TYPES:
        broadlink_device = broadlink.sp1((ip_addr, 80), mac_addr, None)
        switches = [BroadlinkSP1Switch(friendly_name, broadlink_device, retry_times)]
    elif switch_type in SP2_TYPES:
        broadlink_device = broadlink.sp2((ip_addr, 80), mac_addr, None)
        switches = [BroadlinkSP2Switch(friendly_name, broadlink_device, retry_times)]
    elif switch_type in MP1_TYPES:
        switches = []
        broadlink_device = broadlink.mp1((ip_addr, 80), mac_addr, None)
        parent_device = BroadlinkMP1Switch(broadlink_device, retry_times)
        for i in range(1, 5):
            slot = BroadlinkMP1Slot(
                _get_mp1_slot_name(friendly_name, i),
                broadlink_device,
                i,
                parent_device,
                retry_times,
            )
            switches.append(slot)
    elif switch_type in BG1_TYPES:
        broadlink_device = broadlink.bg1((ip_addr, 80), mac_addr, None)
        switches = []
        parent_device = BroadlinkBG1Switch(broadlink_device, retry_times)
        switches.append(BroadlinkBG1Slot(friendly_name + " left", broadlink_device, 1, parent_device, retry_times))
        switches.append(BroadlinkBG1Slot(friendly_name + " right", broadlink_device, 2, parent_device, retry_times))

    broadlink_device.timeout = config.get(CONF_TIMEOUT)
    try:
        broadlink_device.auth()
    except OSError:
        _LOGGER.error("Failed to connect to device")

    add_entities(switches)


class BroadlinkRMSwitch(SwitchDevice, RestoreEntity):
    """Representation of an Broadlink switch."""

    def __init__(
        self, name, friendly_name, device, command_on, command_off, retry_times
    ):
        """Initialize the switch."""
        self.entity_id = f"{DOMAIN}.{slugify(name)}"
        self._name = friendly_name
        self._state = False
        self._command_on = command_on
        self._command_off = command_off
        self._device = device
        self._is_available = False
        self._retry_times = retry_times
        _LOGGER.debug("_retry_times : %s", self._retry_times)

    async def async_added_to_hass(self):
        """Call when entity about to be added to hass."""
        await super().async_added_to_hass()
        state = await self.async_get_last_state()
        if state:
            self._state = state.state == STATE_ON

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return True

    @property
    def available(self):
        """Return True if entity is available."""
        return not self.should_poll or self._is_available

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state

    def turn_on(self, **kwargs):
        """Turn the device on."""
        if self._sendpacket(self._command_on, self._retry_times):
            self._state = True
            self.schedule_update_ha_state()

    def turn_off(self, **kwargs):
        """Turn the device off."""
        if self._sendpacket(self._command_off, self._retry_times):
            self._state = False
            self.schedule_update_ha_state()

    def _sendpacket(self, packet, retry):
        """Send packet to device."""
        if packet is None:
            _LOGGER.debug("Empty packet")
            return True
        try:
            self._device.send_data(packet)
        except (ValueError, OSError) as error:
            if retry < 1:
                _LOGGER.error("Error during sending a packet: %s", error)
                return False
            if not self._auth(self._retry_times):
                return False
            return self._sendpacket(packet, retry - 1)
        return True

    def _auth(self, retry):
        _LOGGER.debug("_auth : retry=%s", retry)
        try:
            auth = self._device.auth()
        except OSError:
            auth = False
            if retry < 1:
                _LOGGER.error("Timeout during authorization")
        if not auth and retry > 0:
            return self._auth(retry - 1)
        return auth


class BroadlinkSP1Switch(BroadlinkRMSwitch):
    """Representation of an Broadlink switch."""

    def __init__(self, friendly_name, device, retry_times):
        """Initialize the switch."""
        super().__init__(friendly_name, friendly_name, device, None, None, retry_times)
        self._command_on = 1
        self._command_off = 0
        self._load_power = None

    def _sendpacket(self, packet, retry):
        """Send packet to device."""
        try:
            self._device.set_power(packet)
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error("Error during sending a packet: %s", error)
                return False
            if not self._auth(self._retry_times):
                return False
            return self._sendpacket(packet, retry - 1)
        return True


class BroadlinkSP2Switch(BroadlinkSP1Switch):
    """Representation of an Broadlink switch."""

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def current_power_w(self):
        """Return the current power usage in Watt."""
        try:
            return round(self._load_power, 2)
        except (ValueError, TypeError):
            return None

    def update(self):
        """Synchronize state with switch."""
        self._update(self._retry_times)

    def _update(self, retry):
        """Update the state of the device."""
        _LOGGER.debug("_update : retry=%s", retry)
        try:
            state = self._device.check_power()
            load_power = self._device.get_energy()
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error("Error during updating the state: %s", error)
                self._is_available = False
                return
            if not self._auth(self._retry_times):
                return
            return self._update(retry - 1)
        if state is None and retry > 0:
            return self._update(retry - 1)
        self._state = state
        self._load_power = load_power
        self._is_available = True


class BroadlinkMP1Slot(BroadlinkRMSwitch):
    """Representation of a slot of Broadlink switch."""

    def __init__(self, friendly_name, device, slot, parent_device, retry_times):
        """Initialize the slot of switch."""
        super().__init__(friendly_name, friendly_name, device, None, None, retry_times)
        self._command_on = 1
        self._command_off = 0
        self._slot = slot
        self._parent_device = parent_device

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    def _sendpacket(self, packet, retry):
        """Send packet to device."""
        try:
            self._device.set_power(self._slot, packet)
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error("Error during sending a packet: %s", error)
                self._is_available = False
                return False
            if not self._auth(self._retry_times):
                return False
            return self._sendpacket(packet, max(0, retry - 1))
        self._is_available = True
        return True

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    def update(self):
        """Trigger update for all switches on the parent device."""
        self._parent_device.update()
        self._state = self._parent_device.get_outlet_status(self._slot)
        if self._state is None:
            self._is_available = False
        else:
            self._is_available = True


class BroadlinkMP1Switch:
    """Representation of a Broadlink switch - To fetch states of all slots."""

    def __init__(self, device, retry_times):
        """Initialize the switch."""
        self._device = device
        self._states = None
        self._retry_times = retry_times

    def get_outlet_status(self, slot):
        """Get status of outlet from cached status list."""
        if self._states is None:
            return None
        return self._states[f"s{slot}"]

    @Throttle(TIME_BETWEEN_UPDATES)
    def update(self):
        """Fetch new state data for this device."""
        self._update(self._retry_times)

    def _update(self, retry):
        """Update the state of the device."""
        try:
            states = self._device.check_power()
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error("Error during updating the state: %s", error)
                return
            if not self._auth(self._retry_times):
                return
            return self._update(max(0, retry - 1))
        if states is None and retry > 0:
            return self._update(max(0, retry - 1))
        self._states = states

    def _auth(self, retry):
        """Authenticate the device."""
        try:
            auth = self._device.auth()
        except OSError:
            auth = False
        if not auth and retry > 0:
            return self._auth(retry - 1)
        return auth


class BroadlinkBG1Slot(BroadlinkRMSwitch):
    """Representation of a slot of Broadlink switch."""

    def __init__(self, friendly_name, device, slot, parent_device, retry_times):
        """Initialize the slot of switch."""
        super().__init__(friendly_name, friendly_name, device, None, None, retry_times)
        self._command_on = 1
        self._command_off = 0
        self._slot = slot
        self._parent_device = parent_device

    @property
    def assumed_state(self):
        """Return true if unable to access real state of entity."""
        return False

    def turn_on(self, **kwargs):
        """Turn the device on."""
        self._turn_on_off(True)

    def turn_off(self, **kwargs):
        """Turn the device off."""
        self._turn_on_off(False)

    def _turn_on_off(self, state):
        if self._slot == 1:
            r = self._device.set_state(pwr1=int(state))
        else:
            r = self._device.set_state(pwr2=int(state))
        if r:
            _LOGGER.debug("Setting device state: " + str(r))
            self._state = int(state)
            self._parent_device.set_outlet_status(self._slot, int(state))
            self.schedule_update_ha_state()
        else:
            _LOGGER.warn("No response from switch")


    def _sendpacket(self, packet, retry):
        """Send packet to device."""
        try:
            self._device.set_power(self._slot, packet)
        except (socket.timeout, ValueError) as error:
            if retry < 1:
                _LOGGER.error("Error during sending a packet: %s", error)
                self._is_available = False
                return False
            if not self._auth(self._retry_times):
                return False
            return self._sendpacket(packet, max(0, retry - 1))
        self._is_available = True
        return True

    @property
    def should_poll(self):
        """Return the polling state."""
        return True

    @property
    def slot(self):
        return self._slot


    def update(self):
        """Trigger update for all switches on the parent device."""
        self._parent_device.update()
        self._state = self._parent_device.get_outlet_status(self._slot)
        if self._state is None:
            self._is_available = False
        else:
            self._is_available = True


class BroadlinkBG1Switch:
    """Representation of a Broadlink BG1 switch - To fetch states of all slots."""

    def __init__(self, device, retry_times):
        """Initialize the switch."""
        self._device = device
        self._states = None
        self._retry_times = retry_times

    def get_outlet_status(self, slot):
        """Get status of outlet from cached status list."""
        if self._states is None:
            return None
        return self._states[f"s{slot}"]

    def set_outlet_status(self, slot, status):
        """Get status of outlet from cached status list."""
        if self._states is not None:
            self._states[f"s{slot}"] = status

    @Throttle(TIME_BETWEEN_UPDATES)
    def update(self):
        """Fetch new state data for this device."""
        _LOGGER.debug("Polling:")
        self._update(self._retry_times)

    def _update(self, retry):
        """Update the state of the device."""
        states = None
        try:
            # states = self._device.check_power()
            resp = self._device.get_state()
            if resp is not None:
                states = {
                    "s1": resp["pwr1"],  # Left
                    "s2": resp["pwr2"]   # Right
                }
                _LOGGER.debug(states)
        except (socket.timeout, ValueError) as error:
            _LOGGER.debug(f"Polling timeout, trying again: {retry}")
            if retry < 1:
                _LOGGER.error("Error during updating the state: %s", error)
                self._states = None
                return
            if not self._auth(self._retry_times):
                _LOGGER.error("Auth failed: %s", error)
                self._states = None
                return
            return self._update(max(0, retry - 1))
        if states is None and retry > 0:
            return self._update(max(0, retry - 1))
        self._states = states

    def _auth(self, retry):
        """Authenticate the device."""
        try:
            auth = self._device.auth()
        except OSError:
            auth = False
        if not auth and retry > 0:
            return self._auth(retry - 1)
        return auth
