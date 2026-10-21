"""Backend smoke test for Family Blogger (templates stubbed out)."""
import atexit
import io
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from flask import g, template_rendered
from flask.testing import FlaskClient
from jinja2 import FunctionLoader
from PIL import Image
from sqlalchemy.sql import Select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.models import (  # noqa: E402
    Comment,
    Photo,
    Post,
    Reaction,
    User,
    db,
)
from family_blogger.search.routes import search_query  # noqa: E402
from family_blogger.utils.markdown import render_markdown  # noqa: E402
from family_blogger.utils.permissions import (  # noqa: E402
    can_view,
    feed_query,
    profile_query,
)

SCRATCH = tempfile.mkdtemp(prefix="fb-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
INST = os.path.join(SCRATCH, "test_instance")
shutil.rmtree(INST, ignore_errors=True)

app = create_app(
    {"INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False}
)


def _stub_template(name: str) -> str:
    """Stand in for a template: render its name so tests can see it.

    Args:
        name: The template name being loaded.

    Returns:
        Template source that shows the name and the theme colour.
    """
    return f"TEMPLATE:{name} theme={{{{ theme.primary }}}}"


# Stub every template: render its name so we can assert which one
# was used.
app.jinja_loader = FunctionLoader(_stub_template)


# The test holds one app context open, which requests reuse; forget the
# per-request Flask-Login cache and stale ORM state after every request.
@app.teardown_request
def _reset(_exc: BaseException | None) -> None:
    """Clear cached login state and stale ORM objects after a request.

    Args:
        _exc: The exception that ended the request, if any.
    """
    g.pop("_login_user", None)
    db.session.expire_all()


rendered = []


def _record_render(
    sender: object, template: object, context: dict, **kw: object
) -> None:
    """Remember which template was rendered and with what context.

    Args:
        sender: The Flask app that sent the signal.
        template: The Jinja template that was rendered.
        context: The context the template received.
        **kw: Any other signal arguments.
    """
    rendered.append((template.name, context))


template_rendered.connect(_record_render, app, weak=False)

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


def client_for(username: str, password: str = "secret1") -> FlaskClient:
    """Log a user in with a new test client.

    Args:
        username: The username to log in as.
        password: The user's password.

    Returns:
        A test client holding the logged-in session.
    """
    c = app.test_client()
    r = c.post("/login", data={"username": username, "password": password})
    check(r.status_code == 302, f"login {username} -> {r.status_code}")
    return c


def last_ctx(name: str) -> dict:
    """Find the context of the most recent render of a template.

    Args:
        name: The template name.

    Returns:
        The context passed to that template.

    Raises:
        AssertionError: If the template was never rendered.
    """
    for tname, ctx in reversed(rendered):
        if tname == name:
            return ctx
    raise AssertionError(f"{name} not rendered")


def make_image(
    fmt: str = "JPEG", size: tuple[int, int] = (3000, 2000), exif: bool = True
) -> io.BytesIO:
    """Create an in-memory test image.

    Args:
        fmt: The Pillow image format, such as ``"JPEG"`` or ``"PNG"``.
        size: The width and height in pixels.
        exif: Whether to embed EXIF data (JPEG only).

    Returns:
        A buffer holding the image, rewound to the start.
    """
    img = Image.new("RGB", size, (200, 100, 50))
    buf = io.BytesIO()
    kwargs = {}
    if exif and fmt == "JPEG":
        ex = Image.Exif()
        ex[0x010F] = "SecretCameraMaker"
        ex[0x0112] = 6  # rotate 90
        kwargs["exif"] = ex.tobytes()
    img.save(buf, fmt, **kwargs)
    buf.seek(0)
    return buf


with app.test_request_context():
    anon = app.test_client()
    r = anon.get("/")
    check(
        r.status_code == 302 and "/login" in r.location,
        "anon redirected to login",
    )
    check(
        anon.get("/uploads/" + "a" * 32 + ".jpg").status_code == 302,
        "uploads need login",
    )
    check(anon.get("/login").status_code == 200, "login page public")

    # --- signup ---
    clients = {}
    for name in ("Alice", "bob", "carol"):
        c = app.test_client()
        r = c.post(
            "/signup",
            data={
                "username": name,
                "password": "secret1",
                "confirm_password": "secret1",
            },
        )
        check(
            r.status_code == 302 and r.location.endswith("/welcome"),
            f"signup {name}",
        )
        clients[name] = c
    r = app.test_client().post(
        "/signup",
        data={
            "username": "ALICE",
            "password": "secret1",
            "confirm_password": "secret1",
        },
    )
    check(
        r.status_code == 200
        and "username" in last_ctx("auth/signup.html")["form"].errors,
        "case-insensitive unique",
    )
    r = app.test_client().post(
        "/signup",
        data={"username": "a b", "password": "123", "confirm_password": "123"},
    )
    errs = last_ctx("auth/signup.html")["form"].errors
    check(
        "username" in errs and "password" in errs,
        "username/password validation",
    )

    alice, bob, carol = (
        User.find_by_username(n) for n in ("alice", "bob", "carol")
    )
    check(
        alice.is_admin and not bob.is_admin and not carol.is_admin,
        "first user is admin",
    )
    ca, cb, cc = clients["Alice"], clients["bob"], clients["carol"]

    # --- onboarding gate ---
    r = cb.get("/")
    check(
        r.status_code == 302 and r.location.endswith("/welcome"),
        "un-onboarded -> /welcome",
    )
    check(cb.get("/welcome").status_code == 200, "welcome renders")
    r = ca.post(
        "/welcome",
        data={
            "display_name": "Alice A",
            "bio": "Mum",
            "avatar": "owl",
            "theme_preset": "ocean",
            "avatar_upload": (make_image(), "me.jpg"),
            "background": (make_image("PNG"), "bg.png"),
        },
        content_type="multipart/form-data",
    )
    check(
        r.status_code == 302 and r.location.endswith("/"),
        "onboarding submit",
    )
    db.session.expire_all()
    check(
        alice.onboarded
        and alice.has_uploaded_avatar
        and alice.background_image,
        "onboarding saved uploads",
    )
    with Image.open(os.path.join(INST, "uploads", alice.avatar)) as im:
        check(im.size == (256, 256), "avatar 256 square")
    check(alice.theme["primary"] == "#0284c7", "ocean preset applied")
    for c in (cb, cc):
        c.post(
            "/welcome",
            data={
                "display_name": "",
                "avatar": "fox",
                "theme_preset": "night",
                "custom_colours": "y",
                "colour_primary": "#123456",
            },
        )
    db.session.expire_all()
    check(
        bob.onboarded
        and bob.theme["primary"] == "#123456"
        and bob.theme["bg"] == "#161a2e",
        "custom colours",
    )
    r = cb.get("/")
    check(
        r.status_code == 200 and b"theme=#123456" in r.data,
        "theme in context",
    )

    # --- posts ---
    def new_post(
        c: FlaskClient,
        title: str,
        vis: str,
        mood: str = "",
        photos: list | None = None,
    ) -> int:
        """Create a post through the form and return its id.

        Args:
            c: The logged-in test client.
            title: The post title.
            vis: The visibility setting.
            mood: The mood key, or an empty string.
            photos: Optional ``(file, filename)`` pairs to upload.

        Returns:
            The id of the new post.
        """
        data = {
            "title": title,
            "body": "Body of " + title,
            "mood": mood,
            "visibility": vis,
        }
        if photos:
            data["photos"] = photos
        r = c.post("/post/new", data=data, content_type="multipart/form-data")
        check(r.status_code == 302, f"create {title}: {r.status_code}")
        return int(r.location.rstrip("/").split("/")[-1])

    a_every = new_post(
        ca,
        "A everyone",
        "everyone",
        "great",
        photos=[
            (make_image(), "1.jpg"),
            (make_image("PNG", (100, 100)), "2.png"),
        ],
    )
    a_prof = new_post(ca, "A profile", "profile", "sad")
    a_priv = new_post(
        ca,
        "A private secret",
        "private",
        "grumpy",
        photos=[(make_image(size=(50, 50)), "p.jpg")],
    )
    b_every = new_post(cb, "B everyone", "everyone", "good")
    b_priv = new_post(cb, "B private", "private", "okay")

    p = db.session.get(Post, a_every)
    check(len(p.photos) == 2, "two photos saved")
    with Image.open(
        os.path.join(INST, "uploads", p.photos[0].filename)
    ) as im:
        check(max(im.size) == 1600, f"resized to 1600 ({im.size})")
        check(im.size == (1067, 1600), f"exif_transpose applied ({im.size})")
        check(not im.getexif(), "EXIF stripped")
    check(p.photos[1].filename.endswith(".jpg"), "RGB png saved as jpg")

    # Bad upload rejected, no post created
    count = db.session.scalar(db.select(db.func.count(Post.id)))
    r = cb.post(
        "/post/new",
        data={
            "title": "x",
            "visibility": "everyone",
            "photos": [(io.BytesIO(b"not an image"), "evil.jpg")],
        },
        content_type="multipart/form-data",
    )
    check(
        r.status_code == 200
        and db.session.scalar(db.select(db.func.count(Post.id))) == count,
        "bad image rejected",
    )

    # --- permission logic ---
    posts = {
        pid: db.session.get(Post, pid)
        for pid in (a_every, a_prof, a_priv, b_every, b_priv)
    }
    check(
        can_view(posts[a_priv], alice) and not can_view(posts[a_priv], bob),
        "private: author only",
    )
    check(can_view(posts[b_priv], alice), "private: admin can view")
    check(
        can_view(posts[a_prof], bob) and can_view(posts[a_every], carol),
        "family-visible",
    )

    class Anon:
        """A signed-out visitor, as Flask-Login would represent one."""

        is_authenticated = False

    check(not can_view(posts[a_every], Anon()), "anon cannot view")

    feed_ids = {p.id for p in db.session.scalars(feed_query())}
    check(feed_ids == {a_every, b_every}, f"feed only everyone {feed_ids}")

    def ids(stmt: Select) -> set[int]:
        """Run a post query and collect the ids it returns.

        Args:
            stmt: A select statement for posts.

        Returns:
            The set of matching post ids.
        """
        return {p.id for p in db.session.scalars(stmt)}

    check(
        ids(profile_query(alice, bob)) == {a_every, a_prof},
        "bob sees alice profile w/o private",
    )
    check(
        ids(profile_query(alice, alice)) == {a_every, a_prof, a_priv},
        "owner sees own private",
    )
    check(
        ids(profile_query(bob, alice)) == {b_every, b_priv},
        "admin sees others' private",
    )
    check(
        ids(profile_query(bob, carol)) == {b_every},
        "carol sees bob everyone only",
    )

    check(
        ids(search_query(carol)) == {a_every, a_prof, b_every},
        "search obeys can_view (carol)",
    )
    check(ids(search_query(alice)) == set(posts), "search admin sees all")
    check(
        ids(search_query(bob, q="private")) == {b_priv},
        "search q, own private only",
    )
    check(
        ids(search_query(carol, q="secret")) == set(),
        "search can't find others' private text",
    )
    check(
        ids(search_query(carol, author="ALICE")) == {a_every, a_prof},
        "search author",
    )
    check(ids(search_query(carol, mood="good")) == {b_every}, "search mood")
    today = datetime.now().date()
    check(
        ids(search_query(carol, date_from=today, date_to=today))
        == {a_every, a_prof, b_every},
        "search date today",
    )
    check(
        ids(search_query(carol, date_to=today - timedelta(days=1))) == set(),
        "search date_to",
    )

    # HTTP views
    cb.get("/")
    ctx = last_ctx("posts/feed.html")
    check({p.id for p in ctx["posts"]} == {a_every, b_every}, "feed route")
    check(
        cb.get(f"/post/{a_priv}").status_code == 404,
        "bob cannot open alice private",
    )
    check(
        ca.get(f"/post/{b_priv}").status_code == 200,
        "admin opens bob private",
    )
    check(
        cb.get(f"/post/{a_prof}").status_code == 200,
        "bob opens profile post",
    )
    cb.get("/u/alice")
    ctx = last_ctx("profile/view.html")
    check(
        {p.id for p in ctx["posts"]} == {a_every, a_prof},
        "profile route visibility",
    )
    cal = ctx["mood_calendar"]
    check(
        len(cal) == 30
        and cal[-1]["date"] == today
        and cal[-1]["mood"] == "sad",
        f"calendar latest visible {cal[-1]}",
    )
    ca.get("/u/ALICE")
    check(
        last_ctx("profile/view.html")["mood_calendar"][-1]["mood"]
        == "grumpy",
        "owner calendar uses private",
    )
    j = cb.get("/u/alice/moods.json").get_json()
    check(j == [[today.isoformat(), "sad", 2]], f"moods.json {j}")
    check(cb.get("/u/nobody").status_code == 404, "missing profile 404")
    cc.get("/search?q=everyone&page=1")
    ctx = last_ctx("search/index.html")
    check(
        {p.id for p in ctx["posts"]} == {a_every, b_every}
        and ctx["page_args"] == {"q": "everyone"},
        "search route",
    )
    cc.get("/search?date_from=notadate")
    check(
        last_ctx("search/index.html")["pagination"] is None,
        "bad date -> no results, form errors",
    )
    cc.get("/family")
    check(
        len(last_ctx("profile/family.html")["members"]) == 3,
        "family directory",
    )

    # Uploads serving
    photo_every = posts[a_every].photos[0].filename
    photo_priv = posts[a_priv].photos[0].filename
    check(
        cb.get(f"/uploads/{photo_every}").status_code == 200,
        "bob sees everyone photo",
    )
    check(
        cb.get(f"/uploads/{photo_priv}").status_code == 404,
        "bob blocked from private photo",
    )
    check(
        ca.get(f"/uploads/{photo_priv}").status_code == 200,
        "alice sees own private photo",
    )
    check(
        cb.get(f"/uploads/{alice.avatar}").status_code == 200,
        "avatar served",
    )
    check(
        cb.get("/uploads/../secret_key").status_code == 404,
        "traversal blocked",
    )

    # Comments
    check(
        cb.post(f"/post/{a_priv}/comment", data={"body": "hi"}).status_code
        == 404,
        "no comment on hidden post",
    )
    cb.post(f"/post/{a_every}/comment", data={"body": "Nice!"})
    cc.post(f"/post/{a_every}/comment", data={"body": "Lovely"})
    cmts = db.session.scalars(
        db.select(Comment).where(Comment.post_id == a_every)
    ).all()
    check(len(cmts) == 2, "comments added")
    bob_c, carol_c = cmts
    check(
        cc.post(f"/comment/{bob_c.id}/delete").status_code == 403,
        "carol can't delete bob's comment",
    )
    check(
        ca.post(f"/comment/{carol_c.id}/delete").status_code == 302,
        "post author deletes comment",
    )
    check(
        cb.post(f"/comment/{bob_c.id}/delete").status_code == 302,
        "author deletes own comment",
    )

    # Reactions
    check(
        cb.post(f"/post/{a_priv}/react/heart").status_code == 404,
        "no reaction on hidden post",
    )
    check(
        cb.post(f"/post/{a_every}/react/bogus").status_code == 404,
        "unknown reaction 404",
    )
    r = cb.post(
        f"/post/{a_every}/react/heart",
        headers={"Accept": "application/json"},
    )
    check(
        r.get_json()["reacted"] is True
        and r.get_json()["counts"]["heart"] == 1,
        "react on",
    )
    cc.post(f"/post/{a_every}/react/heart")
    r = cb.post(
        f"/post/{a_every}/react/heart",
        headers={"X-Requested-With": "fetch"},
    )
    check(
        r.get_json()
        == {
            "kind": "heart",
            "reacted": False,
            "counts": {
                "heart": 1,
                "thumbs": 0,
                "laugh": 0,
                "wow": 0,
                "hug": 0,
            },
        },
        "react toggle off",
    )
    p = db.session.get(Post, a_every)
    db.session.refresh(p)
    check(
        p.user_reacted(carol, "heart") and not p.user_reacted(bob, "heart"),
        "user_reacted",
    )
    summ = p.reaction_summary(carol)
    check(
        summ[0]["kind"] == "heart"
        and summ[0]["reacted"]
        and summ[0]["count"] == 1,
        "reaction_summary",
    )

    # Edit / delete permissions
    check(
        cb.get(f"/post/{a_every}/edit").status_code == 403,
        "non-author can't edit",
    )
    check(
        ca.get(f"/post/{b_every}/edit").status_code == 403,
        "admin can't edit others",
    )
    r = ca.get(f"/post/{a_every}/edit")
    check(
        r.status_code == 200
        and last_ctx("posts/form.html")["form"].title.data == "A everyone",
        "edit prefilled",
    )
    rm_id = posts[a_every].photos[1].id
    rm_file = posts[a_every].photos[1].filename
    r = ca.post(
        f"/post/{a_every}/edit",
        data={
            "title": "A edited",
            "body": "x",
            "mood": "",
            "visibility": "profile",
            "remove_photos": [str(rm_id)],
        },
    )
    check(r.status_code == 302, "edit ok")
    db.session.expire_all()
    p = db.session.get(Post, a_every)
    check(
        p.title == "A edited"
        and p.mood is None
        and p.visibility == "profile"
        and len(p.photos) == 1,
        "edit applied",
    )
    check(
        not os.path.exists(os.path.join(INST, "uploads", rm_file)),
        "removed photo deleted from disk",
    )
    check(
        cb.post(f"/post/{a_every}/delete").status_code == 403,
        "bob can't delete alice post",
    )
    check(
        ca.post(f"/post/{b_every}/delete").status_code == 302,
        "admin deletes any post",
    )
    check(db.session.get(Post, b_every) is None, "post gone")
    r = ca.post(f"/post/{a_prof}/delete", data={"next": f"/post/{a_prof}"})
    check(
        r.status_code == 302 and r.location.endswith("/"),
        "delete never redirects to dead permalink",
    )

    # --- admin rules ---
    check(cb.get("/admin").status_code == 403, "non-admin blocked from /admin")
    check(
        cb.post(f"/admin/user/{bob.id}/promote").status_code == 403,
        "non-admin can't promote",
    )
    check(ca.get("/admin").status_code == 200, "admin page")
    rows = last_ctx("admin/index.html")["rows"]
    arow = next(r for r in rows if r["user"].id == alice.id)
    check(
        not arow["can_demote"] and not arow["can_delete"],
        "last admin row flags",
    )
    ca.post(f"/admin/user/{alice.id}/demote")
    db.session.expire_all()
    check(alice.is_admin, "last admin cannot be demoted")
    ca.post(f"/admin/user/{bob.id}/promote")
    db.session.expire_all()
    check(bob.is_admin, "promote")
    r = ca.post(f"/admin/user/{alice.id}/demote")
    db.session.expire_all()
    check(
        not alice.is_admin and r.location.endswith("/"),
        "self-demote allowed when another admin",
    )
    check(
        cb.post(f"/admin/user/{bob.id}/delete").status_code == 302
        and db.session.get(User, bob.id),
        "can't delete self",
    )
    ca2 = client_for("alice")
    check(ca2.get("/admin").status_code == 403, "demoted alice blocked")
    cb.post(f"/admin/user/{alice.id}/promote")
    # Bob is admin; alice admin again. Demote bob, then try to delete
    # alice (now last admin).
    ca2.post(f"/admin/user/{bob.id}/demote")
    db.session.expire_all()
    check(alice.is_admin and not bob.is_admin, "demote other")
    # Reset password
    r = ca2.post(
        f"/admin/user/{carol.id}/reset-password", data={"new_password": "123"}
    )
    check(
        app.test_client()
        .post("/login", data={"username": "carol", "password": "123"})
        .status_code
        == 200,
        "short reset rejected",
    )
    ca2.post(
        f"/admin/user/{carol.id}/reset-password",
        data={"new_password": "newpass1"},
    )
    client_for("carol", "newpass1")
    # Delete user cascades
    alice_files = alice.uploaded_files()
    check(len(alice_files) >= 3, "alice owns files")
    cb2 = client_for("bob")
    cb2.post(f"/post/{a_priv}/react/hug")  # bob can't see it -> 404, no row
    # carol comments + reacts on alice's post, bob reacts too
    cc2 = client_for("carol", "newpass1")
    cc2.post(f"/post/{a_every}/comment", data={"body": "c2"})
    cc2.post(f"/post/{a_every}/react/wow")
    ca2.post(f"/admin/user/{bob.id}/promote")
    r = cb2.post(f"/admin/user/{alice.id}/delete")
    check(db.session.get(User, alice.id) is None, "user deleted")
    check(
        db.session.scalar(
            db.select(db.func.count(Post.id)).where(Post.author_id == alice.id)
        )
        == 0,
        "posts cascade",
    )
    check(
        db.session.scalar(
            db.select(db.func.count(Comment.id)).where(
                Comment.post_id == a_every
            )
        )
        == 0,
        "comments cascade",
    )
    check(
        db.session.scalar(
            db.select(db.func.count(Reaction.id)).where(
                Reaction.post_id == a_every
            )
        )
        == 0,
        "reactions cascade",
    )
    check(
        db.session.scalar(db.select(db.func.count(Photo.id))) == 0,
        "photos cascade",
    )
    check(
        all(
            not os.path.exists(os.path.join(INST, "uploads", f))
            for f in alice_files
        ),
        "files removed from disk",
    )
    check(
        cb2.post(f"/admin/user/{bob.id}/demote").status_code == 302
        and db.session.get(User, bob.id).is_admin,
        "bob last admin can't demote",
    )

    # --- settings ---
    cc2.get("/settings")
    ctx = last_ctx("settings/index.html")
    check(
        all(
            k in ctx
            for k in (
                "profile_form",
                "theme_form",
                "background_form",
                "password_form",
            )
        ),
        "settings forms",
    )
    check(
        ctx["theme_form"].custom_colours.data is True,
        "custom colours => custom ticked",
    )
    cc2.post(
        "/settings/password",
        data={
            "current_password": "wrong",
            "new_password": "abcdef",
            "confirm_password": "abcdef",
        },
    )
    check(
        "current_password"
        in last_ctx("settings/index.html")["password_form"].errors,
        "wrong current pw",
    )
    r = cc2.post(
        "/settings/password",
        data={
            "current_password": "newpass1",
            "new_password": "abcdef",
            "confirm_password": "abcdef",
        },
    )
    check(r.status_code == 302, "password change")
    client_for("carol", "abcdef")
    cc2.post(
        "/settings/theme",
        data={"theme_preset": "forest", "colour_primary": "#000000"},
    )
    db.session.expire_all()
    carol = User.find_by_username("carol")
    check(
        carol.theme["primary"] == "#2f855a",
        "preset fills colours when not custom",
    )
    cc2.post(
        "/settings/theme",
        data={
            "theme_preset": "forest",
            "custom_colours": "y",
            "colour_primary": "#zzzzzz",
        },
    )
    check(
        "colour_primary"
        in last_ctx("settings/index.html")["theme_form"].errors,
        "hex validated",
    )
    cc2.post(
        "/settings/background",
        data={"background": (make_image(), "b.jpg")},
        content_type="multipart/form-data",
    )
    db.session.expire_all()
    old_bg = carol.background_image
    check(
        old_bg and carol.theme["has_custom_background"],
        "background uploaded",
    )
    cc2.post(
        "/settings/background",
        data={"background": (make_image(), "b2.jpg")},
        content_type="multipart/form-data",
    )
    db.session.expire_all()
    check(
        carol.background_image != old_bg
        and not os.path.exists(os.path.join(INST, "uploads", old_bg)),
        "old bg deleted",
    )
    cc2.post("/settings/background/remove")
    db.session.expire_all()
    check(
        carol.background_image is None
        and carol.theme["background_url"].endswith("default.svg"),
        "bg removed",
    )
    cc2.post(
        "/settings/profile",
        data={"display_name": "Caz", "bio": "hi", "avatar": "panda"},
        content_type="multipart/form-data",
    )
    db.session.expire_all()
    check(
        carol.name == "Caz"
        and carol.avatar_url.endswith("avatars/panda.svg"),
        "profile saved",
    )

    # --- markdown ---
    html = str(
        render_markdown(
            "# Hi\n\n**bold** <script>alert(1)</script>\n\n"
            "![x](http://e/x.png)\n\n"
            "[l](http://e.com) javascript:x [j](javascript:alert(1))"
        )
    )
    check(
        "<h3>Hi</h3>" in html and "<strong>bold</strong>" in html,
        f"md basics {html}",
    )
    check(
        "<script" not in html and "&lt;script&gt;" in html,
        "raw html escaped",
    )
    check(
        "<img" not in html
        and 'rel="nofollow"' in html
        and 'href="javascript' not in html,
        f"img/links {html}",
    )

    # --- logout ---
    check(cc2.get("/logout").status_code == 405, "logout is POST only")
    r = cc2.post("/logout")
    check(
        r.status_code == 302 and cc2.get("/").status_code == 302,
        "logged out",
    )

print(f"ALL {passed} CHECKS PASSED")
