"""Check the coordinator and platforms against a simulated Able account."""

from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import loader
from homeassistant.bootstrap import async_from_config_dict
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry

from custom_components.able_to_use import api
from custom_components.able_to_use.api import AbleAuthError, AbleClient, Login
from custom_components.able_to_use.const import DOMAIN
from custom_components.able_to_use.models import parse_device


def raw_action(device_id: str, action_id: str, name: str, code: str = "") -> dict:
    return {"uuid": action_id, "enabled": True, "device": {"uuid": device_id}, "static_action": {
        "name": name, "command_name": code, "is_available_for_user": True,
    }}


class IntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_config_flow_devices_entities_and_commands(self) -> None:
        raw_gate = {"uuid": "gate-uuid", "name": "Outer Gate", "type": "GATE", "open_status": "OPENED",
                    "open_perc": "30", "firmware": "test-firmware", "last_activity": "2026-01-02T10:15:00Z"}
        raw_roller = {"uuid": "roller-uuid", "name": "Side blind", "type": "SUNSHADING", "subtype": "BLINDS",
                      "open_perc": 44, "open": True, "favorite_position": "45"}
        raw_light = {"uuid": "light-uuid", "name": "Yard lights", "type": "DUAL_LIGHT", "status_rele_1": False,
                     "status_rele_2": True}
        devices = [raw_gate, raw_roller, raw_light]
        catalog = {
            "gate-uuid": [raw_action("gate-uuid", key, name) for key, name in (
                ("start-id", "Start"), ("stop-id", "Stop"), ("partial-id", "Partial Start"), ("open-id", "Open"),
                ("close-id", "Close"), ("pedestrian-id", "Pedestrian"),
            )],
            "roller-uuid": [raw_action("roller-uuid", "target-id", "Target position", "TARGET_POSITION")],
            "light-uuid": [raw_action("light-uuid", "on-id", "On", "RELE_ON"), raw_action("light-uuid", "off-id", "Off", "RELE_OFF")],
        }

        async def request(_session, method, url, **kwargs):
            if url == api.LOGIN_URL and method == "POST":
                return {"localId": "fixture-account", "refreshToken": "fixture-refresh", "idToken": "fixture-identity", "expiresIn": "3600"}
            if url == api.REFRESH_URL and method == "POST":
                return {"user_id": "fixture-account", "refresh_token": "fixture-rotated", "id_token": "fixture-new", "expires_in": "3600"}
            if url == f"{api.ABLE_URL}devices" and method == "GET":
                return [record.copy() for record in devices]
            if url.startswith(f"{api.ABLE_URL}actions/") and method == "GET":
                return catalog[url.rsplit("/", 1)[-1]]
            self.fail(f"Unexpected fixture request: {method} {url}")

        with TemporaryDirectory() as temp:
            source = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
            copytree(source, Path(temp) / "custom_components" / DOMAIN)
            hass = HomeAssistant(temp)
            loader.async_setup(hass)
            self.assertIs(await async_from_config_dict({}, hass), hass)
            try:
                with patch.object(api, "_request", side_effect=request), patch.object(api, "_perform_once") as command:
                    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
                    self.assertEqual(form["step_id"], "user")
                    result = await hass.config_entries.flow.async_configure(form["flow_id"], {
                        "email": "test@example.invalid", "password": "fixture-password", "project_key": "fixture-key",
                    })
                    self.assertEqual(result["type"], "create_entry")
                    entry = result["result"]
                    await hass.async_block_till_done()
                    self.assertIs(entry.state, ConfigEntryState.LOADED)
                    self.assertNotIn("password", entry.data)
                    self.assertEqual(entry.data["refresh_token"], "fixture-rotated")

                    registry = entity_registry.async_get(hass)
                    gate_id = registry.async_get_entity_id("cover", DOMAIN, "gate-uuid:cover")
                    state = hass.states.get(gate_id)
                    self.assertEqual(state.state, "open")
                    self.assertEqual(state.attributes["device_class"], "gate")
                    self.assertEqual(state.attributes["current_position"], 30)
                    self.assertIsNotNone(device_registry.async_get(hass).async_get_device(identifiers={(DOMAIN, "gate-uuid")}))
                    self.assertIsNotNone(registry.async_get_entity_id("button", DOMAIN, "gate-uuid:action:partial-id"))
                    self.assertIsNotNone(registry.async_get_entity_id("light", DOMAIN, "light-uuid:light:2"))
                    self.assertIsNotNone(registry.async_get_entity_id("binary_sensor", DOMAIN, "light-uuid:relay:2"))
                    self.assertIsNotNone(registry.async_get_entity_id("sensor", DOMAIN, "roller-uuid:sensor:favorite"))

                    coordinator = entry.runtime_data
                    await coordinator.async_run("gate-uuid", role="close")
                    command.assert_called_with("close-id", "fixture-new", {})
                    with self.assertRaises(HomeAssistantError):
                        await coordinator.async_run("gate-uuid", action_id="partial-id")
                    raw_gate.update(open_status="CLOSED", open_perc=0)
                    await coordinator.async_refresh()
                    self.assertEqual(hass.states.get(gate_id).state, "closed")
                    await coordinator.async_run("gate-uuid", action_id="partial-id")
                    command.assert_called_with("partial-id", "fixture-new", {})
                    raw_gate.update(open_status="OPENING", open_perc=12)
                    await coordinator.async_refresh()
                    await coordinator.async_run("gate-uuid", action_id="start-id")
                    command.assert_called_with("start-id", "fixture-new", {})
                    await coordinator.async_run("gate-uuid", role="stop")
                    command.assert_called_with("stop-id", "fixture-new", {})
                    await coordinator.async_run("roller-uuid", role="target", position=66)
                    command.assert_called_with("target-id", "fixture-new", {"parameter": 66})
                    await coordinator.async_run("light-uuid", role="off", relay=2)
                    command.assert_called_with("off-id", "fixture-new", {"rele": 2})
                    devices.remove(raw_gate)
                    await coordinator.async_refresh()
                    self.assertEqual(hass.states.get(gate_id).state, "unavailable")
            finally:
                await hass.async_stop(force=True)

    async def test_expired_command_is_not_replayed(self) -> None:
        client = AbleClient(Mock(), "fixture-key", "fixture-account", "fixture-refresh")
        client.use_login(Login("fixture-account", "fixture-refresh", "fixture-identity", 3600))
        with patch.object(api, "_perform_once", side_effect=AbleAuthError("rejected")) as command:
            with self.assertRaises(AbleAuthError):
                await client.async_perform("start-id")
            command.assert_called_once_with("start-id", "fixture-identity", None)

    async def test_rejected_read_refreshes_token_once(self) -> None:
        client = AbleClient(Mock(), "fixture-key", "fixture-account", "fixture-refresh")
        client.use_login(Login("fixture-account", "fixture-refresh", "fixture-identity", 3600))
        refresh = {"user_id": "fixture-account", "refresh_token": "rotated", "id_token": "renewed", "expires_in": 3600}
        with patch.object(api, "_request", new=AsyncMock(side_effect=[AbleAuthError("rejected"), refresh, []])) as request:
            self.assertEqual(await client.async_devices(), {})
            self.assertEqual(request.await_count, 3)
            self.assertEqual(request.await_args.kwargs["headers"]["Authorization"], "Bearer renewed")

    def test_action_permission_and_device_scope(self) -> None:
        gate = parse_device({"uuid": "gate", "type": "GATE"})
        scoped = raw_action("gate", "open-id", "Open")
        unrelated = raw_action("other-gate", "other-id", "Open")
        disabled = {**raw_action("gate", "denied-id", "Open"), "enabled": False}
        self.assertEqual(gate.with_actions([scoped, unrelated, disabled]).action_for("open").id, "open-id")
