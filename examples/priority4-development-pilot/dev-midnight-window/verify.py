import runpy
import sys

active = runpy.run_path(sys.argv[1])["active"]
assert active(23 * 60 + 45, 23 * 60 + 30, 30)
assert active(15, 23 * 60 + 30, 30)
assert not active(30, 23 * 60 + 30, 30)
assert not active(12 * 60, 23 * 60 + 30, 30)
assert active(10 * 60, 9 * 60, 17 * 60)
assert not active(10 * 60, 10 * 60, 10 * 60)
try:
    active(1440, 0, 1)
except ValueError:
    pass
else:
    raise AssertionError("out-of-range minute was accepted")
