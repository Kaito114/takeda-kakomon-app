import os
import re
import gzip
import json
import time
import hashlib
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from analyzer_engine import KNOWLEDGE_PATH, reload_knowledge_base
from ten_year_dna_engine import CONF_BOOKS_PATH, reload_confirmation_test_books

BASE_DIR = Path(__file__).resolve().parent
SYNC_STATUS_PATH = BASE_DIR / "sync_status.json"

CONFIG_BUCKET = "app-config"
REMOTE_SYNC_STATUS_KEY = "kakomon_sync_status.json"
REMOTE_KB_GZ_KEY = "kakomon_knowledge_base_synced.json.gz"
REMOTE_CONF_BOOKS_KEY = "kakomon_conf_books_synced.json"

_SYNC_LOCK = threading.Lock()
_IS_SYNCING = False

JST = timezone(timedelta(hours=9))

# All 15 vocab books in knowledge_base.json with resilient matching rules
VOCAB_BOOK_SPECS = [
    {"key": "systan", "exact": "【[5訂版]システム英単語(黒字の訳追加ver)】", "must": ["システム英単語"], "exclude": ["Basic", "多義語", "中学"]},
    {"key": "systan_basic", "exact": "【5訂版】システム英単語Basic", "must": ["システム英単語", "Basic"], "exclude": ["中学"]},
    {"key": "target1900", "exact": "【6訂版】ターゲット1900", "must": ["ターゲット1900"], "exclude": ["熟語"]},
    {"key": "target1200", "exact": "【改訂版】英単語ターゲット1200", "must": ["ターゲット1200"], "exclude": ["熟語"]},
    {"key": "leap", "exact": "【改訂版】必携 英単語LEAP", "must": ["LEAP"], "exclude": ["Basic"]},
    {"key": "teppeki", "exact": "★[改訂版]鉄緑会東大英単語熟語 鉄壁", "must": ["鉄壁"], "exclude": []},
    {"key": "sokutan_adv", "exact": "★【改訂第5版】速読英単語 上級編", "must": ["速読英単語", "上級"], "exclude": []},
    {"key": "eiken_pre1", "exact": "★[第2版]出る順で最短合格！英検 準1級単熟語EX", "must": ["準1級", "EX"], "exclude": []},
    {"key": "sokudoku_idiom", "exact": "【改訂版】速読英熟語", "must": ["速読英熟語"], "exclude": []},
    {"key": "target_idiom1000", "exact": "【5訂版】英熟語ターゲット1000", "must": ["ターゲット1000"], "exclude": []},
    {"key": "kobun315", "exact": "【四訂版】読んで見て覚える 古文単語315", "must": ["古文単語315"], "exclude": []},
    {"key": "kokugo1700", "exact": "【国語力を伸ばす 語彙1700】", "must": ["語彙1700"], "exclude": []},
    {"key": "systan_tagigo", "exact": "★[5訂版]【多義語(minimalフレーズ)】システム英単語", "must": ["システム英単語", "多義語"], "exclude": []},
    {"key": "sparta3", "exact": "英単語 SPARTA3 mastery level 1000語", "must": ["SPARTA3"], "exclude": []},
    {"key": "systan_chugaku", "exact": "【改訂版】中学版 システム英単語", "must": ["システム英単語", "中学"], "exclude": []},
]

WORD_BOOK_KEYS = {
    "systan", "systan_basic", "target1900", "target1200",
    "leap", "teppeki", "sokutan_adv", "eiken_pre1",
    "systan_tagigo", "sparta3", "systan_chugaku",
}

SPECIAL_BOOK_SPECS = [
    {"key": "math_rikei", "exact": "2026 実戦 数学重要問題集 数学I・II・III・A・B・C(理系)", "must": ["実戦", "数学重要問題集", "理系"], "exclude": []},
    {"key": "math_bunkei", "exact": "【新課程】実戦 数学重要問題集 数学I・II・A・B・C〔ベクトル〕(文系)", "must": ["実戦", "数学重要問題集", "文系"], "exclude": []},
    {"key": "chem_juyo", "exact": "2026 実戦 化学重要問題集 化学基礎・化学", "must": ["実戦", "化学重要問題集"], "exclude": []},
    {"key": "nihonshi", "exact": "【日本史一問一答 完全版 (3rd edition)】", "must": ["日本史一問一答"], "exclude": []},
    {"key": "sekaishi", "exact": "【世界史一問一答 完全版 (4th edition)】", "must": ["世界史一問一答"], "exclude": []},
]


