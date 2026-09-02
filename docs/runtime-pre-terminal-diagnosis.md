# Priority 4 Runtime Pre-terminal Diagnosis

Status: **root cause located and repaired — the operator proxy environment never reached the
agent container; the repaired chain produced the first live Agent behavioral failure in an
out-of-band probe; the fresh one-shot confirmation awaits exact user authorization**

This record begins with the offline diagnosis of six `runtime_pre_terminal_failure` Attempts from
repaired development pilot Plan
`33544aa6d8ec292daf8b69390c4731ee67ee70184f2e7cd788c3baeeb4824099`, then records the separately
authorized one-shot result and the follow-up offline repair.

## Retained evidence

The six distinct Cases each produced exactly one non-retry Attempt. Their durations were
319,607, 316,149, 316,877, 318,797, 319,974, and 318,220 milliseconds. Every Attempt used the
same 300-second Agent timeout and 360-second outer Attempt envelope, and every published lifecycle
was the same generic `runtime_pre_terminal_failure`. The complete custody replay has no orphan,
ambiguous claim, or incident receipt.

The published artifacts intentionally contain no raw Harbor result, Runtime transcript, exception
text, or provider output. Therefore the historical Attempts cannot be retroactively asserted to be
timeouts. Their correct evidentiary status remains `inconclusive`.

## First confirmed classifier defect

Harbor 0.16.1 creates `agent_result` before entering the Agent call. If its `asyncio.wait_for`
expires, `SingleStepTrial` records `AgentTimeoutError`, retains that initialized Agent result, and
continues through the enabled Verifier before writing the closed Trial result.

The companion classifier previously checked whether `agent_result` or `verifier_result` was
present before it inspected `exception_info.exception_type`. A normal Harbor Agent timeout thus
took the generic `runtime_pre_terminal_failure` branch solely because `agent_result` had already
been initialized. The development-pilot policy then safely discarded the private temporary Harbor
tree, making the lost timeout discriminator unrecoverable. The 16–20 seconds beyond the frozen
Agent limit are consistent with Harbor/Codex cancellation, log/auth cleanup, container teardown,
strict result loading, and publication within the separate 360-second envelope.

Exact offline replays now model both closed Harbor shapes: the default initialized `AgentContext`
and the same context with usage metrics recovered from a partial Codex session. Both include
`AgentTimeoutError` with the authority-bound 300-second message, ordered Agent/exception/Verifier
timing, the enabled Verifier's exact reward result, and a 316-second process receipt. Before the
fix they deterministically reproduced the reported generic lifecycle. Later live evidence proved
that requiring a successful post-timeout Verifier result was itself too strong; the current rule
is recorded below.

This proves a classification defect and a mechanism consistent with the six-way timing/category
pattern. Because the closed historical artifacts omit the exception discriminator, it cannot
establish that mechanism as the explanation for any historical Runtime invocation.

## Authorized one-shot result

The user subsequently authorized exactly Plan
`6a342640911cade0ed3bd381e3ff80e0327d5230817a72ef6bac5d46e8d8bd4a` and request
`5b6bb9b88e8923cac40a5924597dbe5d9f9faa879731d307edfb8013034fba42`. Execution
`763fdfd345d440bfba69088d73c982ebe5e81d7af243593747f5e8416027dae3` consumed its sole claim
and published controlled Attempt `00500a16ae423c1fc8069fcf067551db67e06da67e3d28b617b7d40caa2ee416`
and artifact `9ffacba0442b7b0eb5289a62c0563a29cb365dd538011bc8f23005ece2c70db2`.
The Attempt completed in 319,467 milliseconds as non-retry
`runtime-pre-terminal-failure`; outcome
`2301be8f276435cabae85995727dfcd1525fbc4ad51656216801e1adfa0c479e` is `inconclusive`.
Strict replay, an idempotent second CLI invocation, credential scans, and container cleanup checks
all passed; the second invocation adopted the existing outcome and made no provider call.

