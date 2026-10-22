"""Account settings.

GET /settings shows four forms; each form POSTs to its own endpoint
(no hidden ``form_name`` field is needed).
"""
from flask import flash, redirect, render_template, url_for
from flask_login import current_user
from werkzeug.wrappers import Response

from ..constants import DEFAULT_AVATARS, THEME_COLOUR_KEYS, THEME_PRESETS
from ..forms import BackgroundForm, ChangePasswordForm, ProfileForm, ThemeForm
from ..models import db
from ..utils import end_other_sessions
from ..utils.uploads import (
    UploadError,
    commit_with_files,
    has_file,
    save_image,
)
from . import bp


def _prefilled_forms() -> dict:
    """Build fresh, unbound forms filled with the user's values.

    Returns:
        The forms by template variable name: ``profile_form``,
        ``theme_form``, ``background_form`` and ``password_form``.
    """
    user = current_user
    profile_form = ProfileForm(formdata=None)
    profile_form.display_name.data = user.display_name
    profile_form.bio.data = user.bio
    if user.has_uploaded_avatar:
        profile_form.avatar.data = "current"
    else:
        profile_form.avatar.data = user.avatar

    theme_form = ThemeForm(formdata=None)
    theme_form.theme_preset.data = user.theme_preset
    theme = user.theme
    for key in THEME_COLOUR_KEYS:
        getattr(theme_form, f"colour_{key}").data = theme[key]
    # Tick "custom" when the stored colours differ from the preset's.
    preset = THEME_PRESETS.get(user.theme_preset, {})
    theme_form.custom_colours.data = any(
        theme[k] != preset.get(k) for k in THEME_COLOUR_KEYS
    )

    return {
        "profile_form": profile_form,
        "theme_form": theme_form,
        "background_form": BackgroundForm(formdata=None),
        "password_form": ChangePasswordForm(formdata=None),
    }


def _render(**bound_forms: object) -> str:
    """Render the settings page.

    Args:
        **bound_forms: Submitted (invalid) forms that replace the
            prefilled ones, keyed by template variable name.

    Returns:
        The settings page.
    """
    forms = _prefilled_forms()
    forms.update(bound_forms)
    return render_template(
        "settings/index.html", avatars=DEFAULT_AVATARS, **forms
    )


@bp.route("/settings")
def index() -> str:
    """Show the settings page with its four forms.

    For signed-in users.

    Returns:
        The settings page.
    """
    return _render()


@bp.route("/settings/profile", methods=["POST"])
def profile() -> str | Response:
    """Save the display name, bio and avatar.

    POST only, for signed-in users. An invalid form or upload error
    re-shows the settings page. On success it redirects to settings.

    Returns:
        The settings page, or a redirect.
    """
    form = ProfileForm()
    if not form.validate_on_submit():
        return _render(profile_form=form)

    user = current_user
    new_files = []
    old_files = []
    if has_file(form.avatar_upload.data):
        try:
            new_avatar = save_image(form.avatar_upload.data, "avatar")
        except UploadError as exc:
            flash(str(exc), "error")
            return _render(profile_form=form)
        new_files.append(new_avatar)
        if user.has_uploaded_avatar:
            old_files.append(user.avatar)
        user.avatar = new_avatar
    elif (
        form.avatar.data in DEFAULT_AVATARS
        and form.avatar.data != user.avatar
    ):
        if user.has_uploaded_avatar:
            old_files.append(user.avatar)
        user.avatar = form.avatar.data

    user.display_name = (form.display_name.data or "").strip() or None
    user.bio = (form.bio.data or "").strip() or None
    commit_with_files(new_files, old_files)
    flash("Profile saved.", "success")
    return redirect(url_for("settings.index"))


@bp.route("/settings/theme", methods=["POST"])
def theme() -> str | Response:
    """Save the colour theme.

    POST only, for signed-in users. An invalid form re-shows the
    settings page. On success it redirects to the theme section.

    Returns:
        The settings page, or a redirect.
    """
    form = ThemeForm()
    if not form.validate_on_submit():
        return _render(theme_form=form)
    current_user.apply_theme(form.theme_preset.data, form.chosen_colours())
    db.session.commit()
    flash("Theme saved.", "success")
    return redirect(url_for("settings.index", _anchor="theme"))


@bp.route("/settings/background", methods=["POST"])
def background() -> str | Response:
    """Upload a new background image, replacing any old one.

    POST only, for signed-in users. An invalid form or upload error
    re-shows the settings page. On success it redirects to the
    background section.

    Returns:
        The settings page, or a redirect.
    """
    form = BackgroundForm()
    if not form.validate_on_submit():
        return _render(background_form=form)
    try:
        filename = save_image(form.background.data, "background")
    except UploadError as exc:
        flash(str(exc), "error")
        return _render(background_form=form)
    old = current_user.background_image
    current_user.background_image = filename
    commit_with_files([filename], [old])
    flash("Background updated.", "success")
    return redirect(url_for("settings.index", _anchor="background"))


@bp.route("/settings/background/remove", methods=["POST"])
def remove_background() -> Response:
    """Remove the user's background image, if they have one.

    POST only, for signed-in users.

    Returns:
        A redirect to the background section of settings.
    """
    if current_user.background_image:
        old = current_user.background_image
        current_user.background_image = None
        commit_with_files([], [old])
        flash("Background removed.", "success")
    return redirect(url_for("settings.index", _anchor="background"))


@bp.route("/settings/password", methods=["POST"])
def password() -> str | Response:
    """Change the user's own password.

    POST only, for signed-in users. It needs the current password. A
    bad form or wrong current password re-shows the settings page.
    On success the user stays logged in on this device, is logged out
    everywhere else, and is redirected to settings.

    Returns:
        The settings page, or a redirect.
    """
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            current_user.set_password(form.new_password.data)
            end_other_sessions(current_user._get_current_object())
            db.session.commit()
            flash(
                "Password changed. You've been logged out on your other "
                "devices.",
                "success",
            )
            return redirect(url_for("settings.index"))
        form.current_password.errors.append(
            "That isn't your current password."
        )
    return _render(password_form=form)


@bp.route("/settings/sign-out-everywhere", methods=["POST"])
def sign_out_everywhere() -> Response:
    """Log the user out on every other device.

    POST only, for signed-in users. Rotates the session token, so
    every other session and "Keep me logged in" cookie stops working,
    and logs the user in again on this device only.

    Returns:
        A redirect to the devices section of settings.
    """
    end_other_sessions(current_user._get_current_object())
    db.session.commit()
    flash("Done! You've been logged out on your other devices.", "success")
    return redirect(url_for("settings.index", _anchor="devices"))
