# QA Impact Portal redesign (short design)

Date: 2026-11-24. Size: small change (restyle of existing pages; no new features or data).

## Intent

QA engineers use the portal to decide what to test before a release. Today it looks like a bare admin page
(gridded tables, system-default form controls, inline summary text). Give it a professional, modern look so it
is quicker to scan and reads as a real internal product.

## Goals

- Layout **A** (picked): dark top bar with product name, `Epics` and `Tickets` sections and a search box; the
  Epic list is the landing page; each Epic opens full width. See `2026-11-24-portal-redesign/mockup-epic-page.html`
  (option A) and `mockup-epic-list.html`.
- Epic page: breadcrumb, title + monospace key, "Report vN · +n commits since vN-1" line, summary cards
  (tickets, developers, areas, overlaps, high risk), tabs with counts, content in white table cards.
- Epic list: full-width search, project filter, "High risk only" toggle; right-aligned numbers; risk badges;
  "Report pending" / "Test scope pending" as small state labels; the two catch-all rows under a
  "Not in an Epic" group heading.
- Visual language: soft severity badges with a coloured dot (red high, amber medium, grey low); overlap rows
  amber with a left accent bar; green "new in vN" tags; quiet tables (row dividers only, uppercase small
  headers); monospace ticket keys; visible keyboard focus.
- Ticket page, matrix, matrix cell, test scope, risks and history get the same styling.
- Dark mode kept (follows the OS), same design in dark tokens.

## Non-goals and anti-goals

- No new features: no checklists, sign-off, editing or new data. The portal stays read-only.
- No change to routes, query parameters, data or the htmx tab behaviour (tab switch still swaps `#tabs`).
- Anti-goal: anything loaded from a public CDN or the internet (blocked on the corporate network).

## Constraints

- Nothing from public CDNs: htmx is vendored into `web/static/` (same version 1.9.12) and loaded from
  `/portal/static/`; system font stack; icons, if any, inline SVG.
- Server-rendered Jinja2 with autoescape stays; no front-end build step or framework.
- Existing page tests keep passing (they assert content, not markup), plus a test that fails if any portal
  page references an external `http(s)://` script, stylesheet or font.
- Accessible contrast in both themes (WCAG AA for text and badges).

## Approaches

1. **Hand-written stylesheet with design tokens** (picked, builder's call; the user handed over the how).
   Rewrite `portal.css` around CSS variables (colour, spacing, type, radius) with light and dark values;
   update template markup. No build step, no dependencies, exact control of the look.
2. Vendored Bootstrap: ready components, but ~200 KB, generic look, overriding its defaults to reach the
   mockup.
3. Tailwind: needs a Node build step in the image and utility classes through every template; too much
   machinery for 12 small pages.

## Left to the builder

Exact token values, spacing scale, responsive breakpoints (the summary cards wrap on narrow screens; wide
tables scroll horizontally), empty-state wording, and how the top-bar search submits (to the Epic list `q`).
