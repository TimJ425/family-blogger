"""Blueprint for the signed-in user's account settings."""
from flask import Blueprint

bp = Blueprint("settings", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
