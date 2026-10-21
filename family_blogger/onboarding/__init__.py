"""Blueprint for the first-run onboarding form."""
from flask import Blueprint

bp = Blueprint("onboarding", __name__)

# Imported last: routes.py imports bp from this module (E402).
from . import routes  # noqa: E402,F401
