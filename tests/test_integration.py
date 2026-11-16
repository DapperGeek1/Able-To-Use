"""Check the coordinator and platforms against a simulated Able account."""

from asyncio import Event, create_task, sleep, wait_for
from collections import deque
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory
from time import monotonic
import unittest
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import loader
from homeassistant.bootstrap import async_from_config_dict
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry

from custom_components.able_to_use import api
from custom_components.able_to_use.api import AbleAuthError, AbleClient, Login
from custom_components.able_to_use.const import DEFAULT_PROJECT_KEY, DOMAIN
from custom_components.able_to_use.coordinator import ActionPoll
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
        login_keys = []
        refresh_keys = []
        gate_reports = deque()
        device_reads = []

        async def request(_session, method, url, **kwargs):
            if url == api.LOGIN_URL and method == "POST":
                login_keys.append(kwargs["params"]["key"])
                return {"localId": "fixture-account", "refreshToken": "fixture-refresh", "idToken": "fixture-identity", "expiresIn": "3600"}
            if url == api.REFRESH_URL and method == "POST":
                refresh_keys.append(kwargs["params"]["key"])
                return {"user_id": "fixture-account", "refresh_token": "fixture-rotated", "id_token": "fixture-new", "expires_in": "3600"}
            if url == f"{api.ABLE_URL}devices" and method == "GET":
                device_reads.append(monotonic())
                if gate_reports:
                    status, position = gate_reports.popleft()
                    raw_gate.update(open_status=status, open_perc=position)
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
                        "email": "test@example.invalid", "password": "fixture-password",
                    })
                    self.assertEqual(result["type"], "create_entry")
                    entry = result["result"]
                    await hass.async_block_till_done()
                    self.assertIs(entry.state, ConfigEntryState.LOADED)
                    self.assertNotIn("password", entry.data)
                    self.assertNotIn("project_key", entry.data)
                    self.assertEqual(entry.data["refresh_token"], "fixture-rotated")
                    self.assertEqual(login_keys, [DEFAULT_PROJECT_KEY])
                    self.assertEqual(refresh_keys, [DEFAULT_PROJECT_KEY])

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
                    self.assertEqual(coordinator.update_interval, timedelta(seconds=1))
                    motion_reported = Event()

                    @callback
                    def observe_motion() -> None:
                        if coordinator.data["gate-uuid"].state == "closing":
                            motion_reported.set()

                    unsubscribe = coordinator.async_add_listener(observe_motion)
                    raw_gate.update(open_status="CLOSING", open_perc=20)
                    try:
                        await wait_for(motion_reported.wait(), timeout=2.5)
                    finally:
                        unsubscribe()
                    self.assertEqual(hass.states.get(gate_id).state, "closing")
                    self.assertEqual(coordinator.update_interval, timedelta(seconds=1))
                    with self.assertRaises(HomeAssistantError):
                        await coordinator.async_run("gate-uuid", action_id="partial-id")
                    raw_gate.update(open_status="CLOSED", open_perc=0)
                    await coordinator.async_refresh()
                    self.assertEqual(hass.states.get(gate_id).state, "closed")
                    self.assertEqual(coordinator.update_interval, timedelta(seconds=15))
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
                    raw_roller.update(open_perc=66)
                    await coordinator.async_run("light-uuid", role="off", relay=2)
                    command.assert_called_with("off-id", "fixture-new", {"rele": 2})
                    raw_light.update(status_rele_2=False)

                    percentage = registry.async_get_entity_id("number", DOMAIN, "gate-uuid:partial_open_percentage")
                    move_button = registry.async_get_entity_id("button", DOMAIN, "gate-uuid:move_to_partial_position")
                    self.assertEqual(percentage, "number.outer_gate_partial_open_percentage")
                    self.assertEqual(move_button, "button.outer_gate_move_to_partial_position")
                    self.assertEqual(float(hass.states.get(percentage).state), 30)
                    self.assertIsNone(registry.async_get_entity_id("number", DOMAIN, "roller-uuid:partial_open_percentage"))
                    command.reset_mock()
                    await hass.services.async_call(
                        "number", "set_value", {"entity_id": percentage, "value": 35}, blocking=True,
                    )
                    self.assertEqual(coordinator.partial_position("gate-uuid"), 35)
                    command.assert_not_called()

                    async def press_position() -> None:
                        await hass.services.async_call("button", "press", {"entity_id": move_button}, blocking=True)

                    requested = []
                    for initial, start_status, direction, final in (
                        (0, "CLOSED", "open", 35),
                        (100, "OPENED", "close", 35),
                        (20, "OPENED", "open", 37),
                        (54, "OPENING", "close", 35),
                        (35, "OPENED", None, 35),
                    ):
                        requested.clear()
                        raw_gate.update(open_status=start_status, open_perc=initial)

                        def position_command(action_id, _token, params):
                            requested.append(action_id)
                            self.assertEqual(params, {})
                            if action_id in ("open-id", "close-id"):
                                gate_reports.append(("OPENING" if action_id == "open-id" else "CLOSING", final))
                            elif action_id == "stop-id":
                                stopped = initial if start_status == "OPENING" and len(requested) == 1 else final
                                gate_reports.append(("OPENED", stopped))

                        command.side_effect = position_command
                        read_offset = len(device_reads)
                        if final != 35:
                            with self.assertRaisesRegex(HomeAssistantError, "stopped at 37%"):
                                await wait_for(press_position(), timeout=8)
                        else:
                            await wait_for(press_position(), timeout=8)
                        expected = ["stop-id"] if start_status == "OPENING" else []
                        if direction:
                            expected += [f"{direction}-id", "stop-id"]
                        self.assertEqual(requested, expected)
                        self.assertEqual(hass.states.get(gate_id).attributes["current_position"], final)
                        self.assertEqual(coordinator.update_interval, timedelta(seconds=15))
                        if initial == 0:
                            samples = device_reads[read_offset:]
                            self.assertGreaterEqual(len(samples), 4)
                            for previous, current in zip(samples, samples[1:]):
                                self.assertAlmostEqual(current - previous, 0.5, delta=0.25)
                            await sleep(0.75)
                            self.assertEqual(len(device_reads), read_offset + len(samples))

                    raw_gate.update(open_status="CLOSED", open_perc=0)
                    opening, release = Event(), Event()

                    async def hold_open(action_id, _params):
                        if action_id == "open-id":
                            opening.set()
                            await release.wait()
                        requested.append(action_id)

                    requested.clear()
                    with patch.object(coordinator.client, "async_perform", side_effect=hold_open):
                        move = create_task(press_position())
                        await wait_for(opening.wait(), timeout=3)
                        self.assertEqual(coordinator.update_interval, timedelta(milliseconds=500))
                        with self.assertRaisesRegex(HomeAssistantError, "Use Stop"):
                            await coordinator.async_run("gate-uuid", role="close")
                        active = coordinator._gate_moves["gate-uuid"]
                        active.updated.clear()
                        stop = create_task(coordinator.async_run("gate-uuid", role="stop"))
                        await wait_for(active.updated.wait(), timeout=3)
                        self.assertTrue(active.cancelled)
                        self.assertEqual(requested, [])
                        release.set()
                        await wait_for(stop, timeout=3)
                        with self.assertRaisesRegex(HomeAssistantError, "cancelled"):
                            await wait_for(move, timeout=3)
                    self.assertEqual(requested, ["open-id", "stop-id"])
                    command.side_effect = None

                    devices.remove(raw_gate)
                    await coordinator.async_refresh()
                    self.assertEqual(hass.states.get(gate_id).state, "unavailable")
                    devices.append(raw_gate)

                    for override in ({"project_key": "fixture-override"}, {}):
                        form = await hass.config_entries.flow.async_init(
                            DOMAIN, context={"source": "reauth", "entry_id": entry.entry_id}, data=entry.data,
                        )
                        result = await hass.config_entries.flow.async_configure(form["flow_id"], {
                            "email": "test@example.invalid", "password": "fixture-password", **override,
                        })
                        self.assertEqual(result["reason"], "reauth_successful")
                        await hass.async_block_till_done()
                        self.assertEqual(entry.data["project_key"], "fixture-override")
                        self.assertEqual(login_keys[-1], "fixture-override")
                        self.assertEqual(refresh_keys[-1], "fixture-override")
                        self.assertEqual(float(hass.states.get(percentage).state), 35)
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

    def test_partial_tracks_motion_and_settles_without_a_fixed_target(self) -> None:
        closed = parse_device({"uuid": "gate", "open_status": "CLOSED", "open_perc": 0})
        moving = replace(closed, state="opening", position=18)
        open_partway = replace(closed, state="partial", position=35)
        action = ActionPoll(closed, "partial", 100, None, None)
        self.assertFalse(action.complete(closed, 101))
        self.assertFalse(action.complete(moving, 110))
        self.assertFalse(action.complete(open_partway, 114))
        self.assertFalse(action.complete(open_partway, 116))
        self.assertTrue(action.complete(open_partway, 117))

    def test_no_change_and_continuous_motion_have_limits(self) -> None:
        closed = parse_device({"uuid": "gate", "open_status": "CLOSED"})
        action = ActionPoll(closed, "open", 100, None, None)
        self.assertFalse(action.complete(closed, 111))
        self.assertTrue(action.complete(closed, 112))
        moving = replace(closed, state="opening", position=15)
        action = ActionPoll(closed, "open", 100, None, None)
        self.assertFalse(action.complete(moving, 189))
        self.assertTrue(action.complete(moving, 190))
