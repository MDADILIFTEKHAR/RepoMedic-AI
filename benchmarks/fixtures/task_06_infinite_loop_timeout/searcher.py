"""Linear searcher."""

def find_item(items: list, target: int) -> int:
    idx = 0
    while idx < len(items):
        if items[idx] == target:
            return idx
        # Bug: missing pointer increment causes infinite loop
    return -1
