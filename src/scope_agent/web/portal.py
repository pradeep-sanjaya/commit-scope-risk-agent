"""Read-only, server-rendered QA portal. Every tab is also a full page (`?tab=`); htmx only enhances."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, StrictUndefined

from scope_agent.persistence.database import Clock, utcnow
from scope_agent.services.portal_queries import PortalQueries

TABS = ("areas-list", "areas-matrix", "tickets", "test-scope", "risks", "history")
DEFAULT_TAB = "areas-list"
SEVERITY_CLASS = {"high": "sev-h", "medium": "sev-m", "low": "sev-l", "info": "sev-l"}


def _ago_filter(clock: Clock):
    def ago(when: datetime | None) -> str:
        if when is None:
            return "-"
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = int((clock() - when).total_seconds())
        if seconds < 3600:
            return f"{max(seconds // 60, 0)}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        return f"{seconds // 86400}d ago"
    return ago


def _environment(clock: Clock) -> Environment:
    env = Environment(
        loader=PackageLoader("scope_agent.web", "templates"), autoescape=True,
        undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["ago"] = _ago_filter(clock)
    env.filters["sev_class"] = lambda sev: SEVERITY_CLASS.get(getattr(sev, "value", sev) or "", "sev-l")
    env.filters["sev_value"] = lambda sev: getattr(sev, "value", sev) or ""
    return env


def build_router(queries: PortalQueries, user_dependency, clock: Clock = utcnow) -> APIRouter:
    env = _environment(clock)
    router = APIRouter(prefix="/portal", dependencies=[Depends(user_dependency)])

    def render(name: str, **ctx) -> HTMLResponse:
        return HTMLResponse(env.get_template(f"portal/{name}.html.j2").render(**ctx))

    def tab_context(subject: str, tab: str, version: int | None) -> dict:
        if tab not in TABS:
            raise HTTPException(404, "unknown tab")
        page = queries.epic_page(subject, version)
        if page is None:
            raise HTTPException(404, "unknown epic or version")
        ctx = {"page": page, "report": page.report, "agg": page.report.content.aggregate,
               "diff": page.report.diff, "tab": tab, "pinned": version is not None, "tabs": TABS}
        if tab == "risks":
            ctx["risks"] = queries.epic_risks(subject)
        return ctx

    @router.get("", response_class=HTMLResponse)
    def epic_list(q: str = "", project: str = "", high: str = ""):
        rows = queries.epic_list(q or None, project or None, bool(high))
        return render("epic_list", rows=rows, q=q, project=project, high=bool(high))

    def tab_body(ctx: dict) -> str:
        return env.get_template(f"portal/_tab_{ctx['tab'].replace('-', '_')}.html.j2").render(**ctx)

    @router.get("/epics/{subject}", response_class=HTMLResponse)
    def epic(subject: str, tab: str = DEFAULT_TAB, v: int | None = None):
        ctx = tab_context(subject, tab, v)
        return render("epic", body=tab_body(ctx), **ctx)

    @router.get("/epics/{subject}/tab/{tab}", response_class=HTMLResponse)
    def epic_tab(subject: str, tab: str, v: int | None = None):
        """htmx partial: the tab nav and body together, so the swap also moves the highlight."""
        ctx = tab_context(subject, tab, v)
        return render("_tabs", body=tab_body(ctx), **ctx)

    @router.get("/epics/{subject}/cell", response_class=HTMLResponse)
    def cell(subject: str, area: str, ticket: str):
        if queries.epic_page(subject, None) is None:
            raise HTTPException(404, "unknown epic")
        return render("_matrix_cell", subject=subject, area=area, ticket=ticket,
                      records=queries.matrix_cell(subject, area, ticket))

    @router.get("/tickets/{key}", response_class=HTMLResponse)
    def ticket(key: str, request: Request):
        page = queries.ticket_page(key)
        if page is None:
            raise HTTPException(404, "unknown ticket")
        return render("ticket", page=page)

    return router
