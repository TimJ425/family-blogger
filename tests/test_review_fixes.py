"""Tests for the fixes from the second security review.

Covers fair log-in throttling (pauses keyed on name and device, no
cheap re-arming, the per-device spray limit, the many-devices bound,
admin "Clear failed log-ins" and password resets), the out-of-range
page redirect, the upload pixel cap and decode limit, control
characters in Markdown, "Log out on my other devices" and lookalike
display names. Uses the real templates.
"""
import atexit
import io
import os
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from flask.testing import FlaskClient
from PIL import Image
from PIL.JpegImagePlugin import JpegImageFile
from werkzeug.datastructures import FileStorage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The project imports must stay below sys.path.insert (E402).
from family_blogger import create_app  # noqa: E402
from family_blogger.forms import NAME_IN_USE  # noqa: E402
from family_blogger.models import (  # noqa: E402
    LoginFailure,
    LoginLock,
    Post,
    User,
    db,
    name_key,
    utc_now,
)
from family_blogger.utils import throttle, uploads  # noqa: E402
from family_blogger.utils.markdown import render_markdown  # noqa: E402
from helpers import add_member, join_family  # noqa: E402

SCRATCH = tempfile.mkdtemp(prefix="fb-review-test-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
INST = os.path.join(SCRATCH, "review_instance")

app = create_app(
    {"INSTANCE_DIR": INST, "TESTING": True, "WTF_CSRF_ENABLED": False}
)
app.config["PROPAGATE_EXCEPTIONS"] = False
app.logger.disabled = True

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
        User.find_by_username(username).onboarded = True
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


def fail(username: str, ip: str, times: int) -> list[int]:
    """Type a wrong password several times from one address.

    Args:
        username: The username to type.
        ip: The address to send from.
        times: How many wrong tries.

    Returns:
        The status code of each try.
    """
    client = app.test_client()
    return [login(client, username, "wrong-pass", ip).status_code
            for _ in range(times)]


def throttled(username: str, ip: str) -> bool:
    """Ask the throttle whether a name and address are paused.

    Args:
        username: The username.
        ip: The address.

    Returns:
        ``True`` if a log-in would be refused.
    """
    with app.app_context():
        return throttle.is_throttled(username, ip)


def expire_locks() -> None:
    """Make every running pause end a second ago.

    The failures that caused them are moved back too, as if the
    lock time had really passed.
    """
    with app.app_context():
        now = utc_now()
        latest = db.session.scalar(
            db.select(db.func.max(LoginLock.locked_until))
            .where(LoginLock.locked_until > now))
        if latest is None:
            return
        shift = latest - now + timedelta(seconds=1)
        # Plain Python date maths: SQLite cannot subtract intervals.
        for lock in db.session.scalars(
                db.select(LoginLock).where(LoginLock.locked_until > now)):
            lock.started_at -= shift
            lock.locked_until -= shift
        for row in db.session.scalars(db.select(LoginFailure)):
            row.created_at -= shift
        db.session.commit()


def lock_count(scope: str) -> int:
    """Count the pauses ever made for one scope.

    Args:
        scope: ``"pair"``, ``"ip"`` or ``"name"``.

    Returns:
        The number of ``LoginLock`` rows for that scope.
    """
    with app.app_context():
        return db.session.scalar(
            db.select(db.func.count(LoginLock.id))
            .where(LoginLock.scope == scope))


def reset_throttle() -> None:
    """Forget every failure and pause between scenarios."""
    with app.app_context():
        db.session.execute(db.delete(LoginFailure))
        db.session.execute(db.delete(LoginLock))
        db.session.commit()


def user_id(username: str) -> int:
    """Look up a member's id.

    Args:
        username: The member's username.

    Returns:
        Their id.
    """
    with app.app_context():
        return User.find_by_username(username).id


def location(response: object) -> str:
    """Get a redirect's target without the scheme and host.

    Args:
        response: The test response.

    Returns:
        The path, query and fragment of ``Location``.
    """
    loc = response.headers.get("Location", "")
    parts = urlsplit(loc)
    out = parts.path + ("?" + parts.query if parts.query else "")
    return out + ("#" + parts.fragment if parts.fragment else "")


parent = join_family(app, "parent")
onboard("parent")
for member in ("kid", "sib", "gran"):
    add_member(app, member)
    onboard(member)

# --- H-A: the reviewer's probe ------------------------------------
codes = fail("PARENT ", "10.0.0.5", throttle.MAX_FAILURES)
check(codes == [200] * throttle.MAX_FAILURES,
      f"five wrong tries are ordinary wrong-password pages: {codes}")
r = login(app.test_client(), "parent", "secret1", "10.0.0.5")
check(r.status_code == 429 and b"ask a grown-up" in r.data,
      "the kid's device is paused for that name, even with the "
      "right password")
r = login(app.test_client(), "parent", "secret1", "10.0.0.9")
check(r.status_code == 302,
      f"parent on her own device logs in fine ({r.status_code})")
check(lock_count("name") == 0 and lock_count("ip") == 0,
      "five tries make only a name-and-device pause")

# --- H-A: re-arming after a pause needs a full new set -------------
expire_locks()
check(not throttled("parent", "10.0.0.5"), "pause ended")
codes = fail("parent", "10.0.0.5", 1)
check(codes == [200] and not throttled("parent", "10.0.0.5"),
      "one wrong try after the pause does not pause again")
fail("parent", "10.0.0.5", throttle.MAX_FAILURES - 2)
check(not throttled("parent", "10.0.0.5"),
      "four new tries after the pause are still fine")
fail("parent", "10.0.0.5", 1)
check(throttled("parent", "10.0.0.5"),
      "a full new set of five pauses again")
check(lock_count("pair") == 2, "exactly two pauses so far")

# --- H-A: shared family tablet ------------------------------------
reset_throttle()
fail("parent", "10.0.0.7", throttle.MAX_FAILURES)
check(throttled("parent", "10.0.0.7"), "parent paused on the tablet")
r = login(app.test_client(), "sib", "secret1", "10.0.0.7")
check(r.status_code == 302, "a sibling still logs in on the tablet")
r = login(app.test_client(), "kid", "secret1", "10.0.0.7")
check(r.status_code == 302, "the kid still logs in to their own account")
r = login(app.test_client(), "parent", "secret1", "10.0.0.8")
check(r.status_code == 302, "parent fine on the laptop")

# --- H-A: admin "Clear failed log-ins" -----------------------------
r = parent.get("/admin")
html = r.get_data(as_text=True)
check(r.status_code == 200 and "Clear failed log-ins" in html
      and "Paused" in html and 'value="parent"' in html,
      "admin page offers to clear a paused member")
fail("ghost", "10.0.0.6", throttle.MAX_FAILURES)
html = parent.get("/admin").get_data(as_text=True)
check("“ghost”" in html and 'value="ghost"' in html,
      "admin page lists an unknown username with a clear button")
kid = app.test_client()
login(kid, "kid", "secret1", "10.0.0.50")
check(kid.post("/admin/login-failures/clear",
               data={"username": "parent"}).status_code == 403,
      "children cannot clear failed log-ins")
check(parent.get("/admin/login-failures/clear").status_code == 405,
      "clearing is POST only")
r = parent.post("/admin/login-failures/clear",
                data={"username": "parent"}, follow_redirects=True)
check(r.status_code == 200
      and "They can try again now" in r.get_data(as_text=True),
      "clearing shows a calm message")
check(not throttled("parent", "10.0.0.7"), "clearing lifts the pause")
r = login(app.test_client(), "parent", "secret1", "10.0.0.7")
check(r.status_code == 302, "parent logs in on the tablet after clearing")
parent.post("/admin/login-failures/clear", data={"username": "GHOST "})
check(not throttled("ghost", "10.0.0.6"),
      "unknown usernames can be cleared too (any case)")
with app.app_context():
    left = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "ghost",
               LoginFailure.cleared.is_(False)))
    kept = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "ghost"))
