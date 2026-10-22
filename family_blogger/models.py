"""Database models.

Timestamps are stored as naive *local* time: this is a single-household
app on a local network, and diary dates should match the family's wall
clock. The exceptions are ``LoginFailure`` and ``LoginLock``, which
use UTC because only the time between attempts matters there.
"""
import re
import secrets
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import url_for
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash

from .constants import (
    DEFAULT_AVATARS,
    DEFAULT_BACKGROUND,
    DEFAULT_PRESET,
    MOODS,
    REACTIONS,
    THEME_COLOUR_KEYS,
    THEME_PRESETS,
    VISIBILITIES,
)

db = SQLAlchemy()


def new_session_token() -> str:
    """Make a fresh random session token for a user.

    Returns:
        64 random hex characters.
    """
    return secrets.token_hex(32)


def now() -> datetime:
    """Return the current local time, without microseconds.

    Returns:
        A naive datetime in local time.
    """
    return datetime.now().replace(microsecond=0)


_HEX_COLOUR_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _is_dark(hex_colour: str) -> bool:
    """Rough perceived-luminance check, to pick light/dark UI details.

    Args:
        hex_colour: A colour such as ``#161a2e``.

    Returns:
        ``True`` if the colour is dark, ``False`` if it is light or not
        a valid hex colour.
    """
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    except (TypeError, ValueError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 < 0.5


def resolve_theme(preset: str | None = None, colours: dict | None = None,
                  background_url: str | None = None) -> dict:
    """Build the theme dict exposed to templates as ``theme``.

    Args:
        preset: A ``THEME_PRESETS`` key; unknown or ``None`` falls back
            to the default preset.
        colours: Custom colours keyed by ``THEME_COLOUR_KEYS``. Missing
            or empty entries use the preset's colour.
        background_url: URL of an uploaded background, or ``None`` for
            the default background.

    Returns:
        A dict of the five colours plus ``preset``, ``background_url``,
        ``has_custom_background`` and ``is_dark``.
    """
    preset = preset if preset in THEME_PRESETS else DEFAULT_PRESET
    base = THEME_PRESETS[preset]
    colours = colours or {}
    # Only well-formed #rrggbb values reach the inline theme <style>;
    # anything else falls back to the preset's colour.
    theme = {
        key: (colours.get(key)
              if _HEX_COLOUR_RE.fullmatch(colours.get(key) or "")
              else base[key])
        for key in THEME_COLOUR_KEYS
    }
    theme.update(
        preset=preset,
        background_url=background_url or url_for(
            "static", filename=DEFAULT_BACKGROUND),
        has_custom_background=bool(background_url),
        is_dark=_is_dark(theme["bg"]),
    )
    return theme


class User(UserMixin, db.Model):
    """A family member who can sign in and write posts."""

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    display_name = db.Column(db.String(60))
    bio = db.Column(db.String(500))
    # Either a DEFAULT_AVATARS name ("fox") or an uploaded filename.
    avatar = db.Column(db.String(64), nullable=False, default="fox")
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    onboarded = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)
    # Part of the login id stored in session and remember-me cookies.
    # Changing it signs the user out everywhere (see get_id). The SQL
    # default only fills rows added by migration 1; new users get
    # new_session_token().
    session_token = db.Column(
        db.String(64), nullable=False, default=new_session_token,
        server_default=db.text("lower(hex(randomblob(32)))"))

    # Theme
    theme_preset = db.Column(
        db.String(20), nullable=False, default=DEFAULT_PRESET)
    colour_primary = db.Column(db.String(7))
    colour_accent = db.Column(db.String(7))
    colour_bg = db.Column(db.String(7))
    colour_card = db.Column(db.String(7))
    colour_text = db.Column(db.String(7))
    background_image = db.Column(db.String(64))

    posts = db.relationship(
        "Post", back_populates="author", cascade="all, delete-orphan",
        order_by="Post.created_at.desc()")
    comments = db.relationship(
        "Comment", back_populates="author", cascade="all, delete-orphan")
    reactions = db.relationship(
        "Reaction", back_populates="user", cascade="all, delete-orphan")

    # Usernames are unique regardless of case. AUTOINCREMENT stops a
    # deleted member's id ever being given to someone new.
    __table_args__ = (
        db.Index("ix_user_username_lower", func.lower(username),
                 unique=True),
        {"sqlite_autoincrement": True},
    )

    # --- auth ---------------------------------------------------
    def get_id(self) -> str:
        """Build the login id kept in session and remember cookies.

        It joins the user id and the session token, so rotating the
        token makes every existing cookie for this user invalid.

        Returns:
            ``"<id>:<session_token>"``.
        """
        return f"{self.id}:{self.session_token}"

    def rotate_session_token(self) -> None:
        """Sign the user out on every device by changing their token.

        The caller commits the change. To keep the current device
        signed in, call ``login_user`` again afterwards.
        """
        self.session_token = new_session_token()

    def set_password(self, password: str) -> None:
        """Hash a password and store it on the user.

        Args:
            password: The plain-text password.
        """
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Check a plain-text password against the stored hash.

        Args:
            password: The password to check.

        Returns:
            ``True`` if it matches.
        """
        return check_password_hash(self.password_hash, password)

    @classmethod
    def find_by_username(cls, username: str) -> "User | None":
        """Look a user up by username, ignoring case and outer spaces.

        Args:
            username: The username to search for.

        Returns:
            The matching user, or ``None`` if the name is empty or not
            found.
        """
        if not username:
            return None
        return db.session.scalar(
            db.select(cls).where(
                func.lower(cls.username) == username.strip().lower())
        )

    # --- display helpers ----------------------------------------
    @classmethod
    def name_in_use(cls, name: str | None,
                    exclude_id: int | None = None) -> bool:
        """Check whether a name looks the same as another member's.

        Names are compared with ``name_key`` against every other
        member's username and display name, so ``Mum``, `` mum `` and
        a full-width ``Ｍｕｍ`` all clash.

        Args:
            name: The display name or username being chosen.
            exclude_id: The id of the member choosing it, whose own
                names never clash; ``None`` checks everyone.

        Returns:
            ``True`` if another member already uses that name.
        """
        key = name_key(name)
        if not key:
            return False
        stmt = db.select(cls.id, cls.username, cls.display_name)
        for user_id, username, display_name in db.session.execute(stmt):
            if user_id == exclude_id:
                continue
            if key in (name_key(username), name_key(display_name)):
                return True
        return False

    @property
    def name(self) -> str:
        """Display name, falling back to the username."""
        return self.display_name or self.username

    @property
    def has_uploaded_avatar(self) -> bool:
        """Whether the avatar is an uploaded photo, not a default."""
        return self.avatar not in DEFAULT_AVATARS

    @property
    def avatar_url(self) -> str:
        """The URL of the user's avatar image."""
        if self.has_uploaded_avatar:
            return url_for("uploads.serve", filename=self.avatar)
        return url_for("static", filename=f"img/avatars/{self.avatar}.svg")

    @property
    def background_url(self) -> str | None:
        """URL of the uploaded background, or None."""
        if self.background_image:
            return url_for("uploads.serve", filename=self.background_image)
        return None

    @property
    def profile_url(self) -> str:
        """The URL of the user's profile page."""
        return url_for("profile.view_profile", username=self.username)

    @property
    def has_posted_today(self) -> bool:
        """True if this user posted (any visibility) today."""
        start = now().replace(hour=0, minute=0, second=0)
        query = db.session.query(Post.id).filter(
            Post.author_id == self.id, Post.created_at >= start)
        return query.first() is not None

    @property
    def theme_colours(self) -> dict:
        """Stored colours (None for any that were never set)."""
        return {key: getattr(self, f"colour_{key}")
                for key in THEME_COLOUR_KEYS}

    @property
    def theme(self) -> dict:
        """The user's resolved theme, as built by ``resolve_theme``."""
        return resolve_theme(
            self.theme_preset, self.theme_colours, self.background_url)

    def apply_theme(self, preset: str, colours: dict | None = None) -> None:
        """Set the preset; colours default to that preset's palette.

        Args:
            preset: A ``THEME_PRESETS`` key; unknown falls back to the
                default preset.
            colours: Custom colours keyed by ``THEME_COLOUR_KEYS``.
                Missing or empty entries use the preset's colour.
        """
        if preset not in THEME_PRESETS:
            preset = DEFAULT_PRESET
        self.theme_preset = preset
        palette = THEME_PRESETS[self.theme_preset]
        colours = colours or {}
        for key in THEME_COLOUR_KEYS:
            colour = colours.get(key) or palette[key]
            setattr(self, f"colour_{key}", colour.lower())

    def uploaded_files(self) -> list[str]:
        """All upload filenames owned by this user (for cleanup).

        Returns:
            Filenames of the user's post photos, uploaded avatar and
            uploaded background.
        """
        files = [p.filename for post in self.posts for p in post.photos]
        if self.has_uploaded_avatar:
            files.append(self.avatar)
        if self.background_image:
            files.append(self.background_image)
        return files

    def __repr__(self) -> str:
        """Return a short debugging representation.

        Returns:
            Text such as ``<User tim>``.
        """
        return f"<User {self.username}>"


