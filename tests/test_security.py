"""Security hardening tests: redirects, throttling, headers and more.

Covers the open-redirect check, log-in throttling, upload limits,
security headers and the Content-Security-Policy, cookie flags, the
last-admin guard, the Markdown sanitiser, self-hosted assets, the
secret key file, SQLite settings and migrations 2 and 3. Uses the
real templates.
"""
import atexit
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path

from flask.testing import FlaskClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import FileStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
import config  # noqa: E402
from family_blogger import create_app  # noqa: E402
from family_blogger.admin import routes as admin_routes  # noqa: E402
from family_blogger.auth import routes as auth_routes  # noqa: E402
from family_blogger.constants import MOODS  # noqa: E402
from family_blogger.migrations import latest_version  # noqa: E402
from family_blogger.models import (  # noqa: E402
    LoginFailure,
    LoginLock,
    Post,
    User,
    db,
    utc_now,
)
from family_blogger.posts.routes import _post_return_url  # noqa: E402
from family_blogger.utils import is_safe_url  # noqa: E402
from family_blogger.utils import throttle  # noqa: E402
from family_blogger.utils.markdown import render_markdown  # noqa: E402
from family_blogger.utils.uploads import (  # noqa: E402
    FILENAME_RE,
    UploadError,
    delete_upload,
    save_image,
)
from helpers import add_member, join_family  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "family_blogger"
SCRATCH = tempfile.mkdtemp(prefix="fb-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
INST = os.path.join(SCRATCH, "sec_instance")

os.environ.pop("FB_COOKIE_SECURE", None)
app = create_app(
    {"INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False}
)

# Let the 500 handler run (testing mode re-raises by default) and
# keep the expected traceback out of the test output.
app.config["PROPAGATE_EXCEPTIONS"] = False
app.logger.disabled = True


@app.route("/__boom")
def boom() -> str:
    """Fail on purpose so the 500 page can be checked.

    Returns:
        Never returns.

    Raises:
        RuntimeError: Always.
    """
    raise RuntimeError("boom")


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


def login(client: FlaskClient, username: str, password: str,
          ip: str, **extra: object) -> object:
    """Post the log-in form from a given client address.

    Args:
        client: The test client.
        username: The username to type.
        password: The password to type.
        ip: The ``REMOTE_ADDR`` to send from.
        **extra: More form fields (e.g. ``remember="y"``).

    Returns:
        The test response.
    """
    data = {"username": username, "password": password}
    data.update(extra)
    return client.post(
        "/login", data=data, environ_overrides={"REMOTE_ADDR": ip})


def png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    """Encode a blank PNG of the given size.

    Args:
        size: Width and height in pixels.
        mode: The Pillow image mode.

    Returns:
        The PNG file contents.
    """
    buf = io.BytesIO()
    Image.new(mode, size).save(buf, "PNG")
    return buf.getvalue()


def upload(data: bytes, name: str, kind: str = "post") -> str:
    """Run ``save_image`` on in-memory bytes inside a request.

    Args:
        data: The file contents.
        name: The uploaded file name.
        kind: The upload kind.

    Returns:
        The stored filename.
    """
    with app.test_request_context("/"):
        return save_image(
            FileStorage(stream=io.BytesIO(data), filename=name), kind)


def upload_error(data: bytes, name: str) -> str:
    """Run ``save_image`` and return its error message.

    Args:
        data: The file contents.
        name: The uploaded file name.

    Returns:
        The ``UploadError`` message, or ``""`` if it was accepted.
    """
    try:
        upload(data, name)
    except UploadError as exc:
        return str(exc)
    return ""


mum = join_family(app, "mum")
onboard("mum")
add_member(app, "kid")
onboard("kid")
add_member(app, "gran")
onboard("gran")
kid = app.test_client()
check(login(kid, "kid", "secret1", "10.0.0.50").status_code == 302,
      "kid logs in")

# --- M1: open redirects -------------------------------------------
with app.test_request_context("/", base_url="http://fb.local"):
    for good in ("/", "/post/1", "/search?q=a%20b",
                 "http://fb.local/u/mum"):
        check(is_safe_url(good), f"safe url rejected: {good!r}")
    for bad in ("/\\evil.com", "\\\\evil.com", "/\\/evil.com",
                "/%5Cevil.com", "/%5cevil.com", "/%2F/evil.com",
                "/%2f%2fevil.com", "//evil.com", "///evil.com",
                "/\t/evil.com", "/\n/evil.com", "/%09/evil.com",
                "/%0d%0a/evil.com", " /x", "/x\x7f", "/x y",
                "http://evil.com", "https://evil.com/x", "https:evil.com",
                "javascript:alert(1)", "evil.com", "", None):
        check(not is_safe_url(bad), f"unsafe url accepted: {bad!r}")
    check(_post_return_url(3, "/\\evil.com") is None,
          "post return url refuses backslash")
    check(_post_return_url(3, "/post/3") is None
          and _post_return_url(3, "/post/3x") == "/post/3x"
          and _post_return_url(3, "/u/mum") == "/u/mum",
          "post return url keeps its own-post rule")

for target in ("/\\evil.com", "/%5Cevil.com", "/%09/evil.com"):
    c = app.test_client()
    r = c.post(f"/login?next={target}",
               data={"username": "gran", "password": "secret1"},
               environ_overrides={"REMOTE_ADDR": "10.0.1.1"})
    loc = r.headers.get("Location", "")
    check(r.status_code == 302 and "evil" not in loc,
          f"login next {target!r} -> {loc!r}")

# --- M2: log-in throttling ----------------------------------------
attacker = app.test_client()
for i in range(throttle.MAX_FAILURES):
    r = login(attacker, "kid", "wrong", "10.0.0.2")
    check(r.status_code == 200 and b"Wrong username" in r.data,
          f"failure {i + 1} is a normal wrong-password page")
r = login(attacker, "kid", "secret1", "10.0.0.2")
check(r.status_code == 429 and "Too many tries".encode() in r.data
      and b"ask a grown-up" in r.data,
      f"name paused on that device after 5 failures ({r.status_code})")
r = login(app.test_client(), "KID", "secret1", "10.0.0.3")
check(r.status_code == 302,
      "the same name still works from another device")
r = login(app.test_client(), "gran", "secret1", "10.0.0.2")
check(r.status_code == 302, "other names still work on that device")
with app.app_context():
    blocked = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "kid"))
