"""
takeda2026rout PDF Parser & Schedule Engine
==========================================
Reads all instructor route PDFs placed inside `takeda2026rout/` (e.g.
`【講師用】2026英語(MARCH).pdf`), extracts:
  - Subject & Target University Tier (基礎徹底 / スモールステップ / 日大 / MARCH / 地方国公立 / 難関国公立 / 早慶 / 旧帝 / 東大 等)
  - Total weeks (`total_weeks`)
  - Every in-route reference book (`books`):
      * title, category, pace (`4日2日ペース` etc.)
      * start_week, end_week, duration_weeks, active_weeks
      * weekly assignment milestones (`sample_tasks`)
      * choice group flag (`※以下のN冊のうち1冊を使用`) vs companion lecture book
  - Stage breakthrough milestones (`段階突破テスト 日大レベル` 等)

Caches parsed output in `takeda_routes_cache.json` keyed by file mtime+size so
dropping or replacing PDFs in `takeda2026rout/` is detected automatically.
"""

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz

BASE_DIR = Path(__file__).resolve().parent
ROUTE_PDF_DIR = BASE_DIR / "takeda2026rout"
ROUTE_CACHE_PATH = BASE_DIR / "takeda_routes_cache.json"

SUBJECT_MAP = [
    ("理系数学", "math_rikei", "理系数学"),
    ("文系数学", "math_bunkei", "文系数学"),
    ("政治経済", "seikei", "政治・経済"),
    ("英語", "english", "英語"),
    ("現代文", "gendai", "現代文"),
    ("古文", "kobun", "古文"),
    ("物理", "physics", "物理"),
    ("化学", "chemistry", "化学"),
    ("生物", "biology", "生物"),
    ("日本史", "nihonshi", "日本史"),
    ("世界史", "sekaishi", "世界史"),
    ("地理", "chiri", "地理"),
    ("数学", "math", "数学"),
]

TIER_ORDER = [
    "スモールステップ",
    "基礎徹底",
    "日大",
    "MARCH",
    "地方国公立",
    "九大北大",
    "阪大名古屋大東北大",
    "難関国公立",
    "旧帝",
    "早慶",
    "早稲田",
    "東大",
    "東大京大科学大",
]

IGNORED_COLORED_EXACT = {
    "□", "※", "日大", "MARCH", "早慶", "東大", "早稲田",
    "日", "大", "M", "A", "R", "C", "H", "早", "慶",
    "＋", "・", "＆", "or", "政治・経済", "ハンドブック", "計算・論述",
}

COLUMN_HEADER_WORDS = {
    "問題集", "計算", "講義", "講 義", "単語", "熟語", "文法",
    "構文・解釈", "長文", "古文単語・古文常識", "文法問題集",
    "読み方本", "読解・問題集", "分野別対策", "歴史総合対策",
    "漢字・語彙", "読解", "要約", "共通テスト対策", "記述対策",
}


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u3000", " ")).strip()


def _detect_subject_and_tier(filename: str) -> Tuple[str, str, str]:
    subj_key = "other"
    subj_jp = "その他"
    for kw, sk, sjp in SUBJECT_MAP:
        if kw in filename:
            subj_key, subj_jp = sk, sjp
            break
    m_tier = re.search(r"\(([^)]+)\)", filename)
    tier_label = m_tier.group(1).strip() if m_tier else "標準"
    return subj_key, subj_jp, tier_label


def _canonical_kb_key(book_title: str) -> str:
    """Map an extracted book title to known KNOWLEDGE_BASE / simulator book IDs if applicable."""
    t = book_title.lower().replace(" ", "").replace("　", "")
    rules = [
        ("システム英単語basic", "systan_basic"),
        ("中学版システム英単語", "systan_chugaku"),
        ("システム英単語", "systan"),
        ("ターゲット1900", "target1900"),
        ("ターゲット1400", "target1400"),
        ("ターゲット1200", "target1200"),
        ("ターゲット1800", "target_chugaku1800"),
        ("leap", "leap"),
        ("鉄壁", "teppeki"),
        ("速読英単語上級", "sokutan_adv"),
        ("速読英単語必修", "sokutan_hisshu"),
        ("準1級単熟語ex", "eiken_pre1"),
        ("sparta3", "sparta3"),
        ("速読英熟語", "sokudoku_idiom"),
        ("英熟語1000", "target_idiom1000"),
        ("ターゲット1000", "target_idiom1000"),
        ("古文単語315", "kobun315"),
        ("古文単語330", "kobun330"),
        ("漢字マスター1800", "kanji1800"),
        ("語彙1700", "kokugo1700"),
        ("日本史一問一答", "nihonshi"),
        ("世界史一問一答", "sekaishi"),
        ("数学重要問題集", "math_juyo"),
        ("化学重要問題集", "chem_juyo"),
        ("物理重要問題集", "physics_juyo"),
    ]
    for pat, key in rules:
        if pat in t:
            return key
    return ""


