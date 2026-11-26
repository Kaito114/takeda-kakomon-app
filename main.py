import os
import gc
import json
import base64
import hashlib
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from analyzer_engine import analyze_past_exam_on_memory, KNOWLEDGE_BASE, get_curriculum_routes_with_db_status
from ten_year_dna_engine import (
    CONFIRMATION_TEST_BOOKS,
    MAJOR_UNIVERSITIES_10YR_DNA,
    configure_dna_supabase_sync,
    load_dna_store,
    load_accumulated_dna_records,
    upsert_accumulated_dna_profile,
    delete_accumulated_dna_profile,
    update_accumulated_dna_profile,
    analyze_10yr_university_and_concurrent,
    find_merge_candidates,
    get_metadata_suggestions,
)
from supabase_sync_engine import (
    get_admin_sync_overview,
    sync_all_from_supabase,
    restore_synced_db_from_supabase_if_newer,
)

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

AUTH_CONFIG_FILE = BASE_DIR / "auth_config.json"
INDEX_HTML_PATH = BASE_DIR / "index.html"

# Optional Supabase client for persistent auth_config backup across cloud restarts
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
CONFIG_BUCKET = "app-config"
REMOTE_AUTH_KEY = "kakomon_auth_config.json"

_sb_client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        _sb_client = None

configure_dna_supabase_sync(_sb_client, CONFIG_BUCKET)
restore_synced_db_from_supabase_if_newer(_sb_client)


def _upload_auth_to_supabase(data_bytes: bytes):
    if not _sb_client:
        return
    try:
        try:
            _sb_client.storage.create_bucket(CONFIG_BUCKET, options={"public": False})
        except Exception:
            pass
        _sb_client.storage.from_(CONFIG_BUCKET).upload(
            REMOTE_AUTH_KEY,
            data_bytes,
            {"content-type": "application/json", "upsert": "true"}
        )
    except Exception:
        pass


def _download_auth_from_supabase() -> Optional[dict]:
    if not _sb_client:
        return None
    try:
        res = _sb_client.storage.from_(CONFIG_BUCKET).download(REMOTE_AUTH_KEY)
        if res:
            return json.loads(res.decode("utf-8"))
    except Exception:
        pass
    return None


def load_auth_config() -> dict:
    default_user_id = os.getenv("APP_AUTH_ID", "takeda")
    default_user_pw = os.getenv("APP_AUTH_PASSWORD", "pass2025")
    default_admin_id = os.getenv("APP_ADMIN_ID", "admin")
    default_admin_pw = os.getenv("APP_ADMIN_PASSWORD", "admin2025")

    cfg = {}
    if AUTH_CONFIG_FILE.exists():
        try:
            cfg = json.loads(AUTH_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    else:
        remote_cfg = _download_auth_from_supabase()
        if isinstance(remote_cfg, dict):
            cfg = remote_cfg
            try:
                AUTH_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass

    user_cfg = cfg.get("user", {})
    user_u = user_cfg.get("username") or default_user_id
    user_p = user_cfg.get("password") or default_user_pw

    admin_cfg = cfg.get("admin", {})
    admin_u = admin_cfg.get("username") or default_admin_id
    admin_p = admin_cfg.get("password") or default_admin_pw

    return {
        "user": {"username": user_u, "password": user_p},
        "admin": {"username": admin_u, "password": admin_p}
    }


def save_auth_config(user_dict: dict, admin_dict: dict):
    data = {"user": user_dict, "admin": admin_dict}
    content = json.dumps(data, ensure_ascii=False, indent=2)
    AUTH_CONFIG_FILE.write_text(content, encoding="utf-8")
    _upload_auth_to_supabase(content.encode("utf-8"))


def get_token_for(role: str) -> str:
    cfg = load_auth_config()
    info = cfg.get(role, {})
    raw = f"kakomon_app_{role}::{info.get('username')}::{info.get('password')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    cfg = load_auth_config()
    for role in ["admin", "user"]:
        if token == get_token_for(role):
            return {"role": role, "username": cfg[role]["username"]}
    return None


def get_auth_token_from_request(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization") or ""
    bearer_token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else None
    return (
        request.headers.get("X-Auth-Token")
        or bearer_token
        or request.cookies.get("kakomon_app_token")
        or request.query_params.get("token")
    )


app = FastAPI(
    title="過去問×参考書 網羅率＆合格戦略アナライザー",
    description="オンメモリ完結・即時データ破棄型 大学過去問照合プラットフォーム（管理者／利用者ロール認証搭載）"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }
    if request.method == "OPTIONS":
        return JSONResponse(status_code=200, content={"status": "ok"}, headers=cors_headers)

    path = request.url.path
    # Protect all /api/* except /api/login and /api/health
    if path.startswith("/api/") and path not in ("/api/login", "/api/health"):
        token = get_auth_token_from_request(request)
        auth_info = verify_token(token)
        if not auth_info:
            return JSONResponse(
                status_code=401,
                content={"detail": "ログインが必要です。IDとパスワードを入力してください。"},
                headers=cors_headers,
            )
        admin_endpoints = [
            "/api/update-password",
            "/api/auth-config",
            "/api/admin/sync-supabase-status",
            "/api/admin/sync-supabase",
            "/api/admin/dna-profiles",
        ]
        if (path in admin_endpoints or path.startswith("/api/admin/")) and auth_info["role"] != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "この操作には管理者権限が必要です。"},
                headers=cors_headers,
            )
        request.state.auth_user = auth_info

    response = await call_next(request)
    for k, v in cors_headers.items():
        response.headers[k] = v
    return response


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordUpdateRequest(BaseModel):
    user_username: Optional[str] = None
    user_password: Optional[str] = None
    admin_username: Optional[str] = None
    admin_password: Optional[str] = None


