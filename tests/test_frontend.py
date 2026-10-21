"""Front-end smoke test: real templates, forms and CSRF tokens."""
import atexit
import io
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from flask.testing import FlaskClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.models import Post  # noqa: E402

SCR = tempfile.mkdtemp(prefix="fb-test-")
atexit.register(shutil.rmtree, SCR, ignore_errors=True)
INST = os.path.join(SCR, "fe_instance")
shutil.rmtree(INST, ignore_errors=True)
app = create_app(
    {"INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False}
)
fails = []
CSRF_RE = r'name="csrf_token" value="([^"]+)"'


def check(c: object, m: str) -> None:
    """Record a failure, and print it, when a condition is falsy.

    Args:
        c: The condition to test.
        m: The message to report on failure.
    """
    if not c:
        fails.append(m)
        print("FAIL", m)


def img() -> io.BytesIO:
    """Create a small in-memory PNG image.

    Returns:
        A buffer holding the image, rewound to the start.
    """
    b = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 80, 80)).save(b, "PNG")
    b.seek(0)
    return b


def page(
    c: FlaskClient,
    url: str,
    code: int = 200,
    contains: tuple[str, ...] | list[str] = (),
) -> str:
    """Fetch a page and check its status code and content.

    Args:
        c: The test client.
        url: The URL to request.
        code: The expected status code.
        contains: Strings that the page must include.

    Returns:
        The response body as text.
    """
    r = c.get(url)
    check(r.status_code == code, f"GET {url} -> {r.status_code} (want {code})")
    t = r.get_data(as_text=True)
    for s in contains:
        check(s in t, f"{url} missing {s!r}")
    return t


c = app.test_client()
page(c, "/login", contains=["csrf_token", "--fb-primary"])
page(c, "/signup")
r = c.get("/")
check(r.status_code == 302, "anon redirect")
r = c.post(
    "/signup",
    data={
        "username": "mum",
        "password": "secret1",
        "confirm_password": "secret1",
    },
)
check(r.status_code == 302, f"signup {r.status_code}")
t = page(
    c,
    "/welcome",
    contains=["--fb-primary", "preset-ocean", "avatar-fox", "data-presets"],
)
r = c.post(
    "/welcome",
    data={
        "display_name": "Mum",
        "bio": "Hi",
        "avatar": "fox",
        "theme_preset": "ocean",
        "background": (img(), "bg.png"),
    },
    content_type="multipart/form-data",
)
check(r.status_code == 302, f"onboard {r.status_code}")
t = page(c, "/", contains=["Write today", "Log out", "Admin", "--fb-primary"])
for vis in ("everyone", "profile", "private"):
    r = c.post(
        "/post/new",
        data={
            "title": f"T {vis}",
            "body": "**bold** text",
            "mood": "great",
            "visibility": vis,
            "photos": [(img(), "a.png"), (img(), "b.png")],
        },
        content_type="multipart/form-data",
    )
    check(r.status_code == 302, f"new post {vis} {r.status_code}")
page(c, "/post/new", contains=["mood-great", "vis-private", "bold"])
with app.app_context():
    ids = {p.visibility: p.id for p in Post.query.all()}
for vis, pid in ids.items():
    t = page(
        c,
        f"/post/{pid}",
        contains=['id="comments"', "react/heart", "<strong>bold</strong>"],
    )
    page(c, f"/post/{pid}/edit", contains=["remove_photos"])
    r = c.post(f"/post/{pid}/comment", data={"body": "Nice one\nline2"})
    check(r.status_code == 302, "comment")
    r = c.post(
        f"/post/{pid}/react/heart", headers={"X-Requested-With": "fetch"}
    )
    check(r.status_code == 200 and r.json["reacted"], "react")
page(
    c,
    f"/post/{ids['everyone']}",
    contains=["Nice one", "is-active", "delete"],
)
t = page(c, "/", contains=["T everyone"])
check("T private" not in t, "private leaked in feed")
page(
    c,
    "/u/mum",
    contains=["mood-chart", "heat-great", "moods.json", "T private"],
)
page(c, "/u/MUM?page=1")
r = c.get("/u/mum/moods.json")
check(r.status_code == 200 and r.json, "moods json")
page(c, "/family", contains=["Mum"])
page(c, "/search", contains=["Search"])
t = page(c, "/search?q=bold&mood=great", contains=["T everyone", "found"])
page(c, "/search?q=zzzz", contains=["No posts found"])
page(c, "/search?date_from=garbage")
t = page(
    c,
    "/settings",
    contains=[
        'id="theme"',
        'id="background"',
        'id="password"',
        "settings/profile",
        "background/remove",
    ],
)
check("--fb-primary: #" in t, "theme vars")
for url, data in [
    ("/settings/profile", {"display_name": "M", "bio": "", "avatar": "owl"}),
    ("/settings/theme", {"theme_preset": "night"}),
    (
        "/settings/password",
        {
            "current_password": "bad",
            "new_password": "abcdef",
            "confirm_password": "abcdef",
        },
    ),
]:
    r = c.post(url, data=data)
    check(r.status_code in (200, 302), f"{url} {r.status_code}")
