"""Pagination utility."""

def paginate(items: list, page: int = 1, page_size: int = 5) -> list:
    """Return slice of items for 1-indexed page number."""
    # Bug: page=1 gives start = 5, skipping page 1 items
    start = page * page_size
    end = start + page_size
    return items[start:end]