class Post(db.Model):
    """A diary entry with optional mood, photos, comments, reactions."""

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(120), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    mood = db.Column(db.String(10))  # a MOODS key or None
    visibility = db.Column(
        db.String(10), nullable=False, default="everyone", index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=now, index=True)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=now, onupdate=now)

    author = db.relationship("User", back_populates="posts")
    photos = db.relationship(
        "Photo", back_populates="post", cascade="all, delete-orphan",
        order_by="Photo.id")
    comments = db.relationship(
        "Comment", back_populates="post", cascade="all, delete-orphan",
        order_by="Comment.created_at")
    reactions = db.relationship(
        "Reaction", back_populates="post", cascade="all, delete-orphan")

    @property
    def url(self) -> str:
        """The URL of the page showing this post."""
        return url_for("posts.view_post", post_id=self.id)

    @property
    def mood_info(self) -> dict | None:
        """The ``MOODS`` entry for the post's mood, or ``None``."""
        return MOODS.get(self.mood)

    @property
    def visibility_info(self) -> dict:
        """The ``VISIBILITIES`` entry for the post's visibility."""
        return VISIBILITIES.get(self.visibility, VISIBILITIES["everyone"])

    @property
    def is_edited(self) -> bool:
        """True if the post was changed over a minute after creation."""
        # Timestamps are naive local time, so when the clocks go back
        # an edit can look earlier than creation; abs() copes with
        # that. (Moving storage to UTC would be the full fix, but it
        # needs a data migration, so it is deliberately not done.)
        gap = abs(self.updated_at - self.created_at)
        return gap > timedelta(minutes=1)

    def can_edit(self, user: "User | None") -> bool:
        """Decide whether a user may edit the post.

        Args:
            user: The signed-in user, or ``None`` when signed out.

        Returns:
            ``True`` only for the post's author.
        """
        return bool(
            user and user.is_authenticated and user.id == self.author_id)

    def can_delete(self, user: "User | None") -> bool:
        """Decide whether a user may delete the post.

        Args:
            user: The signed-in user, or ``None`` when signed out.

        Returns:
            ``True`` for the post's author and for admins.
        """
        return bool(user and user.is_authenticated and (
            user.id == self.author_id or user.is_admin))

    # --- reactions ----------------------------------------------
    def reaction_counts(self) -> dict:
        """Count reactions per kind.

        Returns:
            A ``{kind: count}`` dict covering every ``REACTIONS`` kind,
            zeros included.
        """
        counts = Counter(r.kind for r in self.reactions)
        return {kind: counts.get(kind, 0) for kind in REACTIONS}

    def reaction_summary(self, user: "User | None") -> list[dict]:
        """Summarise the post's reactions for display.

        Args:
            user: The signed-in user, or ``None`` when signed out.

        Returns:
            One dict per reaction kind with ``kind``, ``emoji``,
            ``label``, ``count`` and ``reacted`` (whether ``user`` used
            that kind).
        """
        counts = self.reaction_counts()
        mine = {r.kind for r in self.reactions
                if user and user.is_authenticated and r.user_id == user.id}
        return [
            {"kind": kind, "emoji": info["emoji"], "label": info["label"],
             "count": counts[kind], "reacted": kind in mine}
            for kind, info in REACTIONS.items()
        ]

    def __repr__(self) -> str:
        """Return a short debugging representation.

        Returns:
            Text such as ``<Post 3 'My day'>``.
        """
        return f"<Post {self.id} {self.title!r}>"


