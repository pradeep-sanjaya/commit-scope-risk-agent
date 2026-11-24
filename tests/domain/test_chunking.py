from scope_agent.domain.chunking import chunk


def test_chunk_budget_and_skipped():
    diffs = [(f"f{i}", "x" * 40_000) for i in range(14)]
    chunks, skipped = chunk(diffs, max_tokens=60_000, max_chunks=3)
    assert 0 < len(chunks) <= 3
    assert skipped
    sent = [p for c in chunks for p, _ in c]
    assert sent + skipped == [p for p, _ in diffs]


def test_everything_fits_one_chunk():
    chunks, skipped = chunk([("a", "x" * 40), ("b", "y" * 40)], 1000, 3)
    assert len(chunks) == 1 and skipped == []


def test_oversize_single_diff_truncated_not_skipped():
    chunks, skipped = chunk([("a", "x" * 100_000)], 1000, 3)
    assert skipped == []
    text = chunks[0][0][1]
    assert text.endswith("\n... [truncated]")
    assert (len("a") + len(text)) // 4 <= 750


def test_empty_input():
    assert chunk([], 1000, 3) == ([], [])
