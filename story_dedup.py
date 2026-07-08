"""Detect and remove duplicate or near-duplicate news stories."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher

_HEBREW_WORD = re.compile(r"[\u0590-\u05FF]{2,}")
_LATIN_WORD = re.compile(r"[a-zA-Z0-9]{2,}")

_STOP_WORDS = frozenset(
    {
        "של",
        "על",
        "את",
        "עם",
        "הוא",
        "היא",
        "זה",
        "לא",
        "גם",
        "אחרי",
        "בין",
        "אל",
        "או",
        "כל",
        "עוד",
        "כי",
        "אם",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "has",
        "have",
        "its",
        "how",
        "why",
        "what",
        "new",
        "say",
        "says",
        "said",
        "will",
        "can",
        "may",
        "not",
        "just",
        "about",
        "into",
        "over",
        "after",
        "before",
        "more",
        "most",
        "other",
        "their",
        "they",
        "them",
        "been",
        "being",
        "also",
        "than",
        "then",
        "when",
        "where",
        "while",
        "who",
        "which",
        "our",
        "your",
        "his",
        "her",
        "as",
        "at",
        "by",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "to",
        "an",
        "be",
        "do",
        "if",
        "so",
        "up",
        "no",
        "we",
        "he",
        "she",
    }
)

_HEADLINE_NOISE = frozenset(
    {
        "unveils",
        "unveil",
        "unveiled",
        "announces",
        "announced",
        "announce",
        "launch",
        "launches",
        "launched",
        "reveals",
        "revealed",
        "reveal",
        "report",
        "reports",
        "reported",
        "latest",
        "update",
        "updated",
        "breaking",
        "news",
        "says",
        "said",
        "according",
        "effort",
        "part",
        "deal",
        "built",
        "build",
        "meet",
        "secret",
        "weapon",
        "first",
        "custom",
        "new",
        "its",
        "itself",
        "distances",
        "continue",
        "continues",
        "put",
        "test",
        "tests",
        "tested",
        "quietly",
        "learns",
        "listen",
        "talk",
    }
)

_VENDOR_TOKENS = frozenset(
    {
        "openai",
        "chatgpt",
        "anthropic",
        "claude",
        "google",
        "deepmind",
        "gemini",
        "meta",
        "llama",
        "microsoft",
        "copilot",
        "azure",
        "nvidia",
        "apple",
        "amazon",
        "bedrock",
        "aws",
        "xai",
        "grok",
        "mistral",
        "cohere",
        "intel",
        "amd",
        "ibm",
        "oracle",
        "salesforce",
        "adobe",
        "tesla",
        "broadcom",
    }
)


def _normalize_story_text(text: str) -> str:
    cleaned = unicodedata.normalize("NFKC", text).lower().strip()
    cleaned = cleaned.replace("\ufffd", "")
    cleaned = re.sub(r"jalape.n.o", "jalapeno", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^\w\s\u0590-\u05FF]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned)


def _tokenize(text: str) -> list[str]:
    normalized = _normalize_story_text(text)
    words = _HEBREW_WORD.findall(normalized) + _LATIN_WORD.findall(normalized)
    return [
        word
        for word in words
        if word not in _STOP_WORDS
        and word not in _HEADLINE_NOISE
        and len(word) > 2
    ]


def story_keywords(title: str, snippet: str = "") -> set[str]:
    tokens = _tokenize(title)
    if snippet:
        tokens.extend(_tokenize(snippet[:240]))
    return set(tokens)


def _title_keywords(title: str) -> set[str]:
    return set(_tokenize(title))


def _title_bigrams(title: str) -> set[str]:
    words = _tokenize(title)
    return {f"{words[i]} {words[i + 1]}" for i in range(len(words) - 1)}


def _char_ngrams(text: str, n: int = 4) -> set[str]:
    compact = _normalize_story_text(text).replace(" ", "")
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def _content_tokens(keywords: set[str]) -> set[str]:
    return {word for word in keywords if word not in _VENDOR_TOKENS}


def build_idf_weights(pool: list[dict]) -> dict[str, float]:
    doc_count = max(len(pool), 1)
    doc_freq: Counter[str] = Counter()
    for item in pool:
        for word in _title_keywords(item.get("title", "")):
            doc_freq[word] += 1
    return {
        word: math.log((doc_count + 1) / (freq + 1)) + 1.0
        for word, freq in doc_freq.items()
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def _weighted_overlap(
    left: set[str],
    right: set[str],
    idf_weights: dict[str, float],
) -> float:
    shared = left & right
    if not shared:
        return 0.0
    union = left | right
    intersection_weight = sum(idf_weights.get(word, 1.0) for word in shared)
    union_weight = sum(idf_weights.get(word, 1.0) for word in union)
    return intersection_weight / union_weight if union_weight else 0.0


def _content_coverage(left: set[str], right: set[str]) -> float:
    content_left = _content_tokens(left)
    content_right = _content_tokens(right)
    if not content_left or not content_right:
        return 0.0
    shared = content_left & content_right
    if not shared:
        return 0.0
    return len(shared) / min(len(content_left), len(content_right))


def _shared_idf_mass(
    left: set[str],
    right: set[str],
    idf_weights: dict[str, float],
) -> float:
    shared = _content_tokens(left) & _content_tokens(right)
    if not shared:
        return 0.0
    return sum(idf_weights.get(word, 1.0) for word in shared)


def story_similarity(
    title_a: str,
    snippet_a: str,
    title_b: str,
    snippet_b: str,
    *,
    idf_weights: dict[str, float] | None = None,
) -> float:
    norm_a = _normalize_story_text(title_a)
    norm_b = _normalize_story_text(title_b)
    title_ratio = SequenceMatcher(None, norm_a, norm_b).ratio()

    title_kw_a = _title_keywords(title_a)
    title_kw_b = _title_keywords(title_b)
    title_jaccard = _jaccard(title_kw_a, title_kw_b)
    bigram_jaccard = _jaccard(_title_bigrams(title_a), _title_bigrams(title_b))
    ngram_jaccard = _jaccard(_char_ngrams(title_a), _char_ngrams(title_b))

    full_kw_a = story_keywords(title_a, snippet_a)
    full_kw_b = story_keywords(title_b, snippet_b)
    full_jaccard = _jaccard(full_kw_a, full_kw_b)

    scores = [title_ratio, title_jaccard, bigram_jaccard, full_jaccard]
    shared_content = _content_tokens(title_kw_a) & _content_tokens(title_kw_b)
    if shared_content and min(len(norm_a), len(norm_b)) >= 12:
        scores.append(ngram_jaccard)

    if idf_weights:
        scores.append(_weighted_overlap(title_kw_a, title_kw_b, idf_weights))
        scores.append(_weighted_overlap(full_kw_a, full_kw_b, idf_weights))
        coverage = _content_coverage(title_kw_a, title_kw_b)
        idf_mass = _shared_idf_mass(title_kw_a, title_kw_b, idf_weights)
        if coverage >= 0.34 and idf_mass >= 2.8:
            scores.append(0.42 + min(coverage, 1.0) * 0.35)
        if coverage >= 0.5 and idf_mass >= 1.8:
            scores.append(0.55)

    return max(scores)


def is_similar_story(
    left: dict,
    right: dict,
    *,
    threshold: float = 0.38,
    idf_weights: dict[str, float] | None = None,
) -> bool:
    title_a = left.get("title", "")
    snippet_a = left.get("snippet", "")
    title_b = right.get("title", "")
    snippet_b = right.get("snippet", "")

    title_kw_a = _title_keywords(title_a)
    title_kw_b = _title_keywords(title_b)
    shared_content = _content_tokens(title_kw_a) & _content_tokens(title_kw_b)

    similarity = story_similarity(
        title_a,
        snippet_a,
        title_b,
        snippet_b,
        idf_weights=idf_weights,
    )

    if idf_weights and shared_content:
        coverage = _content_coverage(title_kw_a, title_kw_b)
        idf_mass = _shared_idf_mass(title_kw_a, title_kw_b, idf_weights)
        if coverage >= 0.45 and idf_mass >= 2.2:
            return True
        if coverage >= 0.34 and idf_mass >= 3.2:
            return True

    if not shared_content:
        return similarity >= 0.62

    if similarity >= threshold:
        return True

    return similarity >= 0.52 and len(shared_content) >= 2


def build_story_cluster_map(
    pool: list[dict],
    idf_weights: dict[str, float],
    *,
    rank_key=None,
) -> dict[str, str]:
    """Map each story title to one representative title in its duplicate cluster."""
    if not pool:
        return {}

    parent = list(range(len(pool)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left_index, left in enumerate(pool):
        for right_index in range(left_index + 1, len(pool)):
            right = pool[right_index]
            if is_similar_story(left, right, idf_weights=idf_weights):
                left_root = find(left_index)
                right_root = find(right_index)
                if left_root != right_root:
                    parent[right_root] = left_root

    grouped: dict[int, list[dict]] = defaultdict(list)
    for index, item in enumerate(pool):
        grouped[find(index)].append(item)

    title_to_rep: dict[str, str] = {}
    for members in grouped.values():
        if rank_key is not None:
            representative = max(members, key=rank_key)
        else:
            representative = members[0]
        rep_title = representative["title"]
        for member in members:
            title_to_rep[member["title"]] = rep_title
    return title_to_rep


def dedupe_with_backfill(
    selected: list[dict],
    pool: list[dict],
    *,
    count: int,
    threshold: float = 0.38,
) -> list[dict]:
    """Keep diverse stories; backfill from pool when picks overlap."""
    idf_weights = build_idf_weights(pool)
    cluster_map = build_story_cluster_map(pool, idf_weights)
    result: list[dict] = []
    used_titles: set[str] = set()
    used_clusters: set[str] = set()

    def _accept(item: dict) -> bool:
        title = item.get("title", "")
        if title in used_titles:
            return False
        cluster_id = cluster_map.get(title, title)
        if cluster_id in used_clusters:
            return False
        if any(
            is_similar_story(item, kept, threshold=threshold, idf_weights=idf_weights)
            for kept in result
        ):
            return False
        result.append(item)
        used_titles.add(title)
        used_clusters.add(cluster_id)
        return True

    for item in selected:
        _accept(item)
        if len(result) >= count:
            return result[:count]

    for item in pool:
        if len(result) >= count:
            break
        _accept(item)

    return result[:count]
