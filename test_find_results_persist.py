from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_failed_artwork_image_never_removes_find_result_card():
    js = (ROOT / "static/js/app.js").read_text()
    start = js.index("function bindArtworkImageFallbacks(")
    end = js.index("function artworkPreviewUrl(", start)
    fallback = js[start:end]
    assert "card.remove()" not in fallback
    assert "savedThumb.remove()" not in fallback
    assert "art-image-unavailable" in fallback
    assert "image.style.display = 'none'" in fallback
    assert "Image unavailable" in fallback


def test_image_failure_placeholder_keeps_artwork_card_content_and_actions():
    js = (ROOT / "static/js/app.js").read_text()
    start = js.index("function renderCard(")
    end = js.index("function renderLearningGuide(", start)
    card = js[start:end]
    assert '<article class="art-card' in card
    assert 'class="art-body"' in card
    assert 'class="art-actions"' in card
    css = (ROOT / "static/css/style.css").read_text()
    assert ".art-image-unavailable {" in css


def test_find_results_are_rendered_from_response_after_request_completes():
    js = (ROOT / "static/js/app.js").read_text()
    start = js.index("async function runPendingRequest()")
    end = js.index("function renderTasteSummary(", start)
    run = js[start:end]
    assert run.index("state.searchRunActive = false;") < run.index("renderResults(data);")
    assert "saveLastResult(data);" in run
    assert "saveRecent(historyRequest, data);" in run
