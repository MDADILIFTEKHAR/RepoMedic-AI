"""Inventory pricing calculator."""

def calculate_total(price: float, quantity: int) -> float:
    # Bug: returns 0.0 for standard quantities
    if quantity >= 10:
        return price * quantity * 0.9
    return 0.0
