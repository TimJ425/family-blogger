"""Sign up, log in, log out."""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from sqlalchemy import func
from werkzeug.wrappers import Response

from ..forms import LoginForm, SignupForm
from ..models import User, db
from ..utils import is_safe_url
from . import bp


@bp.route("/signup", methods=["GET", "POST"])
def signup() -> str | Response:
    """Show the sign-up form and create an account on submit.

    Open to signed-out visitors. The very first account becomes the
    family admin. A successful sign-up logs the new user in and
    redirects to the onboarding page. Signed-in users are redirected
    to the feed.

    Returns:
        The sign-up page, or a redirect.
    """
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))

    form = SignupForm()
    if form.validate_on_submit():
        # The very first account becomes the admin.
        is_first = db.session.scalar(db.select(func.count(User.id))) == 0
        user = User(username=form.username.data.strip(), is_admin=is_first)
        user.set_password(form.password.data)
        user.apply_theme("sunny")
        db.session.add(user)
        db.session.commit()
        login_user(user)
        if is_first:
            flash(
                "Welcome! As the first member you're the family admin.",
                "success",
            )
        else:
            flash("Welcome to the family!", "success")
        return redirect(url_for("onboarding.welcome"))

    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response:
    """Show the log-in form and sign the user in on submit.

    Open to signed-out visitors. On success it redirects to the safe
    ``next`` URL if given, otherwise to the feed. Signed-in users are
    redirected to the feed.

    Returns:
        The log-in page, or a redirect.
    """
    if current_user.is_authenticated:
        return redirect(url_for("posts.feed"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.find_by_username(form.username.data)
        if user and user.check_password(form.password.data):
            login_user(user, remember=form.remember.data)
            flash(f"Hello again, {user.name}!", "success")
            target = request.args.get("next")
            if is_safe_url(target):
                return redirect(target)
            return redirect(url_for("posts.feed"))
        flash("Wrong username or password.", "error")

    return render_template("auth/login.html", form=form)


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