This result supplies usable terminal evidence and closes the authorization without ambiguity, but
does not confirm the Agent-timeout hypothesis.

The attempt exposed a second deterministic defect in the diagnostic design: after private-value
scanning succeeded, the fail-closed branch intentionally collapsed every strict result rejection
to the same public lifecycle and discarded the temporary Harbor tree. That preserved secrecy and
terminality but also discarded the value-free identity of the failed invariant. Offline source
inspection ruled out one suspected reward-type mismatch: Harbor 0.16.1 parses the verifier's
textual `0` or `1` with `float(...)`, exactly matching the classifier's `0.0`/`1.0` contract.

The repaired diagnostic path now assigns only a closed enum code to each boundary: Agent result,
Agent timing shape, Verifier result, Verifier timing shape, timeout message, traceback, timestamp
parse, timezone/order, duration envelope, non-timeout phase evidence, missing Trial result, outer
process envelope, Trial-tree identity, job-config authority, general pre-terminal structure,
infrastructure, transient provider, or unclassified Runtime exception. The code is durably
published before the terminal artifact, is bound into the final outcome, and contains no exception
text, stdout/stderr, proxy endpoint, credential, transcript, or raw Harbor result. Exact offline
replay proves both accepted-timeout and rejected-timeout paths, while old custody remains
byte-for-byte replay compatible.

## Second authorized discriminator result and proven root cause

The user separately authorized exactly Plan
`b039fa42eafc1a85be6e79bbbb4952639f64d8b4b89d3f62184b68838058ff76` and request
`8f9243aef0b8d03cc2733a9a57ab7195699469d806e3d3d0cf9a046c47016149`.
Execution `6258319652a2afa8b2c12ffda53e026f700d2a25c1e96f7c82ed49bcb8966647`
consumed its only claim and published Attempt
`a01927ddf80360870629b3cddb5d018c3549771efe4903fea3f29b3b22d5df1c` and artifact
`42949fc87a785737f8edf4147d49160754516c96da72bce8976b061593f16b34` after
317,379 milliseconds. The terminal lifecycle remained non-retry
`runtime-pre-terminal-failure`, while diagnostic receipt
`e96603afb942e463cf1cfb04eaa4c11d3d1ab43cd55260dd9613e47506a652ea` recorded the fixed code
`job-config-authority-rejected`. Outcome
`113338c58409554e0d1be3444d59a919eced74f9af4fa7f7357cc948c43b60cc` is
`inconclusive`. Strict replay, a credential-free idempotent second CLI invocation, custody secret
scanning, and exact image container cleanup checks passed; the second invocation adopted the
existing outcome and made no provider call.

The fixed code moved the failure boundary ahead of timeout/result classification. An offline
reproduction now sends the executor's exact argv through Harbor 0.16.1's real Typer parser with
`--init`, which constructs the actual `JobConfig` without Docker or provider access. It proves four
deterministic mismatches in the companion validator:

- Harbor stores a local `-p` source in Job-level `tasks[]`; the validator expected `datasets[]`.
- Harbor stores the unresolved custom `-a` value in Job-level `agent.name` with
  `import_path=None`; the validator expected the normalized Trial-level representation.
- Job-level `agent.n_concurrent` is `None`; the validator expected Trial-level `1`.
- Harbor's retry exclusions are set-backed and may serialize in any order; the validator required
  one fixed list order.

This is the root cause of the systematic published `runtime_pre_terminal_failure`: the live Trial
had already closed, but the local JobConfig authority gate rejected its valid Harbor representation
before the classifier could inspect terminal evidence. The evidence does not prove whether the
underlying Agent invocation timed out; it proves why every invocation was collapsed into the same
outer lifecycle. The validator now distinguishes Job-level from Trial-level representations and
compares retry exclusions as an exact duplicate-free string set while retaining strict checks for
every other field and JSON type. The real-parser regression is red on the old implementation and
green on the repair.

