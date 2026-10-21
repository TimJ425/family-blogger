"""Family Blogger application factory."""
import os
from datetime import date, datetime
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user
from flask_wtf.csrf import CSRFError, CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wrappers import Response

from config import DEFAULT_INSTANCE_DIR, Config, load_or_create_secret_key

from .constants import (
    DEFAULT_AVATARS,
    MOODS,
    REACTIONS,
    THEME_PRESETS,
    VISIBILITIES,
)
from .models import User, db, resolve_theme
from .utils import is_safe_url, redirect_back
from .utils.markdown import render_markdown

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


@login_manager.user_loader
def load_user(user_id: str) -> "User | None":
    """Load a user for Flask-Login from the id stored in the session.

    Args:
        user_id: The user's primary key, as a string.

    Returns:
        The matching user, or ``None`` if there is no such user.
    """
    return db.session.get(User, int(user_id))


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
    )
    app.config.update(overrides)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    # Behind a reverse proxy (e.g. Nginx Proxy Manager doing HTTPS),
    # trust one hop of X-Forwarded-* headers so redirects and links
    # keep the right scheme.
    if os.environ.get("FB_TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    _register_blueprints(app)
    _register_hooks(app)
    _register_template_helpers(app)
    _register_error_handlers(app)

    with app.app_context():
        db.create_all()

    return app


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
        """Add the theme and the shared lookup tables to every template.

        Returns:
            The extra template context.
        """
        if current_user.is_authenticated:
            theme = current_user.theme
        else:
            theme = resolve_theme()
        return {
            "theme": theme,
            "THEME_PRESETS": THEME_PRESETS,
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
        flash(
            "That upload is too big. Photos must be under 10 MB each "
            "(40 MB total).", "error")
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
