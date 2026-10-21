"""Profiles, mood data and the family directory."""
from datetime import date, datetime, time, timedelta

from flask import abort, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import func
from werkzeug.wrappers import Response

from ..constants import MOODS
from ..models import Post, User, db
from ..utils.permissions import FAMILY_VISIBLE, profile_query
from . import bp


def _user_or_404(username: str) -> User:
    """Look a user up by username.

    Args:
        username: The username to find.

    Returns:
        The matching user.

    Raises:
        werkzeug.exceptions.NotFound: If there is no such user (404).
    """
    user = User.find_by_username(username)
    if user is None:
        abort(404)
    return user


def daily_moods(
    profile_user: User, viewer: User, days: int
) -> dict[date, str]:
    """Collect one mood per day for the last few days.

    Uses only posts the viewer may see. The latest post with a mood
    wins each day.

    Args:
        profile_user: The user whose posts are used.
        viewer: The user looking at the profile.
        days: How many days to cover, today included.

    Returns:
        A mapping of date to mood key.
    """
    start = datetime.combine(date.today() - timedelta(days=days - 1), time.min)
    stmt = (
        profile_query(profile_user, viewer)
        .where(Post.mood.is_not(None), Post.created_at >= start)
        .order_by(None)
        .order_by(Post.created_at.asc(), Post.id.asc())
    )
    moods = {}
    for post in db.session.scalars(stmt):
        if post.mood in MOODS:
            moods[post.created_at.date()] = post.mood  # later posts overwrite
    return moods


@bp.route("/u/<username>")
def view_profile(username: str) -> str:
    """Show a member's profile, mood calendar and visible posts.

    For signed-in users. Only posts the viewer may see are listed. A
    404 if the username does not exist.

    Args:
        username: The username of the profile owner.

    Returns:
        The profile page.
    """
    user = _user_or_404(username)
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(
        profile_query(user, current_user),
        page=page,
        per_page=current_app.config["POSTS_PER_PAGE"],
        error_out=False,
    )

    moods = daily_moods(user, current_user, 30)
    today = date.today()
    mood_calendar = []
    for offset in range(29, -1, -1):  # oldest first, today last
        day = today - timedelta(days=offset)
        mood = moods.get(day)
        mood_calendar.append(
            {"date": day, "mood": mood, "info": MOODS.get(mood)}
        )

    return render_template(
        "profile/view.html",
        user=user,
        pagination=pagination,
        posts=pagination.items,
        mood_calendar=mood_calendar,
        is_own_profile=user.id == current_user.id,
    )


@bp.route("/u/<username>/moods.json")
def moods_json(username: str) -> Response:
    """Give a member's recent moods as JSON.

    For signed-in users. Only posts the viewer may see count. A 404 if
    the username does not exist.

    Args:
        username: The username of the profile owner.

    Returns:
        JSON for the last 90 days, oldest first, as a list of
        ``["YYYY-MM-DD", mood, score]`` entries.
    """
    user = _user_or_404(username)
    moods = daily_moods(user, current_user, 90)
    return jsonify(
        [
            [day.isoformat(), mood, MOODS[mood]["score"]]
            for day, mood in sorted(moods.items())
        ]
    )


@bp.route("/family")
def family() -> str:
    """Show the family directory with each member's post count.

    For signed-in users. Counts include only posts any family member
    can see.

    Returns:
        The family page.
    """
    users = db.session.scalars(
        db.select(User).order_by(
            func.lower(func.coalesce(User.display_name, User.username))
        )
    ).all()
    # Count only posts any family member can see (everyone + profile).
    counts = dict(
        db.session.execute(
            db.select(Post.author_id, func.count(Post.id))
            .where(Post.visibility.in_(FAMILY_VISIBLE))
            .group_by(Post.author_id)
        ).all()
    )
    members = [{"user": u, "post_count": counts.get(u.id, 0)} for u in users]
    return render_template("profile/family.html", members=members)
