import runpy
import sys

split_record = runpy.run_path(sys.argv[1])["split_record"]
assert split_record("a,b,c") == ["a", "b", "c"]
assert split_record('a,"b,c",d') == ["a", "b,c", "d"]
assert split_record('"x""y",z') == ['x"y', "z"]
assert split_record("a,,b") == ["a", "", "b"]
assert split_record("a,") == ["a", ""]
assert split_record("") == [""]
try:
    split_record('"a,b')
except ValueError:
    pass
else:
    raise AssertionError("unterminated quoted field was accepted")
