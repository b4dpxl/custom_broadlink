"""The broadlink component."""
import asyncio
from base64 import b64decode, b64encode
from binascii import unhexlify
from datetime import timedelta
import logging
import re
import socket

import voluptuous as vol

from homeassistant.const import CONF_HOST
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.util.dt import utcnow

from .const import CONF_PACKET, DOMAIN, SERVICE_LEARN, SERVICE_SEND

_LOGGER = logging.getLogger(__name__)

DEFAULT_RETRY = 3


def data_packet(value):
    """Decode a data packet given for broadlink."""
    value = cv.string(value)
    extra = len(value) % 4
    if extra > 0:
        value = value + ("=" * (4 - extra))
    return b64decode(value)


def hostname(value):
    """Validate a hostname."""
    host = str(value).lower()
    if len(host) > 253:
        raise ValueError
    if host[-1] == ".":
        host = host[:-1]
    allowed = re.compile(r"(?!-)[a-z\d-]{1,63}(?<!-)$")
    if not all(allowed.match(elem) for elem in host.split(".")):
        raise ValueError
    return host


def mac_address(value):
    """Validate and coerce a 48-bit MAC address."""
    mac = str(value).lower()
    if len(mac) == 17:
        mac = mac[0:2] + mac[3:5] + mac[6:8] + mac[9:11] + mac[12:14] + mac[15:17]
    elif len(mac) == 14:
        mac = mac[0:2] + mac[2:4] + mac[5:7] + mac[7:9] + mac[10:12] + mac[12:14]
    elif len(mac) != 12:
        raise ValueError
    return unhexlify(mac)


SERVICE_SEND_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_PACKET): vol.All(cv.ensure_list, [data_packet]),
    }
)

SERVICE_LEARN_SCHEMA = vol.Schema({vol.Required(CONF_HOST): cv.string})


@callback
def async_setup_service(hass, host, device):
    """Register a device for given host for use in services."""
    hass.data.setdefault(DOMAIN, {})[host] = device

    if hass.services.has_service(DOMAIN, SERVICE_LEARN):
        return

    async def _learn_command(call):
        """Learn a packet from remote."""

        device = hass.data[DOMAIN][call.data[CONF_HOST]]

        for retry in range(DEFAULT_RETRY):
            try:
                await hass.async_add_executor_job(device.enter_learning)
                break
            except (socket.timeout, ValueError):
                try:
                    await hass.async_add_executor_job(device.auth)
                except socket.timeout:
                    if retry == DEFAULT_RETRY - 1:
                        _LOGGER.error("Failed to enter learning mode")
                        return

        _LOGGER.info("Press the key you want Home Assistant to learn")
        start_time = utcnow()
        while (utcnow() - start_time) < timedelta(seconds=20):
            packet = await hass.async_add_executor_job(device.check_data)
            if packet:
                data = b64encode(packet).decode("utf8")
                log_msg = f"Received packet is: {data}"
                _LOGGER.info(log_msg)
                hass.components.persistent_notification.async_create(
                    log_msg, title="Broadlink switch"
                )
                return
            await asyncio.sleep(1)
        _LOGGER.error("No signal was received")
        hass.components.persistent_notification.async_create(
            "No signal was received", title="Broadlink switch"
        )

    hass.services.async_register(
        DOMAIN, SERVICE_LEARN, _learn_command, schema=SERVICE_LEARN_SCHEMA
    )

    async def _send_packet(call):
        """Send a packet."""
        device = hass.data[DOMAIN][call.data[CONF_HOST]]
        packets = call.data[CONF_PACKET]
        for packet in packets:
            for retry in range(DEFAULT_RETRY):
                try:
                    await hass.async_add_executor_job(device.send_data, packet)
                    break
                except (socket.timeout, ValueError):
                    try:
                        await hass.async_add_executor_job(device.auth)
                    except socket.timeout:
                        if retry == DEFAULT_RETRY - 1:
                            _LOGGER.error("Failed to send packet to device")

    hass.services.async_register(
        DOMAIN, SERVICE_SEND, _send_packet, schema=SERVICE_SEND_SCHEMA
    )
