"""Regression tests for the medium and low bug fixes (M6-M10, M16).

Covers pagination redirects, upload clean-up order, unique-constraint
races, the 500 handler, query counts, search escaping, date-range
validation, mood chart gaps, the onboarding Enter key, edited-post
detection, the colour input after a validation error, post photo
clean-up when a commit fails (L2) and migration 1 refusing usernames
that differ only in case (L8). Uses the real templates.
"""
import atexit
import io
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask.testing import FlaskClient
from PIL import Image
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.migrations import MigrationError  # noqa: E402
from family_blogger.models import (  # noqa: E402
    Comment,
    Photo,
    Post,
    Reaction,
    User,
    db,
)
from family_blogger.utils.uploads import upload_folder  # noqa: E402
from helpers import join_family  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = tempfile.mkdtemp(prefix="fb-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
INST = os.path.join(SCRATCH, "bugfix_instance")

app = create_app(
    {"INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False}
)
# Let the 500 handler run and keep the expected tracebacks quiet.
app.config["PROPAGATE_EXCEPTIONS"] = False
app.logger.disabled = True


@app.route("/__fail_mid_transaction")
def fail_mid_transaction() -> str:
    """Break the database transaction, then fail.

    Returns:
        Never returns.

    Raises:
        IntegrityError: Always, from a duplicate username.
    """
    db.session.add(User(username="tim", is_admin=False))
    db.session.flush()
    return "unreachable"


passed = 0


def check(cond: object, msg: str) -> None:
    """Assert that a condition holds and count it as a passed check.

    Args:
        cond: The condition, which must be truthy.
        msg: The failure message.

    Raises:
        AssertionError: If the condition is falsy.
    """
    global passed
    if not cond:
        raise AssertionError(msg)
    passed += 1


def onboard(username: str) -> None:
    """Mark a member as onboarded so every page is reachable.

    Args:
        username: The member's username.
    """
    with app.app_context():
        user = User.find_by_username(username)
        user.onboarded = True
        db.session.commit()


def png_bytes() -> bytes:
    """Make a small PNG image.

    Returns:
        The encoded image.
    """
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


def upload_names() -> set[str]:
    """List the files in the upload folder.

    Returns:
        The set of file names.
    """
    with app.app_context():
        return set(os.listdir(upload_folder()))


def add_posts(
    username: str, count: int, title: str = "Post",
    visibility: str = "everyone"
) -> list[int]:
    """Create posts for a member, oldest first.

    Args:
        username: The author's username.
        count: How many posts to create.
        title: Title prefix for the posts.
        visibility: The visibility of every post.

    Returns:
        The new post ids.
    """
    with app.app_context():
        author = User.find_by_username(username)
        base = datetime(2026, 1, 1, 12, 0, 0)
        ids = []
        for n in range(count):
            post = Post(
                author_id=author.id, title=f"{title} {n}", body="body",
                visibility=visibility, mood="happy",
                created_at=base + timedelta(hours=n),
                updated_at=base + timedelta(hours=n),
            )
            db.session.add(post)
            db.session.flush()
            ids.append(post.id)
        db.session.commit()
        return ids


def location(response: object) -> str:
    """Get a redirect's target as a path plus query string.

    Args:
        response: The test response.

    Returns:
        The ``Location`` header without the host.
    """
    return re.sub(r"^https?://[^/]+", "", response.headers["Location"])


admin = join_family(app, "tim")
onboard("tim")
mum = join_family(app, "mum")
onboard("mum")

# --- M6: empty state and out-of-range pages -----------------------
r = admin.get("/")
check(r.status_code == 200 and b"No posts yet" in r.data,
      "empty feed shows the empty state")

ids = add_posts("tim", 21)
r = admin.get("/?page=99")
check(r.status_code == 302 and location(r) == "/?page=3",
      f"feed page 99 -> last page: {r.status_code} {location(r)}")
r = admin.get("/?page=3")
check(r.status_code == 200 and b"No posts yet" not in r.data,
      "last feed page renders posts")
with app.app_context():
    db.session.delete(db.session.get(Post, ids[0]))  # oldest = page 3
    db.session.commit()
r = admin.get("/?page=3")
check(r.status_code == 302 and location(r) == "/?page=2",
      "after deleting the last post on a page, land on a valid page")
check(admin.get("/?page=2").status_code == 200, "page 2 renders")

r = admin.get("/u/tim?page=50")
check(r.status_code == 302 and location(r) == "/u/tim?page=2",
      f"profile out of range -> last page: {location(r)}")
r = mum.get("/u/mum")
check(r.status_code == 200 and b"No posts to show yet" in r.data,
      "profile with no posts shows the empty state")

r = admin.get("/search?q=Post&page=40")
check(r.status_code == 302 and "page=2" in location(r)
      and "q=Post" in location(r),
      f"search out of range keeps filters: {location(r)}")
r = admin.get("/search?q=zzzzzz")
check(r.status_code == 200 and b"No posts found" in r.data,
      "search with no results shows 'No posts found'")

# --- M7: upload files are removed only after a good commit ---------
fail_commit = []


@event.listens_for(Session, "before_commit")
def _maybe_fail(_session: object) -> None:
    """Make the next commit fail when a test asks for it.

    Args:
        _session: The session being committed (unused).

    Raises:
        IntegrityError: When ``fail_commit`` holds a flag.
    """
    if fail_commit:
        fail_commit.pop()
        raise IntegrityError("commit", {}, Exception("forced"))


def post_avatar(client: FlaskClient) -> object:
    """Upload a new avatar through the settings form.

    Args:
        client: The member's test client.

    Returns:
        The test response.
    """
    return client.post(
        "/settings/profile",
        data={"display_name": "Tim", "bio": "",
              "avatar_upload": (io.BytesIO(png_bytes()), "a.png")},
        content_type="multipart/form-data",
    )


r = post_avatar(admin)
check(r.status_code == 302, f"avatar saved: {r.status_code}")
with app.app_context():
    first_avatar = User.find_by_username("tim").avatar
check(first_avatar in upload_names(), "first avatar is on disk")

before = upload_names()
fail_commit.append(True)
r = post_avatar(admin)
check(r.status_code == 500, f"failed commit -> 500: {r.status_code}")
check(upload_names() == before,
      "failed commit: old avatar kept, new file removed")
with app.app_context():
    check(User.find_by_username("tim").avatar == first_avatar,
          "failed commit: avatar unchanged in the database")

r = post_avatar(admin)
with app.app_context():
    second_avatar = User.find_by_username("tim").avatar
check(second_avatar != first_avatar
      and first_avatar not in upload_names()
      and second_avatar in upload_names(),
      "good commit: old avatar deleted after the commit")


def post_background(client: FlaskClient) -> object:
    """Upload a new background through the settings form.

    Args:
        client: The member's test client.

    Returns:
        The test response.
    """
    return client.post(
        "/settings/background",
        data={"background": (io.BytesIO(png_bytes()), "b.png")},
        content_type="multipart/form-data",
    )


check(post_background(admin).status_code == 302, "background saved")
with app.app_context():
    bg1 = User.find_by_username("tim").background_image
before = upload_names()
fail_commit.append(True)
check(post_background(admin).status_code == 500, "background commit fails")
check(upload_names() == before, "failed background keeps old, drops new")
check(post_background(admin).status_code == 302, "background replaced")
check(bg1 not in upload_names(), "old background deleted after commit")
with app.app_context():
    bg2 = User.find_by_username("tim").background_image
fail_commit.append(True)
check(admin.post("/settings/background/remove").status_code == 500,
      "remove background commit fails")
check(bg2 in upload_names(), "failed remove keeps the file")
admin.post("/settings/background/remove")
check(bg2 not in upload_names(), "successful remove deletes the file")

# Onboarding: a failed commit must not delete the old avatar either.
newbie = join_family(app, "kid")
before = upload_names()
fail_commit.append(True)
r = newbie.post(
    "/welcome",
    data={"display_name": "Kid", "bio": "", "theme_preset": "sunny",
          "avatar": "", "avatar_upload": (io.BytesIO(png_bytes()), "a.png")},
    content_type="multipart/form-data",
)
check(r.status_code == 500, f"onboarding commit fails: {r.status_code}")
check(upload_names() == before, "failed onboarding leaves no new file")

# --- M8: a raced unique insert is not a 500 -----------------------
post_id = add_posts("tim", 1, "Race")[0]
with app.app_context():
    db_path = db.engine.url.database
racing = []


@event.listens_for(Session, "before_commit")
def _race(_session: object) -> None:
    """Insert the same reaction from another connection first.

    Args:
        _session: The session being committed (unused).
    """
    if racing:
        racing.pop()
        con = sqlite3.connect(db_path, timeout=5)
        con.execute(
            "INSERT INTO reaction (post_id, user_id, kind, created_at) "
            "SELECT ?, id, 'heart', datetime('now') FROM user "
            "WHERE username = 'tim'", (post_id,))
        con.commit()
        con.close()


racing.append(True)
r = admin.post(f"/post/{post_id}/react/heart",
               headers={"X-Requested-With": "fetch"})
check(r.status_code == 200 and r.get_json()["reacted"] is True
      and r.get_json()["counts"]["heart"] == 1,
      f"double-tap reaction handled: {r.status_code}")
r = admin.post(f"/post/{post_id}/react/heart",
               headers={"X-Requested-With": "fetch"})
check(r.get_json()["reacted"] is False, "next tap toggles it off")

# --- M9: the 500 page survives a broken transaction ---------------
r = admin.get("/__fail_mid_transaction")
check(r.status_code == 500 and b"Something went wrong" in r.data,
      f"500 page after a failed flush: {r.status_code}")
check(r.headers.get("X-Content-Type-Options") == "nosniff"
      and "default-src" in r.headers.get("Content-Security-Policy", ""),
      "500 page carries the security headers")
check(admin.get("/").status_code == 200, "app still works afterwards")

# --- M10: bounded query count --------------------------------------
with app.app_context():
    author = User.find_by_username("mum")
    members = db.session.scalars(db.select(User)).all()
    base = datetime(2026, 3, 1, 8, 0, 0)
    for n in range(10):
        post = Post(author_id=author.id, title=f"Busy {n}", body="x",
                    visibility="everyone",
                    created_at=base + timedelta(hours=n),
                    updated_at=base + timedelta(hours=n))
        db.session.add(post)
        db.session.flush()
        db.session.add(Photo(post_id=post.id, filename=f"{n:032x}.jpg"))
        db.session.commit()


def count_queries(client: FlaskClient, url: str) -> int:
    """Count the SQL statements one request runs.

    Args:
        client: The test client.
        url: The page to fetch.

    Returns:
        The number of statements executed.
    """
    seen = []

    def on_execute(*_args: object) -> None:
        """Record one statement.

        Args:
            *_args: The SQLAlchemy event arguments (unused).
        """
        seen.append(1)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", on_execute)
    try:
        r = client.get(url)
    finally:
        event.remove(engine, "before_cursor_execute", on_execute)
    check(r.status_code == 200, f"{url} -> {r.status_code}")
    return len(seen)


light = count_queries(admin, "/")
with app.app_context():
    posts = db.session.scalars(
        db.select(Post).where(Post.title.like("Busy %"))).all()
    users = db.session.scalars(db.select(User)).all()
    kinds = ["heart", "hug", "smile", "clap"]
    for post in posts:
        for n in range(15):
            db.session.add(Comment(
                post_id=post.id, author_id=users[n % len(users)].id,
                body=f"c{n}"))
        for user in users:
            db.session.add(Reaction(
                post_id=post.id, user_id=user.id, kind="heart"))
    db.session.commit()
heavy = count_queries(admin, "/")
check(light < 15 and heavy < 15,
      f"feed query count bounded: {light} then {heavy}")
check(heavy <= light + 1, "query count does not grow with comments")
check(count_queries(admin, "/u/mum") < 15, "profile query count bounded")
check(count_queries(admin, "/search?q=Busy") < 15,
      "search query count bounded")
with app.app_context():
    busy = db.session.scalars(
        db.select(Post).where(Post.title == "Busy 3")).one()
    check(busy.comment_count == 15, f"comment_count {busy.comment_count}")
    check(Post.comment_count is not None, "comment_count is a column")

# --- M16: search treats % and _ literally, folds case -------------
with app.app_context():
    author = User.find_by_username("tim")
    for title in ("Got 100% sure", "Got 1000 sure", "a_b", "aXb",
                  "back\\slash", "ÄPFEL pie"):
        db.session.add(Post(author_id=author.id, title=title, body="-",
                            visibility="everyone"))
    db.session.commit()


def found(query: str) -> set[str]:
    """Search and list the post titles that matched.

    Args:
        query: The text to search for.

    Returns:
        The titles of the posts on the first results page.
    """
    with app.app_context():
        from family_blogger.search.routes import search_query
        stmt = search_query(User.find_by_username("tim"), q=query)
        return {p.title for p in db.session.scalars(stmt)}


check(found("100%") == {"Got 100% sure"}, f"percent: {found('100%')}")
check(found("a_b") == {"a_b"}, f"underscore: {found('a_b')}")
check(found("\\") == {"back\\slash"}, "backslash is literal")
check(found("äpfel") == {"ÄPFEL pie"},
      "non-ASCII search ignores case")
check("Got 100% sure" in found("GOT 100%"), "ASCII ignores case")

# --- Search: From after To ----------------------------------------
r = admin.get("/search?date_from=2026-02-01&date_to=2026-01-01")
check(r.status_code == 200 and b"before the" in r.data
      and b"No posts found" not in r.data,
      "reversed dates give an error and no 'No posts found'")
r = admin.get("/search?date_from=2026-01-01&date_to=2026-12-01&q=Busy")
check(r.status_code == 200 and b"post" in r.data
      and b"before the" not in r.data, "valid range still works")

# --- Mood chart: gaps by date --------------------------------------
js = (ROOT / "family_blogger/static/js/mood-chart.js").read_text()
check("spanGaps: false" in js and "spanGaps: true" not in js,
      "mood chart does not join across missing days")
check("values.push(" in js and "86400000" in js,
      "mood chart builds a daily label array")
r = admin.get("/u/tim/moods.json")
check(r.status_code == 200 and all(len(row) == 3 for row in r.get_json()),
      "moods.json still gives [date, mood, score] rows")

# --- Onboarding: Enter advances the wizard -------------------------
js = (ROOT / "family_blogger/static/js/theme.js").read_text()
check("e.key !== 'Enter'" in js and "e.preventDefault()" in js
      and "show(i + 1)" in js, "Enter advances the wizard")
r = newbie.get("/welcome")
check(b"wizard-finish" in r.data and b'method="post"' in r.data
      and b"onsubmit" not in r.data,
      "no-JS fallback is still one plain form")

# --- Edited posts around the clock change -------------------------
base = datetime(2026, 10, 25, 1, 30, 0)
same = Post(created_at=base, updated_at=base)
quick = Post(created_at=base, updated_at=base + timedelta(seconds=30))
later = Post(created_at=base, updated_at=base + timedelta(hours=2))
fallback = Post(created_at=base, updated_at=base - timedelta(minutes=20))
check(not same.is_edited and not quick.is_edited, "untouched = not edited")
check(later.is_edited, "later edit shows as edited")
check(fallback.is_edited, "edit in the repeated DST hour shows as edited")

# --- Invalid colour keeps a valid colour in the input -------------
r = admin.post("/settings/theme", data={
    "theme_preset": "sunny", "custom_colours": "y",
    "colour_primary": "banana", "colour_accent": "#112233",
    "colour_bg": "#ffffff", "colour_card": "#eeeeee",
    "colour_text": "#000000"})
html = r.get_data(as_text=True)
m = re.search(r'name="colour_primary" value="([^"]*)"', html)
check(r.status_code == 200 and "hex colour" in html,
      "invalid colour shows its error")
check(m and re.fullmatch(r"#[0-9a-fA-F]{6}", m.group(1)),
      f"colour input keeps a valid colour: {m and m.group(1)}")
m = re.search(r'name="colour_accent" value="([^"]*)"', html)
check(m and m.group(1) == "#112233", "other submitted colours are kept")

# --- L2: post photos are removed if the commit fails -------------


def post_form(photo_count: int, **extra: object) -> dict:
    """Build the post form data with some photos attached.

    Args:
        photo_count: How many small PNG photos to attach.
        **extra: More form fields.

    Returns:
        The form data for the test client.
    """
    data = {"title": "Photo day", "body": "Look!", "mood": "",
            "visibility": "everyone",
            "photos": [(io.BytesIO(png_bytes()), f"p{n}.png")
                       for n in range(photo_count)]}
    data.update(extra)
    return data


def photo_rows() -> int:
    """Count the photo rows in the database.

    Returns:
        The number of photos.
    """
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Photo.id)))


