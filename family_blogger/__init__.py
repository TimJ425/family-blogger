"""Family Blogger application factory."""
import secrets
from datetime import date, datetime
from pathlib import Path

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import event
from werkzeug.wrappers import Response

from config import (
    DEFAULT_INSTANCE_DIR,
    Config,
    cookie_secure_from_env,
    load_or_create_secret_key,
    trust_any_proxy_from_env,
    trusted_proxies_from_env,
)

from .constants import (
    DEFAULT_AVATARS,
    MOODS,
    REACTIONS,
    THEME_COLOUR_LABELS,
    THEME_PRESETS,
    VISIBILITIES,
)
from .migrations import migrate_database
from .models import User, db, resolve_theme
from .utils import is_safe_url, redirect_back
from .utils.markdown import render_markdown
from .utils.proxy import TrustedProxyMiddleware

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to see the family diary."
login_manager.login_message_category = "info"

csrf = CSRFProtect()

# Endpoints reachable without logging in.
PUBLIC_ENDPOINTS = {"auth.login", "auth.signup", "static"}
# Endpoints reachable while onboarding is unfinished.
ONBOARDING_EXEMPT = {
    "onboarding.welcome", "auth.logout", "static", "uploads.serve",
}


# Sent with every response. The Content-Security-Policy only allows
# this site's own files: no inline scripts, no inline style attributes
# and no outside servers. The one inline <style> (the user's theme
# colours in base.html) carries a per-request nonce, which is added to
# style-src in ``_csp_header``.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}
CSP_DIRECTIVES = [
    "default-src 'self'",
    "img-src 'self' data: blob:",
    "style-src 'self'",
    "script-src 'self'",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
]


def csp_nonce() -> str:
    """Return this request's CSP nonce, making it on first use.

    Returns:
        A random URL-safe string, the same for the whole request.
    """
    nonce = g.get("csp_nonce")
    if nonce is None:
        nonce = g.csp_nonce = secrets.token_urlsafe(16)
    return nonce


def _csp_header() -> str:
    """Build the Content-Security-Policy value for this response.

    The nonce is only added when a template used it.

    Returns:
        The policy, directives separated by ``"; "``.
    """
    nonce = g.get("csp_nonce")
    directives = [
        f"{d} 'nonce-{nonce}'" if nonce and d == "style-src 'self'" else d
        for d in CSP_DIRECTIVES
    ]
    return "; ".join(directives)


@login_manager.user_loader
def load_user(user_id: str) -> "User | None":
    """Load a user for Flask-Login from a session or remember cookie.

    The stored value is ``"<id>:<session_token>"`` (see
    ``User.get_id``). It only matches while the token is unchanged, so
    a password reset or a deleted account signs that cookie out.
    Malformed values and the old plain-id format are rejected.

    Args:
        user_id: The login id from the cookie.

    Returns:
        The matching user, or ``None`` if the value is malformed, the
        user is gone or the token does not match.
    """
    raw_id, sep, token = (user_id or "").partition(":")
    valid_id = raw_id.isascii() and raw_id.isdigit() and len(raw_id) < 19
    if not sep or not token or not valid_id:
        return None
    user = db.session.get(User, int(raw_id))
    if user is None or not user.session_token:
        return None
    if not secrets.compare_digest(
            user.session_token.encode(), token.encode()):
        return None
    return user


