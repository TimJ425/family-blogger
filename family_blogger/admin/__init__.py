"""Blueprint for the admin panel that manages members."""
from flask import Blueprint

bp = Blueprint("admin", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
