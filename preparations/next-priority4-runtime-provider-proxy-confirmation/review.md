# Priority 4 one-shot Runtime diagnostic authorization request

Status: **awaiting explicit user authorization; no execution is authorized**

- Plan ID: `675e6ec549a8610a2fca8400ffc33d10a457c75f4fcca7877e77732738827c67`
- Request ID: `8090fc26c8320108f82bea5e9cde188928199c18e2d5790989a9528a5aeb0c4c`
- Case: `p4-dev-json-pointer`
- Scope: one development-only Trial, exactly one Attempt, no retry
- Bounds: concurrency 1; Agent 300s; Attempt envelope 360s; total wall 900s
- Provider: authenticated OpenAI Codex generation only
- Output: one diagnostic-only controlled terminal artifact
- Pre-terminal diagnostic receipt: at most one fixed value-free code; no raw Runtime evidence
- Excluded: Candidate, held-out, smoke, Study, matrix, or second Attempt authority
