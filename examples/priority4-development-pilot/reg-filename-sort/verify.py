import runpy
import sys

sorted_names = runpy.run_path(sys.argv[1])["sorted_names"]
assert sorted_names(["file2.txt", "file10.txt", "file1.txt"]) == [
    "file1.txt",
    "file2.txt",
    "file10.txt",
]
assert sorted_names(["v10", "v2", "V1"]) == ["V1", "v2", "v10"]
assert sorted_names(["a2b10", "a2b2", "a10b1"]) == ["a2b2", "a2b10", "a10b1"]
assert sorted_names([]) == []
assert sorted_names(["solo"]) == ["solo"]
