"""Blueprint for member profiles and the family directory."""
from flask import Blueprint

bp = Blueprint("profile", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
