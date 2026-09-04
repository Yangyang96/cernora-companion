def sorted_names(names: list[str]) -> list[str]:
    import re

    def key(name: str) -> list[object]:
        parts: list[object] = []
        for chunk in re.findall(r"\d+|\D+", name):
            parts.append(int(chunk) if chunk.isdigit() else chunk.lower())
        return parts

    return sorted(names, key=key)