## Minimal live-confirmation boundary

The next live action, if separately authorized under new wheel bytes and new Plan/request IDs,
should again be one development-only diagnostic Trial. It exists only to confirm that the repaired
JobConfig gate now permits the live terminal evidence to reach classification:

| Bound | Exact proposal |
|---|---|
| Case | `p4-dev-json-pointer` with its existing immutable task authority and image |
| Configuration | the same frozen Baseline prompt, Runtime, model, Harness, and resources |
| Repetitions | one |
| Attempts | exactly one; no retry |
| Concurrency | one |
| Agent timeout | 300 seconds |
| Attempt envelope | 360 seconds |
| Total wall bound | 900 seconds |
| Provider scope | authenticated OpenAI Codex generation only |
| Completion | stop after the single published Attempt |

Interpretation is predeclared:

- exact `timed_out` lifecycle: the validator repair is live-confirmed and the Agent timeout is also
  confirmed;
- evaluated terminal result: the validator repair is live-confirmed and the Trial supplies usable
  behavioral evidence;
- another `job-config-authority-rejected`: contradicts the offline repair and remains
  `inconclusive`;
- any other generic, missing, ambiguous, or structurally invalid evidence: `inconclusive` at its
  newly identified boundary.

Every outcome is diagnostic-only. It cannot become an Agent behavioral failure, Candidate
Development evidence, a prompt Treatment, a smoke result, held-out authority, or part of the
54-Trial Controlled Study. The closed request described below grants no execution authority.

The existing six-Case development-pilot control plane is not authority-compatible with this
proposal: its Plan and request contracts authorize six Trials and as many as twelve Attempts.
The one-Trial diagnostic therefore requires its own exact one-Case, one-Attempt control-plane
authority; the closed six-Case authorization must not be reused or narrowed by convention.

That dedicated control plane is now implemented offline in
`runtime_diagnostic_pilot.py` with the `run_runtime_diagnostic_pilot.py` entry point. It binds the
exact historical source Plan, selected task/specification, current wheel bytes, future custody
path, and 1/1/1 bounds. Preparation requires the exact Plan and request IDs. Execution durably
claims the only Attempt before provider work, atomically publishes a standard controlled terminal
artifact, adopts only an already-published exact artifact after a crash, emits value-free incident
receipts for ambiguity, and never retries. The exact closed timeout replay passes through this
complete control plane and closes as `timed-out` diagnostic evidence.

Both earlier one-shot proposals and their custody remain immutable historical evidence; both
authorities are fully consumed and cannot authorize a retry. A fresh confirmation proposal must
bind the newly repaired Companion wheel and new Plan/request IDs. Merely creating that closed
proposal grants no execution authority.

That fresh closed proposal was materialized at
`preparations/next-priority4-runtime-fix-confirmation`. It binds byte-identical Companion wheel
SHA-256 `9bfe8711049241a368e6259c9b68fa2759e94b297fa140f2e11edc98d28081c7`, Plan
`45fc67a38cb662fea6bab5a5deead3049b93005c18215e0e8c5dec725e991e0b`, and request
`6ce143b2774fd21ece87c4144a12db81b0147f2de5d4b1ce16d6c32366f27f75`. At materialization it
was `awaiting-user-authorization`, recorded `execution_authorized=false`, and had no custody or
claim.

## Third authorized result and TrialConfig defect

