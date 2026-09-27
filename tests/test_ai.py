import re
from pathlib import Path

import pytest

from core import ai

ROOT = Path(__file__).resolve().parent.parent


def test_filter_flags_and_removes_recommendations():
    text = "Egypt's spread widened 45 bp. Investors should buy Nigeria now. Real 10Y is 1.45%."
    clean, flagged = ai.filter_recommendations(text)
    assert flagged == ["buy"]
    assert "buy" not in clean.lower() and "Egypt" in clean and "1.45%" in clean


@pytest.mark.parametrize("word", ["buy", "Sell", "HOLD", "consider", "reduce", "selling", "Considering"])
def test_forbidden_words_detected(word):
    assert ai.find_forbidden(f"You may {word} it.")


def test_holdings_noun_is_not_flagged():
    assert ai.find_forbidden("Your holdings are held via ETF and bond; each holding is shown.") == []


def test_stream_filters_sentences_from_model():
    def producer():
        yield "Brazil's spread tightened 10 bp. You should "
        yield "sell Turkey. Nigeria pays more than peers."

    s = ai.AIStream(producer, lambda: "fallback")
    out = "".join(s)
    assert "sell" not in out.lower()
    assert "Brazil" in out and "Nigeria" in out
    assert s.ai_used and s.flagged == ["sell"]


def test_stream_falls_back_on_error():
    def producer():
        raise TimeoutError("boom")
        yield ""  # pragma: no cover

    s = ai.AIStream(producer, lambda: "Deterministic text.")
    assert "".join(s) == "Deterministic text."
    assert s.ai_used is False and s.notice


def test_stream_without_key_uses_fallback():
    s = ai.AIStream(None, lambda: "Built-in summary.")
    assert "".join(s) == "Built-in summary." and not s.ai_used


def test_briefs_and_answers_work_without_key(seeded_db):
    for scope in ("markets", "portfolio", "home"):
        s = ai.stream_brief(scope)
        text = "".join(s)
        assert len(text) > 80 and not s.ai_used
        assert ai.find_forbidden(text) == []
        assert "illustrative" not in text.lower()  # data is real now; never claim otherwise
    for q in ["Why is Egypt on alert?", "Which of my positions is not paying for its risk?",
              "How much of my book is really in Africa?", "Nigeria or Egypt: which local bond looks better?"]:
        s = ai.stream_answer([], q)
        text = "".join(s)
        assert text and not s.ai_used and ai.find_forbidden(text) == []


def test_keyword_text_signal():
    text = ("The ministry will seek to reprofile Eurobond maturities and notes liquidity pressure. "
            "Talks with creditors continue; hidden debt was found.")
    sig = ai.keyword_signal(text)
    # cues: reprofil, liquidity pressure, creditor, hidden → 4 hits
    assert sig.score == 15 + 13 * 4
    assert all(p in text for p in sig.phrases) and len(sig.phrases) <= 5
    assert ai.keyword_signal("Growth is solid.").score == 15
    many = " ".join(ai.TEXT_CUES)
    assert ai.keyword_signal(many).score == 100


def test_clean_signal_clamps_and_keeps_exact_phrases():
    text = "Reserves declined sharply amid arrears."
    sig = ai._clean_signal({"score": 140, "phrases": ["Reserves declined", "made up phrase"], "summary": "Stress."}, text)
    assert sig.score == 100 and sig.phrases == ["Reserves declined"]


def test_score_statement_persists_and_feeds_t(seeded_db):
    from core.scoring import country_scores

    sig, used = ai.score_statement("EG", "Egypt faces a financing gap and arrears.")
    assert not used and sig.score == 15 + 13 * 2
    row = country_scores().set_index("iso2").loc["EG"]
    assert row["T"] == sig.score and row["layer_sources"]["T"] == "keywords"


def test_no_recommendation_language_in_ui_copy():
    """No buy/sell/hold/consider/reduce anywhere in pages, UI components or AI fallbacks."""
    pattern = re.compile(r"\b(buy|sell|hold|consider|reduce)\b", re.IGNORECASE)
    files = list((ROOT / "pages").glob("*.py")) + list((ROOT / "ui").glob("*.py")) + [ROOT / "app.py"]
    offenders = []
    for f in files:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert offenders == []
