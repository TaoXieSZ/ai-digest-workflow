"""Tests for the Feishu calendar v4 client.

We stub `httpx.get` / `httpx.post` via monkeypatch so no network is touched.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from digest.feishu_calendar import (
    FeishuCalendarClient,
    FeishuCalendarError,
)


def _install_stubs(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[tuple[str, str], httpx.Response],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Patch httpx in the module under test; return a recorder for assertions."""
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def _match(method: str, url: str) -> httpx.Response:
        for (m, sub), resp in responses.items():
            if m == method and sub in url:
                return resp
        return httpx.Response(404, json={"code": -1, "msg": f"no stub for {method} {url}"})

    def fake_get(url: str, **kw: Any) -> httpx.Response:
        calls.append(("GET", url, kw))
        return _match("GET", url)

    def fake_post(url: str, **kw: Any) -> httpx.Response:
        calls.append(("POST", url, kw))
        return _match("POST", url)

    monkeypatch.setattr("digest.feishu_calendar.httpx.get", fake_get)
    monkeypatch.setattr("digest.feishu_calendar.httpx.post", fake_post)
    return calls


def _ok(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=payload)


# ---------- construction ----------


def test_client_requires_app_id_and_secret() -> None:
    with pytest.raises(ValueError):
        FeishuCalendarClient(app_id="", app_secret="x")
    with pytest.raises(ValueError):
        FeishuCalendarClient(app_id="x", app_secret="")


# ---------- auth ----------


def test_fetch_tenant_access_token_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t-abc", "expire": 7200}
            ),
        },
    )
    client = FeishuCalendarClient(app_id="aid", app_secret="sec")
    assert client.fetch_tenant_access_token() == "t-abc"


def test_fetch_tenant_access_token_propagates_feishu_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 99991663, "msg": "app_secret invalid"}
            ),
        },
    )
    client = FeishuCalendarClient(app_id="aid", app_secret="bad")
    with pytest.raises(FeishuCalendarError) as exc:
        client.fetch_tenant_access_token()
    assert "99991663" in str(exc.value)


def test_fetch_tenant_access_token_missing_field_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "expire": 7200}  # no tenant_access_token
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    with pytest.raises(FeishuCalendarError):
        client.fetch_tenant_access_token()


# ---------- list_calendars ----------


def test_list_calendars_parses_summary_and_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("GET", "/calendar/v4/calendars"): _ok(
                {
                    "code": 0,
                    "data": {
                        "calendar_list": [
                            {
                                "calendar_id": "primary-cid",
                                "summary": "Primary",
                                "type": "primary",
                                "permissions": "private",
                            },
                            {
                                "calendar_id": "shared-cid",
                                "summary": "Team Events",
                                "type": "shared",
                            },
                            "garbage-not-an-object",
                        ]
                    },
                }
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    cals = client.list_calendars()
    assert [c.calendar_id for c in cals] == ["primary-cid", "shared-cid"]
    assert cals[0].type == "primary"
    assert cals[1].summary == "Team Events"

    # Token bearer header was sent on the GET.
    get_calls = [c for c in calls if c[0] == "GET"]
    assert get_calls
    assert get_calls[0][2]["headers"]["Authorization"] == "Bearer t"


# ---------- primary ----------


def test_get_or_create_primary_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("POST", "/calendar/v4/calendars/primary"): _ok(
                {
                    "code": 0,
                    "data": {
                        "calendars": [
                            {
                                "calendar": {
                                    "calendar_id": "primary-cid",
                                    "summary": "AI Digest",
                                    "type": "primary",
                                }
                            }
                        ]
                    },
                }
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    primary = client.get_or_create_primary_calendar()
    assert primary.calendar_id == "primary-cid"
    assert primary.type == "primary"


def test_get_or_create_primary_calendar_handles_flat_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some Feishu deployments return calendars without a nested `calendar` key."""
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("POST", "/calendar/v4/calendars/primary"): _ok(
                {
                    "code": 0,
                    "data": {
                        "calendars": [
                            {
                                "calendar_id": "flat-cid",
                                "summary": "Flat",
                                "type": "primary",
                            }
                        ]
                    },
                }
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    assert client.get_or_create_primary_calendar().calendar_id == "flat-cid"


def test_get_or_create_primary_calendar_empty_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("POST", "/calendar/v4/calendars/primary"): _ok({"code": 0, "data": {"calendars": []}}),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    with pytest.raises(FeishuCalendarError):
        client.get_or_create_primary_calendar()


# ---------- create_all_day_event ----------


def test_create_all_day_event_builds_correct_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("POST", "/calendar/v4/calendars/cid-1/events"): _ok(
                {"code": 0, "data": {"event": {"event_id": "ev-1"}}}
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    result = client.create_all_day_event(
        calendar_id="cid-1",
        summary="NVIDIA 创业展示",
        description="📍 上海\n⏰ 报名截止 2026-06-10",
        start_date="2026-06-15",
        idempotency_key="item-abc",
    )
    assert result.event_id == "ev-1"

    create_calls = [c for c in calls if "events" in c[1]]
    assert create_calls
    body = create_calls[0][2]["json"]
    assert body["start_time"] == {"date": "2026-06-15"}
    assert body["end_time"] == {"date": "2026-06-15"}
    assert body["summary"] == "NVIDIA 创业展示"
    assert "📍 上海" in body["description"]
    assert create_calls[0][2]["params"] == {"idempotency_key": "item-abc"}


def test_create_all_day_event_uses_explicit_end_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("POST", "/calendar/v4/calendars/cid/events"): _ok(
                {"code": 0, "data": {"event": {"event_id": "ev-2"}}}
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    client.create_all_day_event(
        calendar_id="cid",
        summary="Conf",
        description="multi-day",
        start_date="2026-06-15",
        end_date="2026-06-17",
    )
    body = [c for c in calls if "events" in c[1]][0][2]["json"]
    assert body["start_time"] == {"date": "2026-06-15"}
    assert body["end_time"] == {"date": "2026-06-17"}


def test_create_all_day_event_requires_calendar_id() -> None:
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    with pytest.raises(ValueError):
        client.create_all_day_event(
            calendar_id="",
            summary="x",
            description="y",
            start_date="2026-01-01",
        )


def test_create_event_propagates_feishu_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): _ok(
                {"code": 0, "tenant_access_token": "t"}
            ),
            ("POST", "/calendar/v4/calendars/cid/events"): _ok(
                {"code": 195100, "msg": "calendar not found"}
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    with pytest.raises(FeishuCalendarError) as exc:
        client.create_all_day_event(
            calendar_id="cid",
            summary="t",
            description="d",
            start_date="2026-01-01",
        )
    assert "195100" in str(exc.value)


# ---------- transport-level errors ----------


def test_http_500_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): httpx.Response(500, text="upstream"),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    with pytest.raises(FeishuCalendarError) as exc:
        client.fetch_tenant_access_token()
    assert "HTTP 500" in str(exc.value)


def test_non_json_response_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_stubs(
        monkeypatch,
        {
            ("POST", "/auth/v3/tenant_access_token/internal"): httpx.Response(
                200, text="<html>oops</html>"
            ),
        },
    )
    client = FeishuCalendarClient(app_id="a", app_secret="b")
    with pytest.raises(FeishuCalendarError):
        client.fetch_tenant_access_token()
