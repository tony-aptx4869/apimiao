"""首页、登录、2FA、通行密钥、用户管理。"""
import base64
import io
import json
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

import pyotp
import qrcode
from flask import (
    g, jsonify, redirect, render_template, request, session, url_for, flash,
)
from werkzeug.security import check_password_hash, generate_password_hash
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    base64url_to_bytes,
    bytes_to_base64url,
    options_to_json,
    parse_authentication_credential_json,
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


def _options_response(options):
    """options_to_json 返回 JSON 字符串，需转成对象再 jsonify，避免双重编码导致前端拿到字符串。"""
    return jsonify(json.loads(options_to_json(options)))

import config
from config import ADMIN_ID, PASSKEY_MAX
from .. import i18n
from ..db import all_rows, check_rate_limit, execute, get_db, new_id, one, q, utcnow
from ..utils import error, gen_recovery_codes, hash_api_key, hash_recovery_code, is_admin, ok, require_admin, require_login
from . import bp


def _create_recovery_codes(user_id):
    """生成 10 个恢复码，哈希入库，返回明文（仅此处可见一次）。"""
    execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    codes = gen_recovery_codes(10)
    rows = [(user_id, hash_recovery_code(c), utcnow()) for c in codes]
    db = get_db()
    db.executemany(
        "INSERT INTO recovery_codes (user_id, code_hash, created_at) VALUES (?, ?, ?)", rows
    )
    db.commit()
    return codes


def _verify_recovery_code(user_id, code):
    """校验并消费一个恢复码（一次性），成功返回 True。"""
    h = hash_recovery_code(code)
    cur = execute(
        "UPDATE recovery_codes SET used = 1 WHERE user_id = ? AND code_hash = ? AND used = 0",
        (user_id, h),
    )
    return cur.rowcount == 1


def _rp_origin():
    # 优先用 config.ini 的显式配置；若 rp_id 留空则退回请求主机名（兼容未配置场景）
    rp_id = config.RP_ID or request.host.split(":")[0]
    origin = config.ORIGIN or request.host_url.rstrip("/")
    return rp_id, origin


def _brute_ok(scope, identity):
    """登录与验证码尝试的粗略限速，按来源和账号维度计数。"""
    key = hash_api_key(f"{scope}:{request.remote_addr}:{identity}")
    return check_rate_limit(key, 5)


def _establish_login(user):
    """建立登录会话，返回应跳转的目标 URL。

    XHR 端点（passkey 认证验证）返回 JSON，由前端自行跳转，因此这里返回
    URL 字符串而非重定向响应，避免 fetch 跟随 302 拿到 HTML 导致解析失败。
    """
    session.clear()
    session["uid"] = user["id"]
    session["email"] = user["email"]
    session["role"] = user["role"]
    session.permanent = True
    if user["must_change_password"]:
        session["force_pwd"] = True
        return url_for("control.profile") + "?force=1"
    return url_for("control.overview")


def _finish_login(user):
    """表单登录收尾：直接重定向到控制台（整页导航，不走 XHR）。"""
    return redirect(_establish_login(user))


@bp.route("/")
def home():
    return render_template("home.html")


@bp.route("/lang/<lang>")
def set_lang(lang):
    if lang not in i18n.LANGS:
        lang = i18n.DEFAULT_LANG
    target = request.referrer or url_for("auth.home")
    if urlsplit(target).netloc and urlsplit(target).netloc != request.host:
        target = url_for("auth.home")
    resp = redirect(target)
    resp.set_cookie("apimiao_lang", lang, max_age=60 * 60 * 24 * 365, path="/", samesite="Lax")
    return resp


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("uid"):
        return redirect(url_for("control.overview"))
    if request.method == "GET":
        return render_template("login.html")

    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    if not _brute_ok("login", email):
        return render_template("login.html", login_error=i18n.t("login.error.locked"), email=email)
    user = one("SELECT * FROM users WHERE email = ?", (email,))
    if not user or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", login_error=i18n.t("login.error.bad"), email=email)

    if user["totp_enabled"]:
        session.clear()
        session["pending_2fa_uid"] = user["id"]
        session["pending_2fa_next"] = request.form.get("next") or ""
        return render_template("verify_2fa.html")

    return _finish_login(user)


