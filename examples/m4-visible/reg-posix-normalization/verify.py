import runpy
import sys

normalize = runpy.run_path(sys.argv[1])["normalize"]
assert normalize("src/./pkg/../main.py") == "src/main.py"
try:
    normalize("../../outside")
except ValueError:
    pass
else:
    raise AssertionError("path escape was accepted")