before_files, before_rows = upload_names(), photo_rows()
fail_commit.append(True)
r = admin.post("/post/new", data=post_form(2),
               content_type="multipart/form-data")
check(r.status_code == 500, f"new post commit fails: {r.status_code}")
check(upload_names() == before_files,
      "failed new post leaves no photo files behind")
check(photo_rows() == before_rows, "failed new post adds no photo rows")
r = admin.post("/post/new", data=post_form(1),
               content_type="multipart/form-data")
check(r.status_code == 302, f"new post with a photo saved: {r.status_code}")
photo_post = int(location(r).rsplit("/", 1)[-1])
with app.app_context():
    kept = db.session.scalars(
        db.select(Photo.filename).where(Photo.post_id == photo_post)).all()
check(len(kept) == 1 and kept[0] in upload_names(), "photo stored")

before_files = upload_names()
fail_commit.append(True)
with app.app_context():
    kept_id = db.session.scalar(
        db.select(Photo.id).where(Photo.post_id == photo_post))
r = admin.post(f"/post/{photo_post}/edit",
               data=post_form(1, remove_photos=[str(kept_id)]),
               content_type="multipart/form-data")
check(r.status_code == 500, f"edit commit fails: {r.status_code}")
check(upload_names() == before_files,
      "failed edit removes the new photo and keeps the old one")
