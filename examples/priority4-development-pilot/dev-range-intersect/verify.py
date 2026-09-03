import runpy
import sys

intersect = runpy.run_path(sys.argv[1])["intersect"]
assert intersect((0, 5), (3, 9)) == (3, 5)
assert intersect((3, 9), (0, 5)) == (3, 5)
assert intersect((0, 5), (5, 10)) is None
assert intersect((5, 10), (0, 2)) is None
assert intersect((0, 5), (2, 3)) == (2, 3)
for empty_or_inverted in ((5, 5), (9, 3)):
    try:
        intersect(empty_or_inverted, (0, 10))
    except ValueError:
        pass
    else:
        raise AssertionError("empty or inverted range was accepted")
    try:
        intersect((0, 10), empty_or_inverted)
    except ValueError:
        pass
    else:
        raise AssertionError("empty or inverted range was accepted")
