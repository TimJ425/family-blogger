"""Sign up, log in, log out.

Public sign-up only creates the first account, who becomes the family
admin. After that, admins add everyone else from the admin page.
"""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.wrappers import Response

from ..constants import DEFAULT_PRESET
from ..forms import LoginForm, SignupForm
from ..models import User, db
from ..utils import is_safe_url
from ..utils.devices import device_key, remember_device
from ..utils.throttle import (
    THROTTLED_MESSAGE,
    attempt_failed,
    attempt_succeeded,
    begin_attempt,
    client_ip,
)
from . import bp

# Checked against when the username doesn't exist, so a wrong
# username takes as long as a wrong password and the response time
# does not reveal who has an account. Made once, with the same
# method as real passwords (``User.set_password``).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def user_count() -> int:
    """Count every account.

    Returns:
        The number of users.
    """
    return db.session.scalar(db.select(func.count(User.id)))


def signup_is_open() -> bool:
    """Say whether public sign-up may create an account.

    Returns:
        ``True`` only while there are no users yet.
    """
    return user_count() == 0


def _signup_closed() -> tuple[str, int]:
    """Render the friendly "ask a grown-up" page.

    Returns:
        The page and status 403.
    """
    return render_template("auth/signup_closed.html"), 403


def _create_first_admin(username: str, password: str) -> User | None:
    """Create the first account as admin, unless someone beat us.

    The insert happens first and the count is checked afterwards in
    the same transaction. SQLite lets only one writer in at a time,
    so if two people sign up together the second one sees two rows
    and is rolled back.

    Args:
        username: The chosen username.
        password: The chosen password.

    Returns:
        The new admin, or ``None`` if another account already exists.
    """
    user = User(username=username, is_admin=False)
    user.set_password(password)
    user.apply_theme(DEFAULT_PRESET)
    db.session.add(user)
    try:
        db.session.flush()
        if user_count() != 1:
            db.session.rollback()
            return None
        user.is_admin = True
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None
    return user


@bp.route("/signup", methods=["GET", "POST"])
def signup() -> str | Response | tuple[str, int]:
    """Create the family's first account, who becomes the admin.

    Open to signed-out visitors only while there are no users. Once
    anyone has joined, GET and POST show a friendly page (status 403)
    asking the child to have a grown-up add them, and nothing is
    created. A successful sign-up logs the new admin in and redirects
    to onboarding. Signed-in users are redirected to the feed.

    Returns:
        The sign-up page, the "sign-up closed" page, or a redirect.
    """
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))
    if not signup_is_open():
        return _signup_closed()

    form = SignupForm()
    if form.validate_on_submit():
        user = _create_first_admin(
            form.username.data.strip(), form.password.data)
        if user is None:
            return _signup_closed()
        login_user(user)
        flash(
            "Welcome! As the first member you're the family admin.",
            "success",
        )
        return redirect(url_for("onboarding.welcome"))

    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response | tuple[str, int]:
    """Show the log-in form and sign the user in on submit.

    Open to signed-out visitors. On success it redirects to the safe
    ``next`` URL if given, otherwise to the feed. Signed-in users are
    redirected to the feed. "Keep me logged in" lasts
    ``REMEMBER_COOKIE_DURATION`` (30 days). The sign-up link is only
    shown while there are no users.

    Failed attempts are recorded and may pause further tries with a
    calm message (status 429); see ``utils.throttle`` for the limits.
    In short: five wrong passwords for one name from one device pause
    that name on that device for five minutes, without affecting the
    same person on another device. A successful log-in clears that
    name's failures from this device. Admins can clear failures and
    lift pauses from the admin page.

    A successful log-in also marks this browser as a trusted device
    for that member (``utils.devices``), so later pauses caused from
    a shared address do not lock them out here. Each attempt is
    reserved before the password is checked, so parallel requests
    cannot get extra guesses past a limit.

    Returns:
        The log-in page (status 429 while throttled), or a redirect.
    """
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        user = User.find_by_username(username)
        attempt = begin_attempt(username, client_ip(), device_key(user))
        if attempt is None:
            flash(THROTTLED_MESSAGE, "error")
            return render_template(
                "auth/login.html", form=form,
                signup_open=signup_is_open()), 429
        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data)
        elif user.check_password(form.password.data):
            attempt_succeeded(attempt, user.username)
            db.session.commit()
            login_user(user, remember=form.remember.data)
            flash(f"Hello again, {user.name}!", "success")
            target = request.args.get("next")
            if not is_safe_url(target):
                target = url_for("posts.feed")
            response = redirect(target)
            remember_device(response, user)
            return response
        attempt_failed(attempt)
        flash("Wrong username or password.", "error")

    return render_template(
        "auth/login.html", form=form, signup_open=signup_is_open())


@bp.route("/logout", methods=["POST"])
def logout() -> Response:
    """Log the current user out.

    POST only, for signed-in users.

    Returns:
        A redirect to the log-in page.
    """
    logout_user()
    flash("You've been logged out. See you soon!", "info")
    return redirect(url_for("auth.login"))
