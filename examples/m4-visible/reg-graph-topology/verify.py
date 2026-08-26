import runpy
import sys

order = runpy.run_path(sys.argv[1])["order"]
assert order({"build": ["test"], "test": ["ship"], "ship": []}) == [
    "build",
    "test",
    "ship",
]
try:
    order({"a": ["b"], "b": ["a"]})
except ValueError:
    pass
else:
    raise AssertionError("cycle was accepted")
