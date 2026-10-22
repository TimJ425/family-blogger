# Family Blogger: code review (2026-10-21)

Review of `main` at `06d3fdc`. It was a read-only review, with security
weighted most heavily. Threat model: **home LAN only**. The realistic
adversaries are a curious or mischievous child account, a guest device
on the Wi-Fi, and malicious uploaded files or post content.

**How it was run**

| Check | Model | Result |
|---|---|---|
| Tests and flake8 | Haiku | 4/4 suites pass (122 backend checks); flake8 is clean |
| `pip-audit` | Haiku | No known CVEs in project dependencies; the local `.venv` has an old `pip` 23.2.1 (14 advisories) |
| Docs vs code | Haiku | All 27 routes match `TEMPLATE_CONTRACT.md`; minor drift |
| Deploy, CI and backup | Sonnet | See Medium and Low |
| App security | Opus | See High and Medium |
| Dead code and logic | Opus | See all sections |

The top findings were then checked by hand against the code. Some
severities were adjusted, for example the backup.sh "stale snapshot"
claim, which was downgraded because `set -e` already prevents it.

---

## 1. What the project does

A private, self-hosted diary and blog for one family, running on their
own network:

- **Diary entries:** each entry has a mood (Great, Good, Okay, Sad or
  Grumpy), a title, Markdown text and up to 10 photos.
- **Visibility:** each entry is shared with **Everyone** (the home
  feed), **Profile** (the author's profile only) or **Private** (the
  author and admins only).
- **Profiles:** a 30-day mood calendar and a 90-day mood chart.
- **Interaction:** comments and five "kind" reactions.
- **Search:** by text, person, mood and date.
- **Personalisation:** themes, custom colours, backgrounds and avatars.
- **PWA:** can be installed to a phone's home screen.
- **Admins (parents):** the first account becomes admin. Admins reset
  passwords, promote and demote, and delete posts and accounts.

## 2. Tools and stack

- **Back end:** Python 3.12, Flask 3.1, Flask-SQLAlchemy / SQLAlchemy
  2.1, Flask-Login, Flask-WTF (CSRF), SQLite.
- **Content:** Markdown and bleach (sanitising), Pillow (image
  re-encoding).
- **Serving:** waitress (production), Flask dev server (`--dev`).
- **Front end:** Jinja templates, vanilla JS with no build step, CSS
  custom properties (`--fb-*`), Chart.js from jsDelivr, Nunito from
  Google Fonts.
- **Ops:** Docker with Docker Compose (from source or from GHCR),
  `deploy/backup.sh`, and GitHub Actions (flake8 and tests, then a
  build and push to GHCR).
- **Quality:** flake8 with naming, docstring, annotation and
  tidy-import plugins; plain-script test suites, including a
  conventions checker.

## 3. What works well

- **Privacy is centralised and holds up.** Every listing (feed,
  profile, search including counts and pagination, mood calendar,
  `moods.json`, family page, uploads) goes through
  `utils/permissions.py`. A non-admin has no path to read a private
  post. Hidden posts return 404 rather than 403, so their existence
  isn't revealed.
- **Image handling is child-safe:**
  - Files are checked by content, not by extension.
  - Each image is re-encoded into a fresh image, which **strips EXIF
    and GPS** data.
  - Filenames are uuid4, gated by a strict pattern.
  - SVG is not accepted.
  - Size and count are capped.
  - Files are deleted when their post or user is deleted.
- **Sanitising and CSRF:**
  - Markdown is escaped first, then passes through a tight bleach
    allowlist: no `img` or `style`, and only http, https and mailto
    links.
  - There is no `|safe` in templates.
  - Theme colours are validated as `#rrggbb` before they reach CSS.
  - CSRF protection is global with no exemptions, and logout is
    POST-only.
- **Admin safeguards:**
  - Every admin route is POST-only and has `@admin_required`.
  - The last admin cannot be demoted or deleted.
  - `is_admin` is never bound from a form.
- **Backups:** `backup.sh` takes a SQLite-safe online snapshot with
  `sqlite3.backup()` and includes the uploads and the secret key.
