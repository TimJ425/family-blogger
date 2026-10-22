"""Serve uploaded images to logged-in users.

Login is enforced app-wide.
"""
from flask import abort, current_app, send_from_directory
from flask_login import current_user
from sqlalchemy import or_
from werkzeug.wrappers import Response

from ..models import Photo, User, db
from ..utils.permissions import can_view
from ..utils.uploads import FILENAME_RE
from . import bp


@bp.route("/uploads/<filename>")
def serve(filename: str) -> Response:
    """Send an uploaded image file.

    For signed-in users. A post photo is served only if the user may
    see its post. Any other file must be some member's avatar or
    background. Everything else gives a 404.

    Args:
        filename: The stored filename of the image.

    Returns:
        The image, marked as privately cacheable for an hour.
    """
    if not FILENAME_RE.fullmatch(filename):
        abort(404)

    photo = db.session.scalar(db.select(Photo).filter_by(filename=filename))
    if photo is not None:
        if not can_view(photo.post, current_user):
            abort(404)
    else:
        # Not a post photo: must be someone's avatar or background.
        owner = db.session.scalar(
            db.select(User.id).where(
                or_(User.avatar == filename, User.background_image == filename)
            )
        )
        if owner is None:
            abort(404)

    response = send_from_directory(
        current_app.config["UPLOAD_FOLDER"], filename, max_age=3600
    )
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response
