"""Blueprint for searching posts."""
from flask import Blueprint

bp = Blueprint("search", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
