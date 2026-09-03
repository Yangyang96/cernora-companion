def slugify(text: str, separator: str = "-") -> str:
    return "".join(char if char.isalnum() else separator for char in text.lower())
