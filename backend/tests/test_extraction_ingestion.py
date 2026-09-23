"""AD-2a: the ingestion port - a page with no embedded text must route to the
attached PDF's own page image, never be read as though the protocol simply
said nothing there.

Claude's Messages API already understands PDF documents natively, including
scanned pages, when one is attached as a "document" content block - so this
does not need a separate pypdfium2-rendering pipeline the way the legacy
Ollama path does. It needs three things wired together: per-page text-status
classification (pages_from_pdf), an explicit marker so the model knows which
pages to fall back to the image for (document_text), and the native PDF
attached only when at least one page actually needs it (AnthropicClient).
"""

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.extraction.contracts import DocumentPage
from app.extraction.documents import document_text, load_pages, pages_from_pdf
from app.extraction.llm_client import AnthropicClient


def page(number: int, text: str, status: str = "text") -> DocumentPage:
    return DocumentPage(page_number=number, text=text, text_status=status)


# --- classification: pages_from_pdf ------------------------------------------

def test_a_normal_text_page_is_classified_as_text(monkeypatch):
    import protocol_document_index as index_module

    monkeypatch.setattr(
        index_module, "_extract_pdf_text_pages",
        lambda pdf_bytes: [
            "Table 2: Schedule of Assessments. This study evaluates the safety and "
            "efficacy of the investigational product across screening, treatment, "
            "and follow-up periods, with assessments performed at each scheduled "
            "clinic visit as detailed in the sections below."
        ],
    )
    pages = pages_from_pdf(b"fake-pdf-bytes")

    assert pages[0].text_status == "text"


def test_a_scanned_page_with_no_text_is_classified_as_image_or_empty(monkeypatch):
    import protocol_document_index as index_module

    monkeypatch.setattr(
        index_module, "_extract_pdf_text_pages",
        lambda pdf_bytes: [
            "Table 2: Schedule of Assessments. This study evaluates the safety and "
            "efficacy of the investigational product across screening, treatment, "
            "and follow-up periods, with assessments performed at each scheduled "
            "clinic visit as detailed in the sections below.",
            "   \n\n  ",
        ],
    )
    pages = pages_from_pdf(b"fake-pdf-bytes")

    assert pages[0].text_status == "text"
    assert pages[1].text_status == "image_or_empty"


# --- document_text marks exactly the pages that need the fallback ------------

def test_document_text_marks_only_pages_with_no_usable_text():
    rendered = document_text([
        page(1, "Real schedule text here.", "text"),
        page(2, "", "image_or_empty"),
        page(3, "A little text.", "sparse_text"),
    ])

    assert "USE THE ATTACHED PDF" not in rendered.split("--- page 2")[0]
    page_2_block, page_3_block = rendered.split("--- page 2")[1], rendered.split("--- page 3")[1]
    assert "scanned or image-only page" in page_2_block.split("--- page 3")[0]
    assert "scanned or image-only page" in page_3_block


# --- AnthropicClient: the document block actually reaches the model call -----

class _RecordingMessages:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        class _Block:
            type = "text"
            text = '{"claims": []}'
        class _Response:
            content = [_Block()]
        return _Response()


class _RecordingAnthropic:
    def __init__(self):
        self.messages = _RecordingMessages()


def test_the_document_block_is_sent_only_when_pdf_bytes_are_set(monkeypatch):
    fake = _RecordingAnthropic()
    client = AnthropicClient(api_key="test-key", document_text="some text")
    monkeypatch.setattr(client, "_client", lambda: fake)

    client.complete(system="sys", prompt="prompt")

    content = fake.messages.calls[0]["messages"][0]["content"]
    assert not any(item.get("type") == "document" for item in content)


def test_the_document_block_carries_the_real_pdf_bytes_when_attached(monkeypatch):
    import base64

    fake = _RecordingAnthropic()
    pdf_bytes = b"%PDF-1.4 fake content"
    client = AnthropicClient(
        api_key="test-key", document_text="some text", pdf_bytes=pdf_bytes)
    monkeypatch.setattr(client, "_client", lambda: fake)

    client.complete(system="sys", prompt="prompt")

    content = fake.messages.calls[0]["messages"][0]["content"]
    document_block = next(item for item in content if item.get("type") == "document")
    assert document_block["source"]["media_type"] == "application/pdf"
    assert document_block["source"]["data"] == base64.standard_b64encode(pdf_bytes).decode("ascii")
    # The document must be readable before the prompt text that references it.
    assert content[0] is document_block


