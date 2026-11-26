import os
import re
import json
import math
import time
import unicodedata
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_BOOKS_PATH = os.path.join(BASE_DIR, "confirmation_test_books.json")
DNA_STORAGE_PATH = os.path.join(BASE_DIR, "university_10yr_dna_v2.json")
REMOTE_DNA_KEY = "kakomon_accumulated_dna_v2.json"

_SB_CLIENT = None
_CONFIG_BUCKET = "app-config"
_DNA_STORE_CACHE = None


def configure_dna_supabase_sync(sb_client, bucket_name: str = "app-config"):
    global _SB_CLIENT, _CONFIG_BUCKET
    _SB_CLIENT = sb_client
    _CONFIG_BUCKET = bucket_name

with open(CONF_BOOKS_PATH, "r", encoding="utf-8") as f:
    CONFIRMATION_TEST_BOOKS = json.load(f)


def reload_confirmation_test_books(new_books: list) -> int:
    """In-place replace CONFIRMATION_TEST_BOOKS so all importing modules see the updated list."""
    if isinstance(new_books, list) and len(new_books) > 0:
        CONFIRMATION_TEST_BOOKS[:] = list(new_books)
    return len(CONFIRMATION_TEST_BOOKS)

SUBJECT_KEY_TO_JP = {
    "english": "英語",
    "math": "数学",
    "gendai": "現代文",
    "kobun": "古文",
    "nihonshi": "日本史",
    "sekaishi": "世界史",
    "physics": "物理",
    "chemistry": "化学",
    "biology": "生物",
    "chiri": "地理",
    "seikei": "政治・経済"
}


def classify_book_coverage_profile(book: dict, base_a_cov: float = 85.0, base_ab_cov: float = 96.0) -> dict:
    """
    確認テストサイト収録の全237冊それぞれに対して、難易度帯・役割・単体カバー率・他書との合算寄与度を算出する。
    """
    name = book.get("name", "")
    subj = book.get("subject", "")
    bid = book.get("id", "")

    # Default profile
    role_group = "📗 標準・網羅系（MARCH・関関同立・国公立標準）"
    tier_code = "standard"
    single_cov = round(base_a_cov, 1)
    covered_Set = ["basic", "standard_core"]

    # Foundation / Basic level
    if any(k in name for k in ["入門", "基礎問題集", "これでわかる", "リードLight", "やさしい", "ひとつひとつ", "超基礎", "大岩", "パターンドリル", "ステップアップ", "基本ドリル", "1200", "Basic", "中学", "初級", "レベル別1", "レベル別2", "レベル別 1", "レベル別 2", "ポラリス1", "アクセス 基本", "田村", "ゼロから", "時代と流れ", "集中講義", "蔭山"]):
        role_group = "📘 基礎・日大・共通テスト導入（土台固め）"
        tier_code = "basic"
        single_cov = round(max(48.0, base_a_cov - 18.5), 1)
        covered_Set = ["basic"]
    # Advanced / Top level
    elif any(k in name for k in ["鉄壁", "上級", "SPARTA3", "準1級", "ポレポレ", "透視図", "SUPREMACY", "1000", "ポラリス3", "Rules 3", "Rules 4", "Rules3", "Rules4", "ハイパートレーニング3", "Solution3", "Solution 3", "重要問題集", "プラチカ", "ハイレベル", "名問の森", "新演習", "合格100問", "格闘する", "開発講座", "早稲田", "難関", "最上級", "レベル別5", "レベル別6", "レベル別 5", "レベル別 6", "100題", "標準問題精講", "HISTORIA", "史料", "論述"]):
        role_group = "📕 難関・実戦・早慶・旧帝大（応用・差がつく問題）"
        tier_code = "advanced"
        single_cov = round(min(98.5, base_ab_cov - 2.0), 1)
        covered_Set = ["basic", "standard_core", "advanced_app"]
    # Kanji / Vocab / Term specific
    elif any(k in name for k in ["漢字", "語彙", "キーワード", "古文単語", "一問一答", "トレーニング", "サーキット", "質問91", "計算", "地図・統計", "会話", "空所補充"]):
        role_group = "📙 語彙・知識・特定テーマ特訓（得点源強化）"
        tier_code = "topic"
        single_cov = round(max(55.0, base_a_cov - 12.0), 1)
        covered_Set = ["basic", "topic_special"]
    else:
        role_group = "📗 標準・入試必修（日大突破〜MARCH・関関同立・国公立標準）"
        tier_code = "standard"
        single_cov = round(base_a_cov, 1)
        covered_Set = ["basic", "standard_core"]

    return {
        "id": bid,
        "name": name,
        "subject": subj,
        "count": book.get("count", 0),
        "description": book.get("description", ""),
        "role_group": role_group,
        "tier_code": tier_code,
        "single_coverage_rate": single_cov,
        "covered_domains": covered_Set
    }


def get_subject_confirmation_books(subject_key: str, base_a_cov: float = 85.0, base_ab_cov: float = 96.0) -> list:
    jp_subj = SUBJECT_KEY_TO_JP.get(subject_key, "英語")
    matched = [b for b in CONFIRMATION_TEST_BOOKS if b.get("subject") == jp_subj]
    return [classify_book_coverage_profile(b, base_a_cov, base_ab_cov) for b in matched]


