"""First-run onboarding: profile, avatar, theme and background."""
from flask import flash, redirect, render_template, url_for
from flask_login import current_user
from werkzeug.wrappers import Response

from ..constants import DEFAULT_AVATARS
from ..forms import OnboardingForm
from ..models import db
from ..utils.uploads import UploadError, delete_upload, has_file, save_image
from . import bp


@bp.route("/welcome", methods=["GET", "POST"])
def welcome() -> str | Response:
    """Show the first-run setup form and save it on submit.

    For signed-in users who have not finished onboarding; others are
    redirected to the feed. The form sets display name, bio, avatar,
    theme and background. On success the user is marked as onboarded
    and redirected to the feed. An upload error re-shows the form.

    Returns:
        The onboarding page, or a redirect.
    """
    if current_user.onboarded:
        return redirect(url_for("posts.feed"))

    form = OnboardingForm()
    if not form.is_submitted():
        form.display_name.data = (
            current_user.display_name or current_user.username
        )
        if current_user.has_uploaded_avatar:
            form.avatar.data = "current"
        else:
            form.avatar.data = current_user.avatar
        form.theme_preset.data = current_user.theme_preset
        for key, value in current_user.theme.items():
            field = getattr(form, f"colour_{key}", None)
            if field is not None:
                field.data = value

    if form.validate_on_submit():
        new_files = []
        try:
            if has_file(form.avatar_upload.data):
                avatar_file = save_image(form.avatar_upload.data, "avatar")
            else:
                avatar_file = None
            if avatar_file:
                new_files.append(avatar_file)
            if has_file(form.background.data):
                bg_file = save_image(form.background.data, "background")
            else:
                bg_file = None
        except UploadError as exc:
            for name in new_files:
                delete_upload(name)
            flash(str(exc), "error")
            return render_template(
                "onboarding/welcome.html",
                form=form,
                avatars=DEFAULT_AVATARS,
            )

        user = current_user
        user.display_name = (form.display_name.data or "").strip() or None
        user.bio = (form.bio.data or "").strip() or None
        if avatar_file:
            delete_upload(user.avatar if user.has_uploaded_avatar else None)
            user.avatar = avatar_file
        elif form.avatar.data in DEFAULT_AVATARS:
            delete_upload(user.avatar if user.has_uploaded_avatar else None)
            user.avatar = form.avatar.data
        if bg_file:
            delete_upload(user.background_image)
            user.background_image = bg_file
        user.apply_theme(form.theme_preset.data, form.chosen_colours())
        user.onboarded = True
        db.session.commit()
        flash(
            f"All set, {user.name}! "
            "Write your first diary entry whenever you like.",
            "success",
        )
        return redirect(url_for("posts.feed"))

    return render_template(
        "onboarding/welcome.html", form=form, avatars=DEFAULT_AVATARS
    )
