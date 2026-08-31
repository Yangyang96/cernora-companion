import runpy
import sys

decode_token = runpy.run_path(sys.argv[1])["decode_token"]
assert decode_token("a~1b") == "a/b"
assert decode_token("m~0n") == "m~n"
assert decode_token("~01") == "~1"
try:
    decode_token("bad~2escape")
except ValueError:
    pass
else:
    raise AssertionError("invalid escape was accepted")
