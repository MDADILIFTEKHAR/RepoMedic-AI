from discount import calculate_discounted_price

def test_discount_standard():
    assert calculate_discounted_price(100.0, 0.20) == 80.0

def test_discount_zero():
    assert calculate_discounted_price(50.0, 0.0) == 50.0

def test_discount_half():
    assert calculate_discounted_price(200.0, 0.5) == 100.0