- **CI and the Docker image:**
  - CI runs with least-privilege permissions, and tests gate the push.
  - The image is minimal, built with explicit `COPY`s, and includes a
    healthcheck.
  - Compose runs the container as a non-root user.
- **Works without JavaScript:**
  - Reactions fall back to a plain form post.
  - The mood chart has `<noscript>` and table fallbacks.
  - The onboarding wizard degrades to a single form.
- **Code discipline:** docstrings and type hints throughout, enforced
  by flake8 and the conventions tests. Ordering is stable (`created_at`
  then `id`). Username uniqueness ignores case. There is one reaction
  per user, post and kind.

## 4. What doesn't work well

- **Account lifecycle is the weak spot:**
  - Signup never closes.
  - Sessions survive password resets.
  - A deleted user's id can be reused by a new account.
- **Inline `onsubmit="confirm(...)"` handlers:**
  - They can be broken, and in one place exploited, through display
    names.
  - Deletion confirmation exists only in JS, which breaks the "forms
    work without JS" rule and the "deletions are confirmed" principle.
- **No schema migration path.** `db.create_all()` won't add columns to
  existing databases, so the first release that changes a model will
  break every family's install.
- **Release hygiene:**
  - Dependencies are unpinned (`>=`).
  - The base image is a floating tag.
  - `latest` tracks every push to `main`.
  - The image is amd64 only, so it won't run on a Raspberry Pi or an
    ARM NAS.
- **No security headers** (CSP, nosniff, frame-ancestors,
  Referrer-Policy).
- **Some edge-case bugs:** an empty page after deleting the last post
  on a page, old files deleted before the commit, IntegrityError races
  and N+1 queries.
- **Local dev environment is broken.** The `.venv/bin/flake8` and
  `.venv/bin/pip` shebangs point to an old `blog-app` path, so the
  commands documented in CLAUDE.md and CONTRIBUTING.md fail until the
  venv is recreated.

## 5. What should change (themes)

1. **Fix the account lifecycle:**
   - Admin-controlled membership.
   - A per-user session token that rotates on password change, reset
     and deletion.
   - `sqlite_autoincrement` on `User`.
2. **Move all confirmations to the server:**
   - Use a confirmation page or a `confirm` field.
   - Use the existing (unused) `data-confirm` handler in `app.js` as a
     JS enhancement.
   - Remove all inline JS handlers. This also makes a strict CSP
     possible.
3. **Add a small migration runner** based on `PRAGMA user_version`,
   with an automatic DB copy before migrating. It needs no new
   dependency.
4. **Make releases reproducible:**
   - Pin dependencies.
   - Pin the base image by digest.
   - Pin actions by SHA.
   - Build a multi-arch image.
   - Make `latest` follow release tags only.
5. **Defence in depth:** security headers, a login backoff, a pixel
   cap on images, and Secure cookies behind HTTPS.
6. **More local-first:** self-host Nunito and Chart.js, so page views
   stop calling Google and jsDelivr.

## 6. Suggested features

All of these fit the guiding principles: calm, family-only, parents in
charge, memories matter.

- **Admin "Add member" or invite code** (needed anyway for H2). Parents
  create children's accounts, so no one signs up from the Wi-Fi.
- **"On this day".** A quiet card on your own profile showing your
  entries from the same date in past years. It's about memories, not
  engagement.
- **Export my diary.** Each author can download their entries and
  photos as a ZIP (HTML or Markdown), a printable keepsake that they
  own.
- **Backup from the admin panel.** A "Download backup" button using
  the same safe snapshot, plus a documented `restore.sh`.
- **Drafts.** Save an entry and finish it later, so children don't
  lose half-written posts.
- **Gentle writing prompts.** Optional, rotating prompts such as "What
  made you smile today?" on the new-post page. No streaks and no
  reminders.
- **Transparency note for children.** A plain-language line on the
  Private option: "Only you and the family grown-ups can see this."
  It's honest about admin access.
- **Family photo album.** A per-person gallery of the photos you're
  allowed to see, using the existing visibility rules.
