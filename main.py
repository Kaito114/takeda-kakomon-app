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

from analyzer_engine import analyze_past_exam_on_memory, KNOWLEDGE_BASE
from ten_year_dna_engine import (
    CONFIRMATION_TEST_BOOKS,
    MAJOR_UNIVERSITIES_10YR_DNA,
    configure_dna_supabase_sync,
    load_dna_store,
    load_accumulated_dna_records,
    upsert_accumulated_dna_profile,
    analyze_10yr_university_and_concurrent,
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
        admin_endpoints = ["/api/update-password", "/api/auth-config"]
        if path in admin_endpoints and auth_info["role"] != "admin":
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


class TextAnalyzeRequest(BaseModel):
    text: str
    subject: str = "auto"
    university: str = ""
    filename: str = ""


class ExamDnaSaveRequest(BaseModel):
    university: str
    faculty: str = "共通・標準"
    exam_type: str = "一般・標準"
    year: str = "2026"
    subject: str
    exam_vector: list
    content_fingerprint: str = ""
    book_rates: dict = {}
    topics: list = []
    deviation_value: Optional[int] = None


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    if not INDEX_HTML_PATH.exists():
        return HTMLResponse("<h1>Initializing UI...</h1>")
    return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))


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


@app.post("/api/save-exam-dna")
def save_exam_dna_endpoint(req: ExamDnaSaveRequest):
    res = upsert_accumulated_dna_profile({
        "university": req.university,
        "faculty": req.faculty,
        "exam_type": req.exam_type,
        "year": req.year,
        "subject": req.subject,
        "exam_vector": req.exam_vector,
        "content_fingerprint": req.content_fingerprint,
        "book_rates": req.book_rates,
        "topics": req.topics,
        "deviation_value": req.deviation_value
    })
    updated_dna = analyze_10yr_university_and_concurrent(
        req.exam_vector,
        req.university,
        req.subject,
        current_faculty=req.faculty,
        current_exam_type=req.exam_type
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
            filename=req.filename
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
            filename=req.filename
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
