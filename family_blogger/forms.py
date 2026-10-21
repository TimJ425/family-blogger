"""WTForms definitions.

Field names here are the HTML ``name`` attributes documented in
TEMPLATE_CONTRACT.md.
"""
import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    MultipleFileField,
    PasswordField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.fields import DateField
from wtforms.validators import (
    DataRequired,
    EqualTo,
    Length,
    Optional,
    Regexp,
    ValidationError,
)

from .constants import DEFAULT_AVATARS, MOODS, THEME_PRESETS, VISIBILITIES
from .models import User
from .utils.uploads import ALLOWED_EXTENSIONS

USERNAME_RE = r"^[A-Za-z0-9_]{3,30}$"
HEX_COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
IMAGE_ONLY = FileAllowed(
    sorted(ALLOWED_EXTENSIONS), "Images only (jpg, png, gif, webp).")

MOOD_CHOICES = [("", "No mood")] + [
    (k, f"{v['emoji']} {v['label']}") for k, v in MOODS.items()]
VISIBILITY_CHOICES = [(k, v["label"]) for k, v in VISIBILITIES.items()]
PRESET_CHOICES = [(k, v["label"]) for k, v in THEME_PRESETS.items()]
# "current" = keep whatever avatar the user already has (e.g. an
# uploaded photo).
AVATAR_CHOICES = [(a, a.title()) for a in DEFAULT_AVATARS] + [
    ("current", "Keep current")]


def hex_colour(label: str) -> StringField:
    """Build an optional text field that only accepts ``#rrggbb``.

    Args:
        label: The label shown next to the field.

    Returns:
        An unbound string field with a hex-colour validator.
    """
    return StringField(label, validators=[
        Optional(),
        Regexp(HEX_COLOUR_RE, message="Use a hex colour like #ff8800."),
    ])


# --- auth -----------------------------------------------------------
class SignupForm(FlaskForm):
    """Create an account with a unique username and a password."""

    username = StringField("Username", validators=[
        DataRequired(),
        Regexp(USERNAME_RE, message="3-30 letters, numbers or underscores."),
    ])
    password = PasswordField("Password", validators=[
        DataRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField("Confirm password", validators=[
        DataRequired(), EqualTo("password", message="Passwords don't match.")])
    submit = SubmitField("Create account")

    def validate_username(self, field: StringField) -> None:
        """Reject a username that is already taken (any case).

        Args:
            field: The username field being validated.

        Raises:
            ValidationError: If the username is already in use.
        """
        if User.find_by_username(field.data):
            raise ValidationError("That username is taken.")


class LoginForm(FlaskForm):
    """Sign in with a username and password."""

    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember me")
    submit = SubmitField("Log in")


# --- profile / theme pieces (shared by onboarding and settings) -----
class ProfileFieldsMixin:
    """Display name, bio and avatar fields shared by two forms."""

    display_name = StringField(
        "Display name", validators=[Optional(), Length(max=60)])
    bio = TextAreaField(
        "About me", validators=[Optional(), Length(max=500)])
    avatar = RadioField("Avatar", choices=AVATAR_CHOICES, default="fox",
                        validators=[Optional()])
    avatar_upload = FileField(
        "Upload a photo", validators=[Optional(), IMAGE_ONLY])


class ThemeFieldsMixin:
    """Theme preset and custom colour fields shared by two forms."""

    theme_preset = RadioField("Theme", choices=PRESET_CHOICES, default="sunny")
    # Unticked: the preset's colours are used. Ticked: the colour
    # fields below.
    custom_colours = BooleanField("Use my own colours")
    colour_primary = hex_colour("Primary")
    colour_accent = hex_colour("Accent")
    colour_bg = hex_colour("Background")
    colour_card = hex_colour("Cards")
    colour_text = hex_colour("Text")

    def chosen_colours(self) -> dict | None:
        """Return the custom colours the user asked for.

        Returns:
            A dict of colour key to hex string (or ``None`` if left
            blank) when "Use my own colours" is ticked, otherwise
            ``None``, meaning the preset should be used.
        """
        if not self.custom_colours.data:
            return None
        return {key: getattr(self, f"colour_{key}").data or None
                for key in ("primary", "accent", "bg", "card", "text")}


class OnboardingForm(ProfileFieldsMixin, ThemeFieldsMixin, FlaskForm):
    """First-run setup: profile, theme and optional background."""

    background = FileField(
        "Background image", validators=[Optional(), IMAGE_ONLY])
    submit = SubmitField("Let's go!")


# --- settings -------------------------------------------------------
class ProfileForm(ProfileFieldsMixin, FlaskForm):
    """Edit the display name, bio and avatar."""

    submit = SubmitField("Save profile")


class ThemeForm(ThemeFieldsMixin, FlaskForm):
    """Choose a theme preset or custom colours."""

    submit = SubmitField("Save theme")


class BackgroundForm(FlaskForm):
    """Upload a new background image."""

    background = FileField(
        "Background image", validators=[DataRequired(), IMAGE_ONLY])
    submit = SubmitField("Upload background")


class ChangePasswordForm(FlaskForm):
    """Change your own password, confirming the current one."""

    current_password = PasswordField(
        "Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[
        DataRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField("Confirm new password", validators=[
        DataRequired(),
        EqualTo("new_password", message="Passwords don't match."),
    ])
    submit = SubmitField("Change password")


# --- posts ----------------------------------------------------------
class PostForm(FlaskForm):
    """Write or edit a post: title, body, mood, visibility, photos."""

    title = StringField("Title", validators=[DataRequired(), Length(max=120)])
    body = TextAreaField("What happened today?", validators=[
        Optional(), Length(max=20000)])
    mood = SelectField(
        "Mood", choices=MOOD_CHOICES, default="", validators=[Optional()])
    visibility = RadioField(
        "Who can see this?", choices=VISIBILITY_CHOICES, default="everyone")
    photos = MultipleFileField("Photos")
    # Edit only: ids of existing photos to remove (shown as
    # checkboxes).
    remove_photos = SelectMultipleField(
        "Remove photos", coerce=int, choices=[], validate_choice=False)
    submit = SubmitField("Save")


class CommentForm(FlaskForm):
    """Add a comment to a post."""

    body = TextAreaField(
        "Comment", validators=[DataRequired(), Length(max=2000)])
    submit = SubmitField("Comment")


# --- admin ----------------------------------------------------------
class AdminResetPasswordForm(FlaskForm):
    """Let an admin set a new password for another user."""

    new_password = PasswordField("New password", validators=[
        DataRequired(), Length(min=6, max=128)])
    submit = SubmitField("Reset")


# --- search (GET, no CSRF) ------------------------------------------
class SearchForm(FlaskForm):
    """Filter posts by text, author, mood and date range."""

    class Meta:
        """Turn off CSRF: the search form is submitted by GET."""

        csrf = False

    q = StringField("Search", validators=[Optional(), Length(max=200)])
    author = SelectField(
        "Author", choices=[("", "Anyone")], default="",
        validators=[Optional()], validate_choice=False)
    mood = SelectField(
        "Mood", choices=[("", "Any mood")] + MOOD_CHOICES[1:], default="",
        validators=[Optional()])
    date_from = DateField("From", validators=[Optional()])
    date_to = DateField("To", validators=[Optional()])
    submit = SubmitField("Search")
