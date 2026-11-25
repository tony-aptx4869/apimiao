#!/usr/bin/env python3
"""Regression checks for the WebAuthn Passkey feature (W3C WebAuthn Level 2).

Drives the full registration (§5.4 CreationOptions / §7.1) and authentication
(§5.5 RequestOptions / §7.2) flows end-to-end using a software authenticator
(ES256, attestation fmt "none") that builds spec-shaped credentials.
"""
import cbor2
import hashlib
import json
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA, SECP256R1, generate_private_key

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-passkey-check-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from webauthn.helpers import bytes_to_base64url

from app import create_app
from app.db import execute, one, utcnow
from config import ORIGIN, RP_ID

RESULT = []


def record(name, ok, detail=""):
    RESULT.append({"case": name, "ok": bool(ok), "detail": detail})


def b64url(b):
    return bytes_to_base64url(b)


def sha256(b):
    return hashlib.sha256(b).digest()


class SoftwareAuthenticator:
    """Minimal ES256 authenticator producing spec-shaped WebAuthn credentials."""

    def __init__(self, credential_id=None, aaguid=None):
        self.priv = generate_private_key(SECP256R1())
        pub = self.priv.public_key()
        pn = pub.public_numbers()
        self.x = pn.x.to_bytes(32, "big")
        self.y = pn.y.to_bytes(32, "big")
        self.credential_id = credential_id or b"\x11" * 32
        self.aaguid = aaguid or b"\x00" * 16
        self.sign_count = 1

    def cose_public_key(self):
        # COSE_Key EC2: kty=2 (EC2), alg=-7 (ES256), crv=1 (P-256), x, y
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: self.x, -3: self.y})

    def _rp_id_hash(self):
        return sha256(RP_ID.encode())

    def _auth_data(self, flags, sign_count, attested=False):
        data = self._rp_id_hash() + bytes([flags]) + sign_count.to_bytes(4, "big")
        if attested:
            ac = (
                self.aaguid
                + len(self.credential_id).to_bytes(2, "big")
                + self.credential_id
                + self.cose_public_key()
            )
            data += ac
        return data

    def _client_data(self, ctype, challenge_b64, origin):
        return json.dumps({
            "type": ctype,
            "challenge": challenge_b64,
            "origin": origin,
        }).encode()

    def register(self, challenge_b64, flags=0x45):
        """Build a RegistrationCredential JSON object (§5.4 → §7.1).

        flags 0x45 = UP(0x01) | UV(0x04) | AT(0x40).
        """
        auth_data = self._auth_data(flags, self.sign_count, attested=True)
        attestation_object = cbor2.dumps({
            "fmt": "none",
            "attStmt": {},
            "authData": auth_data,
        })
        client_data = self._client_data("webauthn.create", challenge_b64, ORIGIN)
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation_object),
                "transports": ["internal"],
            },
        }

    def authenticate(self, challenge_b64, sign_count=None, origin=ORIGIN):
        """Build an AuthenticationCredential JSON object (§5.5 → §7.2).

        flags 0x01 = UP only. Signature covers authData || SHA256(clientDataJSON).
        """
        sc = sign_count if sign_count is not None else self.sign_count + 1
        auth_data = self._auth_data(0x01, sc, attested=False)
        client_data = self._client_data("webauthn.get", challenge_b64, origin)
        signed = auth_data + sha256(client_data)
        signature = self.priv.sign(signed, ECDSA(hashes.SHA256()))
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "authenticatorData": b64url(auth_data),
                "clientDataJSON": b64url(client_data),
                "signature": b64url(signature),
            },
        }


def login(client, uid, role="USER"):
    with client.session_transaction() as session:
        session.clear()
        session["uid"] = uid
        session["role"] = role


