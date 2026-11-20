from flask import Blueprint

bp = Blueprint("control", __name__)

from . import routes  # noqa: E402,F401
