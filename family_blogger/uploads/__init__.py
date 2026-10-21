"""Blueprint that serves uploaded images."""
from flask import Blueprint

bp = Blueprint("uploads", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