t = page(c, "/settings")
check("theme-dark" in t, "night preset dark")
r = c.post(
    "/settings/password",
    data={
        "current_password": "bad",
        "new_password": "abcdef",
        "confirm_password": "abcdef",
    },
)
check(r.status_code == 200 or r.status_code == 302, "pw")
for i in range(12):
    c.post("/post/new", data={"title": f"bulk {i}", "visibility": "everyone"})
page(c, "/", contains=["Older", "page=2"])
page(c, "/?page=2", contains=["Newer"])
page(c, "/u/mum?page=2", contains=["u/mum?page=1"])
page(c, "/search?q=bulk&page=2", contains=["q=bulk"])
page(c, "/admin", contains=["reset-password", "Family admin"])
# second user
c2 = app.test_client()
c2.post(
    "/signup",
    data={
        "username": "kid",
        "password": "secret1",
        "confirm_password": "secret1",
    },
)
c2.post(
    "/welcome",
    data={
        "display_name": "Kid",
        "avatar": "bear",
        "theme_preset": "berry",
        "custom_colours": "y",
        "colour_primary": "#112233",
    },
    content_type="multipart/form-data",
)
page(c2, "/", contains=["#112233"])
page(c2, "/admin", 403)
page(c2, f"/post/{ids['private']}", 404)
page(c, "/admin", contains=["promote", "delete"])
page(c, "/nonexistent", 404, contains=["Take me home"])
page(c2, f"/post/{ids['everyone']}", contains=["react/heart"])
r = c.post(f"/post/{ids['private']}/delete")
check(r.status_code == 302, "delete post")
r = c.post("/logout")
check(r.status_code == 302, "logout")
page(app.test_client(), "/nonexistent", 404, contains=["Take me home"])

# CSRF enabled run
app2 = create_app(
    {"INSTANCE_DIR": INST + "2", "TESTING": True, "WTF_CSRF_ENABLED": True}
)
shutil.rmtree(INST + "2", ignore_errors=True)
app2 = create_app(
    {"INSTANCE_DIR": INST + "2", "TESTING": True, "WTF_CSRF_ENABLED": True}
)
cc = app2.test_client()
for url in ("/login", "/signup"):
    t = cc.get(url).get_data(as_text=True)
    m = re.search(CSRF_RE, t)
    check(m, f"csrf in {url}")
tok = re.search(CSRF_RE, cc.get("/signup").get_data(as_text=True)).group(1)
r = cc.post(
    "/signup",
    data={
        "csrf_token": tok,
        "username": "dad",
        "password": "secret1",
        "confirm_password": "secret1",
    },
)
check(r.status_code == 302, "csrf signup")
t = cc.get("/welcome").get_data(as_text=True)
tok = re.search(CSRF_RE, t).group(1)
r = cc.post(
    "/welcome",
    data={
        "csrf_token": tok,
        "display_name": "Dad",
        "avatar": "cat",
        "theme_preset": "sunny",
    },
    content_type="multipart/form-data",
)
check(r.status_code == 302, "csrf onboard")
t = cc.get("/").get_data(as_text=True)
check(t.count('name="csrf_token"') >= 1, "csrf in logout form on feed")
for url in ("/post/new", "/settings", "/admin"):
    t = cc.get(url).get_data(as_text=True)
    check('name="csrf_token"' in t, f"csrf {url}")
tok = re.search(CSRF_RE, cc.get("/post/new").get_data(as_text=True)).group(1)
r = cc.post(
    "/post/new",
    data={"csrf_token": tok, "title": "Hi", "visibility": "everyone"},
    content_type="multipart/form-data",
)
check(r.status_code == 302, "csrf post")
t = cc.get("/").get_data(as_text=True)
check(
    "react/heart" in t and t.count("csrf_token") > 3,
    "csrf in reaction form",
)
tok = re.search(CSRF_RE, t).group(1)
r = cc.post("/logout", data={"csrf_token": tok})
check(r.status_code == 302, "csrf logout")
t = cc.get("/login").get_data(as_text=True)
tok = re.search(CSRF_RE, t).group(1)
r = cc.post(
    "/login",
    data={"csrf_token": tok, "username": "dad", "password": "secret1"},
)
check(r.status_code == 302, "csrf login")
print("FAILS:", len(fails))
sys.exit(1 if fails else 0)
