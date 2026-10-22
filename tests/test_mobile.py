"""Mobile and PWA test: manifest, icons, tab bar and mobile markup."""
import atexit
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.constants import THEME_PRESETS  # noqa: E402
from family_blogger.models import Post, User, db, now  # noqa: E402
from helpers import join_family  # noqa: E402

SCR = tempfile.mkdtemp(prefix="fb-test-")
atexit.register(shutil.rmtree, SCR, ignore_errors=True)
INST = os.path.join(SCR, "mob_instance")
shutil.rmtree(INST, ignore_errors=True)
app = create_app(
    {"INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False}
)
fails = []


def check(c: object, m: str) -> None:
    """Print the result of a check and record a failure.

    Args:
        c: The condition to test.
        m: The description of the check.
    """
    print(("ok   " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def img() -> io.BytesIO:
    """Create a small in-memory PNG image.

    Returns:
        A buffer holding the image, rewound to the start.
    """
    b = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 80, 80)).save(b, "PNG")
    b.seek(0)
    return b


c = app.test_client()
c.post(
    "/signup",
    data={
        "username": "mum",
        "password": "secret1",
        "confirm_password": "secret1",
    },
)
c.post(
    "/welcome",
    data={
        "display_name": "Mum",
        "bio": "",
        "avatar": "fox",
        "theme_preset": "ocean",
    },
    content_type="multipart/form-data",
)

# manifest + icons
r = c.get("/static/manifest.webmanifest")
check(r.status_code == 200, "manifest 200")
try:
    m = json.loads(r.get_data(as_text=True))
    ok = True
except Exception:
    ok = False
    m = {}
check(
    ok
    and m.get("display") == "standalone"
    and m.get("start_url") == "/"
    and m.get("short_name") == "Family Blogger",
    "manifest valid JSON with fields",
)
check(
    any(i.get("purpose") == "maskable" for i in m.get("icons", [])),
    "manifest has maskable icon",
)
for i in m.get("icons", []):
    rr = c.get(i["src"])
    check(
        rr.status_code == 200 and rr.mimetype == "image/png",
        f"icon {i['src']}",
    )
for n in ("icon-180", "icon-192", "icon-512", "icon-512-maskable"):
    check(
        c.get(f"/static/img/icons-app/{n}.png").status_code == 200,
        f"{n}.png 200",
    )

# base.html tags + theme-color (ocean primary)
t = c.get("/").get_data(as_text=True)
check("viewport-fit=cover" in t, "viewport-fit=cover")
check('rel="manifest"' in t and "manifest.webmanifest" in t, "manifest link")
check(
    'rel="apple-touch-icon"' in t and "icon-180.png" in t,
    "apple-touch-icon",
)
check(
    "apple-mobile-web-app-capable" in t
    and 'name="mobile-web-app-capable"' in t
    and "apple-mobile-web-app-title" in t
    and "apple-mobile-web-app-status-bar-style" in t,
    "web-app meta tags",
)
prim = THEME_PRESETS["ocean"]["primary"]
m_ = re.search(r'<meta name="theme-color" content="([^"]+)"', t)
check(
    m_ and m_.group(1).lower() == prim.lower(),
    f"theme-color {m_.group(1) if m_ else None} == user primary {prim}",
)
r2 = app.test_client().get("/login").get_data(as_text=True)
check(
    'content="#f59e0b"' in r2 and "tabbar" not in r2,
    "logged-out: sunny theme-color, no tab bar",
)
check(
    'class="tabbar"' in t
    and "My profile" in t
    and "Settings" in t
    and "Admin" in t
    and 'action="/logout"' in t
    and "nav-toggle" not in t,
    "tab bar + Me sheet (admin sees Admin), no hamburger",
)
check(
    t.count('aria-current="page"') >= 2,
    "active state rendered (nav + tab)",
)

# diary FAB
check("diary-fab" in t, "diary button shown before posting today")
# both inputs named photos in one request
r = c.post(
    "/post/new",
    data={
        "title": "Cam",
        "body": "x",
        "mood": "great",
        "visibility": "everyone",
        "photos": [
            (img(), "cam.png"),
            (img(), "lib1.png"),
            (img(), "lib2.png"),
        ],
    },
    content_type="multipart/form-data",
)
check(r.status_code == 302, "post with 3 photos from camera+library accepted")
with app.app_context():
    p = Post.query.filter_by(title="Cam").one()
    check(len(p.photos) == 3, f"3 photos saved ({len(p.photos)})")
t = c.get("/").get_data(as_text=True)
check("diary-fab" not in t, "diary button hidden after posting today")
# a private post counts, and another user is unaffected
c2 = join_family(app, "dad")
c2.post(
    "/welcome",
    data={"display_name": "Dad", "avatar": "owl", "theme_preset": "sunny"},
    content_type="multipart/form-data",
)
check(
    "diary-fab" in c2.get("/").get_data(as_text=True),
    "other user still sees diary button",
)
check(
    "Admin</a>" not in c2.get("/").get_data(as_text=True),
    "non-admin has no Admin entry",
)
# yesterday's post does not count
with app.app_context():
    dad = User.query.filter_by(username="dad").one()
    db.session.add(
        Post(
            author_id=dad.id,
            title="old",
            body="",
            visibility="everyone",
            created_at=now() - timedelta(days=1),
        )
    )
    db.session.commit()
check(
    "diary-fab" in c2.get("/").get_data(as_text=True),
    "yesterday's post doesn't hide the button",
)

# form markup
f = c.get("/post/new").get_data(as_text=True)
check(
    'capture="environment"' in f
    and "Take photo" in f
    and "Choose photos" in f
    and f.count('name="photos"') == 2,
    "form: two photo inputs named photos",
)
check(
    f.count('data-preview="photo-preview"') == 2,
    "form: both inputs share the preview box",
)
s = c.get("/settings").get_data(as_text=True)
check(
    "file-btn" in s
    and "Choose a picture" in s
    and "Choose a photo" in s,
    "settings: big file buttons",
)

# admin cards
a = c.get("/admin").get_data(as_text=True)
for lab in (
    'data-label="Role"',
    'data-label="Joined"',
    'data-label="Posts"',
    'data-label="Reset password"',
    'class="cell-actions"',
):
    check(lab in a, f"admin card markup {lab}")
css = c.get("/static/css/main.css").get_data(as_text=True)
check(
    "attr(data-label)" in css
    and "env(safe-area-inset-bottom" in css
    and "100dvh" in css
    and "background-attachment" not in css,
    "css: cards, safe areas, dvh, no attachment:fixed",
)
print("FAILS:", len(fails))
sys.exit(1 if fails else 0)
