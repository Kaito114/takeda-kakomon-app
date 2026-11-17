import os
import re
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(r'c:\Users\fujimoto.k\Desktop\kakomon-app\.env')
sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY'))

def fetch_book_records(title):
    rows = []
    offset = 0
    while True:
        res = sb.table('テスト作成データ').select('chapter, section, q_number, difficulty, question_text, ref_answer').eq('title', title).range(offset, offset+999).execute()
        if not res.data:
            break
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        offset += 1000
    return rows

def extract_num(q_num_str):
    if not q_num_str:
        return 999999
    m = re.search(r'(\d+)', str(q_num_str))
    return int(m.group(1)) if m else 999999

def clean_headword(text):
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r'^[★☆●○◆◇\s]+', '', t)
    t = re.sub(r'[~～…\.]+$', '', t).strip()
    return t

def extract_base_english_word(hw):
    """
    'respond(respond to A)' -> 'respond'
    'reduce②(reduce A to B)' -> 'reduce'
    'lie¹' -> 'lie'
    """
    s = re.split(r'[\(（➀➁➂➃①②③④¹²³\s/／\[［]', hw)[0].strip()
    return s.lower()

def build_vocab_db():
    vocab_books = {
        "systan": "【[5訂版]システム英単語(黒字の訳追加ver)】",
        "systan_basic": "【5訂版】システム英単語Basic",
        "target1900": "【6訂版】ターゲット1900",
        "target1200": "【改訂版】英単語ターゲット1200",
        "leap": "【改訂版】必携 英単語LEAP",
        "teppeki": "★[改訂版]鉄緑会東大英単語熟語 鉄壁",
        "sokutan_adv": "★【改訂第5版】速読英単語 上級編",
        "eiken_pre1": "★[第2版]出る順で最短合格！英検 準1級単熟語EX",
        "sokudoku_idiom": "【改訂版】速読英熟語",
        "target_idiom1000": "【5訂版】英熟語ターゲット1000",
        "kobun315": "【四訂版】読んで見て覚える 古文単語315",
        "kokugo1700": "【国語力を伸ばす 語彙1700】"
    }

    res_tag = sb.table('テスト作成データ').select('title').like('title', '%多義語%システム英単語%').limit(1).execute()
    if res_tag.data:
        vocab_books["systan_tagigo"] = res_tag.data[0]['title']

    kb_vocab = {}
    for key, title in vocab_books.items():
        print(f"Fetching {key}: {title} ...")
        rows = fetch_book_records(title)
        seen = {}
        is_word_book = key in ("systan", "systan_basic", "target1900", "target1200", "leap", "teppeki", "sokutan_adv", "eiken_pre1", "systan_tagigo")
        for r in rows:
            hw = clean_headword(r.get('question_text', ''))
            if not hw:
                continue
            hw_low = hw.lower()
            num = extract_num(r.get('q_number', ''))
            chap = r.get('chapter', '')
            ans = r.get('ref_answer', '')
            entry_obj = {
                "word": hw,
                "num": num,
                "q_number": r.get('q_number', ''),
                "chapter": chap,
                "meaning": str(ans)[:80] if ans else ""
            }
            if hw_low not in seen or num < seen[hw_low]['num']:
                seen[hw_low] = entry_obj
            if is_word_book:
                base_w = extract_base_english_word(hw)
                if base_w and len(base_w) >= 2 and re.match(r'^[a-z\-]+$', base_w):
                    if base_w not in seen or num < seen[base_w]['num']:
                        seen[base_w] = entry_obj

        kb_vocab[key] = {
            "title": title,
            "total_unique": len(seen),
            "entries": seen
        }
        print(f"  Unique keys for {key}: {len(seen)}")

    return kb_vocab

def build_math_juyo_db():
    math_juyo = {"rikei": [], "bunkei": []}
    rikei_path = r'c:\Users\fujimoto.k\Desktop\math-app\scratch\rikei_final_300.json'
    bunkei_path = r'c:\Users\fujimoto.k\Desktop\math-app\scratch\bunkei_final_209.json'
    if os.path.exists(rikei_path):
        with open(rikei_path, 'r', encoding='utf-8') as f:
            math_juyo["rikei"] = json.load(f)
    if os.path.exists(bunkei_path):
        with open(bunkei_path, 'r', encoding='utf-8') as f:
            math_juyo["bunkei"] = json.load(f)
    return math_juyo

def build_chem_juyo_db():
    rows = fetch_book_records("2026 実戦 化学重要問題集 化学基礎・化学")
    items = []
    for r in rows:
        items.append({
            "q_number": r.get('q_number', ''),
            "num": extract_num(r.get('q_number', '')),
            "chapter": r.get('chapter', ''),
            "difficulty": r.get('difficulty', 'A問題')
        })
    items.sort(key=lambda x: x['num'])
    return items

def build_history_db():
    hist = {}
    for key, title in [("nihonshi", "【日本史一問一答 完全版 (3rd edition)】"), ("sekaishi", "【世界史一問一答 完全版 (4th edition)】")]:
        rows = fetch_book_records(title)
        terms = {}
        for r in rows:
            ans = str(r.get('ref_answer') or '').strip()
            if not ans:
                continue
            parts = re.split(r'[、，,／/・\s]+', ans)
            diff = r.get('difficulty') or 'B'
            q_num = r.get('q_number') or ''
            for p in parts:
                p_clean = re.sub(r'[\(\)（）「」『』\[\]]', '', p).strip()
                if len(p_clean) >= 2 and p_clean not in terms:
                    terms[p_clean] = {
                        "term": p_clean,
                        "difficulty": diff,
                        "q_number": q_num,
                        "context": str(r.get('question_text') or '')[:50]
                    }
        hist[key] = {
            "title": title,
            "total_terms": len(terms),
            "terms": terms
        }
    return hist

if __name__ == '__main__':
    kb = {
        "vocab": build_vocab_db(),
        "math_juyo": build_math_juyo_db(),
        "chem_juyo": build_chem_juyo_db(),
        "history": build_history_db()
    }
    out_path = r'c:\Users\fujimoto.k\Desktop\kakomon-app\knowledge_base.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(kb, f, ensure_ascii=False)
    print(f"Saved knowledge_base.json ({os.path.getsize(out_path) / 1024 / 1024:.2f} MB)")