def _extract_num(q_num_str) -> int:
    if not q_num_str:
        return 999999
    m = re.search(r"(\d+)", str(q_num_str))
    return int(m.group(1)) if m else 999999


def _clean_headword(text) -> str:
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"^[★☆●○◆◇\s]+", "", t)
    t = re.sub(r"[~～…\.]+$", "", t).strip()
    return t


def _extract_base_english_word(hw: str) -> str:
    s = re.split(r"[\(（➀➁➂➃①②③④¹²³\s/／\[［]", hw)[0].strip()
    return s.lower()


def _matches_spec(title: str, spec: dict) -> bool:
    if title == spec["exact"]:
        return True
    if all(m in title for m in spec["must"]) and not any(ex in title for ex in spec.get("exclude", [])):
        return True
    return False


def _resolve_best_title(title_counts: dict, spec: dict) -> Optional[str]:
    exact = spec["exact"]
    if title_counts.get(exact, 0) > 0:
        return exact
    cands = [
        (cnt, t)
        for t, cnt in title_counts.items()
        if cnt > 0 and all(m in t for m in spec["must"]) and not any(ex in t for ex in spec.get("exclude", []))
    ]
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


def _norm_book_title(s: str) -> str:
    t = str(s or "").strip()
    t = re.sub(r"[★☆【】\[\]（）\(\)\s　・!！]+", "", t)
    return t.lower()


def _infer_subject_from_book_title(title: str) -> Optional[str]:
    t = str(title or "")
    rules = [
        ("古文", ["古文", "古典文法", "古語", "マドンナ古文"]),
        ("現代文", ["現代文", "漢字", "語彙1700", "小論文", "現代文キーワード"]),
        ("日本史", ["日本史"]),
        ("世界史", ["世界史"]),
        ("地理", ["地理"]),
        ("政治・経済", ["政治・経済", "政治経済", "政経", "公共"]),
        ("物理", ["物理"]),
        ("化学", ["化学"]),
        ("生物", ["生物"]),
        ("数学", ["数学", "青チャート", "黄チャート", "白チャート", "基礎問題精講 数学", "入門問題精講 数学"]),
        ("英語", ["英語", "英単語", "英熟語", "英文法", "英文解釈", "長文", "システム英", "ターゲット", "鉄壁", "LEAP", "Vintage", "Next Stage", "スクランブル", "ポラリス", "SPARTA", "速読英", "パス単", "単熟語", "ルールズ", "Rules"]),
    ]
    for subj, kws in rules:
        if any(k in t for k in kws):
            return subj
    return None


def _atomic_write_json(path: Path, data, indent: Optional[int] = None) -> None:
    tmp_path = Path(str(path) + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp_path, path)


def _upload_to_supabase_storage(sb_client, remote_key: str, data_bytes: bytes, content_type: str) -> None:
    if not sb_client:
        return
    try:
        try:
            sb_client.storage.create_bucket(CONFIG_BUCKET, options={"public": False})
        except Exception:
            pass
        sb_client.storage.from_(CONFIG_BUCKET).upload(
            remote_key,
            data_bytes,
            {"content-type": content_type, "upsert": "true"},
        )
    except Exception:
        pass


def _download_from_supabase_storage(sb_client, remote_key: str) -> Optional[bytes]:
    if not sb_client:
        return None
    try:
        return sb_client.storage.from_(CONFIG_BUCKET).download(remote_key)
    except Exception:
        return None


