# Priority 4 one-shot Runtime diagnostic authorization request

Status: **awaiting explicit user authorization; no execution is authorized**

- Plan ID: `af27b05cbc54b6649856bac996106c21794de3f769bb75f9b98d50dfed87f2bd`
- Request ID: `18ecdf5e3778dbef994fa9733e17cf012bcc82e71c0bd5fef0681dfb07e3763a`
- Case: `p4-dev-json-pointer`
- Scope: one development-only Trial, exactly one Attempt, no retry
- Bounds: concurrency 1; Agent 300s; Attempt envelope 360s; total wall 900s
- Provider: authenticated OpenAI Codex generation only
- Output: one diagnostic-only controlled terminal artifact
- Pre-terminal diagnostic receipt: at most one fixed value-free code; no raw Runtime evidence
- Excluded: Candidate, held-out, smoke, Study, matrix, or second Attempt authority
