def active(minute: int, start: int, end: int) -> bool:
    if not all(0 <= value < 24 * 60 for value in (minute, start, end)):
        raise ValueError("minute outside one day")
    if start == end:
        return False
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end
