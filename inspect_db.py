import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(r'c:\Users\fujimoto.k\Desktop\kakomon-app\.env')
sb = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_KEY'))

# 全タイトル一覧を取得
titles = {}
offset = 0
while True:
    res = sb.table('テスト作成データ').select('title, chapter, section, q_number, difficulty, question_text, ref_answer').range(offset, offset+999).execute()
    if not res.data:
        break
    for r in res.data:
        t = r.get('title') or ''
        if t not in titles:
            titles[t] = {'count': 0, 'chapters': set(), 'sample': r}
        titles[t]['count'] += 1
        if r.get('chapter'):
            titles[t]['chapters'].add(r['chapter'])
    if len(res.data) < 1000:
        break
    offset += 1000

print(f"Total books: {len(titles)}")

# 英語単語帳・熟語帳・文法書
eng_keywords = ['システム英単語', 'ターゲット', 'LEAP', '鉄壁', '速読英', 'パス単', '単熟語EX', 'SPARTA', 'Vintage', 'Next Stage', 'スクランブル', 'ポラリス', '多義語']
print("\n=== ENGLISH BOOKS ===")
for t, info in sorted(titles.items(), key=lambda x: -x[1]['count']):
    if any(k in t for k in eng_keywords):
        chaps = sorted(list(info['chapters']))[:6]
        print(f"Title: {t} | Count: {info['count']} | Chapters: {chaps}")
        print(f"  Sample: q_num={info['sample'].get('q_number')} | text={str(info['sample'].get('question_text'))[:40]} | ans={str(info['sample'].get('ref_answer'))[:40]}")

# 数学・理科の主要参考書
math_sci_keywords = ['チャート', '重要問題集', '基礎問題精講', '入門問題精講', '標準問題精講', '文系の数学', 'やさしい理系数学', 'セオリー', '名問の森', '新演習', '合格177', '合格100', 'これでわかる']
print("\n=== MATH & SCIENCE BOOKS ===")
for t, info in sorted(titles.items(), key=lambda x: -x[1]['count']):
    if any(k in t for k in math_sci_keywords):
        chaps = sorted(list(info['chapters']))[:6]
        print(f"Title: {t} | Count: {info['count']} | Chapters: {chaps}")
        print(f"  Sample: q_num={info['sample'].get('q_number')} | diff={info['sample'].get('difficulty')} | text={str(info['sample'].get('question_text'))[:50]}")