check(blocked == throttle.MAX_FAILURES,
      "refused attempts are not recorded as failures")

# Per address: many different usernames from one device.
for i in range(throttle.IP_MAX_FAILURES):
    login(app.test_client(), f"nobody{i}", "x", "10.0.0.9")
r = login(app.test_client(), "gran", "secret1", "10.0.0.9")
check(r.status_code == 429,
      f"address paused after {throttle.IP_MAX_FAILURES} failures")
r = login(app.test_client(), "gran", "secret1", "10.0.0.10")
check(r.status_code == 302, "gran fine from her own device")
with app.app_context():
    check(not throttle.is_throttled("gran", "10.0.0.10"),
          "is_throttled false for a clean name and address")
    check(throttle.is_throttled("anyone", "10.0.0.9"),
          "is_throttled true for a paused address")

# Four failures in the window are fine.
for _ in range(throttle.MAX_FAILURES - 1):
    login(app.test_client(), "mum", "wrong", "10.0.0.20")
r = login(app.test_client(), "mum", "secret1", "10.0.0.20")
check(r.status_code == 302, "four failures do not lock")

# The pause lasts LOCKOUT from the failure that caused it.
with app.app_context():
    past = utc_now() - throttle.LOCKOUT - timedelta(seconds=5)
    db.session.execute(
        db.update(LoginFailure)
        .where(LoginFailure.username == "kid")
        .values(created_at=past))
    db.session.execute(
        db.update(LoginLock)
        .where(LoginLock.username == "kid")
        .values(started_at=past, locked_until=past + throttle.LOCKOUT))
    db.session.commit()
r = login(app.test_client(), "kid", "secret1", "10.0.0.2")
check(r.status_code == 302, "pause ends after five minutes")
with app.app_context():
    rows = db.session.scalars(
        db.select(LoginFailure).where(LoginFailure.username == "kid")
    ).all()
    check(len(rows) == throttle.MAX_FAILURES
          and all(row.cleared for row in rows),
          "a good log-in clears the count but keeps the rows")