def run():
    app = create_app()
    with app.test_client() as client:
        uid = "passkey-test-user"
        # test_client 的 preserve_context 只保留请求后的上下文；首个请求前需显式推送
        with app.app_context():
            execute(
                "INSERT INTO users (id, created_at, email, password_hash, role, must_change_password) "
                "VALUES (?, ?, ?, ?, 'USER', 0)",
                (uid, utcnow(), "passkey@test.local", "x"),
            )
        login(client, uid)
        auth = SoftwareAuthenticator()

        # ---- Registration options (§5.4) ----
        opts = client.post("/api/passkey/register/options", json={})
        body = opts.get_json()
        record("register_options_ok", opts.status_code == 200 and body.get("challenge"),
               f"status={opts.status_code} body={body}")
        rp = body.get("rp", {})
        user = body.get("user", {})
        asc = body.get("authenticatorSelection", {})
        record("register_options_rp_user",
               rp.get("id") == RP_ID and bool(rp.get("name")) and bool(user.get("id")) and bool(user.get("name")),
               f"rp={rp} user={user}")
        record("register_options_selection_and_attestation",
               asc.get("residentKey") == "preferred"
               and asc.get("requireResidentKey") is False
               and asc.get("userVerification") == "preferred"
               and body.get("attestation") == "none",
               f"asc={asc} attestation={body.get('attestation')}")
        record("register_options_exclude_credentials", body.get("excludeCredentials") == [],
               f"exclude={body.get('excludeCredentials')}")

        challenge = body["challenge"]

        # ---- Registration verify (§7.1) ----
        # 新契约：不再由客户端传 name/platform，服务端依据 AAGUID 推导平台
        # 并给出建议名 平台名-序号，返回 id/platform/suggested_name。
        cred = auth.register(challenge)
        reg = client.post("/api/passkey/register/verify", json=cred)
        reg_body = reg.get_json()
        record("register_verify_ok", reg.status_code == 200 and reg_body.get("ok"),
               f"status={reg.status_code} body={reg_body}")
        record("register_verify_platform_auto",
               bool(reg_body.get("id")) and reg_body.get("platform") == "Other"
               and reg_body.get("suggested_name") == "Other-1",
               f"body={reg_body}")

        row = one("SELECT * FROM passkeys WHERE user_id = ?", (uid,))
        record("register_stored_fields",
               row is not None
               and bytes(row["credential_id"]) == auth.credential_id
               and row["sign_count"] == auth.sign_count
               and row["transports"] == "internal"
               and bool(row["aaguid"])
               and row["user_verified"] == 1
               and row["name"] == "Other-1" and row["platform"] == "Other",
               f"row={dict(row) if row else None}")

        # ---- List ----
        pks = client.get("/api/passkeys").get_json().get("passkeys", [])
        record("passkey_list", len(pks) == 1 and pks[0]["name"] == "Other-1"
               and pks[0]["platform"] == "Other",
               f"passkeys={pks}")

        # ---- Rename (PUT) ----
        rn = client.put(f"/api/passkey/{row['id']}", json={"name": "My Key"})
        record("passkey_rename", rn.status_code == 200 and rn.get_json().get("ok")
               and one("SELECT name FROM passkeys WHERE id = ?", (row["id"],))["name"] == "My Key",
               f"status={rn.status_code} body={rn.get_json()}")
        rn_empty = client.put(f"/api/passkey/{row['id']}", json={"name": "  "})
        record("passkey_rename_empty_rejected", rn_empty.status_code == 400,
               f"status={rn_empty.status_code}")
        rn_foreign = client.put("/api/passkey/999999", json={"name": "X"})
        record("passkey_rename_not_found", rn_foreign.status_code == 404,
               f"status={rn_foreign.status_code}")

        # ---- Duplicate registration blocked (§7.1 credentialId uniqueness) ----
        opts2 = client.post("/api/passkey/register/options", json={}).get_json()
        cred2 = auth.register(opts2["challenge"])
        dup = client.post("/api/passkey/register/verify", json=cred2)
        record("register_duplicate_blocked", dup.status_code == 400,
               f"status={dup.status_code} body={dup.get_json()}")

        # ---- Auth options (§5.5) ----
        aopts = client.post("/api/passkey/auth/options", json={"email": "passkey@test.local"})
        abody = aopts.get_json()
        allow = abody.get("allowCredentials", [])
        record("auth_options_allow_credentials",
               aopts.status_code == 200 and len(allow) == 1
               and allow[0]["id"] == b64url(auth.credential_id),
               f"allow={allow}")

        # ---- Auth verify (§7.2) ----
        # XHR 契约：返回 JSON {ok, redirect}，前端按 redirect 跳转，不再返回 302
        assertion = auth.authenticate(abody["challenge"])
        av = client.post("/api/passkey/auth/verify", json=assertion)
        av_body = av.get_json()
        record("auth_verify_login",
               av.status_code == 200 and av_body.get("ok")
               and av_body.get("redirect") == "/panel/overview",
               f"status={av.status_code} body={av_body}")
        # 会话已建立：带该会话访问控制台应放行
        with client.session_transaction() as s:
            logged_in = s.get("uid") == uid
        record("auth_verify_session_established", logged_in,
               f"uid={s.get('uid')}")
        row2 = one("SELECT sign_count FROM passkeys WHERE user_id = ?", (uid,))
        record("auth_updates_sign_count", row2["sign_count"] == auth.sign_count + 1,
               f"sign_count={row2['sign_count']}")

        # ---- Discoverable login without email (§5.5) ----
        # 无邮箱时走可发现凭证模式：不传 allowCredentials，认证器返回该 RP 下已存储的凭证，
        # verify 端点按 credential_id 定位账户，无需用户先输入邮箱。
        dopts_auth = client.post("/api/passkey/auth/options", json={})
        dbody = dopts_auth.get_json()
        record("auth_options_no_email_discoverable",
               dopts_auth.status_code == 200 and dbody.get("challenge")
               and dbody.get("allowCredentials") == [],
               f"status={dopts_auth.status_code} body={dbody}")
        disc = auth.authenticate(dbody["challenge"], sign_count=auth.sign_count + 10)
        dv = client.post("/api/passkey/auth/verify", json=disc)
        dv_body = dv.get_json()
        record("auth_verify_discoverable_login",
               dv.status_code == 200 and dv_body.get("ok")
               and dv_body.get("redirect") == "/panel/overview",
               f"status={dv.status_code} body={dv_body}")

        # ---- Replay: signCount not greater than stored → rejected (§7.2) ----
        aopts2 = client.post("/api/passkey/auth/options", json={"email": "passkey@test.local"}).get_json()
        replay = auth.authenticate(aopts2["challenge"], sign_count=auth.sign_count + 1)
        rp = client.post("/api/passkey/auth/verify", json=replay)
        record("auth_replay_signcount_rejected", rp.status_code == 400,
               f"status={rp.status_code} body={rp.get_json()}")

        # ---- Wrong origin rejected (§7.1/§7.2 origin check) ----
        aopts3 = client.post("/api/passkey/auth/options", json={"email": "passkey@test.local"}).get_json()
        bad = auth.authenticate(aopts3["challenge"], sign_count=auth.sign_count + 20,
                                origin="https://evil.example.com")
        wrong = client.post("/api/passkey/auth/verify", json=bad)
        record("auth_wrong_origin_rejected", wrong.status_code == 400,
               f"status={wrong.status_code} body={wrong.get_json()}")

        # ---- Delete with passkey verification ----
        did = row["id"]
        dopts = client.post("/api/passkey/delete/options", json={}).get_json()
        del_assertion = auth.authenticate(dopts["challenge"], sign_count=auth.sign_count + 30)
        dr = client.delete(f"/api/passkey/{did}", json={**del_assertion, "method": "passkey"})
        record("delete_with_passkey",
               dr.status_code == 200 and not one("SELECT 1 FROM passkeys WHERE id = ?", (did,)),
               f"status={dr.status_code} body={dr.get_json()}")

        # ---- Auth options for unknown email rejected ----
        unk = client.post("/api/passkey/auth/options", json={"email": "nobody@test.local"})
        record("auth_options_unknown_email", unk.status_code == 400,
               f"status={unk.status_code}")

        # ---- AAGUID → platform from registry (AAGUIDs.csv) ----
        # 表内 AAGUID 识别为对应平台；表外（全零隐私值）落 Other 已由上方注册用例覆盖。
        # 用 Microsoft Password Manager 的真实 AAGUID 复现用户报告场景。
        ms_auth = SoftwareAuthenticator(
            credential_id=b"\x22" * 32,
            aaguid=bytes.fromhex("d345266801fd4c12926c83a4204853aa"),
        )
        mopts = client.post("/api/passkey/register/options", json={}).get_json()
        mreg = client.post("/api/passkey/register/verify", json=ms_auth.register(mopts["challenge"]))
        mbody = mreg.get_json()
        record("platform_mapped_from_aaguid",
               mreg.status_code == 200
               and mbody.get("platform") == "Microsoft Password Manager"
               and mbody.get("suggested_name") == "Microsoft Password Manager-1",
               f"status={mreg.status_code} body={mbody}")

    # ---- Unauthenticated registration blocked ----
    with app.test_client() as anon:
        r = anon.post("/api/passkey/register/options", json={})
        record("register_requires_login", r.status_code == 401, f"status={r.status_code}")

    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
