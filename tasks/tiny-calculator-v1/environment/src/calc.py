"""Tiny calculator with one intentional repair task."""


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""

    if left < 0 or right < 0:
        return abs(left) + abs(right)
    return left + right