# --- GeminiClient: a second LLMClient behind the identical contract ----------

class _RecordingModels:
    def __init__(self):
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        class _Response:
            text = '{"claims": []}'
        return _Response()


class _RecordingGemini:
    def __init__(self):
        self.models = _RecordingModels()


def test_gemini_client_sends_no_pdf_part_when_none_is_attached(monkeypatch):
    from google.genai import types

    from app.extraction.llm_client import GeminiClient

    fake = _RecordingGemini()
    client = GeminiClient(api_key="test-key", document_text="some text")
    monkeypatch.setattr(client, "_client", lambda: fake)

    client.complete(system="sys", prompt="prompt")

    contents = fake.models.calls[0]["contents"]
    assert not any(isinstance(item, types.Part) for item in contents)


def test_gemini_client_attaches_the_real_pdf_bytes_when_set(monkeypatch):
    from google.genai import types

    from app.extraction.llm_client import GeminiClient

    fake = _RecordingGemini()
    pdf_bytes = b"%PDF-1.4 fake content"
    client = GeminiClient(
        api_key="test-key", document_text="some text", pdf_bytes=pdf_bytes)
    monkeypatch.setattr(client, "_client", lambda: fake)

    client.complete(system="sys", prompt="prompt")

    contents = fake.models.calls[0]["contents"]
    part = next(item for item in contents if isinstance(item, types.Part))
    assert part.inline_data.data == pdf_bytes
    assert part.inline_data.mime_type == "application/pdf"
    # The PDF must be readable before the prompt text that references it.
    assert contents[0] is part


def test_gemini_client_returns_the_response_text(monkeypatch):
    from app.extraction.llm_client import GeminiClient

    fake = _RecordingGemini()
    client = GeminiClient(api_key="test-key")
    monkeypatch.setattr(client, "_client", lambda: fake)

    assert client.complete(system="sys", prompt="prompt") == '{"claims": []}'


def test_gemini_client_wraps_a_provider_failure_as_extraction_model_error(monkeypatch):
    from app.extraction.llm_client import ExtractionModelError, GeminiClient

    class _BrokenModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("quota exceeded")

    class _BrokenGemini:
        models = _BrokenModels()

    client = GeminiClient(api_key="test-key")
    monkeypatch.setattr(client, "_client", lambda: _BrokenGemini())

    import pytest as _pytest
    with _pytest.raises(ExtractionModelError, match="quota exceeded"):
        client.complete(system="sys", prompt="prompt")


def test_gemini_client_without_a_key_raises_extraction_not_configured(monkeypatch):
    from app.extraction.llm_client import ExtractionNotConfigured, GeminiClient

    # A developer's real .env (loaded by server.py on import, even in tests)
    # may already have GEMINI_API_KEY set - api_key=None alone only pins the
    # constructor argument, not os.getenv's fallback inside it.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client = GeminiClient(api_key=None)

    with pytest.raises(ExtractionNotConfigured):
        client.complete(system="sys", prompt="prompt")


# --- load_pages: the raw PDF bytes survive the fetch, once ------------------

class _FakeStorage:
    def __init__(self, data: bytes, content_type: str):
        self._data = data
        self._content_type = content_type

    async def open(self, key: str):
        return self._data, self._content_type


@pytest.mark.asyncio
async def test_load_pages_returns_the_pdf_bytes_alongside_the_pages(monkeypatch):
    import storage as storage_module

    pdf_bytes = b"%PDF-1.4 fake content"
    monkeypatch.setattr(
        storage_module, "get_storage",
        lambda: _FakeStorage(pdf_bytes, "application/pdf"))
    monkeypatch.setattr(
        "protocol_document_index._extract_pdf_text_pages",
        lambda data: ["some page text " * 20],
    )

    pages, returned_bytes = await load_pages("private://protocol.pdf")

    assert returned_bytes == pdf_bytes
    assert len(pages) == 1


@pytest.mark.asyncio
async def test_load_pages_for_a_plain_text_upload_has_no_pdf_bytes_to_fall_back_to(monkeypatch):
    import storage as storage_module

    monkeypatch.setattr(
        storage_module, "get_storage",
        lambda: _FakeStorage(b"plain text protocol content", "text/plain"))

    pages, returned_bytes = await load_pages("private://protocol.txt")

    assert returned_bytes is None
    assert pages[0].text == "plain text protocol content"