The user exactly authorized Plan
`45fc67a38cb662fea6bab5a5deead3049b93005c18215e0e8c5dec725e991e0b` and request
`6ce143b2774fd21ece87c4144a12db81b0147f2de5d4b1ce16d6c32366f27f75`.
Execution `f8064b79fd676c38258d453f0bc580e8b39ce6f7b067339b34d4d27012b37f78`
consumed its only claim and published Attempt
`87883eebba3023b1ee47ca293b5ee2a2d70221a7578f5114b1e77ea861d6a64f` and artifact
`4b4f15f3994109fa87c5621bca08152217664a5a525b4170a18ff5cddd2027cf` after
318,513 milliseconds. The result advanced past the repaired JobConfig gate, then closed as
non-retry `runtime-pre-terminal-failure` with diagnostic code
`preterminal-structure-rejected`. Outcome
`b2d195bdc685131431aebf7b8f7243d5b0f772ddba0636dc3eca2bcdd7905418` is
`inconclusive`. Strict replay, a credential-free idempotent second CLI invocation, custody secret
scanning, and exact-image container cleanup checks passed. The authorization is fully consumed.

The next offline minimization constructs Harbor's real `TrialConfig` from the exact parsed
`JobConfig` using the same field transfer as `Job._init_trial_configs`. Harbor does not normalize
the custom Agent at this boundary: the Trial retains `agent.name` equal to the custom import
string, `import_path=None`, and `n_concurrent=None`. The companion's Trial validator and fake
fixtures instead expected `name=None`, the custom string in `import_path`, and `n_concurrent=1`.
That deterministic mismatch necessarily rejects every otherwise valid Trial result before timeout
classification and therefore proves the cause of the third live discriminator. The end-to-end
executor test is red with Harbor's real Trial representation on the old validator and green after
the repair.

Job and Trial validation now share Harbor's actual Agent representation. A new fixed
`trial-config-authority-rejected` code isolates any future TrialConfig drift while retaining exact
recursive JSON type, field, task, environment, verifier, and Runtime checks.

The next closed confirmation proposal is materialized at
`preparations/next-priority4-runtime-trial-config-confirmation`. It binds byte-identical Companion
wheel SHA-256 `6587da0fd9841a7e9815ec136db3a60e6329c375f6f12645dea7812faa0132c8`,
Plan `af27b05cbc54b6649856bac996106c21794de3f769bb75f9b98d50dfed87f2bd`, and request
`18ecdf5e3778dbef994fa9733e17cf012bcc82e71c0bd5fef0681dfb07e3763a`. At materialization it was
`awaiting-user-authorization`, recorded `execution_authorized=false`, and had no custody or claim.
No earlier authorization could execute it.

## Fourth authorized result and post-timeout evidence defect

The user exactly authorized Plan
`af27b05cbc54b6649856bac996106c21794de3f769bb75f9b98d50dfed87f2bd` and request
`18ecdf5e3778dbef994fa9733e17cf012bcc82e71c0bd5fef0681dfb07e3763a`.
Execution `6c9a653e4fe2c8893a007d3cb932b081e7b2126cb3a04a3def9377453c5ba6b3`
consumed its only claim and published Attempt
`072b2eff738add49f6fb99d41039512beaa6e8a072e2cfe5fec50bad98de9083` and artifact
`dec64c439e2d9dfe4f3d927e79ff2e0d4f9220df1e694d091377b5f35062165e` after
318,294 milliseconds. Both repaired configuration gates passed. The fixed discriminator then
recorded `agent-timeout-verifier-result`, proving that the live result contained the exact
`AgentTimeoutError` path and passed Agent-result and Agent-timing validation before it reached the
post-timeout Verifier-result requirement. Outcome
`6d4e5f5736e29a69f3ceefdd8177166882906db203760e752c159193da17e9b7` remains
`inconclusive`; the authority is fully consumed and cannot be reused.

Offline reproduction with Harbor 0.16.1, the exact materialized controlled Verifier, the pinned
task image, and a provider-free sleeping Agent establishes both relevant shapes. A clean timeout
can retain `{"rewards":{"reward":0.0}}`; Harbor also records `AgentTimeoutError` before attempting
the Verifier, so any later Verifier exception leaves `verifier_result=null` while preserving the
complete Agent timeout exception and phase timing. The Verifier result is evaluation evidence,
not evidence that the Agent deadline expired. Making it mandatory therefore erased an already
proven terminal timeout whenever post-timeout evaluation could not complete.

