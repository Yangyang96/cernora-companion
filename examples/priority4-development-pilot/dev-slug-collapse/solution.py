def slugify(text: str, separator: str = "-") -> str:
    collapsed: list[str] = []
    pending_separator = False
    for char in text.lower():
        if char.isalnum():
            if pending_separator and collapsed:
                collapsed.append(separator)
            pending_separator = False
            collapsed.append(char)
        else:
            pending_separator = True
    return "".join(collapsed)