for _ in range(throttle.MAX_FAILURES - 1):
    login(app.test_client(), "kid", "wrong", "10.0.0.30")
r = login(app.test_client(), "kid", "secret1", "10.0.0.31")
check(r.status_code == 302, "cleared failures no longer count")

# Admins can see recent failures.
r = mum.get("/admin")
html = r.get_data(as_text=True)
with app.app_context():
    counts = throttle.recent_failure_counts()
check(counts.get("kid") == 2 * throttle.MAX_FAILURES - 1,
      f"kid's failures counted for the week: {counts}")
check(r.status_code == 200 and "Failed log-ins (7 days)" in html
      and f'badge-warn">{counts["kid"]}<' in html,
      "admin page shows failed log-ins per member")
check(f"also {throttle.IP_MAX_FAILURES} tries with usernames" in html,
      "admin page shows failures for unknown names")
check(kid.get("/admin").status_code == 403,
      "children cannot see the admin page")

# Old rows are pruned.
with app.app_context():
    db.session.add(LoginFailure(
        username="old", ip="10.9.9.9",
        created_at=utc_now() - throttle.KEEP_FOR - timedelta(hours=1)))
    db.session.commit()
    throttle.record_failure("new", "10.9.9.8")
    names = set(db.session.scalars(db.select(LoginFailure.username)))
    check("old" not in names and "new" in names, "old failures pruned")

# Unknown usernames still run a password check (timing).
calls = []
real_check = auth_routes.check_password_hash


def counting_check(pwhash: str, password: str) -> bool:
    """Record a call to ``check_password_hash`` and run it.

    Args:
        pwhash: The stored hash.
        password: The typed password.

    Returns:
        Whatever the real function returns.
    """
    calls.append(pwhash)
    return real_check(pwhash, password)


auth_routes.check_password_hash = counting_check
login(app.test_client(), "ghost", "whatever", "10.0.0.40")
auth_routes.check_password_hash = real_check
check(calls == [auth_routes._DUMMY_HASH],
      "unknown username checked against the dummy hash")
with app.app_context():
    real_hash = User.find_by_username("mum").password_hash
check(auth_routes._DUMMY_HASH.split("$")[0] == real_hash.split("$")[0],
      "dummy hash uses the same method as real passwords")

# --- M3, M15 and fullmatch: uploads -------------------------------
err = upload_error(png((8000, 6000), "1"), "huge.png")
check("megapixels" in err, f"48 MP image refused: {err!r}")
err = upload_error(png((40_000, 1001), "1"), "wide.png")
check("megapixels" in err, f"40 MP+ panorama refused: {err!r}")
check(upload_error(png((300, 200)), "ok.png") == "",
      "a normal image is still accepted")
check("JPG, PNG" in upload_error(b"GIF89a" + b"\0" * 20, "x.gif")
      or "valid image" in upload_error(b"GIF89a" + b"\0" * 20, "x.gif"),
      "a broken image is still refused")
first = Image.new("RGB", (64, 48), (200, 50, 50))
second = Image.new("RGB", (64, 48), (50, 50, 200))
buf = io.BytesIO()
first.save(buf, "MPO", save_all=True, append_images=[second])
check(Image.open(io.BytesIO(buf.getvalue())).format == "MPO",
      "test image really is MPO")
name = upload(buf.getvalue(), "phone.jpg")
with Image.open(os.path.join(app.config["UPLOAD_FOLDER"], name)) as saved:
    check(name.endswith(".jpg") and saved.format == "JPEG",
          f"MPO accepted and re-encoded as JPEG ({name})")

hexname = "a" * 32 + ".jpg"
check(FILENAME_RE.fullmatch(hexname)
      and not FILENAME_RE.fullmatch(hexname + "\n")
      and not FILENAME_RE.fullmatch("x" + hexname),
      "FILENAME_RE needs a full match")
victim = os.path.join(app.config["UPLOAD_FOLDER"], hexname + "\n")
Path(victim).write_bytes(b"x")
with app.app_context():
    delete_upload(hexname + "\n")
check(os.path.exists(victim), "delete_upload ignores trailing newline")
check(mum.get(f"/uploads/{hexname}%0A").status_code == 404,
      "uploads route refuses a trailing newline")
