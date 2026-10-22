"""Configuration for Family Blogger.

Paths default to ``<project>/instance``; set FB_INSTANCE_DIR to move the
database, uploads and secret key somewhere else.
"""
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INSTANCE_DIR = Path(
    os.environ.get("FB_INSTANCE_DIR", BASE_DIR / "instance"))


def cookie_secure_from_env() -> bool:
    """Read FB_COOKIE_SECURE, which marks cookies as HTTPS-only.

    Returns:
        ``True`` only when the variable is exactly ``"1"``.
    """
    return os.environ.get("FB_COOKIE_SECURE") == "1"


def trusted_proxies_from_env() -> list[str]:
    """Read FB_TRUSTED_PROXIES, the proxies allowed to name the client.

    The value is a comma-separated list of IP addresses or networks in
    CIDR form, e.g. ``"172.18.0.0/16, 192.168.1.2"``. Only requests
    arriving *from* one of these addresses have their
    ``X-Forwarded-For``, ``-Proto``, ``-Host`` and ``-Port`` headers
    honoured; everyone else's are ignored, so they cannot pretend to
    be someone else. Empty by default (no proxy trusted).

    Returns:
        The non-empty, stripped entries, unvalidated (the app checks
        them when it starts).
    """
    raw = os.environ.get("FB_TRUSTED_PROXIES", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def trust_any_proxy_from_env() -> bool:
    """Read the legacy FB_TRUST_PROXY switch.

    ``FB_TRUST_PROXY=1`` is kept so older setups keep working: it
    trusts ``X-Forwarded-*`` from whatever connects directly (the
    "immediate peer"). That is only safe when nothing but the proxy
    can reach the app; prefer FB_TRUSTED_PROXIES.

    Returns:
        ``True`` only when the variable is exactly ``"1"``.
    """
    return os.environ.get("FB_TRUST_PROXY") == "1"


def load_or_create_secret_key(instance_dir: Path) -> str:
    """Return the persisted secret key, generating it on first run.

    The key is stored in ``instance_dir/secret_key``. A new file is
    created atomically with owner-only permissions (``0600``) and
    ``O_EXCL``, so it is never readable by others, even briefly. If
    another process creates it first, that process's key is used.

    Args:
        instance_dir: Directory holding the instance data. It is created
            if it does not exist.

    Returns:
        The secret key as a hex string.

    Raises:
        RuntimeError: If the key file exists but stays empty.
    """
    instance_dir.mkdir(parents=True, exist_ok=True)
    key_file = instance_dir / "secret_key"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key
        # An empty file (e.g. a crash mid-write): replace it.
        key_file.unlink()
    key = secrets.token_hex(32)
    try:
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another worker won the race; use the key it wrote.
        for _ in range(50):
            existing = key_file.read_text().strip()
            if existing:
                return existing
            time.sleep(0.02)
        raise RuntimeError(f"Secret key file {key_file} is empty")
    with os.fdopen(fd, "w") as handle:
        handle.write(key)
    return key


class Config:
    """Flask settings: upload limits, paging, CSRF and cookies."""

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # A whole request (e.g. a post with several photos) may be up to
    # 40 MB, but each image is capped at MAX_IMAGE_BYTES.
    MAX_CONTENT_LENGTH = 40 * 1024 * 1024
    MAX_IMAGE_BYTES = 10 * 1024 * 1024
    MAX_PHOTOS_PER_POST = 10

    POSTS_PER_PAGE = 10

    # CSRF tokens stay valid for the whole session (a diary tab may
    # sit open for hours before someone hits "Post").
    WTF_CSRF_TIME_LIMIT = None

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    # FB_COOKIE_SECURE=1 sends the session and "keep me logged in"
    # cookies over HTTPS only. Turn it on only when the family always
    # reaches the app through HTTPS (e.g. a reverse proxy); on plain
    # HTTP the browser would drop the cookies and nobody could log
    # in. Off by default because most home setups use plain HTTP.
    # ``create_app`` re-reads the variable each time it builds an app.
    SESSION_COOKIE_SECURE = cookie_secure_from_env()
    REMEMBER_COOKIE_SECURE = cookie_secure_from_env()
    # "Keep me logged in" lasts 30 days on that device.
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    # Reverse proxies whose X-Forwarded-* headers are believed (see
    # trusted_proxies_from_env). ``create_app`` re-reads both.
    TRUSTED_PROXIES = trusted_proxies_from_env()
    # Legacy: trust X-Forwarded-* from any direct peer.
    TRUST_ANY_PROXY = trust_any_proxy_from_env()
