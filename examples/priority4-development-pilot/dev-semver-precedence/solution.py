def _version(value: str) -> tuple[tuple[int, int, int], tuple[str, ...] | None]:
    public, separator, prerelease = value.partition("-")
    parts = public.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError("invalid semantic version")
    release = tuple(int(part) for part in parts)
    return release, tuple(prerelease.split(".")) if separator else None


def _prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    for left_item, right_item in zip(left, right, strict=False):
        if left_item == right_item:
            continue
        left_number = left_item.isdigit()
        right_number = right_item.isdigit()
        if left_number and right_number:
            return (int(left_item) > int(right_item)) - (int(left_item) < int(right_item))
        if left_number != right_number:
            return -1 if left_number else 1
        return (left_item > right_item) - (left_item < right_item)
    return (len(left) > len(right)) - (len(left) < len(right))


def compare(left: str, right: str) -> int:
    left_release, left_pre = _version(left)
    right_release, right_pre = _version(right)
    if left_release != right_release:
        return (left_release > right_release) - (left_release < right_release)
    if left_pre is None or right_pre is None:
        return (left_pre is None) - (right_pre is None)
    return _prerelease(left_pre, right_pre)
