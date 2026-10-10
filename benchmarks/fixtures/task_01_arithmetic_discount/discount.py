"""Discount calculation module."""

def calculate_discounted_price(price: float, rate: float) -> float:
    """Return final discounted price given original price and discount rate (0..1)."""
    # Bug: returns discount amount instead of discounted price
    return price * rate