upload_src = (PKG / "uploads" / "routes.py").read_text()
utils_src = (PKG / "utils" / "uploads.py").read_text()
check("FILENAME_RE.match" not in upload_src + utils_src
      and "FILENAME_RE.search" not in upload_src + utils_src,
      "FILENAME_RE only used with fullmatch")

# --- M4: security headers and CSP ---------------------------------
EXPECTED_CSP = [
    "default-src 'self'", "img-src 'self' data: blob:",
    "script-src 'self'", "font-src 'self'", "object-src 'none'",
    "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
]


def check_headers(resp: object, label: str) -> str:
    """Check the security headers on one response.

    Args:
        resp: The test response.
        label: What was requested, for messages.

    Returns:
        The Content-Security-Policy value.
    """
    h = resp.headers
    check(h.get("X-Content-Type-Options") == "nosniff",
          f"nosniff on {label}")
    check(h.get("Referrer-Policy") == "same-origin",
          f"referrer policy on {label}")
    check(h.get("X-Frame-Options") == "DENY", f"frame deny on {label}")
    csp = h.get("Content-Security-Policy", "")
    parts = [p.strip() for p in csp.split(";")]
    check(all(d in parts for d in EXPECTED_CSP), f"CSP on {label}: {csp}")
    style = [p for p in parts if p.startswith("style-src")]
    check(len(style) == 1 and re.fullmatch(
        r"style-src 'self'( 'nonce-[A-Za-z0-9_-]{16,}')?", style[0]),
        f"style-src on {label}: {style}")
    check("unsafe-inline" not in csp and "unsafe-eval" not in csp
          and "http" not in csp, f"no unsafe sources on {label}")
    return csp


anon = app.test_client()
pages = [
    (anon, "/login", 200), (anon, "/signup", 403), (anon, "/", 302),
    (mum, "/", 200), (mum, "/admin", 200), (mum, "/u/mum", 200),
    (mum, "/settings", 200), (mum, "/search", 200), (mum, "/family", 200),
    (mum, "/post/new", 200), (mum, "/u/mum/moods.json", 200),
    (mum, "/static/css/main.css", 200), (mum, "/nope", 404),
    (kid, "/admin", 403), (mum, "/__boom", 500),
]
nonces = set()
for client, url, code in pages:
    r = client.get(url)
    check(r.status_code == code, f"{url} -> {r.status_code}, not {code}")
    csp = check_headers(r, url)
    if r.mimetype == "text/html" and code != 302:
        html = r.get_data(as_text=True)
        found = re.search(r"'nonce-([^']+)'", csp)
        check(found and f'<style nonce="{found.group(1)}">' in html,
              f"theme <style> nonce matches the header on {url}")
        nonces.add(found.group(1))
        check("<script>" not in html and " style=" not in html,
              f"no inline script or style attribute on {url}")
check(len(nonces) >= 10, "each page gets a fresh nonce")

r = mum.get("/u/mum")
html = r.get_data(as_text=True)
block = re.search(
    r'<script type="application/json" id="mood-data">(.*?)</script>',
    html, re.S)
check(block and json.loads(block.group(1)) == json.loads(
    json.dumps(MOODS)), "mood data passed as a JSON block")
check("vendor/chart-4.4.1.umd.min.js" in html
      and "js/mood-chart.js" in html, "chart scripts are local")
check("--fb-primary: #" in html and ".swatches-sunny" in html,
      "theme colours and swatches are in the nonced style")
with app.app_context():
    user = User.find_by_username("mum")
    user.colour_primary = "#ff0000;}</style><script>x()</script>"
    db.session.commit()
html = mum.get("/").get_data(as_text=True)
check("x()" not in html and "--fb-primary: #" in html,
      "a malformed stored colour falls back to the preset")
with app.app_context():
    User.find_by_username("mum").colour_primary = None
    db.session.commit()

# Templates: no inline code, handlers, style attributes or outside URLs.
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
EXTERNAL = re.compile(
    r"""(?:\b(?:src|href|action|srcset|poster|data)\s*=\s*["']?\s*"""
    r"""(?:https?:)?//)|url\(\s*["']?\s*(?:https?:)?//|@import""",
    re.I)
