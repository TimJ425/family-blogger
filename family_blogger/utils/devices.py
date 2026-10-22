"""Remember devices someone has logged in on, so pauses can't trap them.

Log-in pauses are keyed on the address a request comes from. When
the whole family shares one address (for example behind a reverse
proxy that is not set up in ``FB_TRUSTED_PROXIES``), a child typing
wrong passwords for Mum could keep Mum paused too, and if Mum is the
only admin nobody could lift it.

So after a successful log-in the browser gets a signed
``fb_device`` cookie holding a random token for that member. On
that browser the member's attempts are counted against the token
instead of the shared address: address and many-device pauses do
not apply, but the token has its own limit. The first time the
token reaches ``throttle.MAX_FAILURES`` wrong passwords it is paused
like a device and stays untrusted for good (that browser falls back
to ordinary address-based pauses until the member logs in there
again, which issues a fresh token).

Each entry is bound to the member's session generation and to the
time it was issued: rotating the session token (password change,
admin reset, "log out on my other devices") or waiting out
``TRUST_FOR`` makes that entry untrusted, entry by entry.

Bounds: a cookie only exists on a browser where the member already
typed the right password, it names only that member, and each token
allows at most ``MAX_FAILURES`` (5) extra guesses in total. Nothing
is sent anywhere: the cookie only goes back to this app.
"""
import hashlib
import secrets
import time
from datetime import timedelta

from flask import Flask, current_app, request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.wrappers import Response

from ..models import User

COOKIE_NAME = "fb_device"
# A browser stays trusted this long after the last good log-in there.
TRUST_FOR = timedelta(days=90)
# Members remembered per browser (a shared family tablet).
MAX_MEMBERS = 6
# Prefix that marks a throttle "address" as a device token.
PREFIX = "dev:"
_SALT = "fb-trusted-device"


def _serializer(app: Flask) -> URLSafeTimedSerializer:
    """Build the signer for device cookies.

    Args:
        app: The application, whose secret key signs the cookie.

    Returns:
        A timed, URL-safe serializer.
    """
    return URLSafeTimedSerializer(app.secret_key, salt=_SALT)


def _generation(user: User) -> str:
    """Return a short fingerprint of a member's session generation.

    It changes whenever the member's session token is rotated
    (password change, admin reset, "log out on my other devices").

    Args:
        user: The member.

    Returns:
        The first 16 hex characters of the SHA-256 of the token.
    """
    return hashlib.sha256(
        user.session_token.encode("utf-8")).hexdigest()[:16]


def _read_cookie() -> dict[str, list]:
    """Read and verify this browser's device cookie.

    Each entry is ``[token, generation, issued_at]``. Entries older
    than ``TRUST_FOR`` are dropped one by one, and so is any entry in
    the old shape (a bare token), so members just earn trust again.

    Returns:
        Member id (as text) mapped to ``[token, generation,
        issued_at]``; empty if the cookie is missing or tampered
        with.
    """
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return {}
    try:
        data = _serializer(current_app).loads(
            raw, max_age=int(TRUST_FOR.total_seconds()))
    except BadSignature:
        return {}
    if not isinstance(data, dict):
        return {}
    now = int(time.time())
    kept = {}
    for key, entry in data.items():
        if not (isinstance(entry, list) and len(entry) == 3
                and isinstance(entry[0], str)
                and isinstance(entry[1], str)
                and isinstance(entry[2], int)
                and not isinstance(entry[2], bool)):
            continue
        age = now - entry[2]
        if age < 0 or age > TRUST_FOR.total_seconds():
            continue
        kept[str(key)] = list(entry)
    return kept


def device_key(user: User | None) -> str | None:
    """Return the throttle key for this browser and member, if any.

    Args:
        user: The member whose username was typed, or ``None`` if it
            belongs to nobody.

    Returns:
        ``"dev:<token>"`` if this browser holds a valid device token
        for that member issued in their current session generation,
        otherwise ``None``. Whether the token is
        still trusted is decided by ``throttle``.
    """
    if user is None:
        return None
    entry = _read_cookie().get(str(user.id))
    if entry is None or entry[1] != _generation(user):
        return None
    token = entry[0]
    if len(token) != 24 or not token.isalnum():
        return None
    return PREFIX + token


def remember_device(response: Response, user: User) -> None:
    """Give this browser a fresh device token for a member.

    Called after a successful log-in. Other members already
    remembered on this browser keep their tokens and issue times
    (up to ``MAX_MEMBERS``, newest kept).

    Args:
        response: The response that will carry the cookie.
        user: The member who just logged in.
    """
    data = _read_cookie()
    data.pop(str(user.id), None)
    data[str(user.id)] = [secrets.token_hex(12), _generation(user),
                          int(time.time())]
    data = dict(list(data.items())[-MAX_MEMBERS:])
    response.set_cookie(
        COOKIE_NAME,
        _serializer(current_app).dumps(data),
        max_age=int(TRUST_FOR.total_seconds()),
        httponly=True,
        samesite="Lax",
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
    )