def parse_single_route_pdf(pdf_path: Path) -> dict:
    subj_key, subj_jp, tier_label = _detect_subject_and_tier(pdf_path.name)
    doc = fitz.open(pdf_path)

    all_spans = []
    for page_idx, page in enumerate(doc):
        if page_idx > 0:
            break  # Takeda 2026 routes are 1-page master charts
        d = page.get_text("dict")
        for b in d["blocks"]:
            for line in b.get("lines", []):
                for sp in line.get("spans", []):
                    txt = _clean_text(sp["text"])
                    if not txt or sp["size"] < 1.8:
                        continue
                    if sp["color"] == 0xFCE4D6:
                        continue
                    x0, y0, x1, y1 = sp["bbox"]
                    all_spans.append({
                        "text": txt,
                        "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                        "cx": (x0 + x1) / 2.0, "cy": (y0 + y1) / 2.0,
                        "color": sp["color"],
                        "size": sp["size"],
                    })
    doc.close()

    # 1. Identify main weekly axis column (most frequent X bucket of `N週目` spans)
    raw_week_spans = []
    for sp in all_spans:
        m = re.match(r"^(\d+)週目$", sp["text"])
        if m:
            raw_week_spans.append((int(m.group(1)), sp))

    week_rows: Dict[int, float] = {}
    main_week_x_max = 80.0
    if raw_week_spans:
        x_buckets = Counter(round(sp["x0"] / 18.0) * 18.0 for _, sp in raw_week_spans)
        dominant_x = x_buckets.most_common(1)[0][0]
        main_week_spans = [
            (w, sp) for w, sp in raw_week_spans
            if abs(sp["x0"] - dominant_x) <= 22.0
        ]
        for w, sp in main_week_spans:
            if w not in week_rows or sp["cy"] < week_rows[w]:
                week_rows[w] = sp["cy"]
        main_week_x_max = max(sp["x1"] for _, sp in main_week_spans)

    sorted_weeks = sorted(week_rows.items())
    max_week = sorted_weeks[-1][0] if sorted_weeks else 0
    first_week_y = sorted_weeks[0][1] if sorted_weeks else 70.0

    def nearest_week(y: float) -> int:
        if not sorted_weeks:
            return 1
        return min(sorted_weeks, key=lambda item: abs(y - item[1]))[0]

    # 2. Extract stage breakthrough milestones (段階突破テスト / MARCHレベル過去問演習 etc.)
    milestones = []
    for sp in all_spans:
        if sp["color"] == 0 and ("段階突破" in sp["text"] or "過去問演習" in sp["text"]):
            w = nearest_week(sp["cy"])
            row_txts = [
                s["text"] for s in all_spans
                if s["color"] == 0 and abs(s["cy"] - sp["cy"]) < 6.0 and s["size"] >= 6.5 and not re.match(r"^\d+週目$", s["text"])
            ]
            label = _clean_text(" ".join(row_txts))
            if label and not any(m["week"] == w and m["label"] == label for m in milestones):
                milestones.append({"week": w, "label": label})
    milestones.sort(key=lambda m: m["week"])

    # 3. Separate choice notes ("※以下のN冊のうち1冊を使用"), column headers, and book spans
    footnote_y = min(
        (sp["y0"] for sp in all_spans if sp["text"].startswith("※複製")),
        default=9999.0,
    )

    choice_headers = []
    col_headers = []
    book_spans = []

    for sp in all_spans:
        if sp["color"] == 0:
            continue
        if sp["y0"] >= footnote_y - 5:
            continue
        txt = sp["text"]
        if txt in IGNORED_COLORED_EXACT:
            continue
        if "2026ルート" in txt or "最短ルート" in txt:
            continue
        if sp["size"] >= 8.8 and sp["y0"] < first_week_y - 10:
            col_headers.append(sp)
            continue
        # Choice banner ("※以下の3冊のうち1冊を使用") -> keep as metadata, NOT inside book title!
        if "以下の" in txt and ("使用" in txt or "選択" in txt or "1冊" in txt):
            choice_headers.append(sp)
            continue
        if txt.startswith("※") and len(txt) <= 3:
            continue
        if "ペース" in txt and sp["y0"] < first_week_y:
            col_headers.append(sp)
            continue
        if txt in COLUMN_HEADER_WORDS and (sp["y0"] < first_week_y - 12 or sp["size"] >= 7.0):
            col_headers.append(sp)
            continue
        book_spans.append(sp)

    # 4. Cluster book_spans carefully WITHOUT merging side-by-side book columns!
    book_spans.sort(key=lambda s: (round(s["y0"] / 2.0), s["x0"]))
    clusters = []
    for sp in book_spans:
        placed = False
        for cl in clusters:
            v_gap = sp["y0"] - cl["y1"]
            h_overlap = min(sp["x1"], cl["x1"]) - max(sp["x0"], cl["x0"])
            cx_diff = abs(sp["cx"] - cl["cx"])
            size_ok = abs(sp["size"] - cl["size"]) <= 1.6
            color_ok = sp["color"] == cl["color"]
            # Same line continuation ONLY if tight horizontal gap (< 5pt) and same y
            same_line = (
                abs(sp["cy"] - cl["last_cy"]) < 3.0
                and -2.0 <= (sp["x0"] - cl["x1"]) <= 5.5
                and size_ok
                and color_ok
            )
            # Multi-line stacked title: directly underneath with meaningful overlap or very tight center
            stacked = (
                -1.5 <= v_gap <= 6.5
                and (h_overlap > 2.0 or cx_diff < 8.5)
                and size_ok
                and color_ok
            )
            if same_line or stacked:
                cl["spans"].append(sp)
                cl["x0"] = min(cl["x0"], sp["x0"])
                cl["y0"] = min(cl["y0"], sp["y0"])
                cl["x1"] = max(cl["x1"], sp["x1"])
                cl["y1"] = max(cl["y1"], sp["y1"])
                cl["cx"] = (cl["x0"] + cl["x1"]) / 2.0
                cl["last_cy"] = sp["cy"]
                placed = True
                break
        if not placed:
            clusters.append({
                "spans": [sp],
                "x0": sp["x0"], "y0": sp["y0"], "x1": sp["x1"], "y1": sp["y1"],
                "cx": sp["cx"], "last_cy": sp["cy"],
                "size": sp["size"], "color": sp["color"],
            })

    # Re-merge orphan short continuation lines like "(理論化学範囲)", "改訂版", "標準レベル", "1900", "LEAP", "英単語"
    # onto the closest book cluster directly above them if needed (already handled by stacked cx_diff < 8.5,
    # expand slightly for single narrow sub-line like "英単語" or "1900" or "改訂版" just under a box).
    clean_clusters = []
    for cl in clusters:
        sps = sorted(cl["spans"], key=lambda s: (round(s["cy"] / 3.0), s["x0"]))
        title = _clean_text(" ".join(s["text"] for s in sps))
        if len(title) < 2 or title in {"改訂版", "三訂版", "四訂版", "五訂版", "新課程"}:
            # try attaching to cluster right above
            attached = False
            for prev in reversed(clean_clusters):
                if 0 <= (cl["y0"] - prev["y1"]) <= 8.0 and abs(cl["cx"] - prev["cx"]) <= 22.0:
                    prev["title"] = _clean_text(prev["title"] + " " + title)
                    prev["y1"] = max(prev["y1"], cl["y1"])
                    attached = True
                    break
            if attached:
                continue
        clean_clusters.append({**cl, "title": title})

    # Clean leading "※ " on book titles if present
    book_nodes = []
    for cl in clean_clusters:
        title = re.sub(r"^[※＊]\s*", "", cl["title"]).strip()
        # Fix tight split Japanese/ASCII like "システム 英単語" -> "システム英単語" when safe
        title = re.sub(r"([\u3040-\u30ff\u4e00-\u9faf])\s+([\u3040-\u30ff\u4e00-\u9faf])", r"\1\2", title)
        if len(title) < 2 or title in COLUMN_HEADER_WORDS:
            continue

        cat = ""
        pace = ""
        if col_headers:
            best_h = min(col_headers, key=lambda h: abs(h["cx"] - cl["cx"]))
            if abs(best_h["cx"] - cl["cx"]) < 145:
                ht = best_h["text"]
                pm = re.search(r"\(([^)]*ペース[^)]*)\)", ht)
                if pm:
                    pace = pm.group(1)
                    cat = re.sub(r"\([^)]*\)", "", ht).strip()
                else:
                    cat = ht

        choice_note = ""
        for ch in choice_headers:
            if 0 <= (cl["y0"] - ch["y1"]) <= 22.0 and (ch["x0"] - 18.0) <= cl["cx"] <= (ch["x1"] + 18.0):
                choice_note = ch["text"].lstrip("※").strip()
                break

        book_nodes.append({
            "book": title,
            "kb_key": _canonical_kb_key(title),
            "category": cat,
            "pace": pace,
            "is_choice_group": bool(choice_note),
            "choice_note": choice_note,
            "x0": cl["x0"], "x1": cl["x1"], "y0": cl["y0"], "y1": cl["y1"], "cx": cl["cx"],
        })

    # 5. Map each book node to its active weeks and weekly task pace
    task_spans = [
        sp for sp in all_spans
        if not re.match(r"^\d+週目$", sp["text"])
        and "段階突破" not in sp["text"]
        and not sp["text"].startswith("※")
        and sp["x0"] > main_week_x_max - 4
        and (first_week_y - 6) <= sp["cy"] < (footnote_y - 8)
        and sp["size"] < 9.5
        and (sp["color"] == 0 or sp["text"] == "□")
    ]

    book_nodes.sort(key=lambda b: (b["y0"], b["x0"]))
    final_books = []
    for i, bn in enumerate(book_nodes):
        # Determine lower Y bound from the next book in the same horizontal column
        y_end = footnote_y - 8
        for j in range(i + 1, len(book_nodes)):
            other = book_nodes[j]
            if other["y0"] > bn["y1"] + 2:
                if (min(bn["x1"], other["x1"]) - max(bn["x0"], other["x0"]) > -4) or abs(bn["cx"] - other["cx"]) < 14:
                    y_end = other["y0"] - 1
                    break

        # Is this book in the left companion/lecture sidebar (left of main week column)?
        is_sidebar = bn["cx"] < main_week_x_max - 4

        matched_weeks = set()
        tasks_by_week: Dict[int, List[str]] = {}
        if not is_sidebar:
            box_w = bn["x1"] - bn["x0"]
            pad_x = 12.0 if box_w < 35 else 6.0
            left_bound = bn["x0"] - pad_x
            right_bound = bn["x1"] + pad_x
            for ts in task_spans:
                if (bn["y0"] - 4) <= ts["cy"] <= y_end and left_bound <= ts["cx"] <= right_bound:
                    w = nearest_week(ts["cy"])
                    matched_weeks.add(w)
                    if ts["text"] != "□" and not (len(ts["text"]) == 1 and ts["text"] in "日大MARCH早慶東大"):
                        tasks_by_week.setdefault(w, []).append(ts["text"])

        if matched_weeks:
            sorted_w = sorted(matched_weeks)
            start_w, end_w = sorted_w[0], sorted_w[-1]
            # Pick milestone/sample tasks (first, middle, last non-empty)
            task_weeks = sorted(tasks_by_week.keys())
            sample_tasks = []
            if task_weeks:
                picks = [task_weeks[0]]
                if len(task_weeks) >= 3:
                    picks.append(task_weeks[len(task_weeks) // 2])
                if len(task_weeks) >= 2:
                    picks.append(task_weeks[-1])
                for pw in picks:
                    sample_tasks.append(f"{pw}週目: {' '.join(tasks_by_week[pw])}")
            duration_w = len(sorted_w)
            role_type = "選択候補（指定冊から1冊）" if bn["is_choice_group"] else "ルート必修"
        else:
            start_w, end_w, duration_w = None, None, 0
            sample_tasks = []
            role_type = "講義・併用サポート"

        # Assign stage tag based on start_week vs milestones
        stage_tag = tier_label
        if start_w is not None and milestones:
            for ms in milestones:
                if start_w <= ms["week"] and "日大" in ms["label"]:
                    stage_tag = "日大レベル"
                    break
                if start_w <= ms["week"] and "MARCH" in ms["label"]:
                    stage_tag = "MARCHレベル"
                    break

        final_books.append({
            "book": bn["book"],
            "kb_key": bn["kb_key"],
            "category": bn["category"] or ("講義系" if is_sidebar else "問題集・演習"),
            "pace": bn["pace"],
            "role_type": role_type,
            "is_choice_group": bn["is_choice_group"],
            "choice_note": bn["choice_note"],
            "is_companion": is_sidebar or (start_w is None),
            "start_week": start_w,
            "end_week": end_w,
            "duration_weeks": duration_w,
            "sample_tasks": sample_tasks,
            "stage_tag": stage_tag,
        })

    # Order: scheduled books by (start_week, category) then companions
    final_books.sort(key=lambda b: (1 if b["start_week"] is None else 0, b["start_week"] or 999, b["book"]))

    return {
        "file": pdf_path.name,
        "subject_key": subj_key,
        "subject_jp": subj_jp,
        "tier_label": tier_label,
        "total_weeks": max_week,
        "milestones": milestones,
        "books": final_books,
    }


def load_all_2026_routes(force_reload: bool = False) -> dict:
    """
    Scan `takeda2026rout/*.pdf` and return structured 2026 route dictionary keyed by subject_key.
    Uses `takeda_routes_cache.json` keyed by file signature to avoid re-parsing unchanged PDFs.
    """
    if not ROUTE_PDF_DIR.exists():
        return {"subjects": {}, "files_count": 0}

    pdfs = sorted(ROUTE_PDF_DIR.glob("*.pdf"))
    sig = [
        {"name": p.name, "size": p.stat().st_size, "mtime": int(p.stat().st_mtime)}
        for p in pdfs
    ]

    if not force_reload and ROUTE_CACHE_PATH.exists():
        try:
            cached = json.loads(ROUTE_CACHE_PATH.read_text(encoding="utf-8"))
            if cached.get("signature") == sig and isinstance(cached.get("subjects"), dict):
                return cached
        except Exception:
            pass

    subjects: Dict[str, dict] = {}
    for p in pdfs:
        parsed = parse_single_route_pdf(p)
        sk = parsed["subject_key"]
        if sk not in subjects:
            subjects[sk] = {"subject_key": sk, "subject_jp": parsed["subject_jp"], "tiers": {}}
        subjects[sk]["tiers"][parsed["tier_label"]] = parsed

    # Also build combined "math" entry if math_rikei / math_bunkei exist
    if "math_rikei" in subjects or "math_bunkei" in subjects:
        combined_tiers = {}
        for sub_k, prefix in [("math_bunkei", "文系・"), ("math_rikei", "理系・")]:
            for tk, tv in subjects.get(sub_k, {}).get("tiers", {}).items():
                combined_tiers[f"{prefix}{tk}"] = tv
        subjects["math"] = {"subject_key": "math", "subject_jp": "数学", "tiers": combined_tiers}

    # Sort tiers in logical progression order inside each subject
    def _tier_rank(name: str) -> Tuple[int, str]:
        for idx, kw in enumerate(TIER_ORDER):
            if kw in name:
                return (idx, name)
        return (99, name)

    for sk, sdata in subjects.items():
        ordered = dict(sorted(sdata["tiers"].items(), key=lambda kv: _tier_rank(kv[0])))
        sdata["tiers"] = ordered

    result = {
        "source_dir": str(ROUTE_PDF_DIR.name),
        "files_count": len(pdfs),
        "signature": sig,
        "subjects": subjects,
    }
    try:
        ROUTE_CACHE_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return result


def get_route_books_for_subject(subject: str, tier_hint: str = "") -> dict:
    """
    Return the best-matching route tier(s) and consolidated in-route book set + schedule
    for internal analyzer consumption.
    """
    data = load_all_2026_routes()
    subjects = data.get("subjects", {})
    # Handle math sub-selection (rikei vs bunkei)
    if subject == "math" and tier_hint == "bunkei" and "math_bunkei" in subjects:
        sdata = subjects["math_bunkei"]
    elif subject == "math" and tier_hint == "rikei" and "math_rikei" in subjects:
        sdata = subjects["math_rikei"]
    else:
        sdata = subjects.get(subject, {})

    tiers = sdata.get("tiers", {})
    if not tiers:
        return {"has_pdf_routes": False, "tiers": {}, "in_route_books": [], "in_route_kb_keys": set()}

    in_route_kb_keys = set()
    all_books_seen = {}
    for tk, tinfo in tiers.items():
        for bk in tinfo.get("books", []):
            if bk.get("kb_key"):
                in_route_kb_keys.add(bk["kb_key"])
            bname = bk["book"]
            if bname not in all_books_seen:
                all_books_seen[bname] = {**bk, "tiers": [tk]}
            else:
                if tk not in all_books_seen[bname]["tiers"]:
                    all_books_seen[bname]["tiers"].append(tk)

    return {
        "has_pdf_routes": True,
        "subject_key": subject,
        "subject_jp": sdata.get("subject_jp", subject),
        "tiers": tiers,
        "in_route_books": list(all_books_seen.values()),
        "in_route_kb_keys": in_route_kb_keys,
    }
