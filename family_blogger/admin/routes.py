"""Admin panel: manage members. Post deletion uses posts.delete_post."""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.wrappers import Response

from ..constants import DEFAULT_PRESET
from ..forms import AddMemberForm, AdminResetPasswordForm
from ..models import Comment, Photo, Post, User, db
from ..utils import end_other_sessions, flash_form_errors
from ..utils.permissions import admin_required
from ..utils.throttle import (
    admin_clear,
    clearable_names,
    paused_devices,
    recent_failure_counts,
    shared_address,
    unpause_device,
)
from ..utils.uploads import delete_upload
from . import bp

# How many unknown usernames with clearable failures to list.
UNKNOWN_NAMES_SHOWN = 10


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


def _commit_keeping_an_admin() -> bool:
    """Commit the pending change unless it would leave no admin.

    The admin count is checked again after the change is flushed, in
    the same transaction, so two admins demoting or deleting each
    other at the same moment cannot both succeed. If the database is
    busy with another change the transaction is rolled back too.

    Returns:
        ``True`` if the change was committed, ``False`` if it was
        rolled back.
    """
    try:
        db.session.flush()
        if admin_count() < 1:
            db.session.rollback()
            return False
        db.session.commit()
    except OperationalError:
        db.session.rollback()
        return False
    return True


def _render_index(add_form: AddMemberForm | None = None) -> str:
    """Render the admin panel.

    Args:
        add_form: A submitted "Add member" form to show with its
            errors, or ``None`` for an empty one.

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
    failures = recent_failure_counts()
    clearable = clearable_names()
    known = {u.username.lower() for u in users}
    rows = [
        {
            "user": u,
            "post_count": post_counts.get(u.id, 0),
            "failed_logins": failures.get(u.username.lower(), 0),
            "can_clear_logins": u.username.lower() in clearable,
            "login_paused": clearable.get(u.username.lower(), False),
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
        "failed_logins_unknown": sum(
            count for name, count in failures.items()
            if name not in known),
    }
    unknown_failures = sorted(
        (
            {
                "username": name,
                "count": failures.get(name, 0),
                "paused": paused,
            }
            for name, paused in clearable.items() if name not in known
        ),
        key=lambda item: (-item["count"], item["username"]),
    )[:UNKNOWN_NAMES_SHOWN]
    return render_template(
        "admin/index.html",
        rows=rows,
        stats=stats,
        unknown_failures=unknown_failures,
        paused_devices=paused_devices(),
        shared_address=shared_address() if len(users) > 1 else None,
        reset_form=AdminResetPasswordForm(formdata=None),
        add_form=add_form or AddMemberForm(formdata=None),
    )


@bp.route("/admin")
@admin_required
def index() -> str:
    """Show the admin panel with members, site totals and "Add member".

    Admins only. Each row says which actions (promote, demote,
    delete) are allowed, so the last admin cannot be removed, and
    how many failed log-ins that username had in the last 7 days.
    Paused devices are listed with an "Unpause this device" button,
    and a calm warning appears when everyone seems to log in from
    the same address (a reverse proxy missing from
    ``FB_TRUSTED_PROXIES``).

    Returns:
        The admin page.
    """
    return _render_index()


@bp.route("/admin/members", methods=["POST"])
@admin_required
def add_member() -> str | Response:
    """Add a family member with a starting password.

    POST only, admins only. Uses the sign-up rules: a unique username
    (any case) and a password of at least 6 characters, typed twice.
    The new member is never an admin, gets the default theme and goes
    through onboarding on first log-in. Invalid input re-shows the
    admin page with the errors.

    Returns:
        A redirect to the admin panel, or the admin page with errors.
    """
    form = AddMemberForm()
    if form.validate_on_submit():
        user = User(username=form.username.data.strip(), is_admin=False)
        user.set_password(form.password.data)
        user.apply_theme(DEFAULT_PRESET)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # Someone took the name between validating and saving.
            db.session.rollback()
            form.username.errors.append("That username is taken.")
            return _render_index(form)
        flash(
            f"{user.username} has been added. They can log in with the "
            "password you chose.",
            "success",
        )
        return redirect(url_for("admin.index"))
    return _render_index(form)


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
    user = db.get_or_404(User, user_id)
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
    user = db.get_or_404(User, user_id)
    if is_last_admin(user):
        flash(
            "You can't demote the last admin. Promote someone else first.",
            "error",
        )
        return redirect(url_for("admin.index"))
    user.is_admin = False
    if not _commit_keeping_an_admin():
        flash(
            "You can't demote the last admin. Promote someone else first.",
            "error",
        )
        return redirect(url_for("admin.index"))
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
    messages. The member's failed log-ins are cleared and any pause
    on their username is lifted. The member is signed out on every
    device. An admin
    resetting their own password stays signed in on this device.

    Args:
        user_id: The id of the user whose password is reset.

    Returns:
        A redirect to the admin panel.
    """
    user = db.get_or_404(User, user_id)
    form = AdminResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        # A fresh start: forget wrong tries and lift any pause.
        admin_clear(user.username)
        # Sign them out everywhere; they log in with the new password.
        if user.id == current_user.id:
            end_other_sessions(user)
        else:
            user.rotate_session_token()
        db.session.commit()
        flash(f"Password for {user.username} has been reset.", "success")
    else:
        flash_form_errors(form)
    return redirect(url_for("admin.index"))


