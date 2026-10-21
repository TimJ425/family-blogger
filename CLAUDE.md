# Family Blogger: guidance for Claude

## Intro

Family Blogger is not meant to copy any other social media platform. It is a social blog that a family runs on their own local network, as a gentle first experience of a social platform for their children:

- parents can keep an eye on it
- children can blog safely, with posts that are personal or private
- the family has a place to keep precious memories

The most common use is a daily entry: how someone feels and what they did today.

## Guiding principles

Respect these whenever you add or change a feature. If a request seems to conflict with one, **ask the user before going ahead**.

1. **Local-first and private.**
   - The app runs on the family's own network and hardware.
   - No ads, analytics, tracking or telemetry.
   - Never send data to third parties.
   - Ask before adding any external service. CDN-hosted assets are fine, but nothing that phones home.
2. **Safe for children.**
   - Content, imagery and wording stay family-friendly.
   - Nothing should make it easier for strangers to find or contact children: no public discovery and no sharing outside the family.
3. **Parents stay in charge.**
   - Keep admin oversight: password resets, moderation and user management.
   - Never add a feature that hides activity from admins without asking first.
4. **Calm, not compulsive.**
   - No follower counts, popularity rankings, streak pressure, algorithmic or infinite feeds, or attention-grabbing notifications.
   - Reactions are kind gestures, not scores.
   - The feed stays chronological.
5. **Memories matter.**
   - Never risk losing data: database changes must be safe, deletions are confirmed, and `deploy/backup.sh` must keep working.
   - Diary entries belong to their author.
6. **Not a clone.** Ask before adding features modelled on mainstream social platforms, such as stories, direct messages, public profiles or trending.
7. **Simple to run.** The stack is Python, Flask and SQLite, deployed with Docker Compose. Keep dependencies to a minimum and ask before adding one.

## Working with the user

- **Don't assume anything; ask.** When requirements are unclear, use clarifying questions before building.
- When using sub-agents, pick a model that fits the task: a larger model for complex work, a smaller one for simple, well-defined jobs.

## Coding rules (summary)

The full rules, with examples, are in [CONTRIBUTING.md](CONTRIBUTING.md). They are not optional.

**Python**

- **Modules:** every module starts with a docstring.
- **Docstrings:** every function and class, including tests, private helpers and nested functions, has a Google-style docstring (`Args:`, `Returns:`, `Raises:`).
- **Type hints on functions:** annotate every argument and every return value. `self` and `cls` are left unannotated.
- **No variable type hints**, global or local. This includes class attributes, so SQLAlchemy models use `db.Column(...)` and never `Mapped[...]`.
- **Allowed types:**
  - Never import `typing` or `collections.abc`.
  - Use only builtins (`str`, `int`, `dict`, `list[str]`, `X | None`, `object`) and project or library classes (`User`, `Post`, `FileStorage`, `Response`).
  - Write forward references in quotes (`user: "User"`).
  - Write callables and `*args`/`**kwargs` as `object`.
- **Style:** PEP 8, with lines of at most 79 characters and docstrings and comments of at most 72.

**Front end**

- Colours only come from the `--fb-*` CSS variables, because users choose their own palettes.
- JavaScript is vanilla, with no build step.
- Every form works without JavaScript, and every POST carries a CSRF token.
- Design mobile-first and keep it accessible: labels, alt text, focus states and tap targets of at least 44px.
- When routes or template context change, update `TEMPLATE_CONTRACT.md` to match.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python run.py --dev        # debug server on :8765
.venv/bin/python tests/run_all.py    # all test suites + conventions check
.venv/bin/flake8                     # PEP 8, docstrings, type hints
```

Before saying any change is finished, run both `tests/run_all.py` and `flake8`, and both must be clean.

## Layout

| Path | Contents |
|---|---|
| `family_blogger/` | Flask app factory (`__init__.py`), `models.py`, `forms.py`, `constants.py` |
| `family_blogger/<blueprint>/routes.py` | `auth`, `onboarding`, `posts`, `profile`, `settings`, `admin`, `search`, `uploads` |
| `family_blogger/utils/` | `permissions.py` (all visibility rules), `uploads.py`, `markdown.py` |
| `family_blogger/templates/`, `family_blogger/static/` | Jinja templates, CSS, JS, SVG and PNG assets |
| `tests/` | Plain-script test suites; `run_all.py` runs them all |
| `deploy/` | `Dockerfile`, `compose.yaml` (builds from source), `compose.ghcr.yaml` (pulls the published image), `backup.sh`, `.env.example` |
| `.github/workflows/docker.yml` | CI: flake8 + tests, then builds and pushes `ghcr.io/timj425/family-blogger` |
| `instance/` | Local data: database, uploads and secret key. Git-ignored; never commit or read it into context |

## Privacy rules

- All visibility decisions go through `family_blogger/utils/permissions.py`. A private post is visible only to its author and admins, and that must never leak into the feed, search, profiles, uploads or JSON endpoints.
- Personal data, such as real names, IP addresses, emails and paths under `/Users/...`, never goes into committed files.
