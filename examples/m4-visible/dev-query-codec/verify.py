import runpy
import sys

encode = runpy.run_path(sys.argv[1])["encode"]
assert encode([("tag", "a b"), ("tag", "x/y"), ("q&", "=z")]) == ("tag=a%20b&tag=x%2Fy&q%26=%3Dz")
