# Priority 4 one-shot Runtime diagnostic authorization request

Status: **awaiting explicit user authorization; no execution is authorized**

- Plan ID: `45fc67a38cb662fea6bab5a5deead3049b93005c18215e0e8c5dec725e991e0b`
- Request ID: `6ce143b2774fd21ece87c4144a12db81b0147f2de5d4b1ce16d6c32366f27f75`
- Case: `p4-dev-json-pointer`
- Scope: one development-only Trial, exactly one Attempt, no retry
- Bounds: concurrency 1; Agent 300s; Attempt envelope 360s; total wall 900s
- Provider: authenticated OpenAI Codex generation only
- Output: one diagnostic-only controlled terminal artifact
- Pre-terminal diagnostic receipt: at most one fixed value-free code; no raw Runtime evidence
- Excluded: Candidate, held-out, smoke, Study, matrix, or second Attempt authority
