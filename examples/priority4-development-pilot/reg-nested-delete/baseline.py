def delete_path(document: dict[str, object], path: str) -> None:
    document.pop(path, None)