- **Admin activity log.** Recent failed logins, new accounts and
  deletions. It supports parental oversight without reading private
  content.

Items to **ask about first**, under principles 4 and 6:

- The family page shows **post counts** per person, which is close to
  a comparison metric.
- Whether remember-me should last 365 days.

---

## 7. Fix pipeline

### 🔴 High: security issues, broken code and broken functionality

| # | Issue | Where | Scenario | Fix |
|---|---|---|---|---|
| H1 | **Stored XSS leading to admin takeover through the display name** | `templates/admin/index.html:43`, `forms.py:105` | A child sets their display name to `x');fetch(...)//`. Jinja's `\|e` produces `&#39;`, which the browser decodes back to `'` inside the `onsubmit` JS. When an admin presses Delete on that child, the script runs as the admin and can POST `/admin/.../promote` with the page's CSRF token. Separately, any harmless name with an apostrophe ("O'Neill") breaks the handler, so the delete happens **with no confirmation**. | Use `data-confirm="{{ msg }}"` with the existing handler in `app.js:37-41`. Remove all inline `onsubmit` handlers. |
| H2 | **Signup is open to anyone on the LAN, forever** | `auth/routes.py:13-47` | A guest's phone opens `/signup`, then reads the family feed, profile posts, moods and comments, and can message the children. | After the first user, only admins can create accounts: an "Add member" form, or a signup toggle or invite code that defaults to closed. |
| H3 | **Sessions survive password reset, change and deletion; deleted user ids are reused** | `__init__.py:50`, `models.py:89`, `admin/routes.py:170`, `settings/routes.py:196` | A sibling who knows a password ticks "remember me", which gives a 365-day cookie. A parent resets the password, but the cookie still works. If the highest-id user is deleted, the next new account reuses that id, so the old cookie logs in as the new child. | Add a `session_token` column, use `get_id()` → `"id:token"`, check it in `load_user`, and rotate it on reset, change and delete. Add `sqlite_autoincrement=True` (needs a safe table rebuild). Shorten `REMEMBER_COOKIE_DURATION`. |
| H4 | **Deletions are confirmed only in JS** | `partials/delete_post_form.html:2`, `posts/view.html:44`, `admin/index.html:43` | With JS off or broken, as in H1, one tap permanently deletes a post or a whole member's diary. This breaks principle 5 and the no-JS rule. | Use a server-rendered "Are you sure?" step. For user deletion, require retyping the username. |
| H5 | **No schema migrations** | `__init__.py:98-99` | The next release that adds a column causes "no such column" errors and 500s on every family's install after `docker compose pull`. The recovery is hand-editing SQLite or losing data. | A `PRAGMA user_version` runner with ordered idempotent steps and an automatic `.db` copy first. Add "back up before upgrading" to the README. |

### 🟠 Medium: minor bugs and hardening gaps

| # | Issue | Where | Fix |
|---|---|---|---|
| M1 | Open redirect: `is_safe_url("/\\evil.com")` is True, and browsers treat `\` as `/`. `delete_post` re-implements the check with the same flaw. | `utils/__init__.py:26`, `posts/routes.py:208-215` | Reject `\` and control characters, and use `is_safe_url` everywhere. |
| M2 | No login throttling or lockout (passwords can be 6 characters). | `auth/routes.py:65-74` | A per-username and per-IP failure counter with backoff, and failures shown to admins. |
| M3 | Decompression bomb: Pillow only errors above about 179 MP, so a 10 MB PNG can need more than 1 GB of RAM (risk of OOM on a Pi). | `utils/uploads.py:87-103` | Check `img.size` before `load()` and cap at about 40 MP. |
| M4 | No security headers (CSP, nosniff, frame-ancestors, Referrer-Policy). Post URLs leak to external sites through the Referer. | `__init__.py` | An `after_request` hook. A CSP becomes strict once the inline handlers (H1, H4) are gone. |
| M5 | Cookies: no `Secure` flag behind the HTTPS proxy, no `REMEMBER_COOKIE_SAMESITE`, and remember-me lasts 365 days. | `config.py:60-62` | Set Secure when `FB_TRUST_PROXY=1`, `SAMESITE=Lax`, and a shorter duration. |
| M6 | Deleting the last post on page N shows "No posts yet", which looks like data loss. Out-of-range `?page=` does the same. | `posts/routes.py:35-40`, `feed.html:12-18`, `profile/view.html:41` | Redirect to the last page, and show the empty state only when `total == 0`. |
| M7 | Old avatar and background files are deleted **before** the DB commit, so a failed commit leaves a broken image. | `settings/routes.py:102-104, 158-160`, `onboarding/routes.py:69-77` | Delete after `commit()`, as the post and user deletion code already does. |
| M8 | IntegrityError races (a double-tapped reaction, simultaneous signup) cause 500s. | `posts/routes.py:291-302`, `auth/routes.py` | Catch `IntegrityError` and roll back. |
| M9 | The 500 handler may itself fail with PendingRollbackError *(plausible)*. | `__init__.py:246-248` | Call `db.session.rollback()` first. |
| M10 | N+1 queries: about 30–40 per feed page, and `comment_count` loads every comment row. | `post_card.html`, `models.py:300-302` | Add `selectinload` and `joinedload` in `feed_query` and `profile_query`, and count comments with a grouped query. |
| M11 | Image is amd64 only, so `docker compose pull` fails on a Raspberry Pi or ARM NAS. | `.github/workflows/docker.yml:77` | Add QEMU and set `platforms: linux/amd64,linux/arm64`. |
| M12 | Builds aren't reproducible: `>=` dependencies, a floating `python:3.12-slim` image, actions pinned by tag, and `latest` updated on every push to main. | `requirements*.txt`, `Dockerfile:1`, `docker.yml`, `.env.example:15` | Pin versions, the image digest and action SHAs. Make `latest` follow release tags only. Add Dependabot. |
| M13 | backup.sh: archives are world-readable (they contain the diary and the secret key), there's no retention, and a failed run leaves an empty folder behind. | `deploy/backup.sh` | Add `umask 077`, a `trap` cleanup, a `find -mtime +N -delete` prune, and `PRAGMA integrity_check` on the snapshot. |
| M14 | Restore docs are thin: no ownership step and no warning about stale `-wal`/`-shm` files. | `README.md:129-133` | Exact restore commands, or a `restore.sh`. |
| M15 | Some phone JPEGs (MPO format) are rejected *(plausible)*. | `utils/uploads.py:12` | Add `"MPO"` to `ALLOWED_FORMATS`. |
| M16 | Search treats `%` and `_` as wildcards, and `ilike` is ASCII-only in SQLite. | `search/routes.py:39-40` | Escape wildcards and pass `escape="\\"`. |
| M17 | Local `.venv` is broken (stale shebangs) and has `pip` 23.2.1 with 14 advisories. | `.venv/` | `rm -rf .venv` and recreate it as in CLAUDE.md. |
| M18 | Docs drift: `has_posted_today` is missing from the contract, and hard-coded colours break the `--fb-*` rule. | `TEMPLATE_CONTRACT.md:213`, `main.css:154,187,216,309,337-338` | Update the contract and move colours into variables. The mood colours may be intentional, so confirm that first. |

### 🟢 Low: suggested improvements

**Security and ops hardening**

- **First-admin race.** Any LAN device can claim admin before the
  parent does. Fix with a setup token or CLI creation.
- **Last-admin guard race.** Two admins demoting each other at once
  can both pass the check.
- **Login timing** reveals whether a username exists. Fix by doing a
  dummy hash check.
- **`FILENAME_RE`** matches before a trailing newline. Use
  `fullmatch`.
- **`--dev` binds to `0.0.0.0` with the debugger on.** Default it to
  `127.0.0.1`.
- **Proxy headers:** `FB_TRUST_PROXY=1` is hard-wired while the port
  is published to the whole LAN.
- **Secret key file:** use `O_EXCL` with `0600` at creation, rather
  than writing it and then running `chmod`.
- **Waitress:** set `max_request_body_size` to about 45 MB.
- **SQLite:** use WAL and `busy_timeout`, and consider
  `PRAGMA foreign_keys=ON`.
- **CI:** add a `concurrency` group and a pip-audit or Trivy step.
- **`.dockerignore`:** fill the gaps (`.github/`, `*.env`, editor
  folders).
- **Healthcheck:** have it touch the DB.
- **Self-host Nunito and Chart.js** (local-first).
- **bleach is deprecated.** Plan a replacement, but ask before adding
  nh3.

**Bugs and quirks**

- **Search dates:** a "From" date after the "To" date silently returns
  nothing. Add a validator.
- **Invalid search form:** it also shows "No posts found" next to the
  field errors.
- **`delete_post` `next` check:** `/post/1x` passes the prefix check.
- **Mood chart spacing:** the x-axis spaces entries evenly rather than
  by date, so use a time scale.
- **Onboarding:** pressing Enter on step 1 submits the whole wizard
  *(plausible)*.
- **Daylight saving:** naive local timestamps can misorder posts
  during the autumn clock-change hour.
- **Invalid custom colours** show as black in the colour picker.

**Dead code**

- `Post.user_reacted`, used only by tests.
- The `csrf()` macro in `macros.html`, which is never imported.
- CSS classes `.stack`, `.btn-block`, `.form-grid`,
  `.post-body.clamped` and `.i-globe`.
- The empty `.wizard-submit-nojs` div.
- `Reaction.created_at`, which is written but never read.
- The `data-confirm` JS in `app.js` isn't dead: reuse it for H1 and
  H4.

**Duplication**

- The `"sunny"` preset is hard-coded in place of `DEFAULT_PRESET`.
- The colour keys are repeated in 3 places.
- There are two sets of visibility descriptions.
- The 10-photo, 10 MB and 40 MB limits are hard-coded in text.
- A page-title inline style is repeated in 7 templates.
- The post header and action markup is duplicated in `post_card` and
  `view`.

**Docs**

- `macros.html` and `app.js` are missing from `TEMPLATE_CONTRACT.md`.
- `FB_INSTANCE_DIR` isn't in `.env.example`.
- The README says "no third-party" but the app uses CDN fonts and
  Chart.js.
- The README install folder list omits `backups/`.

**Test gaps to add with the fixes**

- `is_safe_url`, `?next=` and crafted `next` values.
- A display name containing `'` (H1).
- Session invalidation after a reset (H3).
- Pagination out of range (M6).
- The 413 and CSRF-expired handlers.
- The photo-limit error and file clean-up.
- The admin password reset success path.
- Search where "From" is after "To", and `%` handling.

