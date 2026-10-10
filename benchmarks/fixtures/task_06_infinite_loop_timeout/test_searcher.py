import pytest
from searcher import find_item

def test_find_item():
    arr = [10, 20, 30, 40]
    assert find_item(arr, 30) == 2
    assert find_item(arr, 99) == -1
