from flask import render_template, request, session

from ..utils import error, ok, require_login
from . import bp
from . import service


def _as_dict(field):
    return {
        "key": field.key,
        "label": field.label,
        "json_type": field.json_type,
        "enabled": field.enabled,
        "order": field.order,
    }


@bp.route("/panel/data-sheets")
def data_sheets_list():
    gate = require_login()
    if gate:
        return gate
    return render_template("data_sheets_list.html")


@bp.route("/panel/data-sheets/new")
def data_sheets_new():
    gate = require_login()
    if gate:
        return gate
    return render_template("data_sheets_form.html", sheet=None)


@bp.route("/panel/data-sheets/<sheet_id>/edit")
def data_sheets_edit(sheet_id):
    gate = require_login()
    if gate:
        return gate
    sheet = service.get_sheet(session["uid"], sheet_id)
    if not sheet:
        return error("Data sheet not found", 404)
    return render_template("data_sheets_form.html", sheet=sheet)


@bp.route("/api/data-sheets", methods=["GET", "POST"])
def api_data_sheets():
    gate = require_login()
    if gate:
        return gate
    if request.method == "GET":
        return ok(sheets=service.list_sheets(session["uid"]))
    try:
        sheet = service.create_sheet(session["uid"], request.get_json(silent=True) or {})
    except ValueError as exc:
        return error(str(exc), 400)
    return ok(sheet=sheet)


@bp.route("/api/data-sheets/discover-fields", methods=["POST"])
def api_discover_fields():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    try:
        fields = service.discover_fields(data.get("sample_json", ""))
    except ValueError as exc:
        return error(str(exc), 400)
    return ok(fields=[_as_dict(field) for field in fields])


@bp.route("/api/data-sheets/<sheet_id>", methods=["GET", "PUT", "DELETE"])
def api_data_sheet(sheet_id):
    gate = require_login()
    if gate:
        return gate
    if request.method == "GET":
        sheet = service.get_sheet(session["uid"], sheet_id)
        return ok(sheet=sheet) if sheet else error("Data sheet not found", 404)
    if request.method == "DELETE":
        return ok() if service.delete_sheet(session["uid"], sheet_id) else error("Data sheet not found", 404)
    try:
        sheet = service.update_sheet(session["uid"], sheet_id, request.get_json(silent=True) or {})
    except ValueError as exc:
        return error(str(exc), 400)
    return ok(sheet=sheet) if sheet else error("Data sheet not found", 404)
