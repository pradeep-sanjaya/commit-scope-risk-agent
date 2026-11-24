# QA Impact Portal Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the QA Impact Portal to the approved layout A: professional, quick to scan, nothing loaded from a public CDN.

**Architecture:** Same server-rendered Jinja2 + htmx portal. `portal.css` is rewritten around CSS custom-property tokens (light + dark); templates get new markup; htmx is vendored into `web/static/`. Routes and data stay; the only query additions are presentation helpers (`EpicListRow.test_scope_status`, `PortalQueries.projects()`, tab counts).

**Tech Stack:** Python 3.12, FastAPI, Jinja2 (autoescape, StrictUndefined), htmx 1.9.12 (vendored), pytest, headless Google Chrome for screenshots.

**Spec:** `docs/superpowers/specs/2026-11-24-portal-redesign-design.md` and mockups in `docs/superpowers/specs/2026-11-24-portal-redesign/` (`mockup-epic-page.html` option A, `mockup-epic-list.html`).

## Global Constraints

- Nothing from public CDNs or the internet: no external `<script src>`, `<link href>`, `@import` or `url(http…)`. htmx served from `/portal/static/htmx.min.js`.
- htmx version stays 1.9.12; the vendored file must match sha384 `ujb1lZYygJmzgSwoxRggbCHcjc0rB2XoQrxeTUQyRjrOnlCoYta87iKBWq3EsdM2`.
- System font stack only: `-apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`; monospace `ui-monospace, "SF Mono", Menlo, Consolas, monospace`. Icons, if any, inline SVG.
- Read-only portal; no new features; routes, query parameters and the htmx tab swap of `#tabs` unchanged.
- Dark mode follows `prefers-color-scheme`; WCAG AA (contrast ≥ 4.5:1) for text and badge pairs in both themes.
- Keep the hooks tests rely on: `<nav class="tabs">`, active tab `class="on"`, overlap rows `class="ov"`, `new-badge`, banner text "Test scope pending (LLM budget)".
- Commit messages: `V4-1234: <Description>` with the `Co-Authored-By` trailer.

## Review Focus

1. **htmx unavailable** (JS blocked or file missing): every tab, toggle and matrix cell still works as a plain link (`href` full-page fallback) — pinned by `test_tab_links_have_full_page_fallbacks` (Task 4).
2. **Long values** (60-char Epic titles, long area names, 20 tickets in the matrix): tables scroll horizontally inside `.table-wrap`, never widen the page — pinned by `test_wide_tables_are_wrapped` (Task 5).
3. **Empty states** (Epic with no findings, no test scope, ticket with no commits): each tab shows its muted empty-state row, not an empty card — existing tests plus `test_empty_states_render` (Task 5).
4. **Search for a ticket key in the top bar** (`demo-101`, lower case, or a key with no commits): exact match with commits opens the ticket page; anything else falls back to the Epic list search — `test_search_ticket_key_redirects` (Task 1).
5. **Dark mode contrast** of severity badges and muted text — `test_theme_tokens_meet_wcag_aa` (Task 2).

---

### Task 1: Vendored htmx, app shell and top-bar search

**Files:**
- Create: `src/scope_agent/web/static/htmx.min.js` (htmx 1.9.12, downloaded once from `https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js` at build time of this task and committed)
- Modify: `src/scope_agent/web/templates/portal/base.html.j2`, `src/scope_agent/web/portal.py` (route `GET /portal/search`)
- Test: `tests/web/test_portal_shell.py`

**Interfaces:**
- Produces: `base.html.j2` blocks `title`, `content`, plus `{% block nav_epics_on %}` not needed — the top bar always marks "Epics" active. Top-bar search form: `GET /portal/search?q=`.
- Produces: route `GET /portal/search?q=` → 303 to `/portal/tickets/{KEY}` when `q.strip().upper()` is a key with records (`queries.ticket_page(KEY)` has records), else 303 to `/portal?q=<q>`.

