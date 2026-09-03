import runpy
import sys

slugify = runpy.run_path(sys.argv[1])["slugify"]
assert slugify("Hello, World!") == "hello-world"
assert slugify("  double   space ") == "double-space"
assert slugify("MIXED Case!!!") == "mixed-case"
assert slugify("---") == ""
assert slugify("") == ""
assert slugify("!!!") == ""
assert slugify("a b", "_") == "a_b"
assert slugify(" dot  and , comma ") == "dot-and-comma"
