# Contributing to Family Blogger

Thank you for helping. Please read the guiding principles in [CLAUDE.md](CLAUDE.md#guiding-principles) first. Family Blogger is a calm, private, family-only space, and new features should keep it that way.

By contributing, you agree that your work is released under the project's licence, the [GNU AGPL v3.0](LICENCE.md).

## Running it locally

You need Python 3.12 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python run.py --dev       # debug server with auto-reload
```

Then open http://localhost:8765. To try it on a phone, use `http://<your-computer's-IP>:8765` from a device on the same Wi-Fi.

- Without `--dev`, `run.py` uses the production server (waitress), the same one the Docker image runs. To change where it listens, use `--host` and `--port`. Never use `--dev` for real family use.
- Local data (the database, uploads and secret key) lives in `instance/`, which is git-ignored. Delete that folder to start fresh. Set `FB_INSTANCE_DIR` to keep the data somewhere else.
- `FB_TRUSTED_PROXIES` (comma-separated IP addresses or CIDR networks, empty by default) lists the reverse proxies whose `X-Forwarded-*` headers are believed; requests from anywhere else have those headers ignored. Log-in pauses rely on it to tell devices apart behind a proxy. The legacy `FB_TRUST_PROXY=1` still works but believes any direct peer, so prefer `FB_TRUSTED_PROXIES`.
- To build and run the Docker image locally:

  ```bash
  cd deploy && mkdir -p data && docker compose up --build
  ```

## Checks

Before you submit a change, both of these must pass with no errors:

```bash
.venv/bin/python tests/run_all.py   # backend, pages, mobile + coding-rules check
.venv/bin/flake8                    # PEP 8, docstrings, type hints, banned imports
```

GitHub Actions runs the same checks on every push and pull request (`.github/workflows/docker.yml`). A Docker image is only published to GHCR when they pass.

The tests use a temporary database, so your `instance/` data is never touched. Each suite is a plain script, so you can also run one on its own, for example `.venv/bin/python tests/test_backend.py`.

## Python rules

### 1. Module docstrings

Every `.py` file starts with a docstring that explains what the module is for.

```python
"""Visibility rules deciding who can see which posts."""
```

### 2. Google-style docstrings on every function and class

This includes private helpers, nested functions, `__init__` methods and test helpers.

```python
def can_view(post: Post, user: User | None) -> bool:
    """Decide whether a user may see a post.

    Args:
        post: The post being viewed.
        user: The signed-in user, or ``None`` when signed out.

    Returns:
        ``True`` if the post is visible to the user.
    """
```

- Add `Args:` when the function takes arguments, `Returns:` when it returns something other than `None`, and `Raises:` when it deliberately raises.
- A `@property` gets a one-line summary written as a description, for example `"""The URL of the user's avatar image."""`.
- A class docstring describes the class. If it has notable attributes, list them under `Attributes:`.

### 3. Type hints on arguments and return values

Every argument and every return value is annotated. `self` and `cls` are not.

```python
def save_image(file_storage: FileStorage, kind: str = "post") -> str:
def admin_required(view: object) -> object:            # callables -> object
def wrapped(*args: object, **kwargs: object) -> object:
def can_edit(self, user: "User") -> bool:              # forward reference
```

### 4. No variable type hints

Do not annotate variables anywhere: module globals, locals, or class attributes.

```python
# Wrong
count: int = 0
username: str = db.Column(db.String(30))

# Right
count = 0
username = db.Column(db.String(30))
```

This is why the SQLAlchemy models use `db.Column(...)` and never `Mapped[...]`.

### 5. Never use the `typing` library

Do not import `typing` or `collections.abc`. Describe types using only:

- **builtins:** `str`, `int`, `bool`, `float`, `bytes`, `dict`, `list`, `tuple`, `set`, `object`, `None`
- **builtin generics:** `list[str]`, `dict[str, int]`, `tuple[int, ...]`
- **union syntax:** `str | None`
- **classes from this project or its libraries:** `User`, `Post`, `FileStorage`, `flask.Response`, `werkzeug.Response`

| Instead of | Use |
|---|---|
| `Optional[X]` | `X \| None` |
| `List[X]`, `Dict[K, V]` | `list[X]`, `dict[K, V]` |
| `Any`, `Callable`, `Iterable` | `object` (describe it more precisely in the docstring) |

### 6. PEP 8

- Lines are at most **79** characters. Docstrings and comments are at most **72**.
- Use `snake_case` for functions and variables, `PascalCase` for classes and `UPPER_CASE` for constants.
- Don't use `# noqa` except in these cases, each with a short comment explaining why:
  - **A name the library forces on you**, such as Markdown's `extendMarkdown`: `# noqa: N802`.
  - **The `from . import routes` line at the bottom of a blueprint's `__init__.py`**, needed because `routes.py` imports `bp`: `# noqa: E402,F401`.
  - **Project imports in `tests/` that come after the `sys.path.insert(...)` line:** `# noqa: E402`.

These rules are checked automatically. `flake8` (configured in `.flake8`) covers style, naming, docstrings, annotations and banned imports. `tests/test_conventions.py` catches variable annotations and anything flake8 misses.

## Front-end rules

- **Colours:** use only the `--fb-*` CSS custom properties, because each user chooses their own palette. Never hard-code a colour in a template.
- **Content-Security-Policy:** every response carries a strict CSP, so templates must not contain inline `<script>` code (data goes in `data-*` attributes or `<script type="application/json">`), `on*=` event handlers or `style=""` attributes. Put code in `static/js/` and styles in `main.css`. The only `<style>` is the theme block in `base.html`, which carries the request's nonce. Fonts and libraries are self-hosted; never link to an outside host. `tests/test_security.py` checks this.
- **JavaScript:** vanilla JS only, with no build step and no frameworks.
- **Forms:** every form must still work without JavaScript and include `csrf_token`. Any action that changes data must be a POST.
- **Mobile and accessibility:** design mobile-first, give every input a `<label>`, give images `alt` text, keep focus states visible and make tap targets at least 44px.
- **Template contract:** if you change a route or the context a template receives, update `TEMPLATE_CONTRACT.md` to match.

## Privacy and safety

- All visibility rules live in `family_blogger/utils/permissions.py`. Use its helpers, and never write your own visibility checks in a route.
- Never commit personal data. `instance/`, `deploy/data/`, `.env` files and backups are git-ignored.
- Don't add trackers, analytics, ads or anything that sends data off the family's network.