The classifier now accepts a missing Verifier result only for the strictly validated
`AgentTimeoutError` path with complete ordered Agent and Verifier timing, exact timeout message and
traceback terminus, and bounded duration. This produces only a non-retry `timed_out` lifecycle:
it never creates a Runtime observation, RepairResult, evaluation, pass, or Candidate evidence.
Any present Verifier result must still have the exact Harbor reward shape and float value; malformed
or contradictory evidence remains `inconclusive`. The regression is red on the previous rule and
green on the repair.

The next live action must be a new one-Case, one-Attempt development-only confirmation bound to
new wheel bytes and fresh Plan/request IDs. Its sole purpose is to confirm that the repaired
classifier publishes the already evidenced live `AgentTimeoutError` as `timed_out`; it cannot
authorize a retry, Candidate construction, held-out work, smoke execution, or the Controlled
Study.

That closed proposal is materialized at
`preparations/next-priority4-runtime-timeout-classification-confirmation`. It binds byte-identical
Companion wheel SHA-256
`ea0f48a6a0db3e539b7b2b55942c72d677aabc8f9984cf57068750958b4dee5e`, Plan
`a31414dfcaef392ffe33a648f715aff9d2ce55812ee066064fcbe646759adcfe`, and request
`e368f68cecceb854d2947149ab6c7ad4e230c45549d9aef00fa92b42981b742a`. At materialization it was
`awaiting-user-authorization`, recorded `execution_authorized=false`, and had no custody or claim.
Creating it granted no execution authority.

## Fifth authorized result and live confirmation

The user exactly authorized that Plan and request. Execution
`823871e8102fda1c85a97e2698c9432ca18c2c94c1806a0652e1a3d521e94950`
consumed its only claim and published Attempt
`5b10f7e437715350cd6e93de12ea54f94aa93917e3cfc0d30a2ed257240aeb2b` and artifact
`6e0dc89ceaa15a04e73316f26796c2215ceaaf3018552be65e583e9c4b35152b` after
315,506 milliseconds. The lifecycle is non-retry `timed_out`, diagnostic code
`agent-timeout-evidence-accepted`, and outcome
`080dd83b1f899e95cb8f9d33dc6ecc30412c4e1a51cff35b5d74888d93e4fac4` is
`timed-out`. Runtime observation, RepairResult, evaluation, and Candidate evidence are all absent.

Strict replay, a credential-free idempotent second CLI invocation, custody secret scanning, and
exact-image container cleanup checks passed. The authorization is fully consumed. This confirms
the systemic pre-terminal publication repair and supplies usable terminal lifecycle evidence; it
does not claim that the Agent completed the repair task within its 300-second limit.

## Sixth boundary: proxy-diagnostic follow-up (2026-09-02)

Two additional one-shot diagnostics ran on the same `p4-dev-json-pointer` Case through a locally
selected HTTP/SOCKS proxy. Both remain outside every Candidate, pilot, smoke, held-out, and Study
boundary; neither produced an Agent observation.

- 300-second Agent timeout / 360-second envelope: the provider attempt completed, but the closed
  result failed controlled serialization (`ControlledAttemptSerializationError`,
  classification-recoverable=false) before an Attempt artifact could be published. The claim
  closed as an incident with no terminal Attempt and cannot be retried. This is the only retained
  diagnostic record in which the provider attempt is marked completed; the serialization boundary
  is not yet diagnosed.
- 600-second Agent timeout / 660-second envelope: the sole non-retry Attempt closed as `timed_out`
  (`agent-timeout-evidence-accepted`) after 617,761 milliseconds with no Runtime observation,
  RepairResult, or evaluation. The classifier repair is live-confirmed, and the Agent still does
  not complete within 600 seconds.

