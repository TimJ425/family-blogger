"""Home feed, post CRUD, comments and reactions."""
from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from werkzeug.wrappers import Response

from ..constants import REACTIONS
from ..forms import CommentForm, PostForm
from ..models import Comment, Photo, Post, Reaction, db, now
from ..utils import flash_form_errors, redirect_back
from ..utils.permissions import can_view, feed_query, get_viewable_post_or_404
from ..utils.uploads import UploadError, delete_upload, has_file, save_image
from . import bp


@bp.route("/")
def feed() -> str:
    """Show the home feed, newest first, one page at a time.

    For signed-in users. Only posts the user may see are listed. The
    page number comes from the ``page`` query argument.

    Returns:
        The feed page.
    """
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(
        feed_query(),
        page=page,
        per_page=current_app.config["POSTS_PER_PAGE"],
        error_out=False,
    )
    return render_template(
        "posts/feed.html",
        pagination=pagination,
        posts=pagination.items,
        has_posted_today=current_user.has_posted_today,
    )


def _save_photos(files: object, existing_count: int = 0) -> list[str]:
    """Save uploaded photos; on any failure remove the ones saved.

    Args:
        files: The uploaded ``FileStorage`` objects (a list, or
            ``None``). Empty entries are ignored.
        existing_count: How many photos the post already has.

    Returns:
        The stored filenames of the new photos.

    Raises:
        UploadError: If the post would exceed the photo limit, or an
            image is rejected.
    """
    files = [f for f in files or [] if has_file(f)]
    limit = current_app.config["MAX_PHOTOS_PER_POST"]
    if existing_count + len(files) > limit:
        raise UploadError(f"A post can have at most {limit} photos.")
    saved = []
    try:
        for f in files:
            saved.append(save_image(f, "post"))
    except UploadError:
        for name in saved:
            delete_upload(name)
        raise
    return saved


@bp.route("/post/new", methods=["GET", "POST"])
def new_post() -> str | Response:
    """Show the new-post form and create the post on submit.

    For signed-in users. On success it redirects to the new post. An
    upload error re-shows the form with a message.

    Returns:
        The post form page, or a redirect to the new post.
    """
    form = PostForm()
    if form.validate_on_submit():
        try:
            filenames = _save_photos(form.photos.data)
        except UploadError as exc:
            flash(str(exc), "error")
        else:
            post = Post(
                author=current_user,
                title=form.title.data.strip(),
                body=form.body.data or "",
                mood=form.mood.data or None,
                visibility=form.visibility.data,
            )
            post.photos = [Photo(filename=name) for name in filenames]
            db.session.add(post)
            db.session.commit()
            flash("Your diary entry is posted!", "success")
            return redirect(post.url)
    return render_template("posts/form.html", form=form, post=None)


@bp.route("/post/<int:post_id>")
def view_post(post_id: int) -> str:
    """Show one post with its comments and a comment form.

    Visible only to users who may see the post; others get a 404.

    Args:
        post_id: The id of the post.

    Returns:
        The post page.
    """
    post = get_viewable_post_or_404(post_id)
    return render_template(
        "posts/view.html",
        post=post,
        comments=post.comments,
        comment_form=CommentForm(),
    )


@bp.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
def edit_post(post_id: int) -> str | Response:
    """Show the edit form for a post and save changes on submit.

    Only users who may edit the post can use it (403 otherwise). It
    can change the text, mood and visibility, remove photos and add
    new ones. On success it redirects to the post.

    Args:
        post_id: The id of the post.

    Returns:
        The post form page, or a redirect to the post.
    """
    post = get_viewable_post_or_404(post_id)
    if not post.can_edit(current_user):
        abort(403)

    form = PostForm()
    form.remove_photos.choices = [(p.id, p.filename) for p in post.photos]
    if not form.is_submitted():
        form.title.data = post.title
        form.body.data = post.body
        form.mood.data = post.mood or ""
        form.visibility.data = post.visibility

    if form.validate_on_submit():
        remove_ids = set(form.remove_photos.data or [])
        to_remove = [p for p in post.photos if p.id in remove_ids]
        try:
            filenames = _save_photos(
                form.photos.data, len(post.photos) - len(to_remove)
            )
        except UploadError as exc:
            flash(str(exc), "error")
        else:
            post.title = form.title.data.strip()
            post.body = form.body.data or ""
            post.mood = form.mood.data or None
            post.visibility = form.visibility.data
            post.updated_at = now()
            for photo in to_remove:
                post.photos.remove(photo)
            post.photos.extend(Photo(filename=name) for name in filenames)
            db.session.commit()
            for photo in to_remove:
                delete_upload(photo.filename)
            flash("Post updated.", "success")
            return redirect(post.url)
    return render_template("posts/form.html", form=form, post=post)


