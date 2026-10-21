"""Small helpers shared by several blueprints."""
from urllib.parse import urlsplit

from flask import redirect, request, url_for
from flask_wtf import FlaskForm
from werkzeug.wrappers import Response


def is_safe_url(target: str | None) -> bool:
    """Check that a URL points back at this site.

    This prevents open redirects.

    Args:
        target: The URL or path to check, possibly empty.

    Returns:
        ``True`` for a same-host http(s) URL or a local absolute path.
    """
    if not target:
        return False
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return (parts.scheme in ("http", "https")
                and parts.netloc == request.host)
    return target.startswith("/") and not target.startswith("//")


def redirect_back(endpoint: str, **values: object) -> Response:
    """Redirect to a safe place the user came from.

    Tries the form ``next`` value, the ``?next=`` argument and the
    referrer, in that order, and uses the first one that is safe.

    Args:
        endpoint: The endpoint to redirect to if none of them is safe.
        **values: URL values passed to ``url_for`` for ``endpoint``.

    Returns:
        A redirect response.
    """
    targets = (
        request.form.get("next"), request.args.get("next"),
        request.referrer)
    for target in targets:
        if is_safe_url(target):
            return redirect(target)
    return redirect(url_for(endpoint, **values))


def flash_form_errors(form: FlaskForm) -> None:
    """Flash every validation error of a form.

    Used when the view redirects away instead of re-rendering the form.

    Args:
        form: A validated form whose ``errors`` should be shown.
    """
    from flask import flash

    for field_name, errors in form.errors.items():
        field = getattr(form, field_name, None)
        if field is not None and hasattr(field, "label"):
            label = field.label.text
        else:
            label = field_name
        for error in errors:
            flash(f"{label}: {error}", "error")
