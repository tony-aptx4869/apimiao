from flask import Blueprint

bp = Blueprint("api", __name__)

from . import routes, proxy, trans_origin_routes, trans_user_routes  # noqa: E402,F401
