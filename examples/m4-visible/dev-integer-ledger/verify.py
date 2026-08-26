import runpy
import sys

balance = runpy.run_path(sys.argv[1])["balance"]
assert balance(["0.10", "0.20", "-0.30"]) == 0
assert balance(["90071992547409.91", "-90071992547409.90"]) == 1
