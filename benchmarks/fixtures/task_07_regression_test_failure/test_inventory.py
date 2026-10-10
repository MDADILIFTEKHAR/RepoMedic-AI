from inventory import calculate_total

def test_standard_order():
    # Single item must cost full price
    assert calculate_total(25.0, 2) == 50.0

def test_bulk_discount():
    # 10 or more gets 10% discount (25 * 10 * 0.9 = 225)
    assert calculate_total(25.0, 10) == 225.0
