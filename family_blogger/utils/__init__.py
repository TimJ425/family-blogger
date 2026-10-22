"""Small helpers shared by several blueprints."""
from urllib.parse import unquote, urlsplit

from flask import current_app, flash, redirect, request, url_for
from flask_login import login_user
from flask_wtf import FlaskForm
from werkzeug.wrappers import Response

from ..models import User


def _has_unsafe_chars(text: str, allow_space: bool = False) -> bool:
    r"""Spot characters that can turn a local path into another site.

    Browsers treat ``\`` like ``/`` and silently drop tabs and
    newlines, so ``/\evil.com`` or ``/<tab>/evil.com`` leaves the site.

    Args:
        text: The URL, raw or percent-decoded.
        allow_space: Let plain spaces through (used for the decoded
            form, where ``%20`` from a search query is harmless).

    Returns:
        ``True`` if the text holds a backslash, a control character
        or (unless allowed) whitespace.
    """
    for ch in text:
        if ch == "\\" or ord(ch) < 32 or ord(ch) == 127:
            return True
        if ch.isspace() and not (allow_space and ch == " "):
            return True
    return False


def is_safe_url(target: str | None) -> bool:
    r"""Check that a URL points back at this site.

    This prevents open redirects. Backslashes, whitespace and control
    characters are refused, plain or percent-encoded (``%5C``), as is
    anything that starts with ``//`` once decoded.

    Args:
        target: The URL or path to check, possibly empty.

    Returns:
        ``True`` for a same-host http(s) URL or a local absolute path.
    """
    if not target:
        return False
    decoded = unquote(target)
    if (_has_unsafe_chars(target)
            or _has_unsafe_chars(decoded, allow_space=True)
            or decoded.startswith("//")):
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
    for field_name, errors in form.errors.items():
        field = getattr(form, field_name, None)
        if field is not None and hasattr(field, "label"):
            label = field.label.text
        else:
            label = field_name
        for error in errors:
            flash(f"{label}: {error}", "error")


def end_other_sessions(user: User) -> None:
    """Sign a user out everywhere except on this device.

    Rotates the user's session token (the caller commits) and logs
    them in again on the current request, keeping "Keep me logged in"
    if this device had it.

    Args:
        user: The signed-in user whose other sessions should end
            (the real object, not the ``current_user`` proxy).
    """
    user.rotate_session_token()
    cookie = current_app.config.get("REMEMBER_COOKIE_NAME", "remember_token")
    login_user(user, remember=cookie in request.cookies)


def last_page_redirect(pagination: object,
                       keep: tuple[str, ...] = ()) -> Response | None:
    """Redirect an out-of-range page number to the last real page.

    Only the query arguments named in ``keep`` (the search filters,
    say) are carried over, so a stray ``?endpoint=`` or
    ``?_external=`` can never reach ``url_for``. The URL's own path
    values (``request.view_args``) always win over the query. Used
    after a deletion empties the last page, or when a link is out of
    date.

    Args:
        pagination: A Flask-SQLAlchemy ``Pagination`` object.
        keep: Names of the query arguments this page understands,
            other than ``page``.

    Returns:
        A redirect to the last page, or ``None`` when the page is in
        range (or there are no results at all).
    """
    if pagination.pages >= 1 and pagination.page > pagination.pages:
        values = {
            key: request.args[key] for key in keep
            if request.args.get(key)
        }
        values.update(request.view_args or {})
        values["page"] = pagination.pages
        return redirect(url_for(request.endpoint, **values))
    return None
