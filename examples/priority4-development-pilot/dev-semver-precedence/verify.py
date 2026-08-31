import runpy
import sys

compare = runpy.run_path(sys.argv[1])["compare"]
assert compare("1.10.0", "1.9.9") > 0
assert compare("1.0.0", "1.0.0-rc.1") > 0
assert compare("1.0.0-alpha.2", "1.0.0-alpha.10") < 0
assert compare("1.0.0-alpha", "1.0.0-alpha.1") < 0
assert compare("1.0.0-1", "1.0.0-alpha") < 0
assert compare("2.0.0", "2.0.0") == 0
