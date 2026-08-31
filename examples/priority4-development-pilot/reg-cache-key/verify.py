import runpy
import sys

cache_key = runpy.run_path(sys.argv[1])["cache_key"]
left = {"outer": {"z": 1, "a": [3, {"y": 2, "x": 1}]}, "name": "café"}
right = {"name": "café", "outer": {"a": [3, {"x": 1, "y": 2}], "z": 1}}
assert cache_key(left) == cache_key(right)
assert cache_key({"items": [1, 2]}) != cache_key({"items": [2, 1]})
assert " " not in cache_key(left)
assert "café" in cache_key(left)