---

### Suggested order of work

1. **H1 and H4 together:** remove the inline handlers, add server-side
   confirmation and `data-confirm`, and add tests.
2. **H5:** the migration runner, before any model change.
3. **H2 and H3 together:** admin-managed membership plus the session
   token. This becomes the first migration (a new column and the
   autoincrement rebuild).
4. **M1–M5:** security hardening.
5. **M11–M14:** release and backup hygiene.
6. Remaining Medium items, then Low as time allows.

---

## 8. Resolution (2026-10-22)

All findings above were fixed on branch `fix/high-priority-review`.
Three further independent reviews then ran on the fixed code. Each
found new issues, mostly in code the fixes had added, and all of those
were fixed too. The last review found no High or Medium issues.

### Status of the original findings

| Severity | Count | Status |
|---|---|---|
| 🔴 High (H1–H5) | 5 | Fixed |
| 🟠 Medium (M1–M18) | 18 | Fixed, except M18's mood colours, which stay fixed by decision (see below) |
| 🟢 Low | all | Fixed, except where noted below |

### Main changes

- **Accounts:**
  - Only the very first account can use `/signup`; admins add every
    other member.
  - Each user has a session token that changes when their password
    changes or is reset, and when they choose "Log out on my other
    devices".
  - Remember-me lasts 30 days.
  - User ids are never reused (migration 1).