class Photo(db.Model):
    """An uploaded image attached to a post."""

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    filename = db.Column(db.String(64), nullable=False, unique=True)

    post = db.relationship("Post", back_populates="photos")

    @property
    def url(self) -> str:
        """The URL the photo is served from."""
        return url_for("uploads.serve", filename=self.filename)


class Comment(db.Model):
    """A comment written by a user on a post."""

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    author_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    post = db.relationship("Post", back_populates="comments")
    author = db.relationship("User", back_populates="comments")

    def can_delete(self, user: "User | None") -> bool:
        """Decide whether a user may delete the comment.

        Args:
            user: The signed-in user, or ``None`` when signed out.

        Returns:
            ``True`` for the comment's author, the post's author and
            admins.
        """
        return bool(user and user.is_authenticated and (
            user.id == self.author_id
            or user.id == self.post.author_id
            or user.is_admin))


class Reaction(db.Model):
    """One user's reaction of a given kind to a post."""

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("post.id"), nullable=False, index=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now)

    post = db.relationship("Post", back_populates="reactions")
    user = db.relationship("User", back_populates="reactions")

    __table_args__ = (
        db.UniqueConstraint(
            "post_id", "user_id", "kind", name="uq_reaction"),
    )


# The number of comments, counted by the database in the same query
# that loads the post (no per-post query, no loading every comment).
Post.comment_count = db.column_property(
    db.select(func.count(Comment.id))
    .where(Comment.post_id == Post.id)
    .correlate_except(Comment)
    .scalar_subquery()
)