r = admin.post(f"/post/{photo_post}/edit",
               data=post_form(1, remove_photos=[str(kept_id)]),
               content_type="multipart/form-data")
check(r.status_code == 302, "edit with photo swap saved")
with app.app_context():
    now_files = db.session.scalars(
        db.select(Photo.filename).where(Photo.post_id == photo_post)).all()
check(kept[0] not in upload_names() and len(now_files) == 1
      and now_files[0] in upload_names(),
      "good edit deletes the removed photo after the commit")

# --- L8: migration 1 refuses case-duplicate usernames -------------
dup_dir = Path(SCRATCH) / "dup_instance"
dup_dir.mkdir()
dup_db = dup_dir / "family_blogger.db"
con = sqlite3.connect(dup_db)
con.execute(
    "CREATE TABLE user (id INTEGER NOT NULL PRIMARY KEY, "
    "username VARCHAR(30) NOT NULL UNIQUE, "
    "password_hash VARCHAR(256) NOT NULL, display_name VARCHAR(60), "
    "bio VARCHAR(500), avatar VARCHAR(64) NOT NULL, "
    "is_admin BOOLEAN NOT NULL, onboarded BOOLEAN NOT NULL, "
    "created_at DATETIME NOT NULL, theme_preset VARCHAR(20) NOT NULL, "
    "colour_primary VARCHAR(7), colour_accent VARCHAR(7), "
    "colour_bg VARCHAR(7), colour_card VARCHAR(7), "
    "colour_text VARCHAR(7), background_image VARCHAR(64))")
for dup_name in ("Kid", "kid", "Mum"):
    con.execute(
        "INSERT INTO user (username, password_hash, avatar, is_admin, "
        "onboarded, created_at, theme_preset) VALUES "
        "(?, 'h', 'fox', 0, 1, '2026-01-01 10:00:00', 'sunny')",
        (dup_name,))
con.commit()
con.close()
try:
    create_app({"INSTANCE_DIR": str(dup_dir), "TESTING": True})
    dup_error = ""
except MigrationError as exc:
    dup_error = str(exc)
check("Kid" in dup_error and "kid" in dup_error
      and "Mum" not in dup_error and "Nothing was changed" in dup_error,
      f"migration names the clashing usernames: {dup_error!r}")
con = sqlite3.connect(dup_db)
check(con.execute("PRAGMA user_version").fetchone()[0] == 0,
      "refused migration leaves the version alone")
check(sorted(r[0] for r in con.execute("SELECT username FROM user"))
      == ["Kid", "Mum", "kid"], "refused migration keeps every row")
check(not [r for r in con.execute("PRAGMA table_info(user)")
           if r[1] == "session_token"],
      "refused migration leaves the user table as it was")
con.close()

print(f"ALL {passed} CHECKS PASSED")