- [ ] **Step 1: Write failing tests** in `tests/web/test_portal_shell.py` (client from `tests/web/test_portal.py::client`, demo data via `tests/portal_helpers.seed`):
  - `test_pages_load_nothing_external`: for `/portal`, `/portal/epics/PAY-100`, every `/portal/epics/PAY-100?tab=<t>` in `TABS`, `/portal/tickets/PAY-101`: `re.search(r'(src|href)="https?://', html)` is None and `"unpkg" not in html`.
  - `test_vendored_htmx_matches_pinned_hash`: `GET /portal/static/htmx.min.js` → 200; `base64(sha384(body)) == "ujb1lZYygJmzgSwoxRggbCHcjc0rB2XoQrxeTUQyRjrOnlCoYta87iKBWq3EsdM2"`; base page contains `src="/portal/static/htmx.min.js"`.
  - `test_top_bar_has_brand_nav_and_search`: `/portal` HTML contains `class="topbar"`, `QA Impact`, an `<a ... aria-current="page"...>Epics</a>` and `<form` with `action="/portal/search"`.
  - `test_search_ticket_key_redirects`: `GET /portal/search?q=pay-101` (follow_redirects=False) → 303, `location == "/portal/tickets/PAY-101"`; `q=NOPE-1` → 303 to `/portal?q=NOPE-1`; `q=checkout` → 303 to `/portal?q=checkout`.
- [ ] **Step 2: Run** `uv run pytest tests/web/test_portal_shell.py -v` → FAIL.
- [ ] **Step 3: Implement.** Download htmx, verify the hash, save to `static/htmx.min.js`. `base.html.j2`: `<header class="topbar">` with brand (inline SVG mark + "QA Impact"), nav with one link `Epics` (`aria-current="page"`), search form (`action="/portal/search"`, `name="q"`, placeholder "Search Epic, ticket, area or repo…"); `<main class="page">`; script tag `<script src="/portal/static/htmx.min.js" integrity="sha384-…" defer></script>`. Add the `/search` route in `build_router` using `RedirectResponse(status_code=303)` and `urllib.parse.urlencode`.
- [ ] **Step 4: Run** `uv run pytest tests/web -q` → PASS.
- [ ] **Step 5: Commit** `V4-1234: Vendor htmx and add the portal app shell with top-bar search`.

### Task 2: Design tokens stylesheet and shared macros

**Files:**
- Modify: `src/scope_agent/web/static/portal.css` (rewrite), `src/scope_agent/web/templates/portal/_macros.html.j2`
- Test: `tests/web/test_portal_theme.py`

**Interfaces:**
- Produces CSS tokens (both `:root` and `@media (prefers-color-scheme: dark) { :root {…} }`): `--bg --panel --panel-2 --fg --muted --line --accent --accent-soft --high --high-soft --med --med-soft --low --low-soft --new --new-soft --ov --ov-bar`.
- Produces classes: `.topbar .brand .page .crumb .title-row .key .ver .kpis .kpi .kpi-n .kpi-l .tabs .cnt .card .toolbar .seg .table-wrap .num .badge .b-high .b-medium .b-low .chip .state .state-pending .state-scope .new-badge .banner .muted .empty .filters .group`.
- Produces macros in `_macros.html.j2`: `sev(value, label=None)` → `<span class="badge b-{value}">Label</span>` (info renders as low); `key(k)` → `<a class="key" href="/portal/tickets/{k}">k</a>`; `chip(text)`; `new_tag(version)` → `<span class="new-badge">new in v{version}</span>`.
- Router: `SEVERITY_CLASS` becomes `{"high": "b-high", "medium": "b-medium", "low": "b-low", "info": "b-low"}`.

- [ ] **Step 1: Write failing tests** in `tests/web/test_portal_theme.py`:
  - `test_theme_tokens_meet_wcag_aa`: parse `portal.css` into a light and a dark token dict (regex on `--name: #hex;` inside the first `:root {}` and inside the `prefers-color-scheme: dark` block); for each theme assert contrast ≥ 4.5 for pairs `(fg, bg) (fg, panel) (muted, panel) (accent, panel) (high, high-soft) (med, med-soft) (low, low-soft) (new, new-soft) (fg, ov)` using the WCAG relative-luminance formula.
  - `test_stylesheet_is_self_contained`: `portal.css` has no `@import`, no `url(http`, no `font-face` with remote `src`.
  - `test_severity_macro_renders_badges`: render `{% from "portal/_macros.html.j2" import sev %}{{ sev("high") }}{{ sev("info") }}` through `portal._environment(utcnow)` → contains `class="badge b-high">High<` and `class="badge b-low">Info<`.
