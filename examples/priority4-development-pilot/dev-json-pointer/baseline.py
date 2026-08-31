def decode_token(token: str) -> str:
    return token.replace("~0", "~").replace("~1", "/")