@bp.route("/login/2fa", methods=["POST"])
def login_2fa():
    uid = session.get("pending_2fa_uid")
    if not uid:
        return redirect(url_for("auth.login"))
    if not _brute_ok("2fa", uid):
        return render_template("verify_2fa.html", login_error=i18n.t("login.error.locked"))
    user = one("SELECT * FROM users WHERE id = ?", (uid,))
    code = (request.form.get("code") or "").strip()
    if not user:
        return render_template("verify_2fa.html", login_error=i18n.t("login.error.code"))
    # 先试 TOTP 动态码，再试一次性恢复码
    totp_ok = user["totp_secret"] and pyotp.TOTP(user["totp_secret"]).verify(code, valid_window=1)
    recovery_ok = (not totp_ok) and _verify_recovery_code(uid, code)
    if not (totp_ok or recovery_ok):
        return render_template("verify_2fa.html", login_error=i18n.t("login.error.code"))
    return _finish_login(user)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.home"))


# ---------------- WebAuthn：通行密钥登录（§5.5 RequestOptions / §7.2 认证验证） ----------------

@bp.route("/api/passkey/auth/options", methods=["POST"])
def passkey_auth_options():
    email = str((request.get_json(silent=True) or {}).get("email") or "").strip()
    if not email:
        return error("Email required", 400)
    user = one("SELECT id FROM users WHERE email = ?", (email,))
    if not user:
        return error("No passkeys registered", 400)
    # §5.5：allowCredentials 限定认证器只能返回该用户已注册的凭证
    allow = [
        PublicKeyCredentialDescriptor(id=bytes(row["credential_id"]))
        for row in all_rows("SELECT credential_id FROM passkeys WHERE user_id = ? ORDER BY id", (user["id"],))
    ]
    if not allow:
        return error("No passkeys registered", 400)
    rp_id, _ = _rp_origin()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session["passkey_auth_challenge"] = bytes_to_base64url(options.challenge)
    return _options_response(options)


@bp.route("/api/passkey/auth/verify", methods=["POST"])
def passkey_auth_verify():
    challenge_b64 = session.get("passkey_auth_challenge")
    if not challenge_b64:
        return error("Challenge expired", 400)
    rp_id, origin = _rp_origin()
    try:
        credential = parse_authentication_credential_json(request.get_data(as_text=True))
        row = one("SELECT * FROM passkeys WHERE credential_id = ?", (credential.raw_id,))
        if not row:
            return error("Unknown passkey", 400)
        # §7.2：库内部完成 clientData 类型/挑战/来源校验、rpIdHash、UP/UV 检查、
        # sig 对 authData||SHA256(clientDataJSON) 验签、signCount 单调性（克隆检测）
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=bytes(row["public_key"]),
            credential_current_sign_count=row["sign_count"],
            require_user_verification=False,
        )
    except Exception as exc:
        return error(f"Passkey verification failed: {exc}", 400)

    session.pop("passkey_auth_challenge", None)
    # §7.2：新 signCount 大于存储值则更新；库已保证不会回退（回退即克隆，直接拒绝）
    execute("UPDATE passkeys SET sign_count = ? WHERE id = ?", (verified.new_sign_count, row["id"]))
    user = one("SELECT * FROM users WHERE id = ?", (row["user_id"],))
    if not user:
        return error("User not found", 400)
    # XHR 契约：返回 JSON 而非 302，前端拿到 redirect 后自行跳转
    return ok(redirect=_establish_login(user))


# ---------------- WebAuthn：注册通行密钥（§5.4 CreationOptions / §7.1 注册验证） ----------------

# AAGUID（§6.5.1 attestedCredentialData）→ 平台名。依据 FIDO MDS / 社区通行密钥
# AAGUID 注册表核实，仅覆盖常见认证器；未命中（含全零隐私值）归为 Other。
_AAGUID_PLATFORMS = {
    # Apple iCloud Keychain
    "fbfc3007-154e-4ecc-8c0b-6e020557d7bd": "Apple",
    "dd4ec289-e01d-41c9-bb89-70fa845d4bf2": "Apple",
    # Microsoft Windows Hello
    "08987058-cadc-4b81-b6e1-30de50dcbe96": "Microsoft",
    "9ddd1817-af5a-4672-a2b9-3e3dd95000a9": "Microsoft",
    "6028b017-b1d4-4c02-b4b3-afcdafc96bb2": "Microsoft",
    # Google Password Manager
    "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4": "Google",
    # Yubico
    "2fc0579f-8113-47ea-b116-bb5a8db9202a": "YubiKey",
    "fa2b99dc-9e39-4257-8f92-4a30d23c4118": "YubiKey",
    "6d44ba9b-f6ec-2e49-b930-0c8fe920cb73": "YubiKey",
    "149a2021-8ef6-4133-96b8-81f8d5b7f1f5": "YubiKey",
    "b92c3f9a-c014-4056-887f-140a2501163b": "YubiKey",
}