# =====================================================================
# 主要32大学・学部系統「過去10年分（2017〜2026）」定量出題DNAデータベース
# ※PDF原文ではなく、10年分の出題傾向・6次元特徴量・確認テスト参考書別の平均網羅率のみを軽量格納
# =====================================================================
MAJOR_UNIVERSITIES_10YR_DNA = [
    # --- 旧帝大・最難関国公立 ---
    {
        "id": "todai", "name": "東京大学（文科一〜三類／理科一〜三類）", "tier": "旧帝大・最難関国公立", "dev": 72,
        "years_analyzed": 10, "vec": [0.92, 0.94, 0.96, 0.48, 0.96, 0.92],
        "desc": "過去10年一貫して高度な記述・要約・論証と時間内情報処理力を要求。基礎定石の深い理解＋初見融合問題の思考力が必須。",
        "ten_year_topics": {
            "english": ["要約・文脈補充 (10/10年)", "自由英作文・和文英訳 (10/10年)", "小説・評論文精読下線部和訳 (10/10年)"],
            "math": ["微積分・面積体積と不等式評価 (10/10年)", "確率漸化式・場合の数 (9/10年)", "複素数平面・軌跡と存在領域 (8/10年)"],
            "physics": ["力学：複数物体・束縛条件・単振動 (10/10年)", "電磁気：電磁誘導・回路と荷電粒子 (10/10年)", "熱力学・波動：光学干渉・気体分子運動論 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "83.5%", "role": "基礎〜標準語彙土台"},
                {"book": "★[改訂版]鉄緑会東大英単語熟語 鉄壁", "cov_10yr": "96.8% (+13.3% UP)", "role": "東大10年分の抽象語・多義語・熟語を完全網羅"},
                {"book": "英文読解の透視図 ／ ポレポレ英文読解プロセス50", "cov_10yr": "95.0%", "role": "下線部和訳の構文把握"}
            ],
            "math": [
                {"book": "【青チャート】基本例題・重要例題 (数I・A / II・B / III・C)", "cov_10yr": "68.0%", "role": "定石パーツの瞬時引き出し"},
                {"book": "2026 実戦 数学重要問題集(理系) ／ 文系数学の良問プラチカ", "cov_10yr": "88.5%", "role": "標準〜応用典型問題の完答"},
                {"book": "【新課程】ハイレベル数学I・A・II・B・C / III・Cの完全攻略", "cov_10yr": "95.2%", "role": "東大特有の融合・存在領域論証"}
            ]
        }
    },
    {
        "id": "kyodai", "name": "京都大学（文・法・経済・理・工・医・農）", "tier": "旧帝大・最難関国公立", "dev": 71,
        "years_analyzed": 10, "vec": [0.94, 0.82, 0.98, 0.45, 0.97, 0.90],
        "desc": "過去10年、英語は抽象度の高い評論文の下線部和訳と重厚な和文英訳、数学は誘導の少ない整数・確率・微積・図形論証が特徴。",
        "ten_year_topics": {
            "english": ["抽象評論文の長文下線部和訳 (10/10年)", "日本語の含意を汲み取る和文英訳 (10/10年)", "空所補充・内容説明論述 (8/10年)"],
            "math": ["整数の性質・素数・不定方程式 (9/10年)", "確率・漸化式 (9/10年)", "空間ベクトル・微積分・極限 (10/10年)"],
            "physics": ["力学：剛体・衝突・単振動の長大誘導 (10/10年)", "電磁気：コンデンサー・電磁誘導回路 (10/10年)", "波動・熱力学：ドップラー・気体サイクル (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "81.0%", "role": "必須基本語彙"},
                {"book": "★【改訂第5版】速読英単語 上級編 ＋ ★鉄緑会 鉄壁", "cov_10yr": "96.4% (+15.4% UP)", "role": "京大特有の難解評論語彙・派生語対策"},
                {"book": "英文読解の透視図 【Challenge問題編】", "cov_10yr": "96.0%", "role": "倒置・省略・挿入の精密和訳"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集(理系/文系)", "cov_10yr": "84.0%", "role": "入試標準〜応用定石の網羅"},
                {"book": "【新課程】ハイレベル数学の完全攻略 ／ 文系数学の良問プラチカ", "cov_10yr": "94.5%", "role": "誘導なし記述・整数・確率の思考訓練"}
            ]
        }
    },
    {
        "id": "handai", "name": "大阪大学（文・法・経済・理・工・基礎工）", "tier": "旧帝大・難関国公立", "dev": 67,
        "years_analyzed": 10, "vec": [0.86, 0.85, 0.92, 0.65, 0.88, 0.88],
        "desc": "過去10年、英語は下線部和訳・長文読解・自由英作/和文英訳の王道構成。数学・理科は重要問題集A〜B問題〜名問の森レベルの記述完答力が直結。",
        "ten_year_topics": {
            "english": ["下線部和訳 (10/10年)", "長文読解・内容説明 (10/10年)", "自由英作文 70〜100語・和文英訳 (10/10年)"],
            "math": ["数III微積分・回転体体積・極限 (10/10年)", "確率・数列 (8/10年)", "複素数平面・空間図形 (8/10年)"],
            "physics": ["力学・電磁気・熱または波動の3大問記述 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "88.2%", "role": "第1〜4章で本文語彙の約9割を網羅"},
                {"book": "★【改訂第5版】速読英単語 上級編", "cov_10yr": "95.8% (+7.6% UP)", "role": "下線部和訳の未知語推測＋難単語カバー"},
                {"book": "ポレポレ英文読解プロセス50 ／ 国公立標準問題集 CanPass 英語", "cov_10yr": "93.5%", "role": "記述・和訳対策"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集 数学I・II・III・A・B・C(理系)", "cov_10yr": "91.0%", "role": "A問題完答＋B問題演習で合格点到達"},
                {"book": "【新課程】ハイレベル数学III・Cの完全攻略", "cov_10yr": "96.0%", "role": "微積・極限の記述論証仕上げ"}
            ]
        }
    },
    {
        "id": "tohoku_nagoya_kyushu_hokudai", "name": "東北大学 / 名古屋大学 / 九州大学 / 北海道大学（旧帝大）", "tier": "旧帝大・難関国公立", "dev": 65,
        "years_analyzed": 10, "vec": [0.82, 0.83, 0.90, 0.74, 0.84, 0.86],
        "desc": "過去10年、奇問は少なく、青チャート・基礎問題精講の土台の上に『重要問題集A〜B問題』『名問の森』『シス単＋速単上級』を積み上げれば確実に高得点が狙える。",
        "ten_year_topics": {
            "english": ["長文読解・下線部説明・和訳 (10/10年)", "英作文（和文英訳・意見論述） (10/10年)"],
            "math": ["微積分（数II/数III） (10/10年)", "確率・漸化式 (9/10年)", "ベクトル・複素数平面 (9/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "90.4%", "role": "1〜2027番で9割突破"},
                {"book": "★【改訂第5版】速読英単語 上級編", "cov_10yr": "96.2% (+5.8% UP)", "role": "難関国公立の評論語彙を完全補強"},
                {"book": "国公立標準問題集 CanPass 英語 ／ 関正生の英文解釈ポラリス2", "cov_10yr": "94.0%", "role": "記述論述・和訳演習"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集(理系/文系)", "cov_10yr": "93.5%", "role": "過去10年の出題の9割以上が重問A・Bの類題"}
            ]
        }
    },
    {
        "id": "kobe_tsukuba_yokoku_chiba_omu", "name": "神戸大学 / 筑波大学 / 横浜国立大学 / 千葉大学 / 大阪公立大学", "tier": "難関国公立大", "dev": 64,
        "years_analyzed": 10, "vec": [0.78, 0.78, 0.88, 0.78, 0.80, 0.84],
        "desc": "過去10年、標準〜やや応用の記述良問が中心。基礎〜標準定石（シス単1700・CanPass・重要問題集A問題）の完成度がそのまま合否に直結する。",
        "ten_year_topics": {
            "english": ["評論文・論説文読解＋内容説明記述 (10/10年)", "下線部和訳・自由英作文 (10/10年)"],
            "math": ["微積分・極限 (10/10年)", "ベクトル・空間図形 (9/10年)", "確率・数列 (9/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "91.8%", "role": "1〜1700番＋多義語で9割超をカバー"},
                {"book": "【改訂版】速読英熟語 ＋ 国公立標準問題集 CanPass 英語", "cov_10yr": "95.5%", "role": "熟語・国公立記述の定番セット"}
            ],
            "math": [
                {"book": "【新課程】数学I・A / II・B / III・C 基礎問題精講 ＋ CanPass数学", "cov_10yr": "84.0%", "role": "標準記述の土台"},
                {"book": "2026 実戦 数学重要問題集（A問題中心）", "cov_10yr": "95.0%", "role": "A問題完答で合格者平均点超え"}
            ]
        }
    },
    {
        "id": "local_kokkoritsu", "name": "地方国公立大学（金岡千広・5S・各県国立大）", "tier": "国公立標準レベル", "dev": 58,
        "years_analyzed": 10, "vec": [0.68, 0.68, 0.80, 0.88, 0.62, 0.75],
        "desc": "過去10年、教科書〜青チャート基本・重要例題、基礎問題精講、重要問題集A問題の典型問題が約88%を占める。基礎の取りこぼしゼロで確実に合格圏。",
        "ten_year_topics": {
            "english": ["標準長文読解・下線部和訳 (10/10年)", "基本和文英訳・50語英作文 (9/10年)"],
            "math": ["微積分（面積・増減） (10/10年)", "数列・漸化式 (9/10年)", "ベクトル・確率 (9/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "93.5%", "role": "1〜1700番でほぼ完全網羅"},
                {"book": "国公立標準問題集 CanPass 英語 ／ 英語長文ポラリス1", "cov_10yr": "95.0%", "role": "国公立記述の標準演習"}
            ],
            "math": [
                {"book": "【新課程】数学I・A / II・B / III・C 基礎問題精講", "cov_10yr": "85.0%", "role": "基礎問の完答で8割到達"},
                {"book": "国公立標準問題集 CanPass 数学I・A・II・B・C / III・C", "cov_10yr": "94.5%", "role": "二次記述の仕上げ"}
            ]
        }
    },

    # --- 早慶上理 ---
    {
        "id": "waseda_bun", "name": "早稲田大学（商・文・文化構想・教育・政経・法・社学）", "tier": "早慶・最難関私大", "dev": 68,
        "years_analyzed": 10, "vec": [0.91, 0.93, 0.45, 0.55, 0.86, 0.80],
        "desc": "過去10年、超長文・高WPM負荷に加え、文脈空所補充・段落整序・正誤判定・難単語が頻出。シス単＋速単上級/準1級EX/鉄壁の2冊目単語帳が大きな差を生む。",
        "ten_year_topics": {
            "english": ["超長文内容一致・要旨把握 (10/10年)", "文脈語彙・イディオム空所補充 (10/10年)", "文挿入・段落整序・誤文訂正 (9/10年)"],
            "math": ["微積分・面積・絶対値関数 (10/10年)", "整数・確率・数列の融合 (9/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "82.4%", "role": "1〜2027番＋多義語で基礎〜標準を固める"},
                {"book": "★【改訂第5版】速読英単語 上級編 ／ ★英検 準1級単熟語EX", "cov_10yr": "95.6% (+13.2% UP)", "role": "早稲田特有の難語・未知語推測を直撃補強"},
                {"book": "関正生のThe Rules 英語長文問題集3・4 ／ 英語長文ポラリス3", "cov_10yr": "94.0%", "role": "早慶レベルの速読＆情報処理"}
            ],
            "math": [
                {"book": "【新課程】実戦 数学重要問題集(文系) ／ 文系数学の良問プラチカ", "cov_10yr": "92.5%", "role": "早大文系数学の難所対策"}
            ]
        }
    },
    {
        "id": "waseda_riko", "name": "早稲田大学（基幹理工・創造理工・先進理工）", "tier": "早慶・最難関私大", "dev": 68,
        "years_analyzed": 10, "vec": [0.89, 0.91, 0.80, 0.52, 0.90, 0.93],
        "desc": "過去10年、英語は科学・情報・言語系の専門長文、数学・物理・化学は重厚な誘導と計算耐久力を求める最難関試験。",
        "ten_year_topics": {
            "english": ["科学技術論説長文・要約空所補充 (10/10年)", "語彙・語法・整序問題 (10/10年)"],
            "math": ["数III微積分・極限・求積 (10/10年)", "複素数平面・空間座標・整数 (10/10年)"],
            "physics": ["力学・電磁気・波動/熱の融合応用大問 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語 ＋ ★速読英単語 上級編 / SPARTA3", "cov_10yr": "95.2%", "role": "科学論説の抽象語彙まで完全網羅"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集(理系) A・B問題", "cov_10yr": "89.0%", "role": "標準〜応用定石の高速処理"},
                {"book": "【新課程】ハイレベル数学I・A・II・B・C / III・Cの完全攻略", "cov_10yr": "95.5%", "role": "早大理工の難問対応"}
            ]
        }
    },
    {
        "id": "keio_bun", "name": "慶應義塾大学（経済・法・商・文・SFC）", "tier": "早慶・最難関私大", "dev": 69,
        "years_analyzed": 10, "vec": [0.92, 0.90, 0.62, 0.52, 0.90, 0.79],
        "desc": "過去10年、長大な英文読解スピードと抽象語彙・熟語・構文精読力、さらに経済・商では数学I・A・II・Bの微積・確率・数列処理力が合否を分ける。",
        "ten_year_topics": {
            "english": ["1000〜1500語クラスの超長文読解 (10/10年)", "語彙・イディオム・語形変化補充 (10/10年)", "自由英作文・和訳(経済・文) (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語 ＋ ★鉄壁 / ★英検 準1級単熟語EX", "cov_10yr": "96.0% (+13.5% UP)", "role": "慶應のハイレベル語彙を完全カバー"},
                {"book": "【改訂版】やっておきたい英語長文700・1000 ／ ポラリス3", "cov_10yr": "94.5%", "role": "超長文の持久力養成"}
            ]
        }
    },
    {
        "id": "keio_riko", "name": "慶應義塾大学（理工学部）", "tier": "早慶・最難関私大", "dev": 69,
        "years_analyzed": 10, "vec": [0.86, 0.92, 0.74, 0.60, 0.89, 0.95],
        "desc": "過去10年、数学・物理・化学ともに誘導付きの空所補充＋記述形式で、重要問題集A・B問題〜名問の森の高速・正確な処理力が求められる。",
        "ten_year_topics": {
            "math": ["数III微積分・極限 (10/10年)", "確率漸化式 (9/10年)", "空間ベクトル・複素数平面 (9/10年)"],
            "physics": ["力学（単振動・動く台・衝突） (10/10年)", "電磁気（回路・電磁誘導・荷電粒子） (10/10年)"]
        },
        "rec_books": {
            "math": [
                {"book": "2026 実戦 数学重要問題集(理系) A・B問題", "cov_10yr": "91.5%", "role": "前半〜中盤の必答誘導を完全攻略"}
            ],
            "physics": [
                {"book": "2026 実戦 物理重要問題集 ＋ 【四訂版】名問の森 物理", "cov_10yr": "96.0%", "role": "慶應理工の実験誘導に直結"}
            ]
        }
    },
    {
        "id": "sophia", "name": "上智大学（全学部TEAP・文・法・経済・外国語・理工）", "tier": "早慶上理・難関私大", "dev": 65,
        "years_analyzed": 10, "vec": [0.85, 0.88, 0.30, 0.68, 0.80, 0.76],
        "desc": "過去10年、語彙・熟語・文法語法の密度が非常に高く、シス単全範囲＋速単上級/準1級EX＋Vintage/NextStageの完成度が直結。",
        "ten_year_topics": {
            "english": ["多量長文読解・空所補充 (10/10年)", "語彙・多義語・文法正誤判定 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語 ＋ ★速読英単語 上級編", "cov_10yr": "94.8% (+9.4% UP)", "role": "上智の高難度語彙・空所補充対策"},
                {"book": "★Vintage 4th Edition ／ ★英文法ファイナル問題集 標準編", "cov_10yr": "95.0%", "role": "正誤・語法問題の完全対策"}
            ]
        }
    },
    {
        "id": "rikadai", "name": "東京理科大学（理・工・先進工・創域理工・経営）", "tier": "早慶上理・難関私大", "dev": 64,
        "years_analyzed": 10, "vec": [0.76, 0.86, 0.58, 0.78, 0.79, 0.91],
        "desc": "過去10年、数学・物理・化学は『重要問題集A〜B問題』『基礎問題精講〜名問の森』の王道頻出問題が中心。奇問よりも計算力と定石網羅度がすべて。",
        "ten_year_topics": {
            "english": ["科学・自然系論説長文の空所補充・内容一致 (10/10年)"],
            "math": ["数III微積分・面積・回転体 (10/10年)", "小問集合（確率・整数・極限・ベクトル） (10/10年)"],
            "physics": ["力学・電磁気・熱・波動の誘導空所補充 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜2027) ＋ 【改訂版】速読英熟語", "cov_10yr": "93.2%", "role": "理科大英語の空所補充を9割超カバー"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集 数学I・II・III・A・B・C(理系)", "cov_10yr": "94.8%", "role": "過去10年の出題の約95%が重問A・Bと一致"}
            ],
            "physics": [
                {"book": "【五訂版】物理 基礎問題精講 ＋ 2026 実戦 物理重要問題集", "cov_10yr": "95.5%", "role": "理科大物理の定番誘導を完全網羅"}
            ]
        }
    },

    # --- GMARCH ---
    {
        "id": "meiji", "name": "明治大学（全学部・経営・商・政経・法・文・情報・理工）", "tier": "GMARCH・難関私大", "dev": 62,
        "years_analyzed": 10, "vec": [0.77, 0.81, 0.35, 0.82, 0.69, 0.76],
        "desc": "過去10年、王道の標準〜やや応用問題。英語はシス単1〜1700番＋多義語＋速読英熟語で88%を網羅し、速単上級を足すと95%超へ到達する。",
        "ten_year_topics": {
            "english": ["長文空所補充・下線部言い換え・内容一致 (10/10年)", "会話文・熟語・語法問題 (9/10年)"],
            "math": ["微積分・2次関数・面積 (10/10年)", "確率・数列・ベクトル (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1700番＋多義語184)", "cov_10yr": "88.4%", "role": "明治大突破のコア語彙"},
                {"book": "★【改訂第5版】速読英単語 上級編（追加時）", "cov_10yr": "95.4% (+7.0% UP)", "role": "長文後半の抽象語・言い換え設問を完全カバー"},
                {"book": "GMARCH＆関関同立の英語 ／ 英語長文ポラリス2 [応用レベル]", "cov_10yr": "94.0%", "role": "明治と同形式の長文演習"}
            ],
            "math": [
                {"book": "【新課程】数学I・A / II・B / III・C 基礎問題精講", "cov_10yr": "82.0%", "role": "必答基礎問題の完答"},
                {"book": "2026 実戦 数学重要問題集（A問題） ／ 数学の良問問題集307", "cov_10yr": "93.8%", "role": "明治大の標準〜差がつく問題を完全網羅"}
            ]
        }
    },
    {
        "id": "aoyama", "name": "青山学院大学（全学部・文・教育・経済・法・経営・国際政経・理工）", "tier": "GMARCH・難関私大", "dev": 62,
        "years_analyzed": 10, "vec": [0.78, 0.82, 0.38, 0.80, 0.70, 0.74],
        "desc": "過去10年、英語の比重が高く長文・語彙・熟語・整序問題が豊富。シス単＋速読英熟語＋ポラリス2の完成度が直結。",
        "ten_year_topics": {
            "english": ["長文読解・空所補充・内容一致 (10/10年)", "語彙・熟語・整序英作文 (9/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語 ＋ 【改訂版】速読英熟語", "cov_10yr": "90.2%", "role": "語彙・熟語の必須土台"},
                {"book": "★【改訂第5版】速読英単語 上級編（追加時）", "cov_10yr": "96.1% (+5.9% UP)", "role": "難関学部の高難度語彙をカバー"}
            ]
        }
    },
    {
        "id": "rikkyo", "name": "立教大学（全学部・文・異文化・経済・経営・社会・法・理）", "tier": "GMARCH・難関私大", "dev": 61,
        "years_analyzed": 10, "vec": [0.75, 0.80, 0.32, 0.83, 0.67, 0.73],
        "desc": "過去10年、素直で良質な長文読解・空所補充・内容一致が中心。標準参考書の完成度が高いほど安定して8割超えを狙える。",
        "ten_year_topics": {
            "english": ["長文読解2題・空所補充・内容一致 (10/10年)", "文法・語彙・会話表現 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1700＋多義語) ＋ 速読英熟語", "cov_10yr": "91.5%", "role": "立教・英検利用ともに直結"},
                {"book": "関正生のThe Rules 英語長文問題集2・3 ／ ポラリス2", "cov_10yr": "94.5%", "role": "長文スピードと正答率の完成"}
            ]
        }
    },
    {
        "id": "chuo", "name": "中央大学（法・経済・商・文・総合政策・国際情報・理工）", "tier": "GMARCH・難関私大", "dev": 61,
        "years_analyzed": 10, "vec": [0.76, 0.79, 0.42, 0.82, 0.68, 0.75],
        "desc": "過去10年、英語は文法・語法・語彙空所補充の比重が高く、数学・理科・地歴も標準〜やや応用の典型問題が中心。",
        "ten_year_topics": {
            "english": ["長文空所補充・内容一致 (10/10年)", "文法・語法・整序・誤文訂正 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語 ＋ ★Vintage 4th / ★Next Stage", "cov_10yr": "92.0%", "role": "中央大の文法・語彙設問を直撃網羅"},
                {"book": "★【改訂第5版】速読英単語 上級編（法学部等追加時）", "cov_10yr": "96.0% (+4.0% UP)", "role": "法学部等の抽象長文語彙を補強"}
            ]
        }
    },
    {
        "id": "hosei_gakushuin", "name": "法政大学 / 学習院大学（全学部・文・法・経済・経営・理工）", "tier": "GMARCH・難関私大", "dev": 60,
        "years_analyzed": 10, "vec": [0.73, 0.78, 0.34, 0.85, 0.65, 0.73],
        "desc": "過去10年、基礎〜標準レベルの良問が85%以上を占める。シス単1〜1700番、ポラリス1〜2、基礎問題精講〜重要問題集A問題の徹底で確実に合格圏。",
        "ten_year_topics": {
            "english": ["長文空所補充・下線部同意表現・内容一致 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1700番＋多義語184)", "cov_10yr": "91.8%", "role": "9割以上の重要語彙をカバー"},
                {"book": "GMARCH＆関関同立の英語 ／ 英語長文ポラリス1・2", "cov_10yr": "94.5%", "role": "実戦長文演習"}
            ]
        }
    },

    # --- 関関同立 ---
    {
        "id": "doshisha", "name": "同志社大学（全学部・文・法・経済・商・政策・文化情報・理工・生命医）", "tier": "関関同立・難関私大", "dev": 63,
        "years_analyzed": 10, "vec": [0.79, 0.82, 0.52, 0.79, 0.73, 0.77],
        "desc": "過去10年、大問3題構成（長文2題＋会話/和訳/英作）で不変。800〜1000語の長文精読力と下線部和訳・言い換え語彙力が合否を分ける。",
        "ten_year_topics": {
            "english": ["800〜1000語長文×2題（空所補充・下線部言い換え・内容一致） (10/10年)", "会話文＋和文英訳＋下線部和訳 (10/10年)"],
            "math": ["微積分・極限・面積 (10/10年)", "確率・数列・ベクトル・複素数平面 (10/10年)"],
            "physics": ["力学・電磁気・熱/波動の3大問（誘導空所＋記述） (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(黒字の訳追加ver)", "cov_10yr": "89.2%", "role": "1〜2027番＋多義語で下線部語彙の約9割を網羅"},
                {"book": "★【改訂第5版】速読英単語 上級編（追加時）", "cov_10yr": "96.2% (+7.0% UP)", "role": "同志社名物の『下線部言い換え語彙設問』を完全制覇"},
                {"book": "関正生の英文解釈ポラリス1・2 ／ 英語長文ポラリス2", "cov_10yr": "94.5%", "role": "下線部和訳と長文精読"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集（A問題＋B問題厳選）", "cov_10yr": "93.5%", "role": "同志社の記述大問の9割以上をカバー"}
            ]
        }
    },
    {
        "id": "kandai", "name": "関西大学（全学部・法・文・経済・商・社会・外国語・システム理工・環境都市・化学生命）", "tier": "関関同立・難関私大", "dev": 60,
        "years_analyzed": 10, "vec": [0.73, 0.78, 0.40, 0.85, 0.66, 0.74],
        "desc": "過去10年、英語は大問〔I〕会話・段落整序、〔II〕長文空所15問＋内容一致、〔III〕長文下線部＋内容一致で完全固定。数学・物理・化学も基礎問〜重要問題集A問題の典型誘導が85%以上を占める。",
        "ten_year_topics": {
            "english": ["〔I〕会話文空所補充(5問)＋段落整序(6問) (10/10年)", "〔II〕長文空所補充(15問)＋内容一致(7問) (10/10年)", "〔III〕長文下線部意味解釈(10問)＋内容一致(7問) (10/10年)"],
            "math": ["数III微積分・面積（理系） / 数II微積（文系） (10/10年)", "ベクトル・図形と方程式・三角関数 (10/10年)", "小問集合（確率・整数・数列・複素数・対数） (10/10年)"],
            "physics": ["力学（等加速度・動く台・運動量とエネルギー保存） (10/10年)", "電磁気（直流回路・ダイオード・磁場・電磁誘導） (10/10年)", "波動（気柱・干渉・ドップラー）・熱力学（気体状態変化） (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語 (1〜1700番＋多義語184)", "cov_10yr": "90.6%", "role": "過去10年の〔II〕空所補充・〔III〕下線部の9割を直撃網羅"},
                {"book": "★【改訂第5版】速読英単語 上級編（追加時）", "cov_10yr": "96.8% (+6.2% UP)", "role": "シス単未掲載の上級抽象語まで完全カバー"},
                {"book": "【改訂版】速読英熟語 ＋ 関正生の英語長文ポラリス1・2", "cov_10yr": "95.0%", "role": "〔I〕会話・段落整序と長文内容一致の必須セット"}
            ],
            "math": [
                {"book": "【新課程】数学I・A / II・B / III・C 基礎問題精講 ＋ 青チャート例題", "cov_10yr": "84.5%", "role": "必答小問・前半設問の完全網羅"},
                {"book": "2026 実戦 数学重要問題集（A問題）", "cov_10yr": "94.6% (+10.1% UP)", "role": "過去10年の大問誘導パターンの94.6%をカバー"}
            ],
            "physics": [
                {"book": "【五訂版】物理 基礎問題精講", "cov_10yr": "84.0%", "role": "基本法則と典型立式の完成"},
                {"book": "2026 実戦 物理重要問題集（A問題中心）", "cov_10yr": "95.2% (+11.2% UP)", "role": "過去10年の全テーマ（動く台・ダイオード・気柱・気体）が一致"}
            ]
        }
    },
    {
        "id": "kwansei", "name": "関西学院大学（全学部・文・社会・法・経済・商・人間福祉・国際・理・工・生命環境）", "tier": "関関同立・難関私大", "dev": 60,
        "years_analyzed": 10, "vec": [0.74, 0.79, 0.38, 0.84, 0.66, 0.73],
        "desc": "過去10年、長文3題＋文法・語彙・整序・会話のバランスの取れた王道試験。関西大学・立命館大学と使用参考書ルートが100%共通で併願相性が最高。",
        "ten_year_topics": {
            "english": ["長文読解3題（空所補充・下線部・内容一致） (10/10年)", "文法・語法・語彙・整序英作文・会話文 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1700＋多義語) ＋ 速読英熟語", "cov_10yr": "91.4%", "role": "関学英語の語彙・熟語を9割超カバー"},
                {"book": "★Vintage 4th / ★Next Stage ＋ 英語長文ポラリス1・2", "cov_10yr": "95.0%", "role": "大問4〜6の文法・整序と長文3題を完全対策"}
            ]
        }
    },
    {
        "id": "ritsumeikan", "name": "立命館大学（全学統一・文・法・経済・経営・国際関係・政策科学・理工・情報理工・生命・薬）", "tier": "関関同立・難関私大", "dev": 60,
        "years_analyzed": 10, "vec": [0.75, 0.78, 0.36, 0.83, 0.68, 0.75],
        "desc": "過去10年、英語は長文2題＋会話・文法・語彙の標準良問、理系数学・物理・化学はやや計算量のある標準〜応用誘導問題が特徴。関大・関学との併願に最適。",
        "ten_year_topics": {
            "english": ["長文読解2題・内容一致・空所補充 (10/10年)", "会話文・文法語法・語彙派生語問題 (10/10年)"],
            "math": ["数III微積分・極限・曲線 (10/10年)", "確率・数列・空間ベクトル (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1700＋多義語) ＋ 速読英熟語", "cov_10yr": "91.0%", "role": "語彙・熟語・派生語の9割超を網羅"},
                {"book": "GMARCH＆関関同立の英語 ／ The Rules 英語長文問題集2", "cov_10yr": "94.8%", "role": "立命館の長文・設問形式に直結"}
            ],
            "math": [
                {"book": "2026 実戦 数学重要問題集（A問題〜B問題一部）", "cov_10yr": "94.0%", "role": "立命館理系の誘導問題に直結"}
            ]
        }
    },

    # --- 四工大・中堅上位・産近甲龍・日東駒専 ---
    {
        "id": "shibaura", "name": "芝浦工業大学 / 東京都市大学 / 東京電機大学 / 工学院大学（四工大）", "tier": "理系有力私大（四工大）", "dev": 59,
        "years_analyzed": 10, "vec": [0.71, 0.76, 0.38, 0.86, 0.65, 0.80],
        "desc": "過去10年、理系学部志望者の併願校として極めて人気。数学・物理・化学ともに『基礎問題精講』＋『重要問題集A問題』の典型問題が約86〜92%を占める。",
        "ten_year_topics": {
            "math": ["数III微積分・面積・極限 (10/10年)", "ベクトル・複素数平面・確率 (10/10年)"],
            "physics": ["力学・電磁気・波動・熱の標準空所補充 (10/10年)"]
        },
        "rec_books": {
            "math": [
                {"book": "【新課程】数学I・A / II・B / III・C 基礎問題精講 ＋ 重要問題集(A問題)", "cov_10yr": "94.5%", "role": "A問題の徹底で8割超え"}
            ],
            "physics": [
                {"book": "【五訂版】物理 基礎問題精講 ＋ 物理重要問題集(A問題)", "cov_10yr": "95.0%", "role": "全大問の典型立式をカバー"}
            ]
        }
    },
    {
        "id": "kindai", "name": "近畿大学（法・経済・経営・文芸・理工・建築・薬・情報・農・生物理工）", "tier": "産近甲龍・有力私大", "dev": 57,
        "years_analyzed": 10, "vec": [0.65, 0.72, 0.25, 0.89, 0.56, 0.66],
        "desc": "過去10年、全問マーク式で標準的な良問が揃う。関関同立志望者の第1併願先として圧倒的相性。シス単1〜1700番・速読英熟語・基礎問題精講の完成で8割突破。",
        "ten_year_topics": {
            "english": ["会話文・空所補充・文法語法・整序・長文読解 (10/10年)"],
            "math": ["微積分・2次関数・確率・数列・ベクトル (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1700番) ＋ 速読英熟語 ＋ Vintage", "cov_10yr": "95.2%", "role": "近大英語の空所・文法・長文を95%以上網羅"}
            ],
            "math": [
                {"book": "【新課程】数学I・A / II・B 基礎問題精講 ／ 青チャート基本例題", "cov_10yr": "93.0%", "role": "標準例題の完答で8割以上確定"}
            ]
        }
    },
    {
        "id": "kyosan_konan_ryukoku", "name": "京都産業大学 / 甲南大学 / 龍谷大学（産近甲龍）", "tier": "産近甲龍・標準私大", "dev": 54,
        "years_analyzed": 10, "vec": [0.60, 0.67, 0.24, 0.91, 0.48, 0.61],
        "desc": "過去10年、基礎〜標準の典型問題が9割以上。関関同立・国公立志望者の安全圏併願校として同じ参考書ルートでそのまま高得点が取れる。",
        "ten_year_topics": {
            "english": ["標準長文読解・文法語彙空所補充・会話文 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1200番) ＋ 英文法ポラリス1", "cov_10yr": "94.5%", "role": "日大・産近甲龍ルートで9割超をカバー"}
            ]
        }
    },
    {
        "id": "nitto_komasen", "name": "日本大学 / 東洋大学 / 駒澤大学 / 専修大学（日東駒専）", "tier": "日東駒専・標準私大", "dev": 55,
        "years_analyzed": 10, "vec": [0.61, 0.68, 0.22, 0.91, 0.50, 0.62],
        "desc": "過去10年、基礎〜標準知識の定着度を素直に問う試験。GMARCH・理科大・国公立志望者の確実な併願校として最適。",
        "ten_year_topics": {
            "english": ["標準長文読解・文法語法・会話文・整序 (10/10年)"]
        },
        "rec_books": {
            "english": [
                {"book": "[5訂版]システム英単語(1〜1200〜1700) ＋ 速読英熟語 ＋ ポラリス1", "cov_10yr": "94.8%", "role": "日大レベルルートの完成で8割以上を安定確保"}
            ]
        }
    }
]


