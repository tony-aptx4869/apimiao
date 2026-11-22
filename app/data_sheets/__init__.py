from flask import Blueprint

bp = Blueprint("data_sheets", __name__)

from . import routes  # noqa: E402,F401
