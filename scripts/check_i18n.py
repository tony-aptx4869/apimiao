#!/usr/bin/env python3
"""Check rendered pages and visible JS strings for bilingual consistency."""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.db as dbmod

TMP_DIR = Path(tempfile.mkdtemp(prefix="apimiao-i18n-"))
dbmod.DB_PATH = TMP_DIR / "apimiao.db"

from app import create_app
from app.data_sheets.service import create_sheet
from app.db import execute, utcnow
from config import ADMIN_ID

RESULT = []
CJK = re.compile(r"[\u4e00-\u9fff]")
PAGES = [
    "/", "/login", "/panel/overview", "/panel/logs", "/panel/api-keys",
    "/panel/api-manager", "/panel/api-manager/new", "/panel/data-sheets",
    "/panel/data-sheets/new", "/panel/trans-origin", "/panel/trans-origin/new",
    "/panel/trans-user", "/panel/trans-user/new", "/panel/profile", "/panel/users",
]
ALWAYS_ALLOWED_ENGLISH_CJK = {"汉", "语"}
ALLOWED_ENGLISH_SAMPLES = {
    "/panel/data-sheets/new": {"北京", "上海"},
}


def record(name, ok, detail=""):
    RESULT.append({"case": name, "ok": bool(ok), "detail": detail})


def visible_text(html):
    html = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style\b[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


def static_cjk_candidates():
    candidates = []
    files = list((ROOT / "static/js").glob("*.js")) + list((ROOT / "templates").glob("*.html")) + list((ROOT / "static/css").glob("*.css"))
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if not CJK.search(stripped):
                continue
            if stripped.startswith(("//", "<!--", "#", "/*", "*")):
                continue
            if any(marker in stripped for marker in (
                "T(", "ZH ?", "ZH?'", "LANG() ==", "LANG_ZH ?", "zh:",
                "{object:'对象'", "? (isPrompt ?", "zh ?", "zh?"
            )):
                continue
            if stripped.startswith("labels = ZH") and "对象" in stripped:
                continue
            if path.name == "data_sheets_form.html" and "北京" in stripped:
                continue
            if 'html[lang="zh"]' in stripped or "🇨🇳" in stripped:
                continue
            candidates.append(f"{path.relative_to(ROOT)}:{number}:{stripped[:120]}")
    return candidates


def run():
    app = create_app()
    with app.app_context():
        sheet = create_sheet(ADMIN_ID, {
            "name": "sample-sheet",
            "fields": [
                {"key": "code", "label": "code", "json_type": "number", "enabled": True, "order": 0},
                {"key": "prov", "label": "prov", "json_type": "string", "enabled": True, "order": 1},
            ],
        })
        sid = sheet["id"]
        now = utcnow()
        execute("INSERT INTO apis (id,user_id,endpoint,method,type,auth,note,config,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("sample-proxy", ADMIN_ID, "sample/proxy", "GET", "PROXY", "x-api-key,bearer", "sample note", "{}", now))
        execute("INSERT INTO trans_origins (id,user_id,note,original_url,original_method,sheet_id,created_at) VALUES (?,?,?,?,?,?,?)",
                ("sample-origin", ADMIN_ID, "sample note", "https://upstream.invalid/sample", "GET", sid, now))
        execute("INSERT INTO trans_apis (id,user_id,endpoint,method,auth,note,sheet_ids_json,joins_json,sheet_fields_json,param_mapping_json,body_mapping_json,query_config_v2,output_tree_json,timeout,retry,rate_limit,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("sample-user", ADMIN_ID, "sample/user", "GET", "x-api-key,bearer", "sample note", f'["{sid}"]', "[]", "{}", "[]", "[]", "{}", "", 10, 0, 0, now))

    with app.test_client() as client:
        with client.session_transaction() as session:
            session.clear()
            session["uid"] = ADMIN_ID
            session["role"] = "ADMIN"
        for lang in ("en", "zh"):
            client.set_cookie("apimiao_lang", lang)
            for page in PAGES:
                response = client.get(page)
                record(f"render_{lang}_{page}", response.status_code in (200, 302),
                       f"status={response.status_code}")
                if response.status_code != 200:
                    continue
                text = visible_text(response.get_data(as_text=True))
                cjk = set(CJK.findall(text))
                if lang == "en":
                    allowed = ALWAYS_ALLOWED_ENGLISH_CJK | ALLOWED_ENGLISH_SAMPLES.get(page, set())
                    unexpected = cjk - allowed
                    record(f"english_no_cjk_{page}", not unexpected,
                           f"chars={''.join(sorted(unexpected))}" if unexpected else "")

    with app.test_client() as client:
        client.get("/lang/en", headers={"Referer": "/"})
        home = visible_text(client.get("/").get_data(as_text=True))
        home_cjk = set(CJK.findall(home)) - ALWAYS_ALLOWED_ENGLISH_CJK
        record("language_switch_persists_en", not home_cjk,
               f"chars={''.join(sorted(home_cjk))}")

    candidates = static_cjk_candidates()
    record("static_visible_strings_localized", candidates == [], f"candidates={candidates[:5]}")

    print(json.dumps(RESULT, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in RESULT) else 1


if __name__ == "__main__":
    sys.exit(run())
