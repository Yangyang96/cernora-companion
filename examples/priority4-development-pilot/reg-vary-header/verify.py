import runpy
import sys

vary_fields = runpy.run_path(sys.argv[1])["vary_fields"]
assert vary_fields(["Accept-Encoding, User-Agent", "user-agent", " X-Mode "]) == (
    "accept-encoding",
    "user-agent",
    "x-mode",
)
assert vary_fields(["Accept-Encoding", "*"]) == ("*",)
assert vary_fields(["", " , "]) == ()
