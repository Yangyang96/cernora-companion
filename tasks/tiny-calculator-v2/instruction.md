# Complete the strict integer expression evaluator

Repair `evaluate(expression: str) -> int` in `src/calc.py`. It must parse decimal integer
literals, parentheses, unary `+` and `-`, and binary `+`, `-`, `*`, and `//`. Binary precedence
is `//` and `*` before `+` and `-`; operators at the same level associate left to right. Unary
operators may repeat and bind before binary operators. `//` truncates toward zero, not toward
negative infinity.

ASCII whitespace may appear between tokens but not inside a decimal literal or the two-character
`//` operator. Decimal literals must not contain a leading zero unless the literal is exactly
`0`. Every literal and every intermediate result must fit the signed 32-bit range
`[-2147483648, 2147483647]`. Raise `ValueError` for empty input, invalid or trailing tokens,
unbalanced parentheses, division by zero, or overflow. Return an exact built-in `int` otherwise.

You may edit only `/workspace/src/calc.py`. Do not edit `pyproject.toml`, any test file, task
metadata, or files outside `/workspace/src/calc.py`. Do not use the network. Finish after making
the smallest complete implementation.
