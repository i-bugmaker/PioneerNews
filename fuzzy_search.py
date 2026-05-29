import re
import time
import logging

logger = logging.getLogger(__name__)

_FUZZY_CACHE: dict[str, tuple[list, float]] = {}
_FUZZY_CACHE_TTL = 60.0

FUZZY_DEFAULT_THRESHOLD = 0.35
FUZZY_EXACT_BONUS = 0.3
FUZZY_SHORT_QUERY_LEN = 4


def bigram_set(s: str) -> set[str]:
    if not s or len(s) < 2:
        return {s} if s else set()
    return {s[i:i+2] for i in range(len(s) - 1)}


def _normalize(text: str) -> str:
    return re.sub(r'\s+', '', text).lower().strip()


def levenshtein_ratio(s1: str, s2: str) -> float:
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return 0.0
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    max_dist = max(len(s1), len(s2))
    return 1.0 - prev_row[-1] / max_dist if max_dist > 0 else 1.0


def dice_coefficient(s1: str, s2: str) -> float:
    s1_norm = _normalize(s1)
    s2_norm = _normalize(s2)
    if not s1_norm or not s2_norm:
        return 0.0
    bigrams1 = bigram_set(s1_norm)
    bigrams2 = bigram_set(s2_norm)
    if not bigrams1 or not bigrams2:
        return 1.0 if s1_norm == s2_norm else 0.0
    intersection = bigrams1 & bigrams2
    return 2.0 * len(intersection) / (len(bigrams1) + len(bigrams2))


def lcs_containment_score(query: str, text: str) -> float:
    query_norm = _normalize(query)
    text_norm = _normalize(text)
    if not query_norm:
        return 1.0
    if not text_norm:
        return 0.0
    m = len(query_norm)
    n = len(text_norm)
    prev = [0] * (n + 1)
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if query_norm[i - 1] == text_norm[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    lcs_length = prev[n]
    return lcs_length / m


def containment_score(query: str, text: str) -> float:
    query_norm = _normalize(query)
    text_norm = _normalize(text)
    if not query_norm:
        return 1.0
    if not text_norm:
        return 0.0
    q_bigrams = bigram_set(query_norm)
    if not q_bigrams:
        return 1.0 if query_norm in text_norm else 0.0
    t_bigrams = bigram_set(text_norm)
    if not t_bigrams:
        return 0.0
    intersection = q_bigrams & t_bigrams
    return len(intersection) / len(q_bigrams)


def fuzzy_match_score(query: str, text: str) -> float:
    if not query or not text:
        return 0.0
    query_norm = _normalize(query)
    text_norm = _normalize(text)
    if query_norm == text_norm:
        return 1.0
    if query_norm in text_norm:
        return 0.95

    lcs_cont = lcs_containment_score(query_norm, text_norm)
    bigram_cont = containment_score(query_norm, text_norm)

    if len(query_norm) <= 2:
        best_lev = 0.0
        text_len = len(text_norm)
        for i in range(max(1, text_len - len(query_norm) + 1)):
            window = text_norm[i:i + len(query_norm)]
            if not window:
                break
            r = levenshtein_ratio(query_norm, window)
            pos_weight = 1.0 - (i / max(text_len, 1)) * 0.15
            if r * pos_weight > best_lev:
                best_lev = r * pos_weight
        base_score = max(lcs_cont * 0.7 + bigram_cont * 0.3, best_lev)
        return base_score

    if len(query_norm) <= FUZZY_SHORT_QUERY_LEN:
        window = text_norm[:len(query_norm) + 2]
        lev_ratio = levenshtein_ratio(query_norm, window)
        base_score = max(lcs_cont * 0.5 + bigram_cont * 0.5, lev_ratio)
        if bigram_cont == 0.0:
            base_score *= 0.4
        return base_score

    dice = dice_coefficient(query_norm, text_norm)
    base_score = lcs_cont * 0.5 + bigram_cont * 0.3 + dice * 0.2
    if bigram_cont == 0.0 and lcs_cont < 0.5:
        base_score *= 0.5
    return base_score


def dynamic_threshold(query: str) -> float:
    n = len(_normalize(query))
    if n <= 2:
        return 0.45
    if n == 3:
        return 0.55
    if n == 4:
        return 0.55
    return 0.35


def is_fuzzy_match(query: str, text: str, threshold: float = FUZZY_DEFAULT_THRESHOLD) -> bool:
    return fuzzy_match_score(query, text) >= threshold


def _fuzzy_cache_key(query: str, threshold: float) -> str:
    return f"{query.lower().strip()}|{threshold:.2f}"


def get_cached_fuzzy(query: str, threshold: float) -> tuple[list, float] | None:
    key = _fuzzy_cache_key(query, threshold)
    cached = _FUZZY_CACHE.get(key)
    if cached and time.time() - cached[1] < _FUZZY_CACHE_TTL:
        return cached
    return None


def set_cached_fuzzy(query: str, threshold: float, result: list):
    key = _fuzzy_cache_key(query, threshold)
    _FUZZY_CACHE[key] = (result, time.time())


def clear_fuzzy_cache():
    _FUZZY_CACHE.clear()


def filter_fuzzy_results(
    query: str,
    candidates: list[dict],
    threshold: float = FUZZY_DEFAULT_THRESHOLD,
    max_results: int = 20,
    title_weight: float = 0.7,
    intro_weight: float = 0.3,
) -> list[dict]:
    if not query or not candidates:
        return []

    actual_threshold = dynamic_threshold(query)
    scored = []
    query_lower = query.lower().strip()
    query_norm = _normalize(query)
    seen_urls: set[str] = set()

    for row in candidates:
        title = row.get("title") or ""
        intro = row.get("intro") or ""
        url = row.get("url") or ""

        if url and url in seen_urls:
            continue

        if query_lower in title.lower():
            combined_score = 1.0 + FUZZY_EXACT_BONUS
        elif query_lower in intro.lower():
            combined_score = 0.9 + FUZZY_EXACT_BONUS
        else:
            title_score = fuzzy_match_score(query, title)
            intro_score = fuzzy_match_score(query, intro)
            max_score = max(title_score, intro_score)
            if max_score < actual_threshold:
                continue
            combined_score = title_score * title_weight + intro_score * intro_weight
            if len(query_norm) <= 2:
                title_lcs = lcs_containment_score(query_norm, _normalize(title))
                combined_score += title_lcs * 0.15

        scored.append((combined_score, row))
        if url:
            seen_urls.add(url)

    scored.sort(key=lambda x: (-x[0], -(x[1].get("publish_ts") or 0)))
    return [row for _, row in scored[:max_results]]