@bp.route("/admin/login-failures/clear", methods=["POST"])
@admin_required
def clear_login_failures() -> Response:
    """Clear a username's failed log-ins and lift any pause on it.

    POST only, admins only. The form field ``username`` may name a
    member or a username that belongs to nobody. Failed log-ins stay
    listed for 7 days but stop counting. Whole-device pauses are not
    lifted here (see ``unpause_device_route``).

    Returns:
        A redirect to the admin panel.
    """
    name = (request.form.get("username") or "").strip()
    if name:
        admin_clear(name)
        db.session.commit()
        flash(
            f"Failed log-ins for {name} cleared. They can try again now.",
            "success",
        )
    return redirect(url_for("admin.index", _anchor="people"))


@bp.route("/admin/login-pauses/device", methods=["POST"])
@admin_required
def unpause_device_route() -> Response:
    """Lift a whole-device log-in pause for one address.

    POST only, admins only. The form field ``ip`` names the paused
    address. Only the device pause ends; pauses on particular
    usernames stay until cleared with "Clear failed log-ins".

    Returns:
        A redirect to the admin panel's "Paused devices" list.
    """
    ip = (request.form.get("ip") or "").strip()
    if ip and unpause_device(ip):
        db.session.commit()
        flash(f"The device at {ip} can try logging in again.", "success")
    return redirect(url_for("admin.index", _anchor="paused-devices"))


@bp.route("/admin/user/<int:user_id>/delete", methods=["GET", "POST"])
@admin_required
def delete_user(user_id: int) -> Response | str | tuple[str, int]:
    """Ask for confirmation, then delete a member and their content.

    Admins only. GET shows the "Are you sure?" page, which asks for
    the member's username to be typed. POST deletes only when the
    form carries ``confirm=yes`` and the typed username matches
    (ignoring case); otherwise nothing is deleted and the page is
    shown again with an error (status 400). Admins cannot delete
    themselves, and the last admin cannot be deleted.

    Args:
        user_id: The id of the user to delete.

    Returns:
        The confirmation page or a redirect to the admin panel.
    """
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash(
            "You can't delete your own account from the admin panel.",
            "error",
        )
        return redirect(url_for("admin.index"))
    if is_last_admin(user):
        flash("You can't delete the last admin.", "error")
        return redirect(url_for("admin.index"))
    error = None
    if request.method == "POST":
        typed = request.form.get("confirm_name", "").strip().lower()
        if request.form.get("confirm") != "yes":
            error = "Please confirm before deleting."
        elif typed != user.username.lower():
            error = "That doesn't match the username. Nothing was deleted."
        else:
            files = user.uploaded_files()
            name = user.name
            # Cascades to posts, photos, comments and reactions.
            db.session.delete(user)
            if not _commit_keeping_an_admin():
                flash("You can't delete the last admin.", "error")
                return redirect(url_for("admin.index"))
            for filename in files:
                delete_upload(filename)
            flash(f"{name} and all their posts have been deleted.", "success")
            return redirect(url_for("admin.index"))
    page = render_template(
        "confirm_delete.html",
        heading=f"Delete {user.name}?",
        message=(
            f"{user.name} and ALL of their posts, comments and photos "
            "will be gone for good. This cannot be undone."
        ),
        action_url=url_for("admin.delete_user", user_id=user.id),
        cancel_url=url_for("admin.index"),
        next_url=None,
        confirm_label="Yes, delete this member",
        require_text=user.username,
        error=error,
    )
    return (page, 400) if error else page
