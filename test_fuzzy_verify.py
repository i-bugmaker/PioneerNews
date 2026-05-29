from fuzzy_search import fuzzy_match_score, dynamic_threshold, lcs_containment_score, filter_fuzzy_results, containment_score, dice_coefficient, levenshtein_ratio, _normalize
import sys
sys.path.insert(0, ".")
from main import db_search_news_fuzzy_candidates
import sqlite3

passed = 0
failed = 0

def check(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1

print("=== 1. Core Algorithm Tests ===")
s = fuzzy_match_score("茅合", "贵州茅台")
check("茅合 vs 贵州茅台 >= 0.45", s >= 0.45)

s2 = fuzzy_match_score("江汽集团", "石药集团")
check("江汽集团 vs 石药集团 < 0.55", s2 < 0.55)

s3 = fuzzy_match_score("江汽集团", "江汽集团项兴初")
check("江汽集团 vs 江汽集团项兴初 >= 0.55", s3 >= 0.55)

s4 = fuzzy_match_score("苹果", "特斯拉电动车")
check("苹果 vs 特斯拉电动车 < 0.35", s4 < 0.35)

s5 = fuzzy_match_score("茅台", "贵州茅台")
check("茅台 vs 贵州茅台 == 0.95", s5 == 0.95)

s6 = fuzzy_match_score("茅台", "茅台")
check("茅台 vs 茅台 == 1.0", s6 == 1.0)

print("\n=== 2. Dynamic Threshold Tests ===")
check("2-char threshold == 0.45", dynamic_threshold("茅台") == 0.45)
check("3-char threshold == 0.55", dynamic_threshold("茅台酒") == 0.55)
check("4-char threshold == 0.55", dynamic_threshold("贵州茅台") == 0.55)
check("5+ char threshold == 0.35", dynamic_threshold("贵州茅台酒") == 0.35)

print("\n=== 3. LCS Containment Tests ===")
check("LCS 江汽集团 in 江汽集团项兴初 >= 0.9", lcs_containment_score("江汽集团", "江汽集团项兴初") >= 0.9)
check("LCS 江汽集团 in 石药集团 <= 0.5", lcs_containment_score("江汽集团", "石药集团") <= 0.5)

print("\n=== 4. API Change Tests ===")
check("char_containment_score removed", not hasattr(__import__("fuzzy_search"), "char_containment_score"))
check("lcs_containment_score exists", hasattr(__import__("fuzzy_search"), "lcs_containment_score"))

print("\n=== 5. DB Integration Tests ===")
conn = sqlite3.connect("news.db")
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM news WHERE publish_ts > ?", (int(__import__("time").time()) - 14*86400,))
total = c.fetchone()[0]
conn.close()

candidates = db_search_news_fuzzy_candidates("茅台")
check(f"SQL pre-filter: candidates({len(candidates)}) < total({total})", len(candidates) < total)

results = filter_fuzzy_results("茅合", candidates)
has_maotai = any("茅台" in (r.get("title") or "") for r in results)
check("茅合 fuzzy search finds 茅台", has_maotai)

results2 = filter_fuzzy_results("特斯啦", db_search_news_fuzzy_candidates("特斯啦"))
has_tesla = any("特斯拉" in (r.get("title") or "") for r in results2)
check("特斯啦 fuzzy search finds 特斯拉", has_tesla)

results3 = filter_fuzzy_results("江汽集团", db_search_news_fuzzy_candidates("江汽集团"))
no_shiyao = not any("石药" in (r.get("title") or "") for r in results3)
check("江汽集团 does NOT match 石药集团", no_shiyao)

print(f"\n=== Results: {passed} passed, {failed} failed ===")
sys.exit(0 if failed == 0 else 1)