- **Deleting:** a server-rendered "Are you sure?" page, with a
  JavaScript dialog when JS is on. Deleting a member requires typing
  their username.
- **Database changes:** a migration runner based on
  `PRAGMA user_version` (migrations 1–3).
  - It backs up the database before migrating and doesn't duplicate
    that backup.
  - It runs checks before changing anything, and refuses to drop
    columns unless a step names them.
- **Browser security:**
  - A strict Content-Security-Policy, with no inline scripts, event
    handlers or `style` attributes, and a nonce on the theme `<style>`.
  - Security headers on every response.
  - The open redirect is fixed.
  - Markdown is sanitised with nh3 instead of bleach.
- **Logging in:**
  - **Per-device pause:** after 5 wrong tries, that name is paused for
    5 minutes on that device only.
  - **Per-device limit:** 20 wrong tries across any names pause that
    device for 15 minutes.
  - **Distributed limit:** 30 wrong tries for one name in an hour, from
    6 or more devices, pause that name everywhere.
  - **Trusted browsers:** a browser where a member has logged in before
    gets a signed cookie, so they aren't paused because of someone else
    at the same address.
  - **Admin controls:** admins can clear paused names and unpause
    devices.
  - **Proxies:** `FB_TRUSTED_PROXIES` controls which reverse proxies
    are trusted to report a device's real address.