@bp.route("/post/<int:post_id>/delete", methods=["POST"])
def delete_post(post_id: int) -> Response:
    """Delete a post and its photo files.

    POST only. Allowed for users who may delete the post (403
    otherwise). It redirects to a safe local ``next`` URL from the
    form, unless that points at the deleted post, else to the feed.

    Args:
        post_id: The id of the post.

    Returns:
        A redirect.
    """
    post = get_viewable_post_or_404(post_id)
    if not post.can_delete(current_user):
        abort(403)
    filenames = [p.filename for p in post.photos]
    db.session.delete(post)
    db.session.commit()
    for name in filenames:
        delete_upload(name)
    flash("Post deleted.", "success")
    # Never bounce back to the (now missing) permalink.
    target = request.form.get("next")
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and not target.startswith(f"/post/{post_id}")
    ):
        return redirect(target)
    return redirect(url_for("posts.feed"))


@bp.route("/post/<int:post_id>/comment", methods=["POST"])
def add_comment(post_id: int) -> Response:
    """Add a comment to a post.

    POST only, for users who may see the post. Invalid input is
    reported with flashed messages.

    Args:
        post_id: The id of the post.

    Returns:
        A redirect to the comments section of the post.
    """
    post = get_viewable_post_or_404(post_id)
    form = CommentForm()
    if form.validate_on_submit():
        body = form.body.data.strip()
        db.session.add(Comment(post=post, author=current_user, body=body))
        db.session.commit()
        flash("Comment added.", "success")
    else:
        flash_form_errors(form)
    return redirect(
        url_for("posts.view_post", post_id=post.id, _anchor="comments")
    )


@bp.route("/comment/<int:comment_id>/delete", methods=["POST"])
def delete_comment(comment_id: int) -> Response:
    """Delete a comment.

    POST only. A 404 if the comment's post is not visible to the
    user, and a 403 if the user may not delete the comment.

    Args:
        comment_id: The id of the comment.

    Returns:
        A redirect to the comments section of its post.
    """
    comment = db.session.get(Comment, comment_id)
    if comment is None or not can_view(comment.post, current_user):
        abort(404)
    if not comment.can_delete(current_user):
        abort(403)
    post_id = comment.post_id
    db.session.delete(comment)
    db.session.commit()
    flash("Comment deleted.", "success")
    return redirect(
        url_for("posts.view_post", post_id=post_id, _anchor="comments")
    )


@bp.route("/post/<int:post_id>/react/<kind>", methods=["POST"])
def react(post_id: int, kind: str) -> Response:
    """Toggle one reaction on a post.

    POST only, for users who may see the post. An unknown reaction
    kind gives a 404.

    Args:
        post_id: The id of the post.
        kind: The reaction kind, a key of ``REACTIONS``.

    Returns:
        JSON with the kind, new state and counts for ``fetch()``
        callers, otherwise a redirect back.
    """
    if kind not in REACTIONS:
        abort(404)
    post = get_viewable_post_or_404(post_id)
    existing = db.session.scalar(
        db.select(Reaction).filter_by(
            post_id=post.id, user_id=current_user.id, kind=kind
        )
    )
    if existing:
        db.session.delete(existing)
        reacted = False
    else:
        db.session.add(Reaction(post=post, user=current_user, kind=kind))
        reacted = True
    db.session.commit()

    wants_json = (
        request.accept_mimetypes.best == "application/json"
        or request.headers.get("X-Requested-With") == "fetch"
    )
    if wants_json:
        return jsonify(
            kind=kind, reacted=reacted, counts=post.reaction_counts()
        )
    return redirect_back("posts.view_post", post_id=post.id)