class PdfBase64AnalyzeRequest(BaseModel):
    pdf_base64: str
    filename: str = "uploaded.pdf"
    subject: str = "auto"
    university: str = ""
    faculty: str = ""
    exam_type: str = ""
    year: str = ""


class TextAnalyzeRequest(BaseModel):
    text: str
    subject: str = "auto"
    university: str = ""
    filename: str = ""
    faculty: str = ""
    exam_type: str = ""
    year: str = ""


class ExamDnaSaveRequest(BaseModel):
    university: str
    faculty: str = "共通・標準"
    exam_type: str = "一般入試"
    year: str = "2026"
    subject: str
    exam_vector: list
    content_fingerprint: str = ""
    book_rates: dict = {}
    topics: list = []
    deviation_value: Optional[int] = None
    char_count: int = 1000
    word_count: int = 0
    problem_count: int = 2
    vector_version: int = 2
    force_save: bool = False


class AdminDnaUpdateRequest(BaseModel):
    profile_key: str
    university: Optional[str] = None
    faculty: Optional[str] = None
    exam_type: Optional[str] = None
    review_status: Optional[str] = None
    deviation_value: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if not INDEX_HTML_PATH.exists():
        return HTMLResponse("<h1>Initializing UI...</h1>")
    return HTMLResponse(
        INDEX_HTML_PATH.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/api/login")
def api_login(req: LoginRequest):
    cfg = load_auth_config()
    req_u = req.username.strip()
    req_p = req.password

    if req_u == cfg["admin"]["username"] and req_p == cfg["admin"]["password"]:
        role = "admin"
        username = cfg["admin"]["username"]
        token = get_token_for("admin")
    elif req_u == cfg["user"]["username"] and req_p == cfg["user"]["password"]:
        role = "user"
        username = cfg["user"]["username"]
        token = get_token_for("user")
    else:
        raise HTTPException(status_code=401, detail="IDまたはパスワードが正しくありません。")

    resp = JSONResponse(content={"status": "ok", "token": token, "username": username, "role": role})
    resp.set_cookie(key="kakomon_app_token", value=token, max_age=60 * 60 * 24 * 30, httponly=False, samesite="lax")
    resp.set_cookie(key="kakomon_app_role", value=role, max_age=60 * 60 * 24 * 30, httponly=False, samesite="lax")
    return resp


@app.get("/api/auth-check")
def api_auth_check(request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if auth_info:
        return {"status": "ok", "role": auth_info["role"], "username": auth_info["username"]}
    raise HTTPException(status_code=401, detail="未ログインです。")


@app.get("/api/auth-config")
def api_get_auth_config(request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    cfg = load_auth_config()
    return {
        "user_username": cfg["user"]["username"],
        "user_password": cfg["user"]["password"],
        "admin_username": cfg["admin"]["username"],
        "admin_password": cfg["admin"]["password"]
    }


@app.post("/api/update-password")
def api_update_password(req: PasswordUpdateRequest, request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")

    cfg = load_auth_config()
    user_cfg = cfg["user"]
    admin_cfg = cfg["admin"]

    if req.user_username and req.user_username.strip():
        user_cfg["username"] = req.user_username.strip()
    if req.user_password and req.user_password.strip():
        user_cfg["password"] = req.user_password.strip()

    if req.admin_username and req.admin_username.strip():
        admin_cfg["username"] = req.admin_username.strip()
    if req.admin_password and req.admin_password.strip():
        admin_cfg["password"] = req.admin_password.strip()

    save_auth_config(user_cfg, admin_cfg)
    new_admin_token = get_token_for("admin")
    resp = JSONResponse(content={
        "status": "ok",
        "token": new_admin_token,
        "username": admin_cfg["username"],
        "role": "admin",
        "user_username": user_cfg["username"],
        "admin_username": admin_cfg["username"]
    })
    resp.set_cookie(key="kakomon_app_token", value=new_admin_token, max_age=60 * 60 * 24 * 30, httponly=False, samesite="lax")
    return resp


def _current_memory_kb_stats() -> dict:
    vocab = KNOWLEDGE_BASE.get("vocab", {})
    return {
        "vocab_books": len(vocab),
        "total_vocab_entries": sum(v.get("total_unique", 0) for v in vocab.values()),
        "math_juyo_count": len(KNOWLEDGE_BASE.get("math_juyo", {}).get("rikei", [])) + len(KNOWLEDGE_BASE.get("math_juyo", {}).get("bunkei", [])),
        "chem_juyo_count": len(KNOWLEDGE_BASE.get("chem_juyo", [])),
        "history_terms_count": sum(h.get("total_terms", 0) for h in KNOWLEDGE_BASE.get("history", {}).values()),
        "confirmation_test_books_count": len(CONFIRMATION_TEST_BOOKS),
    }


@app.get("/api/admin/sync-supabase-status")
def api_admin_sync_supabase_status(request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    return get_admin_sync_overview(_sb_client, _current_memory_kb_stats())


@app.post("/api/admin/sync-supabase")
def api_admin_sync_supabase(request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    try:
        return sync_all_from_supabase(_sb_client, admin_username=auth_info.get("username", "admin"))
    except RuntimeError as re_err:
        raise HTTPException(status_code=409 if "実行中" in str(re_err) else 503, detail=str(re_err))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase再同期エラー: {str(e)}")


@app.get("/api/health")
async def health_check():
    vocab_books = len(KNOWLEDGE_BASE.get("vocab", {}))
    total_vocab = sum(v.get("total_unique", 0) for v in KNOWLEDGE_BASE.get("vocab", {}).values())
    math_cnt = len(KNOWLEDGE_BASE.get("math_juyo", {}).get("rikei", [])) + len(KNOWLEDGE_BASE.get("math_juyo", {}).get("bunkei", []))
    store = load_dna_store()
    profs = store.get("profiles", {})
    total_slots = sum(len(p.get("year_slots", {})) for p in profs.values())
    return {
        "status": "ok",
        "storage_policy": "ZERO_STORAGE_ON_MEMORY_ONLY",
        "auth_enabled": True,
        "loaded_vocab_books": vocab_books,
        "total_indexed_headwords": total_vocab,
        "indexed_math_juyo_problems": math_cnt,
        "confirmation_test_books_count": len(CONFIRMATION_TEST_BOOKS),
        "major_universities_10yr_profiles": len(MAJOR_UNIVERSITIES_10YR_DNA),
        "accumulated_measured_profiles": len(profs),
        "accumulated_year_slots": total_slots
    }


@app.get("/api/10yr-dna-list")
def get_10yr_dna_list():
    return {
        "major_universities_10yr": MAJOR_UNIVERSITIES_10YR_DNA,
        "accumulated_profiles": load_accumulated_dna_records(),
        "confirmation_books_count": len(CONFIRMATION_TEST_BOOKS)
    }


@app.get("/api/curriculum-routes")
def get_curriculum_routes_endpoint(reload: bool = False):
    data = get_curriculum_routes_with_db_status(force_reload=reload)
    return {
        "status": "ok",
        "files_count": data.get("files_count", 0),
        "source_dir": data.get("source_dir", "takeda2026rout"),
        "routes": data.get("subjects", {}),
    }


@app.post("/api/admin/reload-routes")
def admin_reload_routes(request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    data = get_curriculum_routes_with_db_status(force_reload=True)
    return {
        "status": "ok",
        "files_count": data.get("files_count", 0),
        "subjects_count": len(data.get("subjects", {})),
        "message": f"takeda2026rout 内のルートPDF（計 {data.get('files_count', 0)} 冊/ファイル）を再読込しました。",
        "routes": data.get("subjects", {}),
    }


@app.get("/api/dna-metadata-suggestions")
def dna_metadata_suggestions():
    return {
        "status": "ok",
        **get_metadata_suggestions(),
    }


@app.get("/api/admin/dna-profiles")
def admin_list_dna_profiles(request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    records = load_accumulated_dna_records()
    return {
        "status": "ok",
        "total_profiles": len(records),
        "total_year_slots": sum(p.get("sample_years_count", len(p.get("year_slots", {}))) for p in records),
        "profiles": records,
        "merge_candidates": find_merge_candidates(),
    }


@app.put("/api/admin/dna-profiles")
def admin_update_dna_profile(req: AdminDnaUpdateRequest, request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    res = update_accumulated_dna_profile(
        req.profile_key,
        {
            "university": req.university,
            "faculty": req.faculty,
            "exam_type": req.exam_type,
            "review_status": req.review_status,
            "deviation_value": req.deviation_value,
        },
    )
    if not res.get("updated"):
        raise HTTPException(status_code=404, detail=res.get("message", "更新対象が見つかりません。"))
    return {"status": "ok", **res}


@app.delete("/api/admin/dna-profiles")
def admin_delete_dna_profile(profile_key: str, request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request))
    if not auth_info or auth_info["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者権限が必要です。")
    res = delete_accumulated_dna_profile(profile_key)
    if not res.get("deleted"):
        raise HTTPException(status_code=404, detail=res.get("message", "削除対象が見つかりません。"))
    return {"status": "ok", **res}


@app.post("/api/save-exam-dna")
def save_exam_dna_endpoint(req: ExamDnaSaveRequest, request: Request):
    auth_info = getattr(request.state, "auth_user", None) or verify_token(get_auth_token_from_request(request)) or {}
    role = auth_info.get("role", "user")
    username = auth_info.get("username", "user")

    # Guard against low-quality / partial-excerpt PDFs polluting 10-year averages
    if req.char_count < 80:
        return {
            "status": "skipped_low_quality",
            "duplicate_status": "SKIPPED",
            "message": "抽出文字数が少なすぎるため（80文字未満）、DNA品質保護のため自動蓄積はスキップしました。",
        }
    if not req.force_save:
        if req.subject == "english" and 0 < req.word_count < 220 and req.problem_count <= 1:
            return {
                "status": "skipped_low_quality",
                "duplicate_status": "SKIPPED_FRAGMENT",
                "message": f"英文{req.word_count}語・大問{req.problem_count}題の抜粋データと判定されたため、10年平均傾向の汚染防止のため自動蓄積をスキップしました。",
            }
        if req.subject != "english" and req.char_count < 180 and req.problem_count <= 1:
            return {
                "status": "skipped_low_quality",
                "duplicate_status": "SKIPPED_FRAGMENT",
                "message": f"問題抜粋（{req.char_count}文字・大問{req.problem_count}題）と判定されたため、傾向平均の汚染防止のため自動蓄積をスキップしました。",
            }
    if not req.university or "未検出" in req.university or "未指定" in req.university:
        raise HTTPException(status_code=400, detail="大学名が未設定のため蓄積できません。")

    res = upsert_accumulated_dna_profile({
        "university": req.university,
        "faculty": req.faculty,
        "exam_type": req.exam_type,
        "year": req.year,
        "subject": req.subject,
        "exam_vector": req.exam_vector,
        "vector_version": req.vector_version,
        "content_fingerprint": req.content_fingerprint,
        "book_rates": req.book_rates,
        "topics": req.topics,
        "deviation_value": req.deviation_value,
        "source_role": role,
        "submitted_by": username,
        "review_status": "approved" if role == "admin" else "auto_submitted",
    })
    updated_dna = analyze_10yr_university_and_concurrent(
        req.exam_vector,
        req.university,
        req.subject,
        current_faculty=req.faculty,
        current_exam_type=req.exam_type,
        exam_level_dev=req.deviation_value,
        exam_topics=req.topics,
        vector_version=req.vector_version,
    )
    return {
        "status": "ok",
        "duplicate_status": res["duplicate_status"],
        "message": res["message"],
        "saved_profile": res["profile"],
        "total_profiles": res["total_profiles"],
        "total_year_slots": res["total_year_slots"],
        "ten_year_dna": updated_dna,
        "concurrent_universities": updated_dna["concurrent_universities"]
    }


@app.post("/api/analyze-pdf")
def analyze_pdf_endpoint(req: PdfBase64AnalyzeRequest):
    pdf_bytes = None
    try:
        raw_b64 = req.pdf_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        pdf_bytes = base64.b64decode(raw_b64)
        req.pdf_base64 = ""

        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="PDFファイルが空です。")

        result = analyze_past_exam_on_memory(
            pdf_bytes=pdf_bytes,
            subject=req.subject,
            university=req.university,
            filename=req.filename,
            faculty=req.faculty,
            exam_type=req.exam_type,
            year=req.year,
        )
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析エラー: {str(e)}")
    finally:
        pdf_bytes = None
        req.pdf_base64 = ""
        gc.collect()


@app.post("/api/analyze-text")
def analyze_text_endpoint(req: TextAnalyzeRequest):
    try:
        if not req.text or len(req.text.strip()) < 10:
            raise HTTPException(status_code=400, detail="解析するテキストを入力してください。")

        result = analyze_past_exam_on_memory(
            raw_text=req.text,
            subject=req.subject,
            university=req.university,
            filename=req.filename,
            faculty=req.faculty,
            exam_type=req.exam_type,
            year=req.year,
        )
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析エラー: {str(e)}")
    finally:
        req.text = ""
        gc.collect()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
