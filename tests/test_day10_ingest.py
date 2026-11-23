from app.document_ingest import _page_chunks


def test_page_chunks_preserve_line_order_without_duplicates():
    text = "".join(f"Metric {i} 100 200 300\n" for i in range(200))
    chunks = _page_chunks(text, target_chars=200)
    assert len(chunks) > 3
    assert "\n".join(chunks).splitlines() == text.splitlines()


def test_a_single_large_financial_row_is_not_split():
    text = "Net interest income " + " ".join(str(i) for i in range(60))
    chunks = _page_chunks(text, target_chars=20)
    assert chunks == [text]
