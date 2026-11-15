"""Async client for Able account sign-in and cloud device actions."""

from asyncio import Lock, to_thread
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from http.client import HTTPException, HTTPSConnection
from json import JSONDecodeError
from time import monotonic
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout

from .models import Device, parse_device

ABLE_URL = "https://app.ableautomation.com/api/"
LOGIN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
TIMEOUT = ClientTimeout(total=12)


class AbleError(Exception):
    """An error communicating with Able."""


class AbleAuthError(AbleError):
    """The account must authenticate again."""


class AblePermissionError(AbleError):
    """The account lacks permission for this operation."""


@dataclass(frozen=True, slots=True)
class Login:
    """Firebase identity and tokens returned by Able sign-in."""

    uid: str
    refresh_token: str = field(repr=False)
    id_token: str = field(repr=False)
    expires_in: int


async def _request(
    session: ClientSession, method: str, url: str, *, auth_service: bool = False,
    **kwargs: Any,
) -> Any:
    """Make one request while keeping response bodies and credentials out of errors."""
    try:
        async with session.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kwargs) as response:
            status = response.status
            if status == 401 or (auth_service and status in (400, 403)):
                raise AbleAuthError("Able did not accept the credentials or session")
            if status == 403:
                raise AblePermissionError("Able denied access to this operation")
            if not 200 <= status < 300:
                raise AbleError(f"Able returned HTTP {status}")
            return await response.json(content_type=None)
    except (ClientError, TimeoutError, JSONDecodeError, UnicodeDecodeError):
        raise AbleError("Unable to read the Able cloud response") from None


def _parse_login(raw: Any, *, refresh: bool = False) -> Login:
    keys = ("user_id", "refresh_token", "id_token", "expires_in") if refresh else (
        "localId", "refreshToken", "idToken", "expiresIn"
    )
    try:
        uid, refresh_token, id_token, expiry = (raw[key] for key in keys)
        if not all(isinstance(value, str) and value for value in (uid, refresh_token, id_token)):
            raise ValueError
        return Login(uid, refresh_token, id_token, int(expiry))
    except (KeyError, TypeError, ValueError):
        raise AbleError("Able returned an incomplete sign-in response") from None


async def async_login(session: ClientSession, project_key: str, email: str, password: str) -> Login:
    """Use the Firebase email and password flow used by Able."""
    response = await _request(
        session, "POST", LOGIN_URL, auth_service=True, params={"key": project_key},
        json={"email": email, "password": password, "returnSecureToken": True},
    )
    return _parse_login(response)


def _perform_once(action_id: str, token: str, params: dict[str, int] | None) -> None:
    """Use one HTTPS request because Able's command GET has side effects."""
    path = f"/api/actions/perform/{action_id}"
    if params:
        path += f"?{urlencode(params)}"
    try:
        with closing(HTTPSConnection("app.ableautomation.com", timeout=12)) as connection:
            connection.request("GET", path, headers={"Authorization": f"Bearer {token}", "Accept-Language": "en"})
            with closing(connection.getresponse()) as response:
                status = response.status
    except (OSError, HTTPException):
        raise AbleError("Able did not confirm the command. Check the device before trying again") from None
    if status == 401:
        raise AbleAuthError("Able rejected the command session")
    if status == 403:
        raise AblePermissionError("Able denied this action")
    if not 200 <= status < 300:
        raise AbleError(f"Able returned HTTP {status}. Check the device before trying again")


class AbleClient:
    """Use account-scoped reads and never replay a state-changing vendor GET."""

    def __init__(
        self, session: ClientSession, project_key: str, uid: str, refresh_token: str,
        on_refresh: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._project_key = project_key
        self._uid = uid
        self._refresh_token = refresh_token
        self._on_refresh = on_refresh
        self._id_token = ""
        self._expires = 0.0
        self._lock = Lock()

    def use_login(self, login: Login) -> None:
        """Record a fresh token and persist rotations through the config entry."""
        if login.uid != self._uid:
            raise AbleAuthError("The session belongs to another account")
        self._id_token = login.id_token
        self._expires = monotonic() + max(1, login.expires_in - 30)
        if login.refresh_token != self._refresh_token:
            self._refresh_token = login.refresh_token
            if self._on_refresh is not None:
                self._on_refresh(login.refresh_token)

    async def _token(self, rejected: str | None = None) -> str:
        async with self._lock:
            if self._id_token and monotonic() < self._expires and self._id_token != rejected:
                return self._id_token
            raw = await _request(
                self._session, "POST", REFRESH_URL, auth_service=True, params={"key": self._project_key},
                data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            )
            self.use_login(_parse_login(raw, refresh=True))
            return self._id_token

    async def _get(self, path: str) -> Any:
        token = await self._token()
        try:
            return await self._with_token(path, token)
        except AbleAuthError:
            return await self._with_token(path, await self._token(token))

    async def _with_token(self, path: str, token: str) -> Any:
        return await _request(
            self._session, "GET", f"{ABLE_URL}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "Accept-Language": "en"},
        )

    async def async_devices(self) -> dict[str, Device]:
        """Read statuses for all devices the current account may see."""
        raw = await self._get("devices")
        if not isinstance(raw, list):
            raise AbleError("Able returned an unexpected device list")
        return {device.id: device for item in raw if (device := parse_device(item)) is not None}

    async def async_device_actions(self, device: Device) -> Device:
        """Return the device with its current action catalog."""
        try:
            raw = await self._get(f"actions/{device.id}")
        except AblePermissionError:
            return device
        if not isinstance(raw, list):
            raise AbleError("Able returned an unexpected action list")
        return device.with_actions(raw)

    async def async_perform(self, action_id: str, params: dict[str, int] | None = None) -> None:
        """Submit a configured action once and leave status to the device report."""
        token = await self._token()
        try:
            await to_thread(_perform_once, action_id, token, params)
        except AbleAuthError:
            if self._id_token == token:
                self._expires = 0.0
            raise
