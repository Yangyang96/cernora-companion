def rollup(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines()[1:]:
        name, amount = line.split(",")
        result[name] = result.get(name, 0) + int(amount)
    return result
