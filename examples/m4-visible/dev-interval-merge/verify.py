import runpy
import sys

merge = runpy.run_path(sys.argv[1])["merge"]
assert merge([(1, 2), (2, 4), (7, 9)]) == [(1, 4), (7, 9)]
assert merge([(-3, -1), (-2, 0)]) == [(-3, 0)]
