import runpy
import sys

rollup = runpy.run_path(sys.argv[1])["rollup"]
assert rollup('name,amount\n"alpha,beta",2\nplain,3\n"alpha,beta",-1\n') == {
    "alpha,beta": 1,
    "plain": 3,
}