for path in sorted((PKG / "templates").rglob("*.html")):
    src = path.read_text()
    rel = path.relative_to(PKG / "templates")
    for attrs, body in SCRIPT_TAG.findall(src):
        ok = "src=" in attrs and not body.strip() or (
            'type="application/json"' in attrs)
        check(ok, f"inline script code in {rel}: {attrs!r}")
    check(not re.search(r"\son[a-z]+\s*=", src, re.I),
          f"inline event handler in {rel}")
    check(not re.search(r"\sstyle\s*=", src, re.I),
          f"style attribute in {rel}")
    check(not EXTERNAL.search(src), f"external URL in {rel}")
    if path.name != "base.html":
        check("<style" not in src, f"extra <style> element in {rel}")
for path in [PKG / "static/css/main.css",
             *sorted((PKG / "static/js").glob("*.js"))]:
    check(not EXTERNAL.search(path.read_text())
          and "http://" not in path.read_text()
          and "https://" not in path.read_text(),
          f"external URL in {path.name}")

# Self-hosted font and Chart.js, with licences.
css = (PKG / "static/css/main.css").read_text()
check("@font-face" in css and "nunito-latin.woff2" in css,
      "Nunito declared with @font-face")
for asset in ("fonts/nunito-latin.woff2", "fonts/nunito-latin-ext.woff2",
              "vendor/chart-4.4.1.umd.min.js"):
    r = anon.get(f"/static/{asset}")
    check(r.status_code == 200 and len(r.data) > 10_000,
          f"{asset} is served locally")
    r.close()
check("Open Font License" in (PKG / "static/fonts/OFL.txt").read_text(),
      "OFL licence next to the fonts")
check("MIT" in (PKG / "static/vendor/chart.js-LICENSE.md").read_text(),
      "MIT licence next to Chart.js")
check("Chart.js v4.4.1" in (
    PKG / "static/vendor/chart-4.4.1.umd.min.js").read_text()[:600],
    "vendored Chart.js is version 4.4.1")

# --- M5: cookies --------------------------------------------------
check(app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
      and app.config["REMEMBER_COOKIE_SAMESITE"] == "Lax"
      and not app.config["SESSION_COOKIE_SECURE"]
      and not app.config["REMEMBER_COOKIE_SECURE"],
      "cookies are Lax and not Secure by default")
r = login(app.test_client(), "gran", "secret1", "10.0.2.1", remember="y")
cookies = r.headers.getlist("Set-Cookie")
check(len(cookies) >= 2 and all("SameSite=Lax" in c for c in cookies)
      and not any("Secure" in c for c in cookies),
      f"default cookies: {cookies}")
