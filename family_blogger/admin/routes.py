"""Admin panel: manage members. Post deletion uses posts.delete_post."""
from flask import flash, redirect, render_template, url_for
from flask_login import current_user
from sqlalchemy import func
from werkzeug.wrappers import Response

from ..forms import AdminResetPasswordForm
from ..models import Comment, Photo, Post, User, db
from ..utils import flash_form_errors
from ..utils.permissions import admin_required
from ..utils.uploads import delete_upload
from . import bp


def admin_count() -> int:
    """Count the admin accounts.

    Returns:
        The number of users who are admins.
    """
    return db.session.scalar(
        db.select(func.count(User.id)).where(User.is_admin.is_(True))
    )


def is_last_admin(user: User) -> bool:
    """Check whether a user is the only remaining admin.

    Args:
        user: The user to check.

    Returns:
        ``True`` if the user is an admin and no other admin exists.
    """
    return user.is_admin and admin_count() <= 1


def _get_user_or_404(user_id: int) -> User:
    """Look a user up by id.

    Args:
        user_id: The id of the user.

    Returns:
        The matching user.

    Raises:
        werkzeug.exceptions.NotFound: If there is no such user (404).
    """
    return db.get_or_404(User, user_id)


@bp.route("/admin")
@admin_required
def index() -> str:
    """Show the admin panel with members and site totals.

    Admins only. Each row says which actions (promote, demote,
    delete) are allowed, so the last admin cannot be removed.

    Returns:
        The admin page.
    """
    users = db.session.scalars(
        db.select(User).order_by(func.lower(User.username))
    ).all()
    post_counts = dict(
        db.session.execute(
            db.select(Post.author_id, func.count(Post.id)).group_by(
                Post.author_id
            )
        ).all()
    )
    admins = admin_count()
    rows = [
        {
            "user": u,
            "post_count": post_counts.get(u.id, 0),
            "is_self": u.id == current_user.id,
            "can_promote": not u.is_admin,
            "can_demote": u.is_admin and admins > 1,
            "can_delete": (
                u.id != current_user.id and not (u.is_admin and admins <= 1)
            ),
        }
        for u in users
    ]
    stats = {
        "users": len(users),
        "admins": admins,
        "posts": db.session.scalar(db.select(func.count(Post.id))),
        "comments": db.session.scalar(db.select(func.count(Comment.id))),
        "photos": db.session.scalar(db.select(func.count(Photo.id))),
    }
    return render_template(
        "admin/index.html",
        rows=rows,
        stats=stats,
        reset_form=AdminResetPasswordForm(formdata=None),
    )


@bp.route("/admin/user/<int:user_id>/promote", methods=["POST"])
@admin_required
def promote(user_id: int) -> Response:
    """Make a member an admin.

    POST only, admins only.

    Args:
        user_id: The id of the user to promote.

    Returns:
        A redirect to the admin panel.
    """
    user = _get_user_or_404(user_id)
    user.is_admin = True
    db.session.commit()
    flash(f"{user.name} is now an admin.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/admin/user/<int:user_id>/demote", methods=["POST"])
@admin_required
def demote(user_id: int) -> Response:
    """Remove admin rights from a member.

    POST only, admins only. The last admin cannot be demoted.

    Args:
        user_id: The id of the user to demote.

    Returns:
        A redirect to the admin panel, or to the feed when the admin
        demoted themselves.
    """
    user = _get_user_or_404(user_id)
    if is_last_admin(user):
        flash(
            "You can't demote the last admin. Promote someone else first.",
            "error",
        )
        return redirect(url_for("admin.index"))
    user.is_admin = False
    db.session.commit()
    flash(f"{user.name} is no longer an admin.", "success")
    # Demoting yourself means you can no longer see this page.
    if user.id == current_user.id:
        return redirect(url_for("posts.feed"))
    return redirect(url_for("admin.index"))


@bp.route("/admin/user/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_password(user_id: int) -> Response:
    """Set a new password for a member.

    POST only, admins only. Invalid input is reported with flashed
    messages.

    Args:
        user_id: The id of the user whose password is reset.

    Returns:
        A redirect to the admin panel.
    """
    user = _get_user_or_404(user_id)
    form = AdminResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        db.session.commit()
        flash(f"Password for {user.username} has been reset.", "success")
    else:
        flash_form_errors(form)
    return redirect(url_for("admin.index"))


@bp.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: int) -> Response:
    """Delete a member with all their posts and uploaded files.

    POST only, admins only. Admins cannot delete themselves, and the
    last admin cannot be deleted.

    Args:
        user_id: The id of the user to delete.

    Returns:
        A redirect to the admin panel.
    """
    user = _get_user_or_404(user_id)
    if user.id == current_user.id:
        flash(
            "You can't delete your own account from the admin panel.",
            "error",
        )
        return redirect(url_for("admin.index"))
    if is_last_admin(user):
        flash("You can't delete the last admin.", "error")
        return redirect(url_for("admin.index"))
    files = user.uploaded_files()
    name = user.name
    db.session.delete(user)  # cascades to posts, photos, comments, reactions
    db.session.commit()
    for filename in files:
        delete_upload(filename)
    flash(f"{name} and all their posts have been deleted.", "success")
    return redirect(url_for("admin.index"))