# Characters that render as nothing (or as blank space) but are not
# whitespace to ``str.split``: Hangul fillers, the Braille blank, the
# combining grapheme joiner and the variation selectors. Format
# characters (category ``Cf``, e.g. zero-width spaces and joiners or
# the soft hyphen) are caught by category in ``is_invisible``.
INVISIBLE_CHARS = frozenset(
    "\u2800\u3164\u115f\u1160\uffa0\u034f"
    + "".join(chr(c) for c in range(0xFE00, 0xFE10))
    + "".join(chr(c) for c in range(0xE0100, 0xE01F0)))


def is_invisible(char: str) -> bool:
    """Say whether a character is invisible when shown in a name.

    Args:
        char: A single character.

    Returns:
        ``True`` for format characters (Unicode category ``Cf``) and
        the characters in ``INVISIBLE_CHARS``.
    """
    return char in INVISIBLE_CHARS or unicodedata.category(char) == "Cf"


def has_invisible_chars(name: str | None) -> bool:
    """Say whether a name contains any invisible characters.

    Args:
        name: A display name, possibly ``None``.

    Returns:
        ``True`` if any character in it is invisible.
    """
    return any(is_invisible(char) for char in name or "")


def name_key(name: str | None) -> str:
    """Reduce a name to the form used to spot lookalikes.

    Drops invisible characters (see ``is_invisible``), then applies
    Unicode NFKC normalisation (so full-width or stylised letters
    match plain ones), case folding, and trims and collapses
    whitespace. Letters from other scripts that merely look alike
    (such as Cyrillic "М" for Latin "M") are deliberately left alone:
    they are real letters in family members' own languages.

    Args:
        name: A username or display name, possibly ``None``.

    Returns:
        The comparison key, ``""`` for an empty name.
    """
    text = "".join(
        char for char in name or "" if not is_invisible(char))
    text = unicodedata.normalize("NFKC", text)
    text = unicodedata.normalize("NFKC", text.casefold())
    text = "".join(char for char in text if not is_invisible(char))
    return " ".join(text.split())


def utc_now() -> datetime:
    """Return the current UTC time as a naive datetime.

    Used where only time differences matter (log-in throttling), so
    clock changes for daylight saving cannot shift the windows.

    Returns:
        A naive datetime in UTC, without microseconds.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


class LoginFailure(db.Model):
    """One failed log-in attempt, used to slow down password guessing.

    Rows are kept for a week so admins can see them, then pruned.

    Attributes:
        username: The username that was tried, lower-cased (it may
            not belong to anyone).
        ip: The address the attempt came from.
        created_at: When it happened, in UTC.
        cleared: ``True`` once that username logged in successfully
            or an admin cleared its failed log-ins; cleared rows no
            longer count towards throttling but still show on the
            admin page.
    """

    __tablename__ = "login_failure"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    ip = db.Column(db.String(45), nullable=False, index=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=utc_now, index=True)
    cleared = db.Column(db.Boolean, nullable=False, default=False)


class LoginLock(db.Model):
    """A pause on log-in attempts, made by ``utils.throttle``.

    Only failures recorded after a lock ends count towards the next
    one, so a lock cannot be kept going with one wrong try at a time.

    Attributes:
        scope: ``"pair"`` (one username from one address), ``"ip"``
            (any username from one address) or ``"name"`` (one
            username from many addresses).
        username: The lower-cased username, or ``""`` for ``"ip"``.
        ip: The address, or ``""`` for ``"name"``.
        started_at: When the lock began, in UTC.
        locked_until: When attempts are allowed again, in UTC.
    """

    __tablename__ = "login_lock"

    id = db.Column(db.Integer, primary_key=True)
    scope = db.Column(db.String(8), nullable=False)
    username = db.Column(db.String(80), nullable=False, default="",
                         index=True)
    ip = db.Column(db.String(45), nullable=False, default="", index=True)
    started_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    locked_until = db.Column(db.DateTime, nullable=False, index=True)