- [ ] **Step 2: Run** `uv run pytest tests/web/test_portal_theme.py -v` → FAIL.
- [ ] **Step 3: Implement** the stylesheet from the mockup values (light: bg `#f7f8fa`, panel `#ffffff`, fg `#1b1f24`, muted `#5f6b7a`, line `#e3e7ec`, accent `#2f5fd0`, high `#c62f36`/`#fdecec`, med `#a05f00`/`#fff4e0`, low `#55606e`/`#eef1f4`, new `#1a7f45`/`#e7f6ee`, ov `#fff8e6`, ov-bar `#e0a100`; top bar `#141a24`), and a dark set chosen to pass the test. Visible `:focus-visible` outline on links, buttons and inputs. Update macros and `SEVERITY_CLASS`.
- [ ] **Step 4: Run** `uv run pytest tests/web -q` → PASS (existing pages still render).
- [ ] **Step 5: Commit** `V4-1234: Rewrite portal stylesheet around light and dark design tokens`.

### Task 3: Epic list page

**Files:**
- Modify: `src/scope_agent/services/portal_queries.py`, `src/scope_agent/web/portal.py` (epic_list route passes `projects`), `src/scope_agent/web/templates/portal/epic_list.html.j2`
- Test: `tests/services/test_portal_queries.py`, `tests/web/test_portal.py`

