# Priority 4 one-shot Runtime diagnostic authorization request

Status: **awaiting explicit user authorization; no execution is authorized**

- Plan ID: `a31414dfcaef392ffe33a648f715aff9d2ce55812ee066064fcbe646759adcfe`
- Request ID: `e368f68cecceb854d2947149ab6c7ad4e230c45549d9aef00fa92b42981b742a`
- Case: `p4-dev-json-pointer`
- Scope: one development-only Trial, exactly one Attempt, no retry
- Bounds: concurrency 1; Agent 300s; Attempt envelope 360s; total wall 900s
- Provider: authenticated OpenAI Codex generation only
- Output: one diagnostic-only controlled terminal artifact
- Pre-terminal diagnostic receipt: at most one fixed value-free code; no raw Runtime evidence
- Excluded: Candidate, held-out, smoke, Study, matrix, or second Attempt authority
