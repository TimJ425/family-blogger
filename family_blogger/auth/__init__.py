"""Blueprint for sign up, log in and log out."""
from flask import Blueprint

bp = Blueprint("auth", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
