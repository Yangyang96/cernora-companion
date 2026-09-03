def intersect(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int] | None:
    lo = max(a[0], b[0])
    hi = min(a[1], b[1]) - 1
    return (lo, hi)
