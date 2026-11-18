"""Regression guards for SHAMAN v1.4 Phase 2.

AIOSEO/TruSEO is an evaluator and metadata consumer, not the specification
for Japanese article prose. The normalized focus keyphrase may be sent to
AIOSEO, but it must not be forced verbatim into article-generation prompts.
"""

import prompts


def test_focus_keyword_is_not_forced_into_outline_prompt():
    assert "${FOCUS_KEYWORD}" not in prompts.P2_T2, (
        "P2 must not force the whitespace-stripped focus keyphrase into H2/H3 headings; "
        "doing so degrades Japanese heading quality to satisfy TruSEO exact-match checks."
    )


def test_focus_keyword_is_not_forced_into_article_body_prompt():
    assert "${FOCUS_KEYWORD}" not in prompts.P3_RAW, (
        "P3 must not force the whitespace-stripped focus keyphrase into the introduction/body. "
        "Use KEYWORD as search intent and write natural Japanese instead."
    )


def test_focus_keyword_is_not_forced_into_p5_copy():
    assert "${FOCUS_KEYWORD}" not in prompts.P5_RAW, (
        "P5 may generate SEO title/meta from KEYWORD and article content, but exact-match "
        "FOCUS_KEYWORD must remain an AIOSEO metadata value rather than a copywriting rule."
    )


def test_p2_does_not_ban_all_spaces_in_headings():
    assert "見出しには全角・半角のスペースを入れないでください" not in prompts.P2_T2
