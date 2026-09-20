# Conversational study-note workflow (v1.3 implementation preview)

This workflow is separate from the v1.2 read-only search registry. The submission
service is opt-in and accepts AI drafts; it never grants human approval or direct
Notion/Drive access. Code and real-client acceptance are tracked separately in
`handoff.md`. Remote-client transport selection and validation are still pending.

## Client behavior

Use the submission workflow only when the user requests a saved study note. Resolve
the exact Session with the existing ULS search tools if necessary; present ambiguous
candidates instead of choosing one silently. A normal question does not imply a save.

1. Call `request_study_note` with a stable idempotency key, exact Session, requested
   evidence mode and optional learner instructions. Reuse that key only for the same
   input. Default mode is `transcript-only.v1`. Use `confirmed-lecture.v1` only when
   requested; `provisional-selected.v1` requires explicit material selection.
2. Retain the returned client request ID. Call `get_study_note_status` with that ID
   to discover preparation state and the exact grant. A pending request is not a
   generated or saved note. Avoid rapid repeated polling; surface a pending status
   if the worker has not prepared the request.
3. Obtain `get_study_note_evidence` for that request. Generate only from its allowed
   evidence and clearly labeled AI supplements. Do not browse Notion/Drive directly,
   invent locators or treat unverified materials as confirmed. Missing evidence and
   Partial coverage must remain visible. Do not silently reduce the requested mode.
4. Include goals, key concepts, worked examples, misconceptions and exercises with
   folded solutions. Keep SOURCE, AI and USER provenance distinct. Label external
   explanations as external. Do not invent a professor-provided proof; acknowledge
   missing derivations. Structural validation alone does not establish correctness.
5. Call `submit_study_note_draft` with the issued grant and draft. Identical retries
   reuse the same payload; never change the body behind an already-consumed grant.
   Follow structural rejection feedback; the third invalid submission fails the
   attempt. An expired or stale grant is not permission to reuse old evidence.
6. Check status with the original request ID. Say saved only after verified storage;
   distinguish published, Partial, awaiting publication, failed and reconciliation
   states. If a user edit blocks publication, preserve it and show the new saved
   artifact link. `cancel_study_note_request` records a cancellation request. Report
   cancellation as pending until the worker applies it; it cannot undo a provider
   write that already happened or another active request reference.

Never set or claim `Material Usage.Verified` or `Exam.Scope Confirmed`. Generated text
remains AI content even when it quotes SOURCE evidence. Never describe an unknown
write outcome as a confirmed failure and repeatedly create a replacement.

## Local operator entry

The implementation exposes `uls --config /absolute/path/config.yaml study-notes
status` and a separate `study-notes local` stdio entry. Local launch resolves no
provider write credentials. The primary worker must separately be configured and
running to prepare evidence and publish drafts. Enabling the submission entry alone
does not start that worker, provision schemas or establish remote-client support.

Use the target client's supported tool-permission mechanism. Permission handling
varies across clients; this document does not claim each tool call has a human
approval dialog. Actual client E2E and one generated teaching-quality sample are
required before marking that client workflow ready.