check(left == 0 and kept == throttle.MAX_FAILURES,
      "cleared rows stop counting but stay for the admin page")
html = parent.get("/admin").get_data(as_text=True)
check('value="ghost"' not in html,
      "nothing left to clear for ghost after clearing")

# --- H-A: an admin password reset clears failures and pauses -------
reset_throttle()
fail("sib", "10.0.0.11", throttle.MAX_FAILURES)
check(throttled("sib", "10.0.0.11"), "sib paused on that device")
r = parent.post(f"/admin/user/{user_id('sib')}/reset-password",
                data={"new_password": "newpass1"})
check(r.status_code == 302, "password reset done")
check(not throttled("sib", "10.0.0.11"), "reset lifts the pause")
with app.app_context():
    left = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "sib",
               LoginFailure.cleared.is_(False)))
check(left == 0, "reset clears the member's failed log-ins")
r = login(app.test_client(), "sib", "newpass1", "10.0.0.11")
check(r.status_code == 302, "sib logs in with the new password there")

# --- H-A: spraying many names from one device ----------------------
reset_throttle()
for n in range(throttle.IP_MAX_FAILURES - 1):
    fail(f"guess{n}", "10.0.0.20", 1)
r = login(app.test_client(), "gran", "secret1", "10.0.0.20")
check(r.status_code == 302,
      f"{throttle.IP_MAX_FAILURES - 1} wrong tries across names are "
      "still allowed (typos, not guessing)")
