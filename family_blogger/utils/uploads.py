"""Image upload handling: validate, normalise, store and delete."""
import io
import os
import re
import threading
import uuid

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

from ..models import db

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
# MPO is the multi-picture JPEG some phone cameras write; it is
# re-encoded as a plain JPEG like any other photo.
ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "GIF", "WEBP"}

# Refuse images bigger than this many pixels before decoding them, so
# a small, highly compressed file cannot use gigabytes of memory.
MAX_PIXELS = 24_000_000

# At most this many images are decoded and re-encoded at once, so a
# burst of uploads cannot add up to more memory than the box has.
IMAGE_SLOTS = threading.BoundedSemaphore(2)

# Long-side limits per kind; avatars are a centre-cropped square.
MAX_DIMENSION = {"post": 1600, "background": 1600}
AVATAR_SIZE = 256

# Filenames we generate: 32 hex chars + .jpg/.png. Anything else is
# rejected. Always use ``FILENAME_RE.fullmatch`` (``$`` alone would
# also accept a trailing newline).
FILENAME_RE = re.compile(r"[0-9a-f]{32}\.(jpg|png)")


class UploadError(ValueError):
    """Raised with a friendly message when an upload is rejected."""


def upload_folder() -> str:
    """Return the upload folder, creating it if needed.

    Returns:
        The path configured as ``UPLOAD_FOLDER``.
    """
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    return folder


def has_file(file_storage: FileStorage | None) -> bool:
    """Check whether a file field actually holds a selected file.

    Args:
        file_storage: The uploaded file, or ``None``.

    Returns:
        ``True`` if there is an upload with a non-empty filename.
    """
    return bool(file_storage and getattr(file_storage, "filename", ""))


def save_image(file_storage: FileStorage, kind: str = "post") -> str:
    """Validate, clean and store an uploaded image.

    Args:
        file_storage: The uploaded file.
        kind: "post", "background" or "avatar". Avatars are cropped to
            a square; the others are shrunk to a maximum size.

    Returns:
        The new filename, inside the upload folder.

    Raises:
        ValueError: If ``kind`` is not a known kind.
        UploadError: If no file was selected, or it has the wrong type,
            is too large, is empty or is not a valid image.
    """
    if kind not in ("post", "background", "avatar"):
        raise ValueError(f"unknown upload kind {kind!r}")
    if not has_file(file_storage):
        raise UploadError("No file was selected.")

    original = file_storage.filename
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError("Please upload a JPG, PNG, GIF or WebP image.")

    max_bytes = current_app.config["MAX_IMAGE_BYTES"]
    data = file_storage.stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        megabytes = max_bytes // (1024 * 1024)
        raise UploadError(f"Images must be under {megabytes} MB.")
    if not data:
        raise UploadError("That file is empty.")

    with IMAGE_SLOTS:
        clean, has_alpha = _decode_and_clean(data, kind)
        filename = f"{uuid.uuid4().hex}.{'png' if has_alpha else 'jpg'}"
        path = os.path.join(upload_folder(), filename)
        if has_alpha:
            clean.save(path, "PNG", optimize=True)
        else:
            clean.save(path, "JPEG", quality=85, optimize=True,
                       progressive=True)
    return filename


def _target_size(kind: str) -> int:
    """Return the longest side, in pixels, an upload is shrunk to.

    Args:
        kind: "post", "background" or "avatar".

    Returns:
        The size limit for that kind.
    """
    return AVATAR_SIZE if kind == "avatar" else MAX_DIMENSION[kind]


def _decode_and_clean(data: bytes, kind: str) -> tuple[Image.Image, bool]:
    """Decode an upload, check it and return clean, resized pixels.

    For JPEG (and MPO) photos, ``Image.draft`` asks the decoder for a
    smaller copy (1/2, 1/4 or 1/8 scale) while both sides stay at
    least the target size, so a big camera photo uses far less memory.
    The target is square, so it holds whichever way EXIF says the
    photo is turned; ``exif_transpose`` still rotates it afterwards.

    Args:
        data: The uploaded bytes.
        kind: "post", "background" or "avatar".

    Returns:
        The cleaned image (no metadata) and whether it has alpha.

    Raises:
        UploadError: If the file has the wrong type, is too large or
            is not a valid image.
    """
    bad_type = "Please upload a JPG, PNG, GIF or WebP image."
    target = _target_size(kind)
    try:
        # Image.open only reads the header, so the size and format can
        # be checked before any pixels are decoded.
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise UploadError(bad_type)
            width, height = probe.size
            if width * height > MAX_PIXELS:
                raise UploadError(
                    "That picture is too big. Please use one under "
                    f"{MAX_PIXELS // 1_000_000} megapixels.")
            # verify() catches truncated/corrupt files but leaves the
            # image unusable, so reopen it afterwards for the real work.
            probe.verify()
        img = Image.open(io.BytesIO(data))
        if img.format in ("JPEG", "MPO"):
            img.draft(img.mode, (target, target))
        img.load()  # first frame only for animated GIF/WebP/MPO
    except UploadError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombError):
        raise UploadError("That file doesn't look like a valid image.")

    img = ImageOps.exif_transpose(img)

    has_alpha = img.mode in ("RGBA", "LA", "PA") or (
        img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")

    if kind == "avatar":
        img = ImageOps.fit(
            img, (target, target), Image.Resampling.LANCZOS)
    else:
        img.thumbnail((target, target), Image.Resampling.LANCZOS)

    # Copy pixels into a fresh image so no EXIF/ICC/text metadata
    # survives.
    clean = Image.new(img.mode, img.size)
    clean.paste(img)
    return clean, has_alpha


def delete_upload(filename: str | None) -> None:
    """Remove an upload from disk.

    Missing files and names that don't match ``FILENAME_RE`` are
    silently ignored.

    Args:
        filename: The stored filename, or ``None``.
    """
    if not filename or not FILENAME_RE.fullmatch(filename):
        return
    try:
        os.remove(os.path.join(upload_folder(), filename))
    except FileNotFoundError:
        pass


def commit_with_files(
    new_files: list[str], old_files: list[str | None]
) -> None:
    """Commit the session, then tidy up the upload files.

    Old files are only deleted after a successful commit. If the
    commit fails, the session is rolled back, the newly saved files
    are removed and the error is raised again.

    Args:
        new_files: Filenames saved for this change.
        old_files: Filenames the change replaced; ``None`` is ignored.

    Raises:
        Exception: Whatever the commit raised, after clean-up.
    """
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        for name in new_files:
            delete_upload(name)
        raise
    for name in old_files:
        delete_upload(name)