def _platform_from_aaguid(aaguid):
    """由注册返回的 AAGUID 推导平台，未命中返回 Other。"""
    return _AAGUID_PLATFORMS.get(str(aaguid or "").strip().lower(), "Other")


@bp.route("/api/passkey/register/options", methods=["POST"])
def passkey_register_options():
    gate = require_login()
    if gate:
        return gate
    user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
    count = one("SELECT COUNT(*) AS n FROM passkeys WHERE user_id = ?", (session["uid"],))["n"]
    if count >= PASSKEY_MAX:
        return error(f"Maximum {PASSKEY_MAX} passkeys reached", 400)
    # §5.4：excludeCredentials 防止同一认证器重复注册
    exclude = [
        PublicKeyCredentialDescriptor(id=bytes(r["credential_id"]))
        for r in all_rows("SELECT credential_id FROM passkeys WHERE user_id = ?", (session["uid"],))
    ]
    rp_id, _ = _rp_origin()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=config.RP_NAME,
        user_id=user["id"].encode(),
        user_name=user["email"],
        user_display_name=user["email"],
        exclude_credentials=exclude,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            require_resident_key=False,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        attestation=AttestationConveyancePreference.NONE,
    )
    session["passkey_reg_challenge"] = bytes_to_base64url(options.challenge)
    return _options_response(options)


@bp.route("/api/passkey/register/verify", methods=["POST"])
def passkey_register_verify():
    gate = require_login()
    if gate:
        return gate
    challenge_b64 = session.get("passkey_reg_challenge")
    if not challenge_b64:
        return error("Challenge expired", 400)
    rp_id, origin = _rp_origin()
    try:
        credential = parse_registration_credential_json(request.get_data(as_text=True))
        # §7.1：库内部完成 clientData 类型=webauthn.create/挑战/来源校验、CBOR 解码
        # attestationObject、rpIdHash、UP/UV、alg 匹配、attStmt 验证
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge_b64),
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=False,
        )
    except Exception as exc:
        return error(f"Passkey registration failed: {exc}", 400)

    session.pop("passkey_reg_challenge", None)
    # §7.1：credentialId 不得已被注册（UNIQUE 约束兜底，显式检查给出更清晰错误）
    if one("SELECT 1 FROM passkeys WHERE credential_id = ?", (verified.credential_id,)):
        return error("Passkey already exists", 400)
    # 平台不再由客户端选择，依据注册返回的 AAGUID（§6.5.1）自动推导
    platform = _platform_from_aaguid(verified.aaguid)
    # 建议名称：平台名-序号，序号为该用户该平台已有数量 + 1
    seq = one(
        "SELECT COUNT(*) AS n FROM passkeys WHERE user_id = ? AND platform = ?",
        (session["uid"], platform),
    )["n"]
    suggested_name = f"{platform}-{seq + 1}"
    transports = ",".join(credential.response.transports or [])
    cur = execute(
        "INSERT INTO passkeys (user_id, name, platform, created_at, credential_id, public_key, "
        "sign_count, transports, aaguid, user_verified) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            session["uid"], suggested_name, platform, utcnow(), verified.credential_id,
            verified.credential_public_key, verified.sign_count, transports,
            verified.aaguid, 1 if verified.user_verified else 0,
        ),
    )
    return ok(id=cur.lastrowid, platform=platform, suggested_name=suggested_name)


@bp.route("/api/passkeys", methods=["GET"])
def passkey_list():
    gate = require_login()
    if gate:
        return gate
    rows = all_rows("SELECT id, name, platform, created_at FROM passkeys WHERE user_id = ? ORDER BY created_at DESC", (session["uid"],))
    return ok(passkeys=[dict(r) for r in rows])