**Interfaces:**
- Produces: `EpicListRow.test_scope_status: str | None = None` (latest report's `content.test_scope_status`; None for pending-report and synthetic rows).
- Produces: `PortalQueries.projects() -> list[str]` — sorted distinct key prefixes (`"PAY"` from `"PAY-100"`) of non-`unassigned/` subjects.

- [ ] **Step 1: Write failing tests:**
  - `test_projects_lists_key_prefixes` (queries): seed → `q.projects() == ["PAY"]`.
  - `test_epic_list_carries_test_scope_status` (queries): `seed(status="pending")` → PAY-100 row `test_scope_status == "pending"`; default seed → `"ok"`.
  - `test_epic_list_page_layout` (web): `/portal` contains `<select name="project"`, `<option value="PAY"`, `High risk only`, `Not in an Epic` (group row placed before `Tickets without an Epic`), `class="num"`; `seed(status="pending")` page contains `Test scope pending`.
- [ ] **Step 2: Run** `uv run pytest tests/services/test_portal_queries.py tests/web/test_portal.py -v` → FAIL.
- [ ] **Step 3: Implement** per `mockup-epic-list.html`: page heading "Epics" + one-line lead; filter bar (search input `q`, project `<select>` with "All projects" = empty value, "High risk only" checkbox, Filter button) submitting `GET /portal`; table card with numeric columns right-aligned (`class="num"`), severity badges via `sev` macro with counts ("1 high"), state labels (`state-pending` "Report pending", `state-scope` "Test scope pending"), green `+n commits` beside last change, synthetic rows after a `<tr class="group">` "Not in an Epic"; empty state "No Epics match these filters."
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `V4-1234: Redesign the Epic list with filters, state labels and grouped rows`.

### Task 4: Epic page header, summary cards and tabs with counts

**Files:**
- Modify: `src/scope_agent/web/portal.py` (`tab_context`), `src/scope_agent/web/templates/portal/epic.html.j2`, `src/scope_agent/web/templates/portal/_tabs.html.j2`
- Test: `tests/web/test_portal.py`

**Interfaces:**
- Produces in `tab_context` ctx: `risks` (always, from `queries.epic_risks`) and `counts: dict[str, int]` with keys `areas` (`len(agg.area_rows)`), `tickets` (`len(agg.tickets)`), `test-scope` (`len(report.content.test_scope or [])`), `risks` (`len(risks)`).

- [ ] **Step 1: Write failing tests** (update `_highlighted_tabs` to strip inner tags: `re.sub(r"<[^>]+>", "", inner)` then take the label before the count):
  - `test_epic_header_and_summary_cards`: `/portal/epics/PAY-100` contains `class="kpis"` and, in order, KPI labels `Tickets`, `Developers`, `Areas`, `Overlaps`, `High risk` with values `4, 3, 3, 1, 1` (PAY-100 seed v2 aggregate); contains `Report v2` and `+2 commits since v1`; title row contains `class="key"` with `PAY-100`.
  - `test_tabs_show_counts`: nav contains `Tickets<span class="cnt">4</span>` and `Risks<span class="cnt">1</span>`.
  - `test_tab_links_have_full_page_fallbacks`: every `<a` in the tab nav and the List/Matrix toggle has an `href="/portal/epics/PAY-100?tab=` (htmx-free fallback).
  - Existing `test_tab_switch_moves_the_highlight` still passes with the updated helper.
- [ ] **Step 2: Run** `uv run pytest tests/web/test_portal.py -v` → FAIL.
- [ ] **Step 3: Implement** per `mockup-epic-page.html` option A: breadcrumb `Epics / KEY`, title row (h1 summary + monospace key + right-aligned "Report vN · +n commits since vN-1", older-version notice with "View latest"), pending/error banners, five KPI cards (High risk card red when > 0), tabs with `<span class="cnt">` counts (History without count).
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `V4-1234: Add Epic summary cards and tab counts`.

### Task 5: Tab contents, matrix cell and ticket page

**Files:**
- Modify: `src/scope_agent/web/templates/portal/{_tab_areas_list,_tab_areas_matrix,_tab_tickets,_tab_test_scope,_tab_risks,_tab_history,_matrix_cell,ticket}.html.j2`
- Test: `tests/web/test_portal.py`

**Interfaces:**
- Consumes: macros `sev`, `key`, `chip`, `new_tag` (Task 2); classes from Task 2.

- [ ] **Step 1: Write failing tests:**
  - `test_tab_bodies_use_cards_and_badges`: areas list body has `class="card"`, a `class="toolbar"` with the `seg` List/Matrix control and the hint "test them together"; overlap row keeps `class="ov"`; risks tab renders `class="badge b-high"`; tickets tab renders areas as `class="chip"`; test scope tab renders type as `class="chip"` and multi-ticket items with `class="ov"`.
  - `test_wide_tables_are_wrapped`: in every tab body and the ticket page, each `<table` is inside a `<div class="table-wrap">`.
  - `test_empty_states_render`: an Epic seeded with no findings → risks tab contains `class="empty"` and "No findings recorded for this Epic."; `seed(status="pending")` test-scope tab contains the banner text.
  - `test_ticket_page_layout`: `/portal/tickets/PAY-101` has breadcrumb with `PAY-100`, header key + status chip, sections "Acceptance criteria", "Impacted areas", "Findings", "Commits (2)", commit SHA `a1b2c3d` in `<code>`.
- [ ] **Step 2: Run** `uv run pytest tests/web/test_portal.py -v` → FAIL.
- [ ] **Step 3: Implement** each body as a `.card` with `.table-wrap` table: quiet headers, row dividers only, monospace keys via `key` macro, chips for change kinds/areas/types, `sev` badges, `new_tag` for new-in-version, muted `.empty` rows. Matrix: sticky first column, centred count links with a severity dot. Matrix cell: card with header `Area × KEY`, commit rows, test-note list. Ticket page: two-column grid (details left, commits table right), collapses to one column under 900px.
- [ ] **Step 4: Run** `uv run pytest -q` → PASS.
- [ ] **Step 5: Commit** `V4-1234: Restyle Epic tabs, matrix cell and ticket page`.

### Task 6: Visual verification and rollout

**Files:**
- Modify: `README.md` (portal section: no CDN, htmx vendored)
- Screenshots (not committed): `$CLAUDE_JOB_DIR/tmp/shots/*.png`

- [ ] **Step 1:** Rebuild and restart `web` on port 18080 (`PORT=18080 docker compose up -d --build web`), seed data already present (`seed-demo` is a no-op otherwise).
- [ ] **Step 2:** Headless Chrome screenshots at 1440×1000 and 390×844 (mobile) of `/portal`, `/portal/epics/DEMO-100`, `?tab=areas-matrix`, `?tab=test-scope`, `/portal/epics/DEMO-200`, `/portal/tickets/DEMO-101`, in light and dark (`--blink-settings=preferredColorScheme=0` for dark): `google-chrome --headless=new --disable-gpu --window-size=1440,1000 --screenshot=<file> <url>`.
- [ ] **Step 3:** Inspect each screenshot against the mockups; fix and re-shoot any page that overflows, clips, or loses contrast.
- [ ] **Step 4:** `uv run pytest -q` → all pass. Commit README `V4-1234: Document the self-contained portal assets`.