fail("guess-last", "10.0.0.20", 1)
r = login(app.test_client(), "gran", "secret1", "10.0.0.20")
check(r.status_code == 429,
      f"{throttle.IP_MAX_FAILURES} wrong tries pause the device")
r = login(app.test_client(), "gran", "secret1", "10.0.0.21")
check(r.status_code == 302, "other devices are not affected")
check(throttle.IP_MAX_FAILURES >= 20 and throttle.IP_WINDOW
      == timedelta(minutes=15), "spray limit is at least 20 in 15 min")

# --- H-A: guessing one name from many devices is bounded -----------
reset_throttle()
devices = throttle.NAME_MAX_FAILURES // throttle.MAX_FAILURES
for n in range(devices - 1):
    fail("gran", f"10.1.0.{n + 1}", throttle.MAX_FAILURES)
check(not throttled("gran", "10.1.0.99"),
      "a few devices' worth of failures do not pause the name")
fail("gran", f"10.1.0.{devices}", throttle.MAX_FAILURES)
check(throttled("gran", "10.1.0.99"),
      f"{throttle.NAME_MAX_FAILURES} failures from {devices} devices "
      "pause the name everywhere")
check(lock_count("name") == 1, "one many-devices pause made")
r = parent.post("/admin/login-failures/clear", data={"username": "gran"})
check(not throttled("gran", "10.1.0.99"), "admin can lift it at once")

# One device, however patient, never pauses a name everywhere.
reset_throttle()
for _ in range(throttle.NAME_MAX_FAILURES // throttle.MAX_FAILURES + 2):
    fail("gran", "10.2.0.1", throttle.MAX_FAILURES)
    expire_locks()
check(lock_count("name") == 0 and not throttled("gran", "10.2.0.2"),
      "one device alone cannot pause a name on other devices")

# A good log-in only clears failures from the same device.
reset_throttle()
fail("gran", "10.3.0.1", 3)
login(app.test_client(), "gran", "secret1", "10.3.0.2")
with app.app_context():
    left = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "gran",
               LoginFailure.cleared.is_(False)))
check(left == 3, "logging in elsewhere keeps the other device's count")
login(app.test_client(), "gran", "secret1", "10.3.0.1")
with app.app_context():
    left = db.session.scalar(
        db.select(db.func.count(LoginFailure.id))
        .where(LoginFailure.username == "gran",
               LoginFailure.cleared.is_(False)))
check(left == 0, "logging in on that device clears its count")
reset_throttle()

# --- L1: out-of-range pages only keep known arguments -------------
with app.app_context():
    for who in ("parent", "kid"):
        db.session.add(Post(author=User.find_by_username(who),
                            title=f"Hello from {who}", body="hi",
                            visibility="everyone"))
    db.session.commit()
r = parent.get("/?page=99&endpoint=x")
check(r.status_code == 302 and location(r) == "/?page=1",
      f"feed ignores ?endpoint=: {location(r)}")
r = parent.get("/?page=99&_external=1&_scheme=javascript&_anchor=x")
loc = r.headers.get("Location", "")
check(r.status_code == 302 and "javascript" not in loc and "#" not in loc
      and location(r) == "/?page=1",
      f"_external, _scheme and _anchor ignored: {loc}")
r = parent.get("/u/parent?page=99&username=evil")
check(r.status_code == 302 and location(r) == "/u/parent?page=1",
      f"profile stays on its own name: {location(r)}")
r = parent.get("/search?q=Hello&page=99&endpoint=x&foo=bar")
query = parse_qs(urlsplit(location(r)).query)
check(r.status_code == 302 and urlsplit(location(r)).path == "/search"
      and query == {"q": ["Hello"], "page": ["1"]},
      f"search keeps only its filters: {location(r)}")
