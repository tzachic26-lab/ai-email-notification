"""Unit tests for story_dedup."""

from __future__ import annotations

import story_dedup as sd


def test_normalize_strips_punctuation_and_case():
    assert sd._normalize_story_text("  OpenAI's GPT-5: Launched!  ").strip() == "openai s gpt 5 launched"


def test_normalize_keeps_hebrew():
    assert sd._normalize_story_text("חדשות, מהארץ!").strip() == "חדשות מהארץ"


def test_tokenize_drops_stopwords_noise_and_short_words():
    tokens = sd._tokenize("Google announces the new Gemini model for AI")
    assert "google" in tokens
    assert "gemini" in tokens
    assert "model" in tokens
    # stop words, headline noise and <=2 char words are removed
    assert "the" not in tokens
    assert "announces" not in tokens
    assert "new" not in tokens
    assert "for" not in tokens
    assert "ai" not in tokens


def test_tokenize_handles_mixed_hebrew_and_latin():
    tokens = sd._tokenize("נתניהו נפגש עם Microsoft בירושלים")
    assert "נתניהו" in tokens
    assert "microsoft" in tokens
    assert "עם" not in tokens


def test_story_keywords_includes_snippet_tokens():
    keywords = sd.story_keywords("Nvidia chip export", "Washington approves shipments")
    assert {"nvidia", "chip", "export", "washington", "approves", "shipments"} <= keywords


def test_story_keywords_truncates_long_snippet():
    long_snippet = ("padding " * 40) + "sentinelword"
    assert len(long_snippet) > 240
    assert "sentinelword" not in sd.story_keywords("Title here", long_snippet)


def test_title_bigrams_are_adjacent_token_pairs():
    assert sd._title_bigrams("Anthropic raises funding round") == {
        "anthropic raises",
        "raises funding",
        "funding round",
    }


def test_title_bigrams_empty_for_single_token():
    assert sd._title_bigrams("Anthropic") == set()


def test_char_ngrams_short_text_returns_whole_string():
    assert sd._char_ngrams("ab", n=4) == {"ab"}
    assert sd._char_ngrams("   ", n=4) == set()


def test_char_ngrams_sliding_window():
    assert sd._char_ngrams("abcde", n=4) == {"abcd", "bcde"}


def test_content_tokens_removes_vendor_names():
    assert sd._content_tokens({"openai", "gemini", "chip", "export"}) == {"chip", "export"}


def test_build_idf_weights_rare_word_outweighs_common_word():
    pool = [
        {"title": "Nvidia chip demand climbs"},
        {"title": "Nvidia chip supply tightens"},
        {"title": "Nvidia chip prices settle"},
        {"title": "Jerusalem municipality budget vote"},
    ]
    weights = sd.build_idf_weights(pool)
    assert weights["jerusalem"] > weights["nvidia"]
    assert all(value >= 1.0 for value in weights.values())


def test_build_idf_weights_empty_pool():
    assert sd.build_idf_weights([]) == {}


def test_jaccard_basic_and_empty_sets():
    assert sd._jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert sd._jaccard(set(), {"a"}) == 0.0
    assert sd._jaccard({"a"}, set()) == 0.0


def test_weighted_overlap_uses_idf_weights():
    weights = {"rare": 9.0, "common": 1.0}
    high = sd._weighted_overlap({"rare", "common"}, {"rare"}, weights)
    low = sd._weighted_overlap({"rare", "common"}, {"common"}, weights)
    assert high > low
    assert sd._weighted_overlap({"a"}, {"b"}, weights) == 0.0


def test_weighted_overlap_defaults_missing_weights_to_one():
    assert sd._weighted_overlap({"x", "y"}, {"x"}, {}) == 0.5


def test_content_coverage_ignores_vendor_only_overlap():
    assert sd._content_coverage({"openai"}, {"openai"}) == 0.0
    assert sd._content_coverage({"chip", "export"}, {"chip"}) == 1.0
    assert sd._content_coverage({"chip"}, {"tariff"}) == 0.0


def test_shared_idf_mass_sums_shared_content_tokens():
    weights = {"chip": 2.0, "export": 3.0, "openai": 5.0}
    mass = sd._shared_idf_mass({"chip", "export", "openai"}, {"chip", "export", "openai"}, weights)
    assert mass == 5.0  # vendor token excluded
    assert sd._shared_idf_mass({"chip"}, {"tariff"}, weights) == 0.0


def test_story_similarity_identical_titles_is_one():
    assert sd.story_similarity("Same headline text", "", "Same headline text", "") == 1.0


def test_story_similarity_unrelated_titles_is_low():
    score = sd.story_similarity(
        "Jerusalem light rail line opens",
        "New tracks serve the city center",
        "Nvidia reports record data center revenue",
        "Chip demand keeps climbing",
    )
    assert score < 0.38