- **Uploads:**
  - 24-megapixel limit, checked before the image is decoded.
  - At most two images are processed at once.
  - Phone MPO photos are accepted.
  - Files are only deleted after the database change succeeds.
- **Privacy and children's safety:**
  - Display names that look like another member's are refused,
    including Unicode lookalikes and invisible characters.
  - Fonts and Chart.js are served by the app itself, so pages make no
    requests to other servers.
- **Operations:**
  - Dependencies, `constraints.txt`, the base image digest and the CI
    actions are all pinned to exact versions.
  - CI runs pip-audit, Dependabot is set up, and the image is built for
    amd64 and arm64.
  - `:latest` is only published from release tags.
  - The container runs as a non-root user.
  - `backup.sh` makes owner-only archives, keeps the newest 14, checks
    each snapshot's integrity and cleans up on failure.
  - The new `restore.sh` asks for confirmation and takes a safety copy
    first.
- **Bugs:**
  - Pagination is handled properly, including after deleting the last
    post on a page.
  - Duplicate-insert races no longer cause 500 errors.
  - The error page rolls back the database session before rendering.
  - N+1 queries are removed.
  - Search escapes `%`, `_` and `\`, and matches non-ASCII text without
    regard to case.
  - The mood chart spaces entries by date.
  - Fixes to onboarding, colour inputs and date validation.
- **Cleanup:** dead code and duplication removed, colours only come
  from `--fb-*` variables, and the docs and template contract are up to
  date.

### Decisions made with the user

- **Family-page post counts:** kept.
- **Mood colours:** fixed across themes, as `--fb-mood-*` variables.
- **New dependencies and services:** nh3, pip-audit and Dependabot were
  approved.
- **Fonts and Chart.js:** self-hosted.
- **Lookalike display names:** blocked.
- **"Log out on my other devices":** added.

### Deliberately not done

- **UTC timestamps:** storing times in UTC needs a data migration.
  Instead, `is_edited` was made safe across clock changes, and a
  comment explains this.
- **Cyrillic and other script homoglyphs** in display names are
  accepted. Invisible characters and compatibility forms are blocked.

### Verification

- **Tests:** 11 suites, about 1,100 checks, plus flake8. All pass.
- **pip-audit:** no known vulnerabilities.
- **Browser walkthrough:** 23 steps in headless Chromium, as a parent,
  a child on a phone, a browser with JavaScript off, and a second
  device. There were no CSP violations, JavaScript errors or 5xx
  responses.
- **Not verified locally:** the Docker image and arm64 builds (Docker
  isn't installed here). The first pull request will run them in CI.

### Release steps

1. Run `./backup.sh` on the server.
2. Open a pull request into `main` and merge once CI is green.
3. Tag `v1.0.0`.
4. If you use a reverse proxy, set `FB_TRUSTED_PROXIES`.
5. Expect everyone to be logged out once.
