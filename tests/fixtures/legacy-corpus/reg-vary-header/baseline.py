def vary_fields(lines: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(lines)))
