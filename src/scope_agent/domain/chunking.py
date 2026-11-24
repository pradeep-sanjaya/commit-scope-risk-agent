TRUNCATED = "\n... [truncated]"
USABLE_FRACTION = 0.75  # the rest is reserved for ticket context and the prompt


def _tokens(path: str, text: str) -> int:
    return (len(path) + len(text)) // 4


def truncate_to_fit(path: str, text: str, usable: int) -> str:
    """Shorten text so path + text (with marker) stays within `usable` tokens."""
    keep = max(usable * 4 - len(path) - len(TRUNCATED), 0)
    return text[:keep] + TRUNCATED


def chunk(
    diffs: list[tuple[str, str]], max_tokens: int, max_chunks: int
) -> tuple[list[list[tuple[str, str]]], list[str]]:
    """Greedily pack diffs into chunks; returns (chunks, skipped paths).

    An oversize single diff is truncated rather than skipped. Diffs that do not fit
    once max_chunks chunks exist are skipped.
    """
    usable = int(max_tokens * USABLE_FRACTION)
    chunks: list[list[tuple[str, str]]] = []
    skipped: list[str] = []
    used = 0
    for path, text in diffs:
        cost = _tokens(path, text)
        if cost > usable:
            text = truncate_to_fit(path, text, usable)
            cost = _tokens(path, text)
        if chunks and used + cost <= usable:
            chunks[-1].append((path, text))
            used += cost
        elif len(chunks) < max_chunks:
            chunks.append([(path, text)])
            used = cost
        else:
            skipped.append(path)
    return chunks, skipped


def was_truncated(text: str) -> bool:
    return text.endswith(TRUNCATED)
