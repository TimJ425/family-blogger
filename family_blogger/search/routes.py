"""Search and filter posts the current user is allowed to see."""
from datetime import date, datetime, time, timedelta

from flask import current_app, render_template, request
from flask_login import current_user
from sqlalchemy import func, or_
from sqlalchemy.sql import Select

from ..constants import MOODS
from ..forms import SearchForm
from ..models import Post, User, db
from ..utils.permissions import visible_clause
from . import bp


def search_query(
    viewer: User,
    q: str | None = None,
    author: str | None = None,
    mood: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Select:
    """Build the search statement. Visibility always obeys can_view.

    Args:
        viewer: The user searching.
        q: Text to look for in titles and bodies.
        author: A username to limit the results to.
        mood: A mood key to limit the results to.
        date_from: The first day to include.
        date_to: The last day to include (the whole day counts).

    Returns:
        A select statement for the matching posts, newest first.
    """
    stmt = db.select(Post).where(visible_clause(viewer))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Post.title.ilike(like), Post.body.ilike(like)))
    if author:
        stmt = stmt.join(Post.author).where(
            func.lower(User.username) == author.strip().lower()
        )
    if mood in MOODS:
        stmt = stmt.where(Post.mood == mood)
    if date_from:
        stmt = stmt.where(
            Post.created_at >= datetime.combine(date_from, time.min)
        )
    if date_to:  # inclusive of the whole "to" day
        end = datetime.combine(date_to + timedelta(days=1), time.min)
        stmt = stmt.where(Post.created_at < end)
    return stmt.order_by(Post.created_at.desc(), Post.id.desc())


@bp.route("/search")
def search() -> str:
    """Show the search form and, when filters are given, results.

    For signed-in users. Filters come from the query string: ``q``,
    ``author``, ``mood``, ``date_from`` and ``date_to``. Only posts
    the user may see are returned.

    Returns:
        The search page.
    """
    form = SearchForm(request.args)
    users = db.session.scalars(
        db.select(User).order_by(func.lower(User.username))
    ).all()
    form.author.choices = [("", "Anyone")] + [
        (u.username, u.name) for u in users
    ]

    filter_keys = ("q", "author", "mood", "date_from", "date_to")
    searched = any(request.args.get(k) for k in filter_keys)
    pagination = None
    if searched and form.validate():
        stmt = search_query(
            current_user,
            form.q.data,
            form.author.data,
            form.mood.data,
            form.date_from.data,
            form.date_to.data,
        )
        pagination = db.paginate(
            stmt,
            page=request.args.get("page", 1, type=int),
            per_page=current_app.config["POSTS_PER_PAGE"],
            error_out=False,
        )

    # Current filters, minus page, for building pagination links.
    page_args = {
        k: v for k, v in request.args.items() if k in filter_keys and v
    }
    return render_template(
        "search/index.html",
        form=form,
        searched=searched,
        pagination=pagination,
        posts=pagination.items if pagination else [],
        page_args=page_args,
    )
