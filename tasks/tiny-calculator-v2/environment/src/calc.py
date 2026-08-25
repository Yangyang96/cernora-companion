"""Integer expression calculator with an intentionally incomplete parser."""


def evaluate(expression: str) -> int:
    """Evaluate a small integer expression."""

    text = expression.strip()
    if not text:
        raise ValueError("empty expression")
    if "//" in text:
        left, right = text.split("//", 1)
        return int(left) // int(right)
    if "+" in text:
        left, right = text.split("+", 1)
        return int(left) + int(right)
    return int(text)