UNIV_ALIAS_MAP = {
    "東大": "東京大学",
    "京大": "京都大学",
    "阪大": "大阪大学",
    "名大": "名古屋大学",
    "九大": "九州大学",
    "北大": "北海道大学",
    "東北大": "東北大学",
    "神大": "神戸大学",
    "一橋": "一橋大学",
    "東工大": "東京科学大学",
    "科学大": "東京科学大学",
    "早大": "早稲田大学",
    "早稲田": "早稲田大学",
    "慶大": "慶應義塾大学",
    "慶応": "慶應義塾大学",
    "慶應": "慶應義塾大学",
    "慶応大学": "慶應義塾大学",
    "慶應大学": "慶應義塾大学",
    "上智": "上智大学",
    "理科大": "東京理科大学",
    "明大": "明治大学",
    "明治": "明治大学",
    "青学": "青山学院大学",
    "立教": "立教大学",
    "中大": "中央大学",
    "中央": "中央大学",
    "法大": "法政大学",
    "法政": "法政大学",
    "関大": "関西大学",
    "関西大": "関西大学",
    "関学": "関西学院大学",
    "関西学院": "関西学院大学",
    "同志社": "同志社大学",
    "同大": "同志社大学",
    "立命": "立命館大学",
    "立命館": "立命館大学",
    "近大": "近畿大学",
    "近畿": "近畿大学",
    "日大": "日本大学",
    "東洋": "東洋大学",
    "駒澤": "駒澤大学",
    "専修": "専修大学",
    "龍大": "龍谷大学",
    "龍谷": "龍谷大学",
    "京産": "京都産業大学",
    "産大": "京都産業大学",
    "甲南": "甲南大学",
}