@bp.route("/api/passkey/<int:passkey_id>", methods=["PUT"])
def passkey_rename(passkey_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT id FROM passkeys WHERE id = ? AND user_id = ?", (passkey_id, session["uid"]))
    if not row:
        return error("Passkey not found", 404)
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return error("Name is required", 400)
    execute("UPDATE passkeys SET name = ? WHERE id = ?", (name, passkey_id))
    return ok()


# 删除通行密钥：可用 2FA 动态码，或任意通行密钥验证

@bp.route("/api/passkey/delete/options", methods=["POST"])
def passkey_delete_options():
    gate = require_login()
    if gate:
        return gate
    rp_id, _ = _rp_origin()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=bytes(r["credential_id"])) for r in all_rows("SELECT credential_id FROM passkeys WHERE user_id = ?", (session["uid"],))],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session["passkey_del_challenge"] = bytes_to_base64url(options.challenge)
    return _options_response(options)


@bp.route("/api/passkey/<int:passkey_id>", methods=["DELETE"])
def passkey_delete(passkey_id):
    gate = require_login()
    if gate:
        return gate
    row = one("SELECT * FROM passkeys WHERE id = ? AND user_id = ?", (passkey_id, session["uid"]))
    if not row:
        return error("Passkey not found", 404)
    data = request.get_json(silent=True) or {}
    method = data.get("method")
    if method == "totp":
        user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
        if not user["totp_secret"] or not pyotp.TOTP(user["totp_secret"]).verify(data.get("code", ""), valid_window=1):
            return error("Invalid 2FA code", 400)
    elif method == "passkey":
        challenge_b64 = session.get("passkey_del_challenge")
        if not challenge_b64:
            return error("Challenge expired", 400)
        rp_id, origin = _rp_origin()
        try:
            credential = parse_authentication_credential_json(request.get_data(as_text=True))
            verify_authentication_response(
                credential=credential,
                expected_challenge=base64url_to_bytes(challenge_b64),
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=bytes(row["public_key"]),
                credential_current_sign_count=row["sign_count"],
                require_user_verification=False,
            )
        except Exception as exc:
            return error(f"Passkey verification failed: {exc}", 400)
        session.pop("passkey_del_challenge", None)
    else:
        return error("Verification required", 400)
    execute("DELETE FROM passkeys WHERE id = ?", (passkey_id,))
    return ok()


# ---------------- 2FA ----------------

@bp.route("/api/2fa/setup", methods=["POST"])
def twofa_setup():
    gate = require_login()
    if gate:
        return gate
    user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
    if user["totp_enabled"]:
        return error("2FA already enabled", 400)
    secret = pyotp.random_base32()
    session["2fa_pending_secret"] = secret
    uri = pyotp.TOTP(secret).provisioning_uri(name=user["email"], issuer_name="APIMiao")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return ok(secret=secret, uri=uri, qr="data:image/png;base64," + base64.b64encode(buf.getvalue()).decode())


@bp.route("/api/2fa/enable", methods=["POST"])
def twofa_enable():
    gate = require_login()
    if gate:
        return gate
    secret = session.get("2fa_pending_secret")
    if not secret:
        return error("No pending setup", 400)
    code = (request.get_json(silent=True) or {}).get("code", "")
    if not pyotp.TOTP(secret).verify(code, valid_window=1):
        return error("Invalid code", 400)
    execute("UPDATE users SET totp_secret = ?, totp_enabled = 1 WHERE id = ?", (secret, session["uid"]))
    session.pop("2fa_pending_secret", None)
    codes = _create_recovery_codes(session["uid"])
    return ok(recovery_codes=codes)


@bp.route("/api/2fa/disable", methods=["POST"])
def twofa_disable():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
    if not user["totp_secret"] or not pyotp.TOTP(user["totp_secret"]).verify(data.get("code", ""), valid_window=1):
        return error("Invalid 2FA code", 400)
    if not check_password_hash(user["password_hash"], data.get("password", "")):
        return error("Invalid password", 400)
    execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?", (session["uid"],))
    execute("DELETE FROM recovery_codes WHERE user_id = ?", (session["uid"],))
    return ok()