def create_app(overrides: dict | None = None) -> Flask:
    """Build and configure the Flask application.

    Creates the instance and upload folders, loads the config and secret
    key, sets up the extensions, blueprints, hooks, template helpers and
    error handlers, and creates any missing database tables.

    Args:
        overrides: Config values that take precedence over the defaults.
            ``INSTANCE_DIR`` moves the instance folder (used by tests).

    Returns:
        The configured application.
    """
    overrides = overrides or {}
    instance_dir = Path(overrides.get("INSTANCE_DIR", DEFAULT_INSTANCE_DIR))
    instance_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, instance_path=str(instance_dir))
    app.config.from_object(Config)
    app.config.update(
        SECRET_KEY=load_or_create_secret_key(instance_dir),
        SQLALCHEMY_DATABASE_URI=(
            f"sqlite:///{instance_dir / 'family_blogger.db'}"),
        UPLOAD_FOLDER=str(instance_dir / "uploads"),
        SESSION_COOKIE_SECURE=cookie_secure_from_env(),
        REMEMBER_COOKIE_SECURE=cookie_secure_from_env(),
        TRUSTED_PROXIES=trusted_proxies_from_env(),
        TRUST_ANY_PROXY=trust_any_proxy_from_env(),
    )
    app.config.update(overrides)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    # Behind a reverse proxy (e.g. Nginx Proxy Manager doing HTTPS),
    # believe one hop of X-Forwarded-* headers, but only from the
    # proxies in FB_TRUSTED_PROXIES, so redirects keep the right
    # scheme and log-in throttling sees each device's own address.
    # TRUST_ANY_PROXY is the legacy FB_TRUST_PROXY=1, which believes
    # any direct peer; kept only so older setups keep working.
    if app.config["TRUSTED_PROXIES"] or app.config["TRUST_ANY_PROXY"]:
        app.wsgi_app = TrustedProxyMiddleware(
            app.wsgi_app, app.config["TRUSTED_PROXIES"],
            trust_any=app.config["TRUST_ANY_PROXY"])

    db.init_app(app)
    with app.app_context():
        event.listen(db.engine, "connect", _set_sqlite_pragmas)
    login_manager.init_app(app)
    csrf.init_app(app)

    _register_blueprints(app)
    _register_hooks(app)
    _register_template_helpers(app)
    _register_error_handlers(app)

    with app.app_context():
        migrate_database(app)

    return app


def _set_sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
    """Configure each new SQLite connection.

    * ``foreign_keys=ON`` makes SQLite enforce the foreign keys.
    * ``journal_mode=WAL`` lets readers carry on while someone writes.
      The database then also uses ``-wal`` and ``-shm`` files next to
      it; restore a backup only while the app is stopped, and delete
      any old ``-wal``/``-shm`` files first.
    * ``busy_timeout=5000`` waits up to five seconds for a lock
      instead of failing at once with "database is locked".

    Args:
        dbapi_conn: The new DB-API (``sqlite3``) connection.
        _record: SQLAlchemy's connection record (unused).
    """
    # SQLite's own lower()/ILIKE only fold ASCII, so search uses this
    # Unicode-aware function instead (see search_query).
    dbapi_conn.create_function(
        "fb_fold", 1, _fold_text, deterministic=True)
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _fold_text(value: str | None) -> str | None:
    """Case-fold text for case-insensitive matching in SQLite.

    Args:
        value: The column value, or ``None``.

    Returns:
        The case-folded text, or ``None`` for ``None``.
    """
    return None if value is None else value.casefold()


def _register_blueprints(app: Flask) -> None:
    """Register every feature blueprint on the app.

    Args:
        app: The application to register the blueprints on.
    """
    from .admin import bp as admin_bp
    from .auth import bp as auth_bp
    from .onboarding import bp as onboarding_bp
    from .posts import bp as posts_bp
    from .profile import bp as profile_bp
    from .search import bp as search_bp
    from .settings import bp as settings_bp
    from .uploads import bp as uploads_bp

    for bp in (auth_bp, onboarding_bp, posts_bp, profile_bp, settings_bp,
               admin_bp, search_bp, uploads_bp):
        app.register_blueprint(bp)


def _register_hooks(app: Flask) -> None:
    """Register the request hooks on the app.

    Args:
        app: The application to add the hooks to.
    """
    @app.before_request
    def require_login_and_onboarding() -> Response | None:
        """Send signed-out users to log in and new users to onboarding.

        Returns:
            A response that replaces the normal view, or ``None`` to let
            the request continue.
        """
        endpoint = request.endpoint
        if endpoint is None:  # unknown URL: let it 404
            return None
        if not current_user.is_authenticated:
            if endpoint in PUBLIC_ENDPOINTS:
                return None
            return login_manager.unauthorized()
        if not current_user.onboarded and endpoint not in ONBOARDING_EXEMPT:
            return redirect(url_for("onboarding.welcome"))
        return None

    @app.after_request
    def add_security_headers(response: Response) -> Response:
        """Add the security headers to every response.

        Headers a view already set are left alone.

        Args:
            response: The outgoing response.

        Returns:
            The same response, with the headers added.
        """
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault(
            "Content-Security-Policy", _csp_header())
        return response