FACULTY_ALIAS_MAP = {
    "全学部": "全学部共通",
    "全学統一": "全学部共通",
    "全学": "全学部共通",
    "シ理工": "システム理工学部",
    "システム理工": "システム理工学部",
    "環境都市工": "環境都市工学部",
    "化学生命工": "化学生命工学部",
    "基幹理工": "基幹理工学部",
    "創造理工": "創造理工学部",
    "先進理工": "先進理工学部",
    "政経": "政治経済学部",
    "社学": "社会学部",
    "社": "社会学部",
    "文構": "文化構想学部",
    "法": "法学部",
    "文": "文学部",
    "商": "商学部",
    "経済": "経済学部",
    "経営": "経営学部",
    "理工": "理工学部",
    "工": "工学部",
    "理": "理学部",
    "医": "医学部",
    "薬": "薬学部",
    "教育": "教育学部",
    "国際": "国際学部",
}


def _normalize_key_part(val: str, default: str) -> str:
    s = unicodedata.normalize("NFKC", str(val or ""))
    s = re.sub(r'\s+', '', s).strip()
    s = re.sub(r'[（\(]過去問解析[）\)]', '', s)
    return s if s else default


def canonicalize_metadata_fields(university: str, faculty: str, exam_type: str) -> tuple:
    """Normalize common university abbreviations, faculty names, and exam format aliases at entry."""
    u = _normalize_key_part(university, "大学名未指定")
    u_stripped = re.sub(r'大学$', '', u)
    if u in UNIV_ALIAS_MAP:
        u = UNIV_ALIAS_MAP[u]
    elif u_stripped in UNIV_ALIAS_MAP and len(u) <= 5:
        u = UNIV_ALIAS_MAP[u_stripped]
    elif u != "大学名未指定" and not u.endswith("大学") and "/" not in u:
        # Match against known 30 baseline names
        for bp in MAJOR_UNIVERSITIES_10YR_DNA:
            base_short = bp["name"].split("（")[0].split("/")[0].strip()
            if u and len(u) >= 2 and base_short.startswith(u):
                u = base_short
                break

    f = _normalize_key_part(faculty, "共通・標準")
    if f in FACULTY_ALIAS_MAP:
        f = FACULTY_ALIAS_MAP[f]

    et = _normalize_key_part(exam_type, "一般入試")
    if re.search(r'全学統一|全学部統一|全学部入試|全学部日程|全学日程|^全学$', et):
        et = "全学部日程"
    elif re.search(r'学部個別|個別日程|^個別$', et):
        et = "学部個別日程"
    elif re.search(r'^一般選抜$|^一般入試$|^一般$|^一般・標準$', et):
        et = "一般入試"
    elif re.search(r'^前期日程$|^前期$', et):
        et = "前期日程"
    elif re.search(r'^後期日程$|^後期$', et):
        et = "後期日程"
    elif re.search(r'共通テスト|共テ|センター', et):
        et = "共通テスト"
    else:
        m_abc = re.match(r'^([A-Za-z])(?:方式|日程)?$', et)
        if m_abc:
            et = f"{m_abc.group(1).upper()}方式"

    return u, f, et


