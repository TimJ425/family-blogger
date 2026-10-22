# Agent prompt checklists

Paste the shared preamble into every prompt, then the agent's checklist.

## Shared preamble

> Read-only: do NOT modify files. Never read anything under `instance/`
> (real family data). Follow `~/.claude/skills/targeted-reading/SKILL.md`
> and `~/.claude/skills/quiet-output/SKILL.md`. Threat model:
> <answer from the user, e.g. home LAN only — adversaries are a curious
> child account, a guest device on the Wi-Fi, malicious uploads or post
> content>. For each finding give file:line, severity (High = security
> hole, privacy leak, broken functionality or data-loss risk; Medium =
> minor bug or realistic hardening gap; Low = improvement), a concrete
> failure scenario, and a one-line fix. Verify by reading the code; mark
> anything unverified PLAUSIBLE. Also list what is done well.

## Test runner (haiku)

- Ensure `.venv` exists with both requirements files installed.
- `tests/run_all.py` → log; pass/fail per suite with exact failure lines.
- `python -m flake8` → violations grouped by code.
- If allowed: install `pip-audit` into `.venv` only (never into
  requirements files); run against `requirements.txt` and the env.
- Report versions of Flask, Werkzeug, SQLAlchemy, Flask-Login,
  Flask-WTF, waitress, Pillow, Markdown, nh3, Jinja2.

## Docs checker (haiku)

- Every route in `TEMPLATE_CONTRACT.md` vs `family_blogger/*/routes.py`
  (URL, methods, endpoint, template, context variables) and the reverse.
- README feature claims vs code.
- Rules: module/function docstrings, no `typing`/`collections.abc`, no
  `Mapped[`, colours only via `--fb-*`, CSRF token in every POST form,
  `alt` on every `<img>`, labels on inputs.
- `deploy/.env.example` vs what `config.py` and compose read.

## Deploy/CI (sonnet)

Files: `deploy/*`, `.dockerignore`, `.gitignore`,
`.github/workflows/*.yml`, `config.py`, `run.py`, `requirements*.txt`,
`family_blogger/__init__.py`.

- Container user, base image pinning, healthcheck, what the image copies.
- Secret key creation and permissions, debug exposure, waitress limits.
- Cookie flags, session lifetime, remember-me, security headers (and
  what a CSP would need given CDN assets and inline scripts).
- `backup.sh`: SQLite-safe snapshot, includes uploads and secret key,
  error handling, retention, archive permissions, restore docs.
- Schema change strategy (migrations vs `create_all` only).
- CI: action pinning, permissions, tests gate the push, `latest` tag
  policy, multi-arch (Raspberry Pi), concurrency.
- Unpinned dependencies; personal data in tracked files.

## Security (opus)

- Every route: authorization, IDOR, admin gating, last-admin rules,
  mass assignment.
- Private posts must not leak via feed, search (counts, snippets,
  filters, pagination), profiles, mood calendar/chart/JSON, uploads,
  direct URLs. All checks should go through `utils/permissions.py`.
- Signup policy after the first admin; first-admin race; password
  hashing, minimum length, rate limiting; session fixation; sessions
  invalidated on password change/reset/deletion; user id reuse;
  remember-me; `next` open redirects (including backslash tricks).
- CSRF on every state change; no state-changing GETs.
- XSS: markdown + nh3 allowlist, CSP (no inline scripts/handlers/styles), `|safe`/Markup, user values inside
  inline JS (`onsubmit`, `onclick`) or `<style>`, SVG.
- Uploads: content validation, re-encoding (EXIF/GPS stripped),
  decompression bombs, size limits, traversal, file clean-up.
- Raw SQL / LIKE escaping; error pages and user enumeration.

## Dead code & logic (opus)

- Dead code: functions, model columns, form fields, templates, macros,
  CSS classes, JS selectors, unlinked routes — each grep-verified across
  `family_blogger/` and `tests/` including Jinja and dynamic class names.
- Walk the flows: onboarding → signup → login → post (mood, visibility,
  photos) → edit → delete → feed pagination → comments/reactions →
  search → profile calendar/chart → settings → admin actions.
- Data integrity: constraints, cascades, file deletion order vs commit,
  IntegrityError races, N+1 queries, timezones.
- Forms that fail without JS; destructive actions without server-side
  confirmation.
- Test coverage gaps; worthwhile duplication clean-ups.