os.environ["FB_COOKIE_SECURE"] = "1"
try:
    secure_app = create_app({
        "INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False})
finally:
    os.environ.pop("FB_COOKIE_SECURE", None)
check(secure_app.config["SESSION_COOKIE_SECURE"]
      and secure_app.config["REMEMBER_COOKIE_SECURE"],
      "FB_COOKIE_SECURE=1 turns on Secure cookies")
c = secure_app.test_client()
r = c.post("/login", data={"username": "gran", "password": "secret1",
                           "remember": "y"},
           environ_overrides={"REMOTE_ADDR": "10.0.2.2"})
cookies = r.headers.getlist("Set-Cookie")
names = {c.split("=", 1)[0] for c in cookies}
check(r.status_code == 302 and {"session", "remember_token"} <= names
      and all("Secure" in c and "HttpOnly" in c for c in cookies),
      f"secure cookies: {cookies}")

# --- Last-admin guard race ----------------------------------------
real_is_last_admin = admin_routes.is_last_admin


def stale_check(_user: User) -> bool:
    """Pretend the early check passed, as in a race.

    Args:
        _user: The user being checked (unused).

    Returns:
        Always ``False``.
    """
    return False


admin_routes.is_last_admin = stale_check
r = mum.post("/admin/user/1/demote")
admin_routes.is_last_admin = real_is_last_admin
with app.app_context():
    check(User.find_by_username("mum").is_admin and r.status_code == 302,
          "demoting the last admin is rolled back after the change")
    gran = User.find_by_username("gran")
    gran.is_admin = True
    db.session.commit()
    mum_user = User.find_by_username("mum")
    mum_user.is_admin = False
    db.session.delete(gran)
    check(not admin_routes._commit_keeping_an_admin(),
          "deleting the last admin is refused")
    db.session.expire_all()
    check(User.find_by_username("gran") is not None
          and User.find_by_username("gran").is_admin
          and User.find_by_username("mum").is_admin,
          "the refused change is fully rolled back")
    gran = User.find_by_username("gran")
    gran.is_admin = False
    check(admin_routes._commit_keeping_an_admin(),
          "a change that keeps an admin commits")

# --- Markdown sanitiser (nh3) -------------------------------------
REL = 'rel="nofollow noopener noreferrer"'
LIVE_TAGS = {"script", "img", "style", "iframe", "svg", "object"}
cases = [
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](&#106;avascript:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "[x](vbscript:msgbox)",
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "<a href=\"http://x\" onclick=\"y()\">hi</a>",
    '[q](http://a.com" onmouseover="alert(1))',
    "[t](http://a.com 'x\" onclick=\"y')",
    "[t](http://a.com \"x' onfocus='y\")",
    "![i](http://a.com/x.png)",
    "<style>body{}</style>",
    "<iframe src=//evil></iframe>",
    "<svg onload=alert(1)>",
    "http://a.com/\"onmouseover=alert(1)//",
]


class TagCollector(HTMLParser):
    """Collect every start tag and its attributes, as a browser sees."""

    def __init__(self) -> None:
        """Start with no tags."""
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        """Remember a start tag.

        Args:
            tag: The tag name.
            attrs: ``(name, value)`` pairs.
        """
        self.tags.append((tag, dict(attrs)))


def parsed_tags(markup: str) -> list[tuple[str, dict]]:
    """Parse HTML and list its start tags.

    Args:
        markup: The HTML.

    Returns:
        ``(tag, attributes)`` pairs in document order.
    """
    collector = TagCollector()
    collector.feed(markup)
    collector.close()
    return collector.tags


for source in cases:
    out = str(render_markdown(source))
    for tag, attrs in parsed_tags(out):
        check(tag not in LIVE_TAGS, f"live <{tag}> for {source!r}: {out}")
        check(set(attrs) <= ({"href", "title", "rel"} if tag == "a"
                             else set()),
              f"unexpected attributes for {source!r}: {out}")
        # Read the URL the way a browser does: tabs and newlines are
        # dropped, and so are spaces and controls at either end.
        url = re.sub(r"[\t\n\r]", "", attrs.get("href") or "")
        url = url.strip("".join(chr(n) for n in range(33)))
        scheme = re.match(r"([a-z][a-z0-9+.-]*):", url, re.I)
        check(scheme is None or scheme.group(1).lower() in (
            "http", "https", "mailto"),
            f"unsafe href kept for {source!r}: {out}")
out = str(render_markdown("[ok](https://a.com) and mail [m](mailto:a@b.c)"))
check('href="https://a.com"' in out and 'href="mailto:a@b.c"' in out
      and out.count(REL) == 2, f"safe links kept with rel: {out}")
out = str(render_markdown("see https://a.com/x. or www.b.org"))
check('<a href="https://a.com/x" ' + REL in out
      and 'href="http://www.b.org"' in out and "x</a>." in out,
      f"bare addresses linked: {out}")
out = str(render_markdown("`https://c.com`\n\n```\nhttps://d.com\n```"))
check("<a" not in out, f"no links inside code: {out}")
out = str(render_markdown("[https://e.com](https://f.com)"))
check(out.count("<a ") == 1, f"no nested links: {out}")
out = str(render_markdown("**hi** _there_\n\n# Head\n\n> q\n\n- a\n- b"))
check(all(tag in out for tag in (
    "<strong>hi</strong>", "<em>there</em>", "<h3>Head</h3>",
    "<blockquote>", "<li>a</li>")), f"formatting kept: {out}")

# --- Secret key file ----------------------------------------------
key_dir = Path(SCRATCH) / "keys"
key = config.load_or_create_secret_key(key_dir)
key_file = key_dir / "secret_key"
check(re.fullmatch(r"[0-9a-f]{64}", key)
      and key_file.read_text() == key, "new secret key written")
if os.name == "posix":
    check(stat.S_IMODE(key_file.stat().st_mode) == 0o600,
          "secret key file is owner-only")
check(config.load_or_create_secret_key(key_dir) == key,
      "existing key is reused")
key_file.write_text("")
fresh = config.load_or_create_secret_key(key_dir)
check(fresh != key and key_file.read_text() == fresh,
      "an empty key file is replaced")
race_dir = Path(SCRATCH) / "race"
race_dir.mkdir()
real_open = config.os.open


def racing_open(path: object, flags: int, mode: int = 0o777) -> int:
    """Act as if another process created the key file first.

    Args:
        path: The file being opened.
        flags: The ``os.open`` flags.
        mode: The permission bits.

    Returns:
        Never returns.

    Raises:
        FileExistsError: Always, after writing the other key.
    """
    Path(path).write_text("theirkey")
    raise FileExistsError(path)


config.os.open = racing_open
try:
    raced = config.load_or_create_secret_key(race_dir)
finally:
    config.os.open = real_open
check(raced == "theirkey", "losing the race reads the winner's key")

# --- SQLite settings ----------------------------------------------
with app.app_context():
    check(db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1,
          "foreign keys enforced")
    check(db.session.execute(text("PRAGMA journal_mode")).scalar()
          == "wal", "WAL journal mode")
    check(db.session.execute(text("PRAGMA busy_timeout")).scalar()
          == 5000, "busy timeout set")
    db.session.add(Post(author_id=99999, title="x", body="y"))
    try:
        db.session.commit()
        orphan = True
    except IntegrityError:
        db.session.rollback()
        orphan = False
    check(not orphan, "a post for a missing user is refused")

# --- Migrations 2 and 3 on an existing v1 database ---------------
mig = Path(SCRATCH) / "mig"
first_app = create_app({"INSTANCE_DIR": str(mig), "TESTING": True})
with first_app.app_context():
    db.engine.dispose()
db_file = mig / "family_blogger.db"
con = sqlite3.connect(db_file)
con.execute("DROP TABLE login_failure")
con.execute("DROP TABLE login_lock")
con.execute(
    "INSERT INTO user (username, password_hash, avatar, is_admin, "
    "onboarded, created_at, session_token, theme_preset) VALUES "
    "('nan', 'h', 'fox', 1, 1, '2026-01-01 10:00:00', 'tok', 'sunny')")
con.execute(
    "INSERT INTO post (author_id, title, body, visibility, created_at, "
    "updated_at) VALUES (1, 'Kept', 'Memory', 'everyone', "
    "'2026-01-01 10:00:00', '2026-01-01 10:00:00')")
con.execute("PRAGMA user_version = 1")
con.commit()
con.close()
second_app = create_app({"INSTANCE_DIR": str(mig), "TESTING": True})
with second_app.app_context():
    db.engine.dispose()
con = sqlite3.connect(db_file)
check(con.execute("PRAGMA user_version").fetchone()[0] == 3
      == latest_version(), "v1 database migrated to v3")
check(con.execute("SELECT username FROM user").fetchall() == [("nan",)]
      and con.execute("SELECT title FROM post").fetchall() == [("Kept",)],
      "existing rows kept")
cols = [r[1] for r in con.execute("PRAGMA table_info(login_failure)")]
check(cols == ["id", "username", "ip", "created_at", "cleared"],
      f"login_failure created: {cols}")
idx = {r[1] for r in con.execute("PRAGMA index_list(login_failure)")}
check({"ix_login_failure_username", "ix_login_failure_ip",
       "ix_login_failure_created_at"} <= idx, f"indexes made: {idx}")
cols = [r[1] for r in con.execute("PRAGMA table_info(login_lock)")]
check(cols == ["id", "scope", "username", "ip", "started_at",
               "locked_until"], f"login_lock created: {cols}")
idx = {r[1] for r in con.execute("PRAGMA index_list(login_lock)")}
check({"ix_login_lock_username", "ix_login_lock_ip",
       "ix_login_lock_locked_until"} <= idx, f"lock indexes made: {idx}")
check(len(list(mig.glob("*.pre-migration-v1-*.db"))) == 1,
      "backup taken before migration 2")
con.close()

print(f"ALL {passed} CHECKS PASSED")
