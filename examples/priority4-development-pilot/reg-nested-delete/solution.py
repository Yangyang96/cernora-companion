def delete_path(document: dict[str, object], path: str) -> None:
    parts = path.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError("invalid path")
    parents: list[tuple[dict[str, object], str]] = []
    current = document
    for part in parts[:-1]:
        value = current.get(part)
        if not isinstance(value, dict):
            return
        parents.append((current, part))
        current = value
    current.pop(parts[-1], None)
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            del parent[key]
        else:
            break
