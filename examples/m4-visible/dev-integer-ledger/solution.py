from decimal import Decimal


def balance(entries: list[str]) -> int:
    return sum(int(Decimal(item) * 100) for item in entries)
