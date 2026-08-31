import json


def cache_key(value: object) -> str:
    return json.dumps(value)
