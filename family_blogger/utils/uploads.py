"""Image upload handling: validate, normalise, store and delete."""
import io
import os
import re
import uuid

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}

# Long-side limits per kind; avatars are a centre-cropped square.
MAX_DIMENSION = {"post": 1600, "background": 1600}
AVATAR_SIZE = 256

# Filenames we generate: 32 hex chars + .jpg/.png. Anything else is
# rejected.
FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png)$")


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

    try:
        # verify() catches truncated/corrupt files but leaves the image
        # unusable, so reopen it afterwards for the real work.
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = Image.open(io.BytesIO(data))
        if img.format not in ALLOWED_FORMATS:
            raise UploadError("Please upload a JPG, PNG, GIF or WebP image.")
        img.load()  # first frame only for animated GIF/WebP
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
            img, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
    else:
        limit = MAX_DIMENSION[kind]
        img.thumbnail((limit, limit), Image.Resampling.LANCZOS)

    # Copy pixels into a fresh image so no EXIF/ICC/text metadata
    # survives.
    clean = Image.new(img.mode, img.size)
    clean.paste(img)

    filename = f"{uuid.uuid4().hex}.{'png' if has_alpha else 'jpg'}"
    path = os.path.join(upload_folder(), filename)
    if has_alpha:
        clean.save(path, "PNG", optimize=True)
    else:
        clean.save(path, "JPEG", quality=85, optimize=True, progressive=True)
    return filename


def delete_upload(filename: str | None) -> None:
    """Remove an upload from disk.

    Missing files and names that don't match ``FILENAME_RE`` are
    silently ignored.

    Args:
        filename: The stored filename, or ``None``.
    """
    if not filename or not FILENAME_RE.match(filename):
        return
    try:
        os.remove(os.path.join(upload_folder(), filename))
    except FileNotFoundError:
        pass
