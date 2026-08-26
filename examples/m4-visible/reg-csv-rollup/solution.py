import csv
import io


def rollup(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, amount in csv.reader(io.StringIO(text)):
        if name == "name":
            continue
        result[name] = result.get(name, 0) + int(amount)
    return result
