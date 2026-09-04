def vary_fields(lines: list[str]) -> tuple[str, ...]:
    fields = {field.strip().lower() for line in lines for field in line.split(",") if field.strip()}
    if "*" in fields:
        return ("*",)
    return tuple(sorted(fields))