The driving command used a `proxy-diagnostic` schema family not preserved in the checked-out
source tree; the runs are recorded from their custody artifacts. No proxy endpoint, credential, or
Runtime home is recorded here. Any follow-up requires new wheel bytes and fresh exact Plan/request
authorization, and the timeout/budget freeze must be re-derived first: 600-second Attempts at the
108-Attempt bound exceed the frozen 43,200-second study budget.

## Root cause: operator proxy never reaches the agent container

An out-of-band probe retained the complete Harbor trial tree for the same Case at 300 seconds.
The trial closed with `AgentTimeoutError`, a zero reward, and a complete but unmodified
candidate; the agent rollout contains only system prompt events and the Codex log records
repeated reconnects up to 5/5, a WebSocket-to-HTTPS fallback that also failed, and two failed
model-list refreshes. The Agent therefore received zero model responses in 300 seconds.

Layer isolation proved where the chain breaks:

- the operator host reaches the provider through the selected proxy (HTTP 401 expected);
- the agent container reaches the selected proxy (TCP open) and reaches the provider through it
  when the proxy variables are set explicitly (HTTP 401 expected);
- the preinstalled Codex inside the container completes an exchange immediately when the proxy
  variables are set in its environment, and stalls without them.

Harbor 0.16.1 forwards the host process environment only to the docker-compose CLI for template
interpolation; it never places the projected proxy variables into the agent container, and its
Codex integration passes only the Codex home and auth variables to the agent exec. The runtime
policy requires the operator proxy, so every frozen Attempt stalled on a direct connection and
closed as an Agent timeout regardless of the frozen limit.

The repair overrides `TelemetryDisabledCodex.exec_as_agent` to merge the projected operator proxy
variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`) from the host process environment
into every agent-side container exec. Caller-provided variables win, and no endpoint value is
recorded in any artifact. Focused tests, Ruff, and strict mypy pass; live confirmation requires
new wheel bytes and fresh exact Plan/request authorization.

## Live end-to-end validation of the repair (out-of-band probe)

After the repair, the same out-of-band probe ran the same Case at 300 seconds. The trial closed
with no exception: the Agent completed within the frozen limit and produced a real evaluated
result with verifier reward `0.0` — a behavioral failure. The retained trajectory shows the Agent
inspecting the task, running its own decode-token checks, and rewriting the module with the
correct RFC 6901 escape order, while the frozen verifier rejects the result because the
`~2`-style invalid-escape validation was not added. This is the first live Agent behavioral
failure and the first live evaluated result on this chain; it remains engineering-only evidence
because the probe stands outside every Candidate, pilot, smoke, held-out, and Study boundary.

The fresh closed confirmation proposal binds Companion wheel
`b2dad2dea3de9227fb7b16a5bf6109c9f03a49e29f8258c3385a784e76f37d30`, Plan
`675e6ec549a8610a2fca8400ffc33d10a457c75f4fcca7877e77732738827c67`, and request
`8090fc26c8320108f82bea5e9cde188928199c18e2d5790989a9528a5aeb0c4c`. It awaits exact user
authorization; its existence grants no execution authority.

## First controlled live attempt after the repair (2026-09-02)

The user authorized that exact one-shot authority and its sole Attempt closed as non-retry
`timed_out` (`agent-timeout-evidence-accepted`) after 318,389 milliseconds. The classifier repair
therefore also holds on the controlled path, but this run produced no evaluated result. The
out-of-band probe had completed the same Case in roughly 68 monotonic seconds with a real
behavioral failure, so the divergence is environmental rather than a transport regression:
`pmset` records 131 sleep/wake cycles and system sleep is not prevented, and the host load
average was above 5 during the run, both of which can stall the Docker VM past the frozen
300-second Agent limit. Live execution therefore requires a non-sleeping host and low load before
the development pilot or 54-Trial Study. No Candidate, held-out, smoke, or Study evidence was
produced by this Attempt.
