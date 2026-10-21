"""Visibility rules and the query builders that enforce them.

Every listing of posts (feed, profile, search, mood data) is built from
these helpers so the rules live in exactly one place.
"""
from functools import wraps

from flask import abort
from flask_login import current_user
from sqlalchemy import ColumnElement, Select, or_, true

from ..models import Post, User, db

FAMILY_VISIBLE = ("everyone", "profile")


def can_view(post: Post | None, user: User | None) -> bool:
    """Decide whether a user may see a post.

    Posts visible to "everyone" or on the "profile" are open to any
    logged-in user; private posts only to their author and admins.

    Args:
        post: The post being viewed, or ``None``.
        user: The signed-in user, or ``None`` when signed out.

    Returns:
        ``True`` if the post is visible to the user.
    """
    if post is None or user is None or not user.is_authenticated:
        return False
    if post.visibility in FAMILY_VISIBLE:
        return True
    return post.author_id == user.id or bool(user.is_admin)


def visible_clause(user: User) -> ColumnElement:
    """Build the SQL condition matching the posts ``can_view`` allows.

    Args:
        user: The signed-in user.

    Returns:
        A condition to pass to ``where()`` on a ``Post`` query.
    """
    if user.is_admin:
        return true()
    return or_(
        Post.visibility.in_(FAMILY_VISIBLE), Post.author_id == user.id)


def feed_query() -> Select:
    """Build the home feed query.

    Returns:
        A select of only "everyone" posts from all users, newest first.
    """
    return (db.select(Post)
            .where(Post.visibility == "everyone")
            .order_by(Post.created_at.desc(), Post.id.desc()))


def profile_query(profile_user: User, viewer: User) -> Select:
    """Build the query for a user's posts as seen by a viewer.

    Args:
        profile_user: The user whose profile is being viewed.
        viewer: The signed-in user looking at the profile.

    Returns:
        A select of the "everyone" and "profile" posts, plus private
        ones when the viewer is the owner or an admin, newest first.
    """
    stmt = db.select(Post).where(Post.author_id == profile_user.id)
    if not (viewer.id == profile_user.id or viewer.is_admin):
        stmt = stmt.where(Post.visibility.in_(FAMILY_VISIBLE))
    return stmt.order_by(Post.created_at.desc(), Post.id.desc())


def get_viewable_post_or_404(post_id: int) -> Post:
    """Fetch a post the current user may see.

    A missing post and a hidden one both give a 404, so the existence
    of private posts is not leaked.

    Args:
        post_id: The id of the post.

    Returns:
        The post.

    Raises:
        werkzeug.exceptions.NotFound: If the post does not exist or the
            current user may not see it.
    """
    post = db.session.get(Post, post_id)
    if post is None or not can_view(post, current_user):
        abort(404)
    return post


def admin_required(view: object) -> object:
    """Decorate a view so only signed-in admins can call it.

    Args:
        view: The view function to protect.

    Returns:
        The wrapped view, which aborts with 403 for non-admins.
    """
    @wraps(view)
    def wrapped(*args: object, **kwargs: object) -> object:
        """Call the view for admins, otherwise abort with 403.

        Args:
            *args: Positional arguments for the view.
            **kwargs: Keyword arguments for the view.

        Returns:
            Whatever the view returns.

        Raises:
            werkzeug.exceptions.Forbidden: If the user is not an admin.
        """
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
