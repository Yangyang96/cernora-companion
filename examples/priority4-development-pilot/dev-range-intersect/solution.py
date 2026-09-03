def intersect(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int] | None:
    for name, (start, end) in (("a", a), ("b", b)):
        if start >= end:
            raise ValueError(f"range {name} is empty or inverted")
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return (lo, hi) if lo < hi else None