def get_local_sync_status() -> dict:
    if SYNC_STATUS_PATH.exists():
        try:
            return json.loads(SYNC_STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def get_admin_sync_overview(sb_client, mem_stats: dict) -> dict:
    status = get_local_sync_status()
    return {
        "supabase_connected": sb_client is not None,
        "is_syncing": _IS_SYNCING,
        "last_synced_at": status.get("synced_at"),
        "last_synced_at_jst": status.get("synced_at_jst"),
        "last_synced_by": status.get("synced_by"),
        "last_duration_sec": status.get("duration_sec"),
        "last_total_rows_scanned": status.get("total_rows_scanned"),
        "last_updated_book_counts": status.get("updated_book_counts", 0),
        "last_added_books": status.get("added_books", []),
        "last_warnings": status.get("warnings", []),
        "current_memory": mem_stats,
    }


def _build_vocab_entry(key: str, title: str, rows: list) -> dict:
    seen = {}
    is_word_book = key in WORD_BOOK_KEYS
    for r in rows:
        hw = _clean_headword(r.get("question_text", ""))
        if not hw:
            continue
        hw_low = hw.lower()
        num = _extract_num(r.get("q_number", ""))
        chap = r.get("chapter") or ""
        ans = r.get("ref_answer") or ""
        entry_obj = {
            "word": hw,
            "num": num,
            "q_number": r.get("q_number") or "",
            "chapter": chap,
            "meaning": str(ans)[:80] if ans else "",
        }
        if hw_low not in seen or num < seen[hw_low]["num"]:
            seen[hw_low] = entry_obj
        if is_word_book:
            base_w = _extract_base_english_word(hw)
            if base_w and len(base_w) >= 2 and re.match(r"^[a-z\-]+$", base_w):
                if base_w not in seen or num < seen[base_w]["num"]:
                    seen[base_w] = entry_obj
    return {
        "title": title,
        "total_unique": len(seen),
        "entries": seen,
    }


def _build_math_juyo_list(rows: list, existing_list: list) -> list:
    existing_by_qnum = {int(item["q_num"]): item for item in (existing_list or []) if "q_num" in item}
    items = []
    for r in rows:
        q_num = _extract_num(r.get("q_number", ""))
        if q_num == 999999:
            continue
        q_txt = str(r.get("question_text") or "").strip()
        m_theme = re.search(r"[〈<](.+?)[〉>]\s*[〉>]?$", q_txt)
        theme = m_theme.group(1).strip() if m_theme else q_txt
        prev = existing_by_qnum.get(q_num, {})
        obj = {
            "q_num": q_num,
            "page": prev.get("page", 1),
            "difficulty": r.get("difficulty") or prev.get("difficulty") or "A問題",
            "title": theme or prev.get("title") or "",
            "raw_text": theme or prev.get("raw_text") or q_txt,
        }
        if "crop_box" in prev:
            obj["crop_box"] = prev["crop_box"]
        items.append(obj)
    items.sort(key=lambda x: x["q_num"])
    return items


def _build_chem_juyo_list(rows: list) -> list:
    items = []
    for r in rows:
        items.append({
            "q_number": r.get("q_number") or "",
            "num": _extract_num(r.get("q_number", "")),
            "chapter": r.get("chapter") or "",
            "difficulty": r.get("difficulty") or "A問題",
        })
    items.sort(key=lambda x: x["num"])
    return items


def _build_history_entry(title: str, rows: list) -> dict:
    terms = {}
    for r in rows:
        ans = str(r.get("ref_answer") or "").strip()
        if not ans:
            continue
        parts = re.split(r"[、，,／/・\s]+", ans)
        diff = r.get("difficulty") or "B"
        q_num = r.get("q_number") or ""
        ctx = str(r.get("question_text") or "")[:50]
        for p in parts:
            p_clean = re.sub(r"[\(\)（）「」『』\[\]]", "", p).strip()
            if len(p_clean) >= 2 and p_clean not in terms:
                terms[p_clean] = {
                    "term": p_clean,
                    "difficulty": diff,
                    "q_number": q_num,
                    "context": ctx,
                }
    return {
        "title": title,
        "total_terms": len(terms),
        "terms": terms,
    }


def _sync_confirmation_books_list(existing_books: list, title_counts: dict) -> tuple[list, int, list]:
    """
    Update question counts on existing confirmation_test_books entries and auto-append
    newly added real reference books (>=25 questions) from Supabase without pulling in
    single-row unit-test tags.
    """
    norm_sb_counts = {}
    for t, c in title_counts.items():
        nt = _norm_book_title(t)
        if nt not in norm_sb_counts or c > norm_sb_counts[nt][0]:
            norm_sb_counts[nt] = (c, t)

    updated_books = []
    existing_norms = set()
    updated_count_num = 0

    for b in existing_books:
        b_copy = dict(b)
        name = b_copy.get("name", "")
        nb = _norm_book_title(name)
        existing_norms.add(nb)

        matched_cnt = None
        if name in title_counts:
            matched_cnt = title_counts[name]
        elif f"【{name}】" in title_counts:
            matched_cnt = title_counts[f"【{name}】"]
        elif nb in norm_sb_counts:
            matched_cnt = norm_sb_counts[nb][0]

        if matched_cnt is not None and matched_cnt != b_copy.get("count"):
            b_copy["count"] = matched_cnt
            updated_count_num += 1
        updated_books.append(b_copy)

    # Known variant aliases already represented under consolidated names
    known_variant_prefixes = (
        "時代と流れで覚える！世界史用語",
        "時代と流れで覚える！日本史用語",
        "青チャート",
    )
    added_books = []
    for t, cnt in sorted(title_counts.items(), key=lambda x: (-x[1], x[0])):
        if cnt < 25:
            continue
        nt = _norm_book_title(t)
        if nt in existing_norms:
            continue
        if any(vp in t and any(_norm_book_title(vp) in en for en in existing_norms) for vp in known_variant_prefixes):
            continue
        subj = _infer_subject_from_book_title(t)
        if not subj:
            continue
        bid = "sb_" + hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]
        new_entry = {
            "id": bid,
            "subject": subj,
            "name": t,
            "description": f"{subj} 確認テスト収録 / 全{cnt}問（Supabase自動同期）",
            "count": cnt,
        }
        updated_books.append(new_entry)
        existing_norms.add(nt)
        added_books.append({"name": t, "subject": subj, "count": cnt})

    return updated_books, updated_count_num, added_books


