"""Feishu Open Platform calendar v4 client.

Pure HTTP client using httpx + tenant_access_token. No DB, no scheduling —
just enough surface to list calendars, fetch the app primary, and create
all-day events. Higher-level sync logic lives elsewhere.

Endpoints:
- POST /open-apis/auth/v3/tenant_access_token/internal
- GET  /open-apis/calendar/v4/calendars
- POST /open-apis/calendar/v4/calendars/primary
- POST /open-apis/calendar/v4/calendars/:calendar_id/events
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

OPEN_BASE = "https://open.feishu.cn"
DEFAULT_TIMEOUT = 30.0

log = logging.getLogger("feishu_calendar")


class FeishuCalendarError(Exception):
    """Raised on transport failure or non-zero `code` in a Feishu response."""


@dataclass(frozen=True)
class CalendarSummary:
    calendar_id: str
    summary: str
    type: str  # primary | shared | google | other
    permissions: str
    description: str


@dataclass(frozen=True)
class CreateEventResult:
    event_id: str
    raw: dict[str, Any]


class FeishuCalendarClient:
    """Thin httpx wrapper. One instance per process is fine; not thread-safe."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not app_id or not app_secret:
            raise ValueError("app_id and app_secret are required")
        self._app_id = app_id
        self._app_secret = app_secret
        self._timeout = timeout
        self._token: str | None = None

    # ---------- auth ----------

    def fetch_tenant_access_token(self) -> str:
        """Always fetches a fresh token. Token TTL is ~7200s; caching is a
        future optimization once we know the call cadence."""
        try:
            resp = httpx.post(
                f"{OPEN_BASE}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self._app_id, "app_secret": self._app_secret},
                timeout=self._timeout,
            )
        except httpx.RequestError as e:
            raise FeishuCalendarError(f"tenant_access_token network error: {e!r}") from e

        body = _ensure_ok(resp, "fetch_tenant_access_token")
        token = body.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise FeishuCalendarError(f"missing tenant_access_token in response: {body}")
        self._token = token
        return token

    # ---------- calendar ----------

    def list_calendars(self, *, page_size: int = 50) -> list[CalendarSummary]:
        """One-page list (no pagination yet — most apps own <50 calendars)."""
        resp = self._get(
            "/open-apis/calendar/v4/calendars",
            params={"page_size": page_size},
        )
        body = _ensure_ok(resp, "list_calendars")
        return _parse_calendar_list(body.get("data", {}).get("calendar_list", []))

    def get_or_create_primary_calendar(self) -> CalendarSummary:
        """POST /calendars/primary — returns (or creates) the app's primary calendar."""
        resp = self._post("/open-apis/calendar/v4/calendars/primary", json=None)
        body = _ensure_ok(resp, "get_or_create_primary_calendar")
        cals = body.get("data", {}).get("calendars", [])
        if not cals:
            raise FeishuCalendarError(
                f"primary endpoint returned no calendars: {body.get('data')!r}"
            )
        # Response shape: {"calendars": [{"calendar": {...}}]}
        first = cals[0]
        cal = first.get("calendar", first) if isinstance(first, dict) else {}
        return _parse_calendar(cal)

    def create_all_day_event(
        self,
        *,
        calendar_id: str,
        summary: str,
        description: str,
        start_date: str,
        end_date: str | None = None,
        idempotency_key: str | None = None,
    ) -> CreateEventResult:
        """Create an all-day event. `start_date`/`end_date` are ISO YYYY-MM-DD;
        when `end_date` is omitted we use a single-day event."""
        if not calendar_id:
            raise ValueError("calendar_id is required")
        end = end_date or start_date
        body: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "start_time": {"date": start_date},
            "end_time": {"date": end},
            "visibility": "default",
            "attendee_ability": "none",
            "free_busy_status": "free",
        }
        params: dict[str, Any] = {}
        if idempotency_key:
            params["idempotency_key"] = idempotency_key
        resp = self._post(
            f"/open-apis/calendar/v4/calendars/{calendar_id}/events",
            json=body,
            params=params,
        )
        ok = _ensure_ok(resp, "create_event")
        ev = ok.get("data", {}).get("event", {})
        event_id = str(ev.get("event_id", ""))
        if not event_id:
            raise FeishuCalendarError(f"create_event response missing event_id: {ok}")
        return CreateEventResult(event_id=event_id, raw=ev if isinstance(ev, dict) else {})

    # ---------- internals ----------

    def _auth_headers(self) -> dict[str, str]:
        token = self._token or self.fetch_tenant_access_token()
        return {"Authorization": f"Bearer {token}"}

    def _get(self, path: str, *, params: dict[str, Any]) -> httpx.Response:
        try:
            return httpx.get(
                f"{OPEN_BASE}{path}",
                params=params,
                headers=self._auth_headers(),
                timeout=self._timeout,
            )
        except httpx.RequestError as e:
            raise FeishuCalendarError(f"GET {path} network error: {e!r}") from e

    def _post(
        self,
        path: str,
        *,
        json: Any,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = self._auth_headers()
        if json is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        try:
            return httpx.post(
                f"{OPEN_BASE}{path}",
                json=json,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.RequestError as e:
            raise FeishuCalendarError(f"POST {path} network error: {e!r}") from e


def _parse_calendar(cal: dict[str, Any]) -> CalendarSummary:
    return CalendarSummary(
        calendar_id=str(cal.get("calendar_id", "")),
        summary=str(cal.get("summary", "")),
        type=str(cal.get("type", "")),
        permissions=str(cal.get("permissions", "")),
        description=str(cal.get("description", "")),
    )


def _parse_calendar_list(rows: list[Any]) -> list[CalendarSummary]:
    return [_parse_calendar(r) for r in rows if isinstance(r, dict)]


def _ensure_ok(resp: httpx.Response, op: str) -> dict[str, Any]:
    """Raise on transport-level failure or non-zero `code` in body."""
    if resp.status_code >= 500:
        raise FeishuCalendarError(f"{op}: HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        body = resp.json()
    except ValueError as e:
        raise FeishuCalendarError(
            f"{op}: non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"
        ) from e
    if not isinstance(body, dict):
        raise FeishuCalendarError(f"{op}: response is not a JSON object: {body!r}")
    code = body.get("code")
    if code != 0:
        raise FeishuCalendarError(
            f"{op}: feishu code={code} msg={body.get('msg')!r}"
        )
    return body
