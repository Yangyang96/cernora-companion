def normalize(path: str) -> str:
    parts: list[str] = []
    if path.startswith("/") or "\\" in path:
        raise ValueError("not relative POSIX")
    for part in path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ValueError("escape")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise ValueError("empty")
    return "/".join(parts)
