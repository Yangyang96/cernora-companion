from urllib.parse import quote


def encode(pairs: list[tuple[str, str]]) -> str:
    return "&".join(f"{quote(key)}={quote(value)}" for key, value in dict(pairs).items())
