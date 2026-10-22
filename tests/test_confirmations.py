"""Delete confirmations: server-rendered pages and safe templates."""
import atexit
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from flask.testing import FlaskClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.models import Comment, Post, User, db  # noqa: E402
from helpers import join_family  # noqa: E402

SCR = tempfile.mkdtemp(prefix="fb-test-")
atexit.register(shutil.rmtree, SCR, ignore_errors=True)
app = create_app(
    {
        "INSTANCE_DIR": SCR + "/conf_instance",
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
    }
)
fails = []
passed = 0
YES = {"confirm": "yes"}


def check(cond: object, msg: str) -> None:
    """Record a failure, and print it, when a condition is falsy.

    Args:
        cond: The condition to test.
        msg: The message to report on failure.
    """
    global passed
    if cond:
        passed += 1
    else:
        fails.append(msg)
        print("FAIL", msg)


def join(username: str, display: str) -> FlaskClient:
    """Add a new member, finish onboarding and return their client.

    The first member signs up and becomes the admin; later members are
    added as an admin would add them.

    Args:
        username: The username to sign up with.
        display: The display name to choose.

    Returns:
        A logged-in test client.
    """
    c = join_family(app, username)
    c.post(
        "/welcome",
        data={
            "display_name": display,
            "avatar": "fox",
            "theme_preset": "ocean",
        },
        content_type="multipart/form-data",
    )
    return c


def new_post(c: FlaskClient, title: str, visibility: str) -> int:
    """Create a post and return its id.

    Args:
        c: The author's client.
        title: The post title.
        visibility: The visibility value.

    Returns:
        The id of the new post.
    """
    c.post(
        "/post/new",
        data={"title": title, "visibility": visibility},
        content_type="multipart/form-data",
    )
    with app.app_context():
        return db.session.scalar(
            db.select(Post.id).where(Post.title == title)
        )


def exists(model: object, ident: int) -> bool:
    """Check whether a row still exists.

    Args:
        model: The model class.
        ident: The primary key.

    Returns:
        ``True`` if the row is in the database.
    """
    with app.app_context():
        return db.session.get(model, ident) is not None


# --- no inline event handlers in any template ---
handler = re.compile(r"""<[^>]*\son[a-z]+\s*=""", re.IGNORECASE)
for path in (ROOT / "family_blogger" / "templates").rglob("*.html"):
    check(
        not handler.search(path.read_text()),
        f"inline on*= handler in {path.name}",
    )

mum = join("mum", "Mum")
kid = join("kid", "Kid")
sib = join("sib", "Sib")
evil = join("evil", "x');alert(1)//")
tricky = join("tricky", "O'Brien")

pub = new_post(kid, "Kid public", "everyone")
priv = new_post(kid, "Kid private", "private")
kid.post(f"/post/{pub}/comment", data={"body": "my words"})
with app.app_context():
    cid = db.session.scalar(db.select(Comment.id))
    evil_id = db.session.scalar(
        db.select(User.id).where(User.username == "evil")
    )
    tricky_id = db.session.scalar(
        db.select(User.id).where(User.username == "tricky")
    )

# --- admin page renders hostile names only escaped ---
html = mum.get("/admin").get_data(as_text=True)
check("x');alert(1)//" not in html, "raw hostile name in admin page")
check("x&#39;);alert(1)//" in html, "hostile name escaped in admin page")
check("O&#39;Brien" in html, "apostrophe name escaped in admin page")
check(not handler.search(html), "admin page has inline handler")
check(
    f"/admin/user/{evil_id}/delete" in html, "admin delete is a link"
)

# --- post: POST without confirmation is rejected ---
r = kid.post(f"/post/{pub}/delete")
check(r.status_code == 302, "unconfirmed post delete redirects")
check("/delete" in r.location, "unconfirmed goes to confirm page")
check(exists(Post, pub), "unconfirmed post delete kept the post")
r = kid.post(f"/post/{pub}/delete", data={"confirm": "no"})
check(exists(Post, pub), "confirm=no kept the post")