r = parent.get("/?page=99&endpoint=x", follow_redirects=True)
check(r.status_code == 200, "following the redirect works")

# --- L4: uploads ---------------------------------------------------
check(uploads.MAX_PIXELS == 24_000_000, "pixel cap is 24 MP")


def upload(data: bytes, name: str, kind: str = "post") -> str:
    """Run ``save_image`` on in-memory bytes inside a request.

    Args:
        data: The file contents.
        name: The uploaded file name.
        kind: The upload kind.

    Returns:
        The stored filename, or the ``UploadError`` message prefixed
        with ``"error: "``.
    """
    with app.test_request_context("/"):
        try:
            return uploads.save_image(
                FileStorage(stream=io.BytesIO(data), filename=name), kind)
        except uploads.UploadError as exc:
            return f"error: {exc}"


def encode(size: tuple[int, int], fmt: str, **save: object) -> bytes:
    """Encode a blank image.

    Args:
        size: Width and height.
        fmt: The Pillow format name.
        **save: Extra ``save`` options (e.g. ``exif``).

    Returns:
        The encoded bytes.
    """
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 140, 200)).save(buf, fmt, **save)
    return buf.getvalue()


over = upload(encode((6000, 4001), "PNG"), "big.png")
check(over.startswith("error:") and "24 megapixels" in over,
      f"just over 24 MP refused: {over!r}")
under = upload(encode((6000, 4000), "JPEG"), "ok.jpg")
check(not under.startswith("error:"), f"exactly 24 MP accepted: {under}")
with Image.open(os.path.join(app.config["UPLOAD_FOLDER"], under)) as im:
    check(max(im.size) == 1600, f"big photo shrunk to 1600: {im.size}")

# EXIF orientation still applied with draft decoding (6 = turn 90).
exif = Image.Exif()
exif[0x0112] = 6
turned = upload(encode((4000, 2000), "JPEG", exif=exif.tobytes()),
                "turned.jpg")
with Image.open(os.path.join(app.config["UPLOAD_FOLDER"], turned)) as im:
    check(im.size == (800, 1600), f"rotated portrait kept: {im.size}")
avatar = upload(encode((4000, 3000), "JPEG"), "face.jpg", "avatar")
with Image.open(os.path.join(app.config["UPLOAD_FOLDER"], avatar)) as im:
    check(im.size == (256, 256), f"avatar still 256 square: {im.size}")

drafts = []
real_draft = JpegImageFile.draft


def spy_draft(image: JpegImageFile, mode: str | None,
              size: tuple[int, int] | None) -> object:
    """Record a call to ``JpegImageFile.draft`` and run the real one.

    Args:
        image: The JPEG (or MPO) being decoded.
        mode: The requested mode.
        size: The requested size.

    Returns:
        Whatever the real ``draft`` returns.
    """
    drafts.append((type(image).__name__, size))
    return real_draft(image, mode, size)


JpegImageFile.draft = spy_draft
try:
    upload(encode((3200, 2400), "JPEG"), "draft.jpg")
    upload(encode((3200, 2400), "PNG"), "nodraft.png")
finally:
    JpegImageFile.draft = real_draft
check(drafts and all(size == (1600, 1600) for _, size in drafts)
      and len(drafts) == 1, f"draft used for JPEG only: {drafts}")

slots = uploads.IMAGE_SLOTS
check(type(slots).__name__ == "BoundedSemaphore"
      and slots._value == 2, "two image slots, both free")
bad = upload(b"\xff\xd8\xff" + b"\0" * 64, "broken.jpg")
check(bad.startswith("error:") and slots._value == 2,
      "a slot is given back after a broken image")
real_save = Image.Image.save


def failing_save(image: Image.Image, *args: object,
                 **kwargs: object) -> None:
    """Pretend the disk is full.

    Args:
        image: The image being saved (ignored).
        *args: Ignored.
        **kwargs: Ignored.

    Raises:
        OSError: Always.
    """
    raise OSError("disk full")


Image.Image.save = failing_save
try:
    upload(encode((50, 50), "PNG"), "x.png")
    save_failed = False
except OSError:
    save_failed = True
finally:
    Image.Image.save = real_save
check(save_failed and slots._value == 2,
      "a slot is given back when saving fails")

