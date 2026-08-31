import runpy
import sys

delete_path = runpy.run_path(sys.argv[1])["delete_path"]
document = {"a": {"b": {"gone": 1, "keep": 0}}, "other": False}
delete_path(document, "a.b.gone")
assert document == {"a": {"b": {"keep": 0}}, "other": False}
delete_path(document, "a.b.keep")
assert document == {"other": False}
delete_path(document, "missing.child")
assert document == {"other": False}
try:
    delete_path(document, "bad..path")
except ValueError:
    pass
else:
    raise AssertionError("empty path segment was accepted")
