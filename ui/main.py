"""FastAPI entrypoint for the local AI digest dashboard."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from db import load_digest_dashboard

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="AI Digest Dashboard")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/digest", status_code=303)


@app.get("/digest")
def digest_today(request: Request) -> Response:
    return _render_digest(request, None)


@app.get("/digest/{target_date}")
def digest_by_date(request: Request, target_date: date) -> Response:
    return _render_digest(request, target_date)


def _render_digest(request: Request, target_date: date | None) -> Response:
    dashboard = load_digest_dashboard(target_date)
    return templates.TemplateResponse(
        request,
        "digest.html",
        {
            "dashboard": dashboard,
            "daily_digest": dashboard.digests.get("daily_digest"),
            "event_digest": dashboard.digests.get("event_batch"),
        },
    )