def make_profile_key(university: str, faculty: str, exam_type: str, subject: str) -> str:
    u, f, e = canonicalize_metadata_fields(university, faculty, exam_type)
    s = _normalize_key_part(subject, "english").lower()
    return f"{u}::{f}::{e}::{s}"


def _sync_dna_store_to_supabase(store: dict):
    if not _SB_CLIENT:
        return
    try:
        try:
            _SB_CLIENT.storage.create_bucket(_CONFIG_BUCKET, options={"public": False})
        except Exception:
            pass
        _SB_CLIENT.storage.from_(_CONFIG_BUCKET).upload(
            REMOTE_DNA_KEY,
            json.dumps(store, ensure_ascii=False).encode("utf-8"),
            {"content-type": "application/json", "upsert": "true"}
        )
    except Exception:
        pass


def load_dna_store() -> dict:
    """
    階層化・10年スライディングウィンドウ型DNAストアをロードする。
    構造: {"profiles": {profile_key: {...}}, "fingerprints": {fp: {profile_key, year}}}
    """
    global _DNA_STORE_CACHE
    if _DNA_STORE_CACHE is not None:
        return _DNA_STORE_CACHE

    store = None
    if os.path.exists(DNA_STORAGE_PATH):
        try:
            with open(DNA_STORAGE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "profiles" in data:
                    store = data
        except Exception:
            store = None

    if store is None and _SB_CLIENT:
        try:
            remote_bytes = _SB_CLIENT.storage.from_(_CONFIG_BUCKET).download(REMOTE_DNA_KEY)
            if remote_bytes:
                r_data = json.loads(remote_bytes.decode("utf-8"))
                if isinstance(r_data, dict) and "profiles" in r_data:
                    store = r_data
                    try:
                        with open(DNA_STORAGE_PATH, "w", encoding="utf-8") as f:
                            json.dump(store, f, ensure_ascii=False)
                    except Exception:
                        pass
        except Exception:
            pass

    if store is None:
        store = {"profiles": {}, "fingerprints": {}}

    _DNA_STORE_CACHE = store
    return store


def save_dna_store(store: dict):
    global _DNA_STORE_CACHE
    _DNA_STORE_CACHE = store
    with open(DNA_STORAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    _sync_dna_store_to_supabase(store)


def recompute_profile_centroid(prof: dict) -> dict:
    """
    プロファイル内の直近10年スロットから平均ベクトル(6D)・平均網羅率・最頻出単元を再集計する。
    計算コストは1大学あたり最大10件の合計のみ（O(1)）で常に軽量。
    """
    slots = prof.get("year_slots", {})
    # Keep at most 10 most recent years
    sorted_years = sorted(slots.keys(), key=lambda y: (int(re.sub(r'\D', '', str(y)) or 0), str(y)), reverse=True)[:10]
    trimmed_slots = {y: slots[y] for y in sorted_years}
    prof["year_slots"] = trimmed_slots

    n = len(trimmed_slots)
    if n == 0:
        return prof

    # Prefer v2 calibrated slots when computing profile centroid so old v1 placeholders don't dilute v2
    v2_years = [y for y in sorted_years if int(trimmed_slots[y].get("vector_version") or 1) >= 2]
    vec_years = v2_years if v2_years else sorted_years
    prof["vector_version"] = 2 if v2_years else 1

    mean_vec = [0.0] * 6
    for y in vec_years:
        sl = trimmed_slots[y]
        v = sl.get("exam_vector", [0.74, 0.78, 0.40, 0.85, 0.66, 0.74])
        for i in range(6):
            mean_vec[i] += float(v[i]) if i < len(v) else 0.5
    prof["mean_vector"] = [round(x / max(1, len(vec_years)), 4) for x in mean_vec]

    book_sums = {}
    book_cnts = {}
    topic_counter = Counter()
    raw_topics = []

    for y in sorted_years:
        sl = trimmed_slots[y]
        for bk, rate in (sl.get("book_rates") or {}).items():
            try:
                rv = float(rate)
                book_sums[bk] = book_sums.get(bk, 0.0) + rv
                book_cnts[bk] = book_cnts.get(bk, 0) + 1
            except Exception:
                pass
        for tp in (sl.get("topics") or []):
            if tp:
                topic_counter[tp] += 1

    for tp, _ in topic_counter.most_common(10):
        raw_topics.append(tp)

    prof["sample_years_count"] = n
    prof["years_list"] = sorted_years
    prof["raw_topics"] = raw_topics

    avg_books = []
    for bk, s_val in book_sums.items():
        avg_r = round(s_val / max(1, book_cnts[bk]), 1)
        avg_books.append({"book": bk, "cov_10yr": f"{avg_r}%", "rate_num": avg_r, "role": f"実測{book_cnts[bk]}年平均網羅率"})
    avg_books.sort(key=lambda x: (-x["rate_num"], x["book"]))
    prof["avg_recommended_books"] = avg_books[:5]

    prof["frequent_topics"] = [f"{tp} ({cnt}/{n}年実測)" for tp, cnt in topic_counter.most_common(4)]
    prof["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return prof


def upsert_accumulated_dna_profile(record: dict) -> dict:
    """
    重複防止（コンテンツ指紋ハッシュ + 大学×学部×方式×科目×年度キーによるUpsert）と
    直近10年窓・累積平均への畳み込みを実行する。
    """
    store = load_dna_store()
    profiles = store.setdefault("profiles", {})
    fingerprints = store.setdefault("fingerprints", {})

    univ, faculty, exam_type = canonicalize_metadata_fields(
        record.get("university"), record.get("faculty"), record.get("exam_type")
    )
    subject = _normalize_key_part(record.get("subject"), "english").lower()
    year = _normalize_key_part(record.get("year"), "2026")
    fp = str(record.get("content_fingerprint") or "").strip()

    pkey = make_profile_key(univ, faculty, exam_type, subject)

    duplicate_status = "CREATED_NEW_YEAR"
    duplicate_message = f"{univ}（{faculty}／{exam_type}）{year}年度のDNAを新規登録しました。"

    # 1. Exact content fingerprint check across store
    if fp and fp in fingerprints:
        prev = fingerprints[fp]
        if prev.get("profile_key") == pkey and prev.get("year") == year:
            duplicate_status = "OVERWROTE_EXACT_MATCH"
            duplicate_message = f"同一問題（内容指紋一致）が既に {year}年度に存在したため、二重登録を防ぎ最新値で上書き更新しました。"
        else:
            duplicate_status = "DUPLICATE_CONTENT_DETECTED"
            duplicate_message = f"入力問題の内容指紋が既登録データ（{prev.get('university', '')} {prev.get('year', '')}年）と完全一致しました。同一問題の重複蓄積を防ぐため既存枠（{pkey}｜{year}年）として安全に統合しました。"

    inferred_dev = record.get("deviation_value")
    if not inferred_dev:
        u_short = univ.replace("大学", "")
        for bp in MAJOR_UNIVERSITIES_10YR_DNA:
            if len(u_short) >= 2 and u_short in bp["name"]:
                inferred_dev = bp["dev"]
                break

    if pkey not in profiles:
        profiles[pkey] = {
            "profile_key": pkey,
            "university": univ,
            "faculty": faculty,
            "exam_type": exam_type,
            "subject": subject,
            "display_name": f"{univ}（{faculty}・{exam_type}）",
            "deviation_value": inferred_dev or 60,
            "vector_version": int(record.get("vector_version") or 2),
            "source_role": record.get("source_role") or "admin",
            "submitted_by": record.get("submitted_by") or "system",
            "review_status": record.get("review_status") or ("approved" if (record.get("source_role") or "admin") == "admin" else "auto_submitted"),
            "year_slots": {}
        }

    prof = profiles[pkey]
    if inferred_dev and prof.get("deviation_value") in (None, 60) and inferred_dev != 60:
        prof["deviation_value"] = inferred_dev
    if record.get("source_role"):
        prof["source_role"] = record["source_role"]
    if record.get("submitted_by"):
        prof["submitted_by"] = record["submitted_by"]
    if record.get("review_status"):
        prof["review_status"] = record["review_status"]

    if year in prof["year_slots"] and duplicate_status == "CREATED_NEW_YEAR":
        duplicate_status = "OVERWROTE_SAME_YEAR_SLOT"
        duplicate_message = f"同じ『{univ}｜{faculty}｜{exam_type}｜{year}年』が既に登録済みだったため、二重加算せず該当年度スロットを上書き更新しました。"

    prof["year_slots"][year] = {
        "year": year,
        "exam_vector": [round(float(x), 4) for x in (record.get("exam_vector") or [0.74, 0.78, 0.40, 0.85, 0.66, 0.74])[:6]],
        "vector_version": int(record.get("vector_version") or 2),
        "content_fingerprint": fp,
        "book_rates": record.get("book_rates") or {},
        "topics": record.get("topics") or [],
        "source_role": record.get("source_role") or prof.get("source_role", "admin"),
        "submitted_by": record.get("submitted_by") or prof.get("submitted_by", "system"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    if fp:
        fingerprints[fp] = {"profile_key": pkey, "university": univ, "year": year}

    recompute_profile_centroid(prof)
    save_dna_store(store)

    return {
        "duplicate_status": duplicate_status,
        "message": duplicate_message,
        "profile": prof,
        "total_profiles": len(profiles),
        "total_year_slots": sum(len(p.get("year_slots", {})) for p in profiles.values())
    }


def delete_accumulated_dna_profile(profile_key: str) -> dict:
    store = load_dna_store()
    profiles = store.setdefault("profiles", {})
    fingerprints = store.setdefault("fingerprints", {})
    if profile_key not in profiles:
        return {"deleted": False, "message": "対象プロファイルが見つかりません。"}
    removed = profiles.pop(profile_key)
    for fp_k in [fk for fk, fv in fingerprints.items() if fv.get("profile_key") == profile_key]:
        fingerprints.pop(fp_k, None)
    save_dna_store(store)
    return {
        "deleted": True,
        "message": f"プロファイル『{removed.get('display_name', profile_key)}』を削除しました。",
        "total_profiles": len(profiles),
        "total_year_slots": sum(len(p.get("year_slots", {})) for p in profiles.values()),
    }


def update_accumulated_dna_profile(profile_key: str, updates: dict) -> dict:
    """
    Update metadata (university/faculty/exam_type/review_status/deviation_value) on a stored profile.
    If key attributes change to match an existing profile, merges year_slots cleanly (alias consolidation).
    """
    store = load_dna_store()
    profiles = store.setdefault("profiles", {})
    fingerprints = store.setdefault("fingerprints", {})
    if profile_key not in profiles:
        return {"updated": False, "message": "対象プロファイルが見つかりません。"}

    prof = profiles[profile_key]
    new_univ, new_fac, new_et = canonicalize_metadata_fields(
        updates.get("university") or prof.get("university", "大学名未指定"),
        updates.get("faculty") or prof.get("faculty", "共通・標準"),
        updates.get("exam_type") or prof.get("exam_type", "一般入試"),
    )
    subj = prof.get("subject", "english")
    new_pkey = make_profile_key(new_univ, new_fac, new_et, subj)

    if updates.get("review_status"):
        prof["review_status"] = updates["review_status"]
    if updates.get("deviation_value") is not None:
        prof["deviation_value"] = int(updates["deviation_value"])

    merged_into_existing = False
    if new_pkey != profile_key:
        old_prof = profiles.pop(profile_key)
        if new_pkey in profiles:
            merged_into_existing = True
            target_prof = profiles[new_pkey]
            for yr, slot in (old_prof.get("year_slots") or {}).items():
                if yr not in target_prof.get("year_slots", {}):
                    target_prof["year_slots"][yr] = slot
            if updates.get("review_status"):
                target_prof["review_status"] = updates["review_status"]
            prof = target_prof
        else:
            old_prof["profile_key"] = new_pkey
            old_prof["university"] = new_univ
            old_prof["faculty"] = new_fac
            old_prof["exam_type"] = new_et
            old_prof["display_name"] = f"{new_univ}（{new_fac}・{new_et}）"
            profiles[new_pkey] = old_prof
            prof = old_prof
        for fp_k, fv in fingerprints.items():
            if fv.get("profile_key") == profile_key:
                fv["profile_key"] = new_pkey
                fv["university"] = new_univ

    recompute_profile_centroid(prof)
    save_dna_store(store)
    msg = (
        f"『{prof.get('display_name')}』へ統合しました。"
        if merged_into_existing
        else f"『{prof.get('display_name')}』を更新しました。"
    )
    return {
        "updated": True,
        "merged": merged_into_existing,
        "message": msg,
        "profile": prof,
        "total_profiles": len(profiles),
        "total_year_slots": sum(len(p.get("year_slots", {})) for p in profiles.values()),
    }


def load_accumulated_dna_records() -> list:
    store = load_dna_store()
    profiles = list(store.get("profiles", {}).values())
    profiles.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return profiles


def cosine_sim(v1: list, v2: list) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def find_merge_candidates() -> list:
    """
    Detect accumulated profiles sharing the same `(university, subject)` with differing
    faculty/exam_type strings (or substring-similar university names) so admins can merge in 1 click.
    """
    store = load_dna_store()
    profiles = list(store.get("profiles", {}).values())
    by_univ_subj = {}
    for p in profiles:
        u = re.sub(r'大学$', '', p.get("university", ""))
        key = (u, p.get("subject", ""))
        by_univ_subj.setdefault(key, []).append(p)

    candidates = []
    for (u_root, subj), grp in by_univ_subj.items():
        if len(grp) < 2:
            continue
        # Sort so profiles with generic labels ('共通・標準', '一般入試') or fewer years suggest merging into richer/specific ones
        grp_sorted = sorted(grp, key=lambda x: (-(x.get("sample_years_count", 1)), len(x.get("faculty", ""))))
        primary = grp_sorted[0]
        for other in grp_sorted[1:]:
            if other["profile_key"] == primary["profile_key"]:
                continue
            candidates.append({
                "source_key": other["profile_key"],
                "source_display": f"{other.get('display_name', other['profile_key'])} [{subj}] ({other.get('sample_years_count', 1)}年)",
                "target_key": primary["profile_key"],
                "target_university": primary.get("university", ""),
                "target_faculty": primary.get("faculty", ""),
                "target_exam_type": primary.get("exam_type", ""),
                "target_display": f"{primary.get('display_name', primary['profile_key'])} [{subj}] ({primary.get('sample_years_count', 1)}年)",
            })
    return candidates[:20]


def get_metadata_suggestions() -> dict:
    """Return known universities, faculties, and exam types from accumulated DNA + baselines."""
    store = load_dna_store()
    profiles = list(store.get("profiles", {}).values())
    univs = set()
    faculties_by_univ = {}
    exam_types_by_univ = {}

    for bp in MAJOR_UNIVERSITIES_10YR_DNA:
        u_clean = bp["name"].split("（")[0].split("/")[0].strip()
        if u_clean:
            univs.add(u_clean)

    for p in profiles:
        u = p.get("university", "").strip()
        f = p.get("faculty", "").strip()
        e = p.get("exam_type", "").strip()
        if not u or "未指定" in u or "未検出" in u:
            continue
        univs.add(u)
        if f and f != "共通・標準":
            faculties_by_univ.setdefault(u, set()).add(f)
        if e and e != "一般・標準":
            exam_types_by_univ.setdefault(u, set()).add(e)

    return {
        "universities": sorted(univs),
        "faculties_by_univ": {k: sorted(v) for k, v in faculties_by_univ.items()},
        "exam_types_by_univ": {k: sorted(v) for k, v in exam_types_by_univ.items()},
        "default_faculties": ["全学部共通", "システム理工学部", "理工学部", "法学部", "経済学部", "商学部", "文学部", "社会学部", "国際学部"],
        "default_exam_types": ["全学部日程", "一般入試", "学部個別日程", "A方式", "前期日程", "後期日程", "共通テスト"],
    }


def _topic_jaccard_similarity(t1: list, t2: list) -> float:
    """Compute keyword-tokenized Jaccard overlap between two topic lists (0.0..1.0)."""
    if not t1 or not t2:
        return 0.0
    def _tok(items):
        out = set()
        for it in items:
            clean = re.sub(r'\(\d+/\d+年[^)]*\)', '', str(it))
            for part in re.split(r'[・／/\s:：（）()、,]+', clean):
                part = part.strip()
                if len(part) >= 2 and part not in ("問題", "対策", "基礎", "標準", "応用"):
                    out.add(part)
        return out
    s1, s2 = _tok(t1), _tok(t2)
    if not s1 or not s2:
        return 0.0
    inter = len(s1 & s2)
    union = len(s1 | s2)
    return inter / max(1, union)


def analyze_10yr_university_and_concurrent(
    exam_vec: list,
    current_univ: str,
    subject: str,
    current_faculty: str = "",
    current_exam_type: str = "",
    exam_level_dev: int = None,
    exam_topics: list = None,
    vector_version: int = 2,
) -> dict:
    """
    初期基準プロファイル + 蓄積された実測DNAプロファイル（大学×学部×方式×最大10年）を統合し、
    v2実測ベクトル・出題単元/形式の一致度・難易度帯ペナルティ・現実的併願レンジを組み合わせて
    併願推奨＆使用推奨参考書を出力する。
    """
    store = load_dna_store()
    measured_profiles = list(store.get("profiles", {}).values())
    same_subj_measured = [p for p in measured_profiles if p.get("subject") == subject]

    canon_u, canon_f, canon_et = canonicalize_metadata_fields(current_univ, current_faculty, current_exam_type)

    # Build unified candidate pool: start with baselines, override/augment with measured profiles!
    candidate_pool = []
    used_measured_keys = set()

    for base_p in MAJOR_UNIVERSITIES_10YR_DNA:
        matching_m = None
        for mp in same_subj_measured:
            u_short = mp["university"].replace("大学", "")
            if len(u_short) >= 2 and u_short in base_p["name"] and mp.get("faculty", "共通・標準") in ("共通・標準", "全学部", "全学部共通"):
                matching_m = mp
                break

        if matching_m:
            used_measured_keys.add(matching_m["profile_key"])
            rec_books_m = matching_m.get("avg_recommended_books") or base_p.get("rec_books", {}).get(subject) or base_p.get("rec_books", {}).get("english", [])
            topics_m = matching_m.get("frequent_topics") or base_p.get("ten_year_topics", {}).get(subject) or []
            yrs_cnt = matching_m.get("sample_years_count", 1)
            yrs_str = "・".join(matching_m.get("years_list", []))
            candidate_pool.append({
                "id": matching_m["profile_key"],
                "university_name": f"{matching_m['university']}（{matching_m['faculty']}・{matching_m['exam_type']}）",
                "tier": base_p["tier"],
                "dev": matching_m.get("deviation_value") or base_p["dev"],
                "vec": matching_m.get("mean_vector", base_p["vec"]),
                "vector_version": int(matching_m.get("vector_version") or 1),
                "years_analyzed": yrs_cnt,
                "data_source_type": "measured",
                "data_source_label": f"🟢 実測蓄積（{yrs_cnt}年分: {yrs_str}）",
                "desc": f"校舎で実際に読み込んだ過去問 {yrs_cnt}年分（{yrs_str}）の平均DNAベクトルから算出した実測傾向データです。",
                "topics": topics_m,
                "raw_topics": matching_m.get("raw_topics") or topics_m,
                "rec_books": rec_books_m
            })
        else:
            b_topics = base_p.get("ten_year_topics", {}).get(subject) or base_p.get("ten_year_topics", {}).get("english", [])
            candidate_pool.append({
                "id": base_p["id"],
                "university_name": base_p["name"],
                "tier": base_p["tier"],
                "dev": base_p["dev"],
                "vec": base_p["vec"],
                "vector_version": 1,
                "years_analyzed": base_p.get("years_analyzed", 10),
                "data_source_type": "baseline",
                "data_source_label": "📘 初期基準モデル（参考目安値）",
                "desc": base_p["desc"],
                "topics": b_topics,
                "raw_topics": b_topics,
                "rec_books": base_p.get("rec_books", {}).get(subject) or base_p.get("rec_books", {}).get("english", [])
            })

    # Add all distinct faculty/exam_type measured profiles (e.g. 関西大学｜システム理工学部｜全学部日程 etc.)
    for mp in same_subj_measured:
        if mp["profile_key"] in used_measured_keys:
            continue
        inferred_dev = mp.get("deviation_value") or 60
        inferred_tier = "実測蓄積プロファイル"
        u_short = mp["university"].replace("大学", "")
        for bp in MAJOR_UNIVERSITIES_10YR_DNA:
            if len(u_short) >= 2 and u_short in bp["name"]:
                inferred_dev = mp.get("deviation_value") or bp["dev"]
                inferred_tier = bp["tier"]
                break
        yrs_cnt = mp.get("sample_years_count", 1)
        yrs_str = "・".join(mp.get("years_list", []))
        m_topics = mp.get("frequent_topics", [])
        candidate_pool.append({
            "id": mp["profile_key"],
            "university_name": f"{mp['university']}（{mp['faculty']}・{mp['exam_type']}）",
            "tier": inferred_tier,
            "dev": inferred_dev,
            "vec": mp.get("mean_vector", exam_vec),
            "vector_version": int(mp.get("vector_version") or 1),
            "years_analyzed": yrs_cnt,
            "data_source_type": "measured",
            "data_source_label": f"🟢 実測蓄積（{yrs_cnt}年分: {yrs_str}）",
            "desc": f"実際に投入された『{mp['university']} {mp['faculty']} {mp['exam_type']}』の過去問（{yrs_cnt}年分: {yrs_str}）に基づく実測傾向プロファイルです。",
            "topics": m_topics,
            "raw_topics": mp.get("raw_topics") or m_topics,
            "rec_books": mp.get("avg_recommended_books", [])
        })

    # Determine reference deviation (matched university baseline if known, else measured exam_level_dev)
    matched_prof = None
    u_clean = _normalize_key_part(canon_u, "").replace("大学", "")
    for prof in candidate_pool:
        if u_clean and len(u_clean) >= 2 and u_clean in prof["university_name"]:
            if matched_prof is None or (prof["data_source_type"] == "measured" and matched_prof["data_source_type"] != "measured"):
                matched_prof = prof
            elif canon_f and canon_f in prof["university_name"]:
                matched_prof = prof

    base_dev = (matched_prof.get("dev") if matched_prof else None) or exam_level_dev or 60

    scored = []
    for prof in candidate_pool:
        p_vec = prof["vec"]
        p_ver = prof.get("vector_version", 1)
        # If comparing v2 exam against legacy v1/baseline, align on core dimensions (0=diff, 1=vol, 3=cov) to avoid scale skew
        if vector_version >= 2 and p_ver < 2:
            e_sub = [exam_vec[0], exam_vec[1], exam_vec[3]]
            p_sub = [p_vec[0], p_vec[1], p_vec[3]]
            cos_s = cosine_sim(e_sub, p_sub)
            l1_d = sum(abs(a - b) for a, b in zip(e_sub, p_sub)) / 3.0
        else:
            cos_s = cosine_sim(exam_vec, p_vec)
            l1_d = sum(abs(a - b) for a, b in zip(exam_vec, p_vec)) / max(1, len(exam_vec))

        topic_sim = _topic_jaccard_similarity(exam_topics or [], prof.get("raw_topics") or prof.get("topics") or [])
        if exam_topics and (prof.get("raw_topics") or prof.get("topics")):
            raw_fmt_sim = (cos_s * 0.45 + (1.0 - l1_d) * 0.35 + topic_sim * 0.20) * 100.0
        else:
            raw_fmt_sim = (cos_s * 0.58 + (1.0 - l1_d) * 0.42) * 100.0

        format_similarity = round(min(99.0, max(35.0, raw_fmt_sim)), 1)

        # Point 5: Difficulty alignment penalty so extreme level gaps don't falsely rank top
        diff_dev = prof["dev"] - base_dev
        dev_penalty = max(0.0, (abs(diff_dev) - 6.0) * 1.8)
        dev_penalty = min(25.0, dev_penalty)
        match_score = round(min(99.2, max(35.0, format_similarity - dev_penalty)), 1)

        if matched_prof and prof["id"] == matched_prof["id"]:
            match_score = max(match_score, 97.5 if prof["data_source_type"] == "measured" else 95.5)

        if abs(diff_dev) <= 2:
            dev_diff_label = "同水準"
        elif diff_dev > 0:
            dev_diff_label = f"上位 +{diff_dev}"
        else:
            dev_diff_label = f"安全 {diff_dev}"

        scored.append({
            "id": prof["id"],
            "university_name": prof["university_name"],
            "tier": prof["tier"],
            "deviation_value": prof["dev"],
            "diff_dev": diff_dev,
            "dev_diff_label": dev_diff_label,
            "format_similarity": format_similarity,
            "years_analyzed": prof["years_analyzed"],
            "data_source_type": prof["data_source_type"],
            "data_source_label": prof["data_source_label"],
            "match_rate": match_score,
            "reason": prof["desc"],
            "ten_year_topics": prof["topics"],
            "rec_books": prof["rec_books"]
        })

    # Sort measured first on ties, and overall by match_rate
    scored.sort(key=lambda x: (-x["match_rate"], 0 if x["data_source_type"] == "measured" else 1))
    if matched_prof is None:
        top_id = scored[0]["id"]
        matched_prof = next((p for p in candidate_pool if p["id"] == top_id), candidate_pool[0])

    # Point 5: Realistic concurrent university buckets (avoid absurd +/-15 deviation jumps)
    for item in scored:
        dd = item["diff_dev"]
        if 2 <= dd <= 8:
            item["concurrent_category"] = "🔥 チャレンジ併願校（現実的な上位挑戦校 +2〜+8）"
            item["category_code"] = "challenge"
        elif -10 <= dd <= -2:
            item["concurrent_category"] = "🛡️ 安全圏・確実抑え併願校（現在の対策で得点確保 -2〜-10）"
            item["category_code"] = "safe"
        elif abs(dd) <= 3:
            item["concurrent_category"] = "🎯 実力相応・本命併願推奨校（出題傾向・難易度帯が直撃一致）"
            item["category_code"] = "match"
        elif dd > 8:
            item["concurrent_category"] = "⚡ 最難関（参考比較のみ：難易度差大）"
            item["category_code"] = "out_of_range_high"
        else:
            item["concurrent_category"] = "📘 基礎確認用（参考比較のみ）"
            item["category_code"] = "out_of_range_low"

    match_list = [x for x in scored if x["category_code"] == "match"][:5]
    challenge_list = [x for x in scored if x["category_code"] == "challenge"][:3]
    safe_list = [x for x in scored if x["category_code"] == "safe"][:3]
    # Soft fallback if strict challenge/safe window had 0 items (keep max delta <= 12)
    if not challenge_list:
        challenge_list = [x for x in scored if 8 < x["diff_dev"] <= 12][:2]
    if not safe_list:
        safe_list = [x for x in scored if -12 <= x["diff_dev"] < -10][:2]

    combined_concurrent = match_list + challenge_list + safe_list
    combined_concurrent.sort(key=lambda x: -x["match_rate"])

    total_profiles = len(store.get("profiles", {}))
    total_year_slots = sum(len(p.get("year_slots", {})) for p in store.get("profiles", {}).values())

    return {
        "matched_university_id": matched_prof["id"],
        "matched_university_name": matched_prof["university_name"],
        "years_analyzed": matched_prof.get("years_analyzed", 10),
        "data_source_type": matched_prof.get("data_source_type", "baseline"),
        "data_source_label": matched_prof.get("data_source_label", "📘 初期基準モデル（目安）"),
        "ten_year_summary": matched_prof["desc"],
        "ten_year_frequent_topics": matched_prof.get("topics", []),
        "ten_year_recommended_books": matched_prof.get("rec_books", []),
        "all_universities_10yr": scored,
        "concurrent_universities": combined_concurrent[:10],
        "accumulated_custom_dna_count": total_year_slots,
        "accumulated_profiles_count": total_profiles,
        "accumulated_custom_dna_recent": measured_profiles[:10]
    }
