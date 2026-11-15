"""Normalized Able device and action data."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import re
from typing import Any

GATE = "GATE"
SUNSHADING = "SUNSHADING"
LIGHT = "LIGHT"
DUAL_LIGHT = "DUAL_LIGHT"
COVER_TYPES = frozenset({GATE, SUNSHADING})
GATE_ACTIONS = {
    "start": "start", "avvio": "start", "partenza": "start",
    "stop": "stop", "arresto": "stop",
    "partial start": "partial", "avvio parziale": "partial", "partenza parziale": "partial",
    "open": "open", "apri": "open", "apertura": "open",
    "close": "close", "chiudi": "close", "chiusura": "close",
    "pedestrian": "pedestrian", "pedonale": "pedestrian", "apertura pedonale": "pedestrian",
}
ROLLER_ACTIONS = {
    "open up": "open", "close down": "close", "stop": "stop",
    "target position": "target", "run to favorite position": "favorite",
}
LIGHT_ACTIONS = {"rele on": "on", "rele off": "off"}
FIXED_ROLLER_ACTIONS = frozenset({"open", "close", "stop", "favorite"})
STATES = {"OPEN": "open", "OPENED": "open", "CLOSED": "closed",
          "OPENING": "opening", "CLOSING": "closing", "MOVING": "moving"}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _identifier(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) else None


def _normal(value: Any) -> str:
    return " ".join(re.split(r"[\s_-]+", _text(value).casefold()))


def _percentage(value: Any) -> int | None:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return round(number) if 0 <= number <= 100 else None


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(_text(value))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Action:
    """An enabled action returned for the current Able account."""

    id: str
    name: str
    role: str | None


@dataclass(frozen=True, slots=True)
class Device:
    """Status and available actions for an Able controller."""

    id: str
    name: str
    kind: str
    subtype: str
    serial: str | None
    firmware: str | None
    state: str | None
    position: int | None
    favorite: int | None
    last_activity: datetime | None
    relays: tuple[bool | None, bool | None]
    actions: tuple[Action, ...] = ()

    def action_for(self, role: str) -> Action | None:
        """Return a role only when it has a unique match."""
        matches = [action for action in self.actions if action.role == role]
        return matches[0] if len(matches) == 1 else None

    @property
    def supports_custom_position(self) -> bool:
        return self.kind == GATE and all(self.action_for(role) for role in ("open", "close", "stop"))

    def buttons(self) -> tuple[Action, ...]:
        """Return the actions the Android app sends without parameters."""
        if self.kind == GATE:
            return self.actions
        return tuple(action for action in self.actions if action.role in FIXED_ROLLER_ACTIONS)

    def with_actions(self, actions: list[Any]) -> "Device":
        """Filter per-account actions and interpret them using the device type."""
        parsed: dict[str, Action] = {}
        for raw in actions:
            if not isinstance(raw, dict) or raw.get("enabled") is not True:
                continue
            static = raw.get("static_action")
            other = raw.get("device")
            action_id = _identifier(raw.get("uuid"))
            if not action_id or not isinstance(static, dict) or static.get("is_available_for_user") is not True:
                continue
            if isinstance(other, dict) and other.get("uuid") not in (None, self.id):
                continue
            name = _text(static.get("name"))
            code = _normal(static.get("command_name"))
            if self.kind == GATE:
                role = GATE_ACTIONS.get(_normal(name))
            elif self.kind == SUNSHADING:
                role = ROLLER_ACTIONS.get(code)
            else:
                role = LIGHT_ACTIONS.get(code)
            if name or role:
                parsed[action_id] = Action(action_id, name or role.title(), role)
        return replace(self, actions=tuple(sorted(parsed.values(), key=lambda item: item.id)))


def parse_device(raw: Any) -> Device | None:
    """Read one device from the cloud list without retaining location or access data."""
    if not isinstance(raw, dict) or not (device_id := _identifier(raw.get("uuid"))):
        return None
    kind = _text(raw.get("type")).upper() or GATE
    position = _percentage(raw.get("open_perc"))
    state = STATES.get(_text(raw.get("open_status")).upper())
    opened = raw.get("open")
    if state is None and isinstance(opened, bool):
        state = "open" if opened else "closed"
    if state == "closed" and position is None:
        position = 0
    if state == "open" and position is not None and 0 < position < 100:
        state = "partial"
    relays = tuple(raw.get(f"status_rele_{n}") for n in (1, 2))
    return Device(
        id=device_id, name=_text(raw.get("name")) or "Able device", kind=kind,
        subtype=_text(raw.get("subtype")).upper(), serial=_text(raw.get("serial")) or None,
        firmware=_text(raw.get("firmware")) or None, state=state, position=position,
        favorite=_percentage(raw.get("favorite_position")), last_activity=_timestamp(raw.get("last_activity")),
        relays=(relays[0] if isinstance(relays[0], bool) else None, relays[1] if isinstance(relays[1], bool) else None),
    )