def test_story_similarity_idf_weights_raise_score():
    title_a = "Rafael unveils new interceptor missile"
    title_b = "Rafael interceptor missile revealed by ministry"
    pool = [
        {"title": title_a},
        {"title": title_b},
        {"title": "Jerusalem budget vote delayed"},
        {"title": "Tel Aviv housing prices climb"},
    ]
    weights = sd.build_idf_weights(pool)
    assert sd.story_similarity(title_a, "", title_b, "", idf_weights=weights) >= sd.story_similarity(
        title_a, "", title_b, ""
    )


def test_is_similar_story_detects_rewritten_headline():
    left = {"title": "Rafael interceptor missile test succeeds", "snippet": "Defense ministry confirms"}
    right = {"title": "Interceptor missile test by Rafael succeeds", "snippet": "Ministry confirms"}
    assert sd.is_similar_story(left, right) is True


def test_is_similar_story_rejects_different_stories():
    left = {"title": "Jerusalem light rail line opens", "snippet": "Tracks serve city center"}
    right = {"title": "Nvidia posts record revenue", "snippet": "Data center demand climbs"}
    assert sd.is_similar_story(left, right) is False


def test_is_similar_story_without_shared_content_needs_high_similarity():
    # Only a vendor token is shared, so the stricter 0.62 threshold applies.
    left = {"title": "OpenAI hires new finance chief", "snippet": ""}
    right = {"title": "OpenAI faces copyright lawsuit", "snippet": ""}
    assert sd.is_similar_story(left, right) is False


def test_is_similar_story_threshold_is_configurable():
    left = {"title": "Bank of Israel holds interest rate steady", "snippet": ""}
    right = {"title": "Interest rate decision expected from Bank of Israel next week", "snippet": ""}
    # similarity sits just above the default 0.38 threshold
    assert sd.is_similar_story(left, right) is True
    assert sd.is_similar_story(left, right, threshold=0.9) is False


def test_build_story_cluster_map_empty_pool():
    assert sd.build_story_cluster_map([], {}) == {}


def test_build_story_cluster_map_groups_duplicates():
    pool = [
        {"title": "Rafael interceptor missile test succeeds", "snippet": ""},
        {"title": "Interceptor missile test by Rafael succeeds", "snippet": ""},
        {"title": "Jerusalem light rail line opens today", "snippet": ""},
    ]
    weights = sd.build_idf_weights(pool)
    cluster_map = sd.build_story_cluster_map(pool, weights)

    rafael_a = cluster_map[pool[0]["title"]]
    rafael_b = cluster_map[pool[1]["title"]]
    rail = cluster_map[pool[2]["title"]]
    assert rafael_a == rafael_b
    assert rail != rafael_a
    assert rail == pool[2]["title"]


def test_build_story_cluster_map_rank_key_picks_representative():
    pool = [
        {"title": "Rafael interceptor missile test succeeds", "snippet": "", "rank": 1},
        {"title": "Interceptor missile test by Rafael succeeds", "snippet": "", "rank": 9},
    ]
    weights = sd.build_idf_weights(pool)
    cluster_map = sd.build_story_cluster_map(pool, weights, rank_key=lambda item: item["rank"])
    assert set(cluster_map.values()) == {pool[1]["title"]}


def test_dedupe_with_backfill_replaces_duplicate_pick_from_pool():
    duplicate_a = {"title": "Rafael interceptor missile test succeeds", "snippet": ""}
    duplicate_b = {"title": "Interceptor missile test by Rafael succeeds", "snippet": ""}
    other = {"title": "Jerusalem light rail line opens today", "snippet": ""}
    pool = [duplicate_a, duplicate_b, other]

    result = sd.dedupe_with_backfill([duplicate_a, duplicate_b], pool, count=2)
    titles = [item["title"] for item in result]
    assert len(result) == 2
    assert duplicate_a["title"] in titles
    assert other["title"] in titles
    assert duplicate_b["title"] not in titles


def test_dedupe_with_backfill_respects_count_and_skips_repeated_titles():
    items = [
        {"title": "Jerusalem light rail line opens today", "snippet": ""},
        {"title": "Nvidia posts record data center revenue", "snippet": ""},
        {"title": "Bank of Israel holds interest rate", "snippet": ""},
    ]
    result = sd.dedupe_with_backfill([items[0], items[0], items[1]], items, count=2)
    assert [item["title"] for item in result] == [items[0]["title"], items[1]["title"]]


def test_dedupe_with_backfill_empty_inputs():
    assert sd.dedupe_with_backfill([], [], count=3) == []
