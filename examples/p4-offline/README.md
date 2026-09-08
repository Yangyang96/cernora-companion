# Current offline comparison fixture

This synthetic fixture binds the current pi-era controlled contracts and the accepted Core
0.1.4 wheel. All twelve Trials are infrastructure-unavailable lifecycle records. The expected
comparison is `uncertain`; this is a packaging and strict-reload check, not Agent performance
or positive-improvement evidence.

The conformance test reconstructs all three records from the existing deterministic test
builders and verifies their cross-artifact identities. The historical `examples/m3-offline`
fixture remains unchanged and is intentionally rejected by current Runtime contracts.

The current wheel verifier defaults to this directory. It creates three independent Batch and
Comparison packages, denies evaluation-time network access, reloads each package and requires
byte-identical outputs. Historical wheel reproduction requires the historical toolchain.