def _register_template_helpers(app: Flask) -> None:
    """Register the Jinja filters and the template context processor.

    Args:
        app: The application to add the helpers to.
    """
    app.add_template_filter(render_markdown, "markdown")

    @app.template_filter("datetimeformat")
    def datetimeformat(value: datetime | None, fmt: str | None = None) -> str:
        """Format a datetime, e.g. ``21 Oct 2026, 20:15``.

        Args:
            value: The datetime to format, or ``None``.
            fmt: An optional ``strftime`` format replacing the default.

        Returns:
            The formatted text, or an empty string for a falsy value.
        """
        if not value:
            return ""
        if fmt:
            return value.strftime(fmt)
        return f"{value.day} {value:%b %Y, %H:%M}"

    @app.template_filter("dateformat")
    def dateformat(value: date | None, fmt: str | None = None) -> str:
        """Format a date, e.g. ``Wed 21 Oct 2026``.

        Args:
            value: The date (or datetime) to format, or ``None``.
            fmt: An optional ``strftime`` format replacing the default.

        Returns:
            The formatted text, or an empty string for a falsy value.
        """
        if not value:
            return ""
        if fmt:
            return value.strftime(fmt)
        return f"{value:%a} {value.day} {value:%b %Y}"

    @app.context_processor
    def inject_globals() -> dict:
        """Add the theme, the shared lookup tables and ``csp_nonce``.

        ``csp_nonce`` is a function: ``{{ csp_nonce() }}`` returns
        the nonce for an inline ``<style>`` element.

        Returns:
            The extra template context.
        """
        if current_user.is_authenticated:
            theme = current_user.theme
        else:
            theme = resolve_theme()
        return {
            "theme": theme,
            "csp_nonce": csp_nonce,
            "THEME_PRESETS": THEME_PRESETS,
            "THEME_COLOUR_LABELS": THEME_COLOUR_LABELS,
            "MAX_PHOTOS_PER_POST": app.config["MAX_PHOTOS_PER_POST"],
            "MOODS": MOODS,
            "REACTIONS": REACTIONS,
            "VISIBILITIES": VISIBILITIES,
            "DEFAULT_AVATARS": DEFAULT_AVATARS,
        }


def _register_error_handlers(app: Flask) -> None:
    """Register the error pages and the upload and CSRF handlers.

    Args:
        app: The application to add the handlers to.
    """
    messages = {
        403: ("Not allowed", "You don't have permission to do that."),
        404: (
            "Page not found",
            "We couldn't find that page. It may have been deleted or be "
            "private.",
        ),
        500: (
            "Something went wrong",
            "Sorry! Something broke on our side. Please try again.",
        ),
    }

    def render_error(code: int) -> tuple[str, int]:
        """Render the shared error page for an HTTP status code.

        Args:
            code: The HTTP status code, a key of ``messages``.

        Returns:
            The rendered page and the status code.
        """
        # A failed transaction leaves the session unusable; the error
        # page (current_user, flashes) needs a clean one.
        db.session.rollback()
        title, message = messages[code]
        page = render_template(
            "errors/error.html", code=code, title=title, message=message)
        return page, code

    for code in messages:
        app.register_error_handler(
            code, lambda e, code=code: render_error(code))

    @app.errorhandler(413)
    def too_large(_e: object) -> Response:
        """Flash a friendly message when an upload is too large.

        Args:
            _e: The 413 error (unused).

        Returns:
            A redirect back to the referring page, or the feed.
        """
        each = app.config["MAX_IMAGE_BYTES"] // (1024 * 1024)
        total = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(
            f"That upload is too big. Photos must be under {each} MB "
            f"each ({total} MB total).", "error")
        # Don't touch request.form here: reading the oversized body
        # re-raises 413.
        if is_safe_url(request.referrer):
            target = request.referrer
        else:
            target = url_for("posts.feed")
        return redirect(target)

    @app.errorhandler(CSRFError)
    def csrf_error(_e: object) -> Response:
        """Flash a message when a form's CSRF token is invalid.

        Args:
            _e: The CSRF error (unused).

        Returns:
            A redirect back to where the user came from.
        """
        flash("Your form expired. Please try again.", "error")
        return redirect_back("posts.feed")