# --- post confirm pages ---
r = kid.get(f"/post/{pub}/delete?next=/")
t = r.get_data(as_text=True)
check(r.status_code == 200, "owner sees post confirm page")
check("Are you sure?" in t and "Kid public" in t, "post confirm text")
check('name="confirm" value="yes"' in t, "post confirm field")
check('name="csrf_token"' in t, "post confirm csrf")
check('name="next" value="/"' in t, "safe next kept")
check(mum.get(f"/post/{pub}/delete").status_code == 200, "admin sees")
check(sib.get(f"/post/{pub}/delete").status_code == 403, "sib 403")
check(sib.get(f"/post/{priv}/delete").status_code == 404, "private 404")
check(
    sib.post(f"/post/{priv}/delete", data=YES).status_code == 404,
    "private POST 404",
)
check(exists(Post, priv), "private post untouched by others")
check(
    app.test_client().get(f"/post/{pub}/delete").status_code == 302,
    "anon redirected",
)
t = kid.get(f"/post/{pub}/delete?next=//evil.example").get_data(
    as_text=True
)
check("evil.example" not in t, "unsafe next ignored on confirm page")

# --- delete post with confirmation; unsafe next ignored ---
r = kid.post(
    f"/post/{priv}/delete", data={**YES, "next": "//evil.example/x"}
)
check(r.status_code == 302, "confirmed delete redirects")
check(urlsplit(r.location).netloc == "", "unsafe next not followed")
check(r.location.endswith("/"), "unsafe next goes to feed")
check(not exists(Post, priv), "confirmed post delete works")
again = new_post(kid, "Kid again", "everyone")
r = kid.post(f"/post/{again}/delete", data={**YES, "next": "/search"})
check(r.location.endswith("/search"), "safe next followed")

# --- comment ---
r = kid.post(f"/comment/{cid}/delete")
check(exists(Comment, cid), "unconfirmed comment delete kept it")
check("/delete" in r.location, "comment goes to confirm page")
check(kid.get(f"/comment/{cid}/delete").status_code == 200, "own")
check(mum.get(f"/comment/{cid}/delete").status_code == 200, "admin")
check(sib.get(f"/comment/{cid}/delete").status_code == 403, "other 403")
check(
    sib.post(f"/comment/{cid}/delete", data=YES).status_code == 403,
    "other POST 403",
)
check(exists(Comment, cid), "comment untouched by others")
r = kid.post(f"/comment/{cid}/delete", data=YES)
check(r.status_code == 302 and not exists(Comment, cid), "comment gone")
priv2 = new_post(kid, "Kid private 2", "private")
kid.post(f"/post/{priv2}/comment", data={"body": "secret"})
with app.app_context():
    cid2 = db.session.scalar(
        db.select(Comment.id).where(Comment.post_id == priv2)
    )
check(sib.get(f"/comment/{cid2}/delete").status_code == 404, "hidden 404")

# --- admin user delete ---
check(kid.get(f"/admin/user/{evil_id}/delete").status_code == 403, "403")
r = mum.get(f"/admin/user/{evil_id}/delete")
t = r.get_data(as_text=True)
check(r.status_code == 200 and "confirm_name" in t, "admin confirm page")
check("x');alert(1)//" not in t, "hostile name escaped on confirm page")
r = mum.post(f"/admin/user/{evil_id}/delete")
check(r.status_code == 400 and exists(User, evil_id), "no confirm kept")
r = mum.post(
    f"/admin/user/{evil_id}/delete", data={**YES, "confirm_name": "nope"}
)
check(r.status_code == 400, "wrong username rejected")
check("Nothing was deleted" in r.get_data(as_text=True), "error shown")
check(exists(User, evil_id), "wrong username kept the member")
r = mum.post(f"/admin/user/{evil_id}/delete", data={"confirm_name": "evil"})
check(exists(User, evil_id), "username without confirm kept the member")
r = mum.post(
    f"/admin/user/{evil_id}/delete", data={**YES, "confirm_name": "EVIL"}
)
check(r.status_code == 302, "right username accepted")
check(not exists(User, evil_id), "member deleted (case-insensitive)")
with app.app_context():
    mum_id = db.session.scalar(
        db.select(User.id).where(User.username == "mum")
    )
check(
    mum.post(
        f"/admin/user/{mum_id}/delete",
        data={**YES, "confirm_name": "mum"},
    ).status_code
    == 302
    and exists(User, mum_id),
    "admin still cannot delete self",
)
check(exists(User, tricky_id), "other members untouched")

if fails:
    print(f"FAILS: {len(fails)}")
    sys.exit(1)
print(f"ALL {passed} CHECKS PASSED")
