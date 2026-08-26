def balance(entries: list[str]) -> int:
    return round(sum(float(item) for item in entries) * 100)