# --- L5: control characters in Markdown ---------------------------
out = str(render_markdown("<\na@b.com\\!>"))
check(not any(ord(ch) < 32 and ch not in "\t\n\r" for ch in out),
      f"fuzz case leaves no control characters: {out!r}")
out = str(render_markdown("a\x02b\x03c\x07d\x00e\x7f\nf"))
check(out == "<p>abcde<br>\nf</p>", f"controls stripped: {out!r}")

# --- L6: log out on my other devices ------------------------------
here = app.test_client()
login(here, "kid", "secret1", "10.0.4.1")
there = app.test_client()
login(there, "kid", "secret1", "10.0.4.2", remember="y")
check(there.get("/settings").status_code == 200, "other device signed in")
html = here.get("/settings").get_data(as_text=True)
check('action="/settings/sign-out-everywhere"' in html
      and 'name="csrf_token"' in html
      and "Log out on my other devices" in html,
      "settings page has the button in a plain POST form")
check(here.get("/settings/sign-out-everywhere").status_code == 405,
      "sign out everywhere is POST only")
r = here.post("/settings/sign-out-everywhere")
check(r.status_code == 302 and location(r) == "/settings#devices",
      f"redirects back to settings: {location(r)}")
check(here.get("/settings").status_code == 200, "this device stays in")
r = there.get("/settings")
check(r.status_code == 302 and "/login" in location(r),
      "the other device is signed out (session and remember cookie)")

# --- L7: lookalike display names ----------------------------------
check(name_key("  Ｍｕｍ ") == name_key("mum") == "mum"
      and name_key("MUM  x") == "mum x", "name_key folds and trims")
with app.app_context():
    User.find_by_username("parent").display_name = "Mum"
    db.session.commit()
kid_client = app.test_client()
login(kid_client, "kid", "secret1", "10.0.5.1")


def set_name(client: FlaskClient, name: str) -> object:
    """Submit the settings profile form with a display name.

    Args:
        client: The member's test client.
        name: The display name to choose.

    Returns:
        The test response.
    """
    return client.post("/settings/profile",
                       data={"display_name": name, "bio": ""})


for clash in ("mum", " MUM ", "Ｍｕｍ", "PARENT", "ｐａｒｅｎｔ", "Sib"):
    r = set_name(kid_client, clash)
    check(r.status_code == 200 and NAME_IN_USE in r.get_data(as_text=True),
          f"settings refuses lookalike {clash!r}")
with app.app_context():
    check(User.find_by_username("kid").display_name is None,
          "refused names are not saved")
check(set_name(kid_client, "KID").status_code == 302,
      "your own username in other capitals is fine")
check(set_name(kid_client, "Captain Kid").status_code == 302,
      "a new name is fine")
check(set_name(kid_client, " captain kid ").status_code == 302,
      "re-saving your own display name is fine")

add_member(app, "newbie")
newbie = app.test_client()
login(newbie, "newbie", "secret1", "10.0.5.2")
welcome = {"bio": "", "theme_preset": "sunny", "avatar": "fox"}
r = newbie.post("/welcome", data={**welcome, "display_name": "ＭＵＭ"})
check(r.status_code == 200 and NAME_IN_USE in r.get_data(as_text=True),
      "onboarding refuses a lookalike display name")
r = newbie.post("/welcome",
                data={**welcome, "display_name": "captain KID"})
check(r.status_code == 200 and NAME_IN_USE in r.get_data(as_text=True),
      "onboarding refuses another member's display name")
r = newbie.post("/welcome", data={**welcome, "display_name": "newbie"})
check(r.status_code == 302, "onboarding accepts your own username")

for clash in ("mum", "MUM", "Captain_Kid"):
    data = {"username": clash, "password": "secret1",
            "confirm_password": "secret1"}
    if clash == "Captain_Kid":
        with app.app_context():
            User.find_by_username("kid").display_name = "captain_kid"
            db.session.commit()
    r = parent.post("/admin/members", data=data)
    check(r.status_code == 200 and NAME_IN_USE in r.get_data(as_text=True),
          f"add member refuses a display-name lookalike {clash!r}")
r = parent.post("/admin/members", data={
    "username": "Cousin", "password": "secret1",
    "confirm_password": "secret1"})
check(r.status_code == 302, "add member still works for a new name")

print(f"ALL {passed} CHECKS PASSED")