def sync_all_from_supabase(sb_client, admin_username: str = "admin") -> dict:
    """
    Admin-only full resync from Supabase 'テスト作成データ':
    - Fetches all rows in 1 streaming scan (~8s)
    - Rebuilds vocab (15 books), math_juyo, chem_juyo, history, and confirmation_test_books
    - Preserves any existing data for a book if missing on Supabase (fail-safe)
    - Reloads in-memory lookup engines immediately
    - Persists locally and uploads compressed snapshots to Supabase Storage 'app-config'
    """
    global _IS_SYNCING
    if sb_client is None:
        raise RuntimeError("Supabaseクライアントが構成されていません（環境変数 SUPABASE_URL / SUPABASE_KEY を確認してください）。")

    if not _SYNC_LOCK.acquire(blocking=False):
        raise RuntimeError("現在ほかの同期処理が実行中です。完了まで数秒お待ちください。")

    _IS_SYNCING = True
    t0 = time.time()
    try:
        all_specs = VOCAB_BOOK_SPECS + SPECIAL_BOOK_SPECS
        title_counts = {}
        rows_by_title = {}
        total_rows = 0
        offset = 0

        while True:
            res = (
                sb_client.table("テスト作成データ")
                .select("title, chapter, q_number, difficulty, question_text, ref_answer")
                .range(offset, offset + 999)
                .execute()
            )
            batch = res.data or []
            if not batch:
                break
            total_rows += len(batch)
            for row in batch:
                t = row.get("title") or ""
                if not t:
                    continue
                title_counts[t] = title_counts.get(t, 0) + 1
                if any(_matches_spec(t, sp) for sp in all_specs):
                    rows_by_title.setdefault(t, []).append(row)
            if len(batch) < 1000:
                break
            offset += 1000

        if total_rows == 0:
            raise RuntimeError("Supabaseの『テスト作成データ』からデータを1件も取得できませんでした。同期を中止しました。")

        # Load existing raw files as fail-safe fallback
        prev_kb = {}
        if Path(KNOWLEDGE_PATH).exists():
            try:
                prev_kb = json.loads(Path(KNOWLEDGE_PATH).read_text(encoding="utf-8"))
            except Exception:
                prev_kb = {}

        prev_conf_books = []
        if Path(CONF_BOOKS_PATH).exists():
            try:
                prev_conf_books = json.loads(Path(CONF_BOOKS_PATH).read_text(encoding="utf-8"))
            except Exception:
                prev_conf_books = []

        warnings = []

        # 1. Build vocab
        new_vocab = {}
        prev_vocab = prev_kb.get("vocab", {})
        for spec in VOCAB_BOOK_SPECS:
            key = spec["key"]
            resolved_title = _resolve_best_title(title_counts, spec)
            if resolved_title and rows_by_title.get(resolved_title):
                new_vocab[key] = _build_vocab_entry(key, resolved_title, rows_by_title[resolved_title])
            elif key in prev_vocab:
                new_vocab[key] = prev_vocab[key]
                warnings.append(f"書籍『{spec['exact']}』がSupabase上で見つからなかったため既存データを保持しました。")

        # 2. Build math_juyo
        prev_math = prev_kb.get("math_juyo", {"rikei": [], "bunkei": []})
        new_math = {}
        for sub_key, spec_key in [("rikei", "math_rikei"), ("bunkei", "math_bunkei")]:
            spec = next(s for s in SPECIAL_BOOK_SPECS if s["key"] == spec_key)
            resolved_title = _resolve_best_title(title_counts, spec)
            if resolved_title and rows_by_title.get(resolved_title):
                parsed = _build_math_juyo_list(rows_by_title[resolved_title], prev_math.get(sub_key, []))
                if parsed:
                    new_math[sub_key] = parsed
                else:
                    new_math[sub_key] = prev_math.get(sub_key, [])
                    warnings.append(f"数学重要問題集({sub_key})のパース結果が空だったため既存データを保持しました。")
            else:
                new_math[sub_key] = prev_math.get(sub_key, [])
                warnings.append(f"書籍『{spec['exact']}』がSupabase上で見つからなかったため既存データを保持しました。")

        # 3. Build chem_juyo
        chem_spec = next(s for s in SPECIAL_BOOK_SPECS if s["key"] == "chem_juyo")
        chem_title = _resolve_best_title(title_counts, chem_spec)
        if chem_title and rows_by_title.get(chem_title):
            new_chem = _build_chem_juyo_list(rows_by_title[chem_title])
        else:
            new_chem = prev_kb.get("chem_juyo", [])
            warnings.append(f"書籍『{chem_spec['exact']}』がSupabase上で見つからなかったため既存データを保持しました。")

        # 4. Build history
        prev_hist = prev_kb.get("history", {})
        new_history = {}
        for h_key in ("nihonshi", "sekaishi"):
            h_spec = next(s for s in SPECIAL_BOOK_SPECS if s["key"] == h_key)
            h_title = _resolve_best_title(title_counts, h_spec)
            if h_title and rows_by_title.get(h_title):
                new_history[h_key] = _build_history_entry(h_title, rows_by_title[h_title])
            elif h_key in prev_hist:
                new_history[h_key] = prev_hist[h_key]
                warnings.append(f"書籍『{h_spec['exact']}』がSupabase上で見つからなかったため既存データを保持しました。")

        raw_kb = {
            "vocab": new_vocab,
            "math_juyo": new_math,
            "chem_juyo": new_chem,
            "history": new_history,
        }

        # 5. Sync confirmation_test_books
        new_conf_books, updated_book_counts, added_books = _sync_confirmation_books_list(prev_conf_books, title_counts)

        # 6. Persist raw files locally (atomically) BEFORE updating memory
        _atomic_write_json(Path(KNOWLEDGE_PATH), raw_kb)
        _atomic_write_json(Path(CONF_BOOKS_PATH), new_conf_books, indent=2)

        # 7. Hot-reload in-memory engines
        mem_stats = reload_knowledge_base(raw_kb)
        conf_count = reload_confirmation_test_books(new_conf_books)
        mem_stats["confirmation_test_books_count"] = conf_count

        now_utc = datetime.now(timezone.utc)
        now_jst = now_utc.astimezone(JST)
        duration_sec = round(time.time() - t0, 2)

        status_payload = {
            "synced_at": now_utc.isoformat(),
            "synced_at_jst": now_jst.strftime("%Y-%m-%d %H:%M:%S JST"),
            "synced_by": admin_username,
            "duration_sec": duration_sec,
            "total_rows_scanned": total_rows,
            "total_titles_in_supabase": len(title_counts),
            "vocab_books_count": mem_stats["vocab_books"],
            "total_vocab_entries": mem_stats["total_vocab_entries"],
            "math_juyo_count": mem_stats["math_juyo_count"],
            "chem_juyo_count": mem_stats["chem_juyo_count"],
            "history_terms_count": mem_stats["history_terms_count"],
            "confirmation_books_count": conf_count,
            "updated_book_counts": updated_book_counts,
            "added_books": added_books,
            "warnings": warnings,
        }
        _atomic_write_json(SYNC_STATUS_PATH, status_payload, indent=2)

        # 8. Upload snapshots to Supabase Storage for cloud restart persistence
        kb_gz_bytes = gzip.compress(json.dumps(raw_kb, ensure_ascii=False).encode("utf-8"), compresslevel=6)
        conf_bytes = json.dumps(new_conf_books, ensure_ascii=False).encode("utf-8")
        status_bytes = json.dumps(status_payload, ensure_ascii=False).encode("utf-8")
        _upload_to_supabase_storage(sb_client, REMOTE_KB_GZ_KEY, kb_gz_bytes, "application/gzip")
        _upload_to_supabase_storage(sb_client, REMOTE_CONF_BOOKS_KEY, conf_bytes, "application/json")
        _upload_to_supabase_storage(sb_client, REMOTE_SYNC_STATUS_KEY, status_bytes, "application/json")

        return {
            "status": "ok",
            "message": f"Supabase『テスト作成データ』（全{total_rows:,}行 / {len(title_counts)}タイトル）から参考書DBを再同期しました。",
            "sync_report": status_payload,
            "current_memory": mem_stats,
        }
    finally:
        _IS_SYNCING = False
        _SYNC_LOCK.release()


