def split_record(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    in_quotes = False
    index = 0
    while index < len(line):
        char = line[index]
        if in_quotes:
            if char == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    current.append('"')
                    index += 2
                    continue
                in_quotes = False
                index += 1
                continue
            current.append(char)
            index += 1
            continue
        if char == '"':
            in_quotes = True
            index += 1
            continue
        if char == ",":
            fields.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if in_quotes:
        raise ValueError("unterminated quoted field")
    fields.append("".join(current))
    return fields
