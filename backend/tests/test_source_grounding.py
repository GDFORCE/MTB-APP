import io
from contextlib import closing

import pypdfium2 as pdfium
import pytest

from app.extraction.graph import DocumentPage
from app.extraction.llm_client import AnthropicClient, GeminiClient
from app.extraction.source_grounding import collective_count_visit, normalized_quote, page_packet, referenced_pages


@pytest.mark.parametrize("client_type", [AnthropicClient, GeminiClient])
def test_page_packets_contain_only_requested_pdf_pages_and_original_page_map(client_type):
    with closing(pdfium.PdfDocument.new()) as document:
        for width in (310, 320, 330, 340):
            document.new_page(width, 400).close()
        output = io.BytesIO()
        document.save(output)
    original = client_type(api_key="test-only", model="test-model", document_text="all pages", pdf_bytes=output.getvalue())
    scoped = page_packet(original, [DocumentPage(page_number=2, text="Current schedule"), DocumentPage(page_number=4, text="Footnotes")])
    with closing(pdfium.PdfDocument(scoped._pdf_bytes)) as packet:
        assert len(packet) == 2
        assert packet.get_page_size(0) == (320, 400)
        assert packet.get_page_size(1) == (340, 400)
    assert '"1": 2' in scoped._document_text and '"2": 4' in scoped._document_text
    assert "Current schedule" in scoped._document_text and "Footnotes" in scoped._document_text
    assert original._document_text == "all pages"
    assert scoped._model == original._model and scoped._api_key == original._api_key
    with pytest.raises(ValueError, match="outside"):
        original.for_document(document_text="bad page", page_numbers=[5])


def test_text_packets_do_not_keep_unrelated_document_content():
    original = GeminiClient(api_key="test-only", document_text="Historical study results and current requirements")
    scoped = page_packet(original, [DocumentPage(page_number=7, text="Day 1: ECG")])
    assert "Historical" not in scoped._document_text
    assert "Day 1: ECG" in scoped._document_text and "ORIGINAL PHYSICAL PAGE 7" in scoped._document_text


def test_referenced_appendices_are_retrieved_transitively_and_missing_sources_reported():
    pages = [DocumentPage(page_number=2, text="Evaluation (See Appendix B). See Table 4."),
        DocumentPage(page_number=9, text="Appendix B: Schedule\nRefer to Annex C."),
        DocumentPage(page_number=12, text="Annex C\nSee Appendix Z."),
        DocumentPage(page_number=14, text="Table 4. Visit windows")]
    selected, missing = referenced_pages(pages, {2})
    assert selected == {2, 9, 12, 14}
    assert missing == [{"page": 12, "reference": "Appendix Z"}]


def test_inline_reference_does_not_count_as_its_own_missing_appendix():
    pages = [DocumentPage(page_number=19, text="6.0 Pretreatment Evaluation (See Appendix B)")]
    assert referenced_pages(pages, {19})[1] == [{"page": 19, "reference": "Appendix B"}]


def test_statistical_population_counts_are_not_patient_visits():
    assert collective_count_visit("7 Patients Evaluated")
    assert collective_count_visit("19 Patients Evaluated")
    assert collective_count_visit("50 participants enrolled")
    for label in ["Day 7", "Cycle 19 Day 1", "Cohort 7", "Patients evaluated on Day 7", "Discontinuation visit"]:
        assert not collective_count_visit(label)


def test_quote_normalization_preserves_meaningful_numeric_differences():
    assert normalized_quote("ﬁrst\u00a0visit\r\nDay 1") == normalized_quote("first visit Day 1")
    assert normalized_quote("Day -1") != normalized_quote("Day 1")
    assert normalized_quote("1-2 weeks") != normalized_quote("1 week")
    assert normalized_quote("7 days") != normalized_quote("7 months")
