"""Configuration for Family Blogger.

Paths default to ``<project>/instance``; set FB_INSTANCE_DIR to move the
database, uploads and secret key somewhere else.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INSTANCE_DIR = Path(
    os.environ.get("FB_INSTANCE_DIR", BASE_DIR / "instance"))


def load_or_create_secret_key(instance_dir: Path) -> str:
    """Return the persisted secret key, generating it on first run.

    The key is stored in ``instance_dir/secret_key`` (created with
    owner-only permissions where the platform allows it).

    Args:
        instance_dir: Directory holding the instance data. It is created
            if it does not exist.

    Returns:
        The secret key as a hex string.
    """
    instance_dir.mkdir(parents=True, exist_ok=True)
    key_file = instance_dir / "secret_key"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    key_file.write_text(key)
    try:
        key_file.chmod(0o600)
    except OSError:
        pass
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
