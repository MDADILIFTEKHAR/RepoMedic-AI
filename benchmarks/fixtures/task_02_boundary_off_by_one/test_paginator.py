from paginator import paginate

def test_first_page():
    data = list(range(10))
    page1 = paginate(data, page=1, page_size=5)
    assert page1 == [0, 1, 2, 3, 4]

def test_second_page():
    data = list(range(10))
    page2 = paginate(data, page=2, page_size=5)
    assert page2 == [5, 6, 7, 8, 9]