def restore_synced_db_from_supabase_if_newer(sb_client) -> bool:
    """
    On startup (especially after cloud container restarts), check if Supabase Storage
    holds a newer admin-synced snapshot than local files, and restore it automatically.
    """
    if not sb_client:
        return False
    try:
        remote_status_bytes = _download_from_supabase_storage(sb_client, REMOTE_SYNC_STATUS_KEY)
        if not remote_status_bytes:
            return False
        remote_status = json.loads(remote_status_bytes.decode("utf-8"))
        remote_ts = remote_status.get("synced_at") or ""
        local_status = get_local_sync_status()
        local_ts = local_status.get("synced_at") or ""

        if local_ts and local_ts >= remote_ts:
            return False

        kb_gz = _download_from_supabase_storage(sb_client, REMOTE_KB_GZ_KEY)
        conf_bytes = _download_from_supabase_storage(sb_client, REMOTE_CONF_BOOKS_KEY)
        if not kb_gz:
            return False

        raw_kb = json.loads(gzip.decompress(kb_gz).decode("utf-8"))
        _atomic_write_json(Path(KNOWLEDGE_PATH), raw_kb)
        reload_knowledge_base(raw_kb)

        if conf_bytes:
            conf_books = json.loads(conf_bytes.decode("utf-8"))
            if isinstance(conf_books, list) and conf_books:
                _atomic_write_json(Path(CONF_BOOKS_PATH), conf_books, indent=2)
                reload_confirmation_test_books(conf_books)

        _atomic_write_json(SYNC_STATUS_PATH, remote_status, indent=2)
        return True
    except Exception:
        return False