@bp.route("/api/2fa/recovery", methods=["GET"])
def twofa_recovery_status():
    gate = require_login()
    if gate:
        return gate
    total = one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = ?", (session["uid"],))["n"]
    remaining = one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = ? AND used = 0", (session["uid"],))["n"]
    return ok(total=total, remaining=remaining)


@bp.route("/api/2fa/recovery/regenerate", methods=["POST"])
def twofa_recovery_regenerate():
    """重新生成恢复码，需验证当前 TOTP 码，旧码全部作废。"""
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
    if not user["totp_secret"] or not pyotp.TOTP(user["totp_secret"]).verify(data.get("code", ""), valid_window=1):
        return error("Invalid 2FA code", 400)
    codes = _create_recovery_codes(session["uid"])
    return ok(recovery_codes=codes)


# ---------------- 用户管理 ----------------

@bp.route("/api/users", methods=["GET"])
def users_list():
    gate = require_admin()
    if gate:
        return gate
    rows = all_rows("SELECT id, created_at, email, role, totp_enabled FROM users ORDER BY created_at")
    return ok(users=[dict(r) for r in rows])


@bp.route("/api/users", methods=["POST"])
def users_create():
    gate = require_admin()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") if data.get("role") in ("ADMIN", "USER") else "USER"
    if not email or len(password) < 8:
        return error("Email required and password must be at least 8 characters", 400)
    if one("SELECT 1 FROM users WHERE email = ?", (email,)):
        return error("Email already exists", 400)
    uid = new_id()
    while one("SELECT 1 FROM users WHERE id = ?", (uid,)):
        uid = new_id()
    execute(
        "INSERT INTO users (id, created_at, email, password_hash, role, must_change_password) VALUES (?, ?, ?, ?, ?, 1)",
        (uid, utcnow(), email, generate_password_hash(password, method="scrypt"), role),
    )
    return ok(id=uid)


@bp.route("/api/users/<uid>", methods=["PUT"])
def users_update(uid):
    gate = require_admin()
    if gate:
        return gate
    if uid == ADMIN_ID and session["uid"] != ADMIN_ID:
        return error("Cannot modify the built-in admin from another account", 403)
    data = request.get_json(silent=True) or {}
    user = one("SELECT * FROM users WHERE id = ?", (uid,))
    if not user:
        return error("User not found", 404)
    email = (data.get("email") or user["email"]).strip()
    role = data.get("role") if data.get("role") in ("ADMIN", "USER") else user["role"]
    password_hash = user["password_hash"]
    if one("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, uid)):
        return error("Email already exists", 400)
    if data.get("password"):
        if len(data["password"]) < 8:
            return error("Password too short", 400)
        password_hash = generate_password_hash(data["password"], method="scrypt")
    execute("UPDATE users SET email = ?, role = ?, password_hash = ? WHERE id = ?", (email, role, password_hash, uid))
    return ok()


@bp.route("/api/users/<uid>", methods=["DELETE"])
def users_delete(uid):
    gate = require_admin()
    if gate:
        return gate
    if uid == ADMIN_ID:
        return error("Built-in admin cannot be deleted", 400)
    if uid == session["uid"]:
        return error("Cannot delete yourself", 400)
    execute("DELETE FROM users WHERE id = ?", (uid,))
    return ok()


# ---------------- 个人资料 / 改密 ----------------

@bp.route("/api/profile", methods=["POST"])
def profile_update():
    gate = require_login()
    if gate:
        return gate
    data = request.get_json(silent=True) or {}
    user = one("SELECT * FROM users WHERE id = ?", (session["uid"],))
    email = (data.get("email") or user["email"]).strip()
    if one("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, session["uid"])):
        return error("Email already exists", 400)
    password_hash = user["password_hash"]
    force_cleared = False
    if session.get("force_pwd") or data.get("password"):
        pwd = data.get("password") or ""
        if len(pwd) < 8:
            return error("Password must be at least 8 characters", 400)
        password_hash = generate_password_hash(pwd, method="scrypt")
        force_cleared = True
    execute("UPDATE users SET email = ?, password_hash = ?, must_change_password = 0 WHERE id = ?", (email, password_hash, session["uid"]))
    session["email"] = email
    if force_cleared:
        session.pop("force_pwd", None)
    return ok()
