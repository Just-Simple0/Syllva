# Review evidence checker: exact reviewed-source pins — local candidate plan

## Authority and scope

Human authorized the recommended bounded implementation and independent reviews on 2026-10-03. Global application is NOT authorized. The orchestrator owns this plan, risk disposition, independent review and candidate acceptance; the continuous `gpt-6-luna/max` technical owner implements and verifies the accepted plan, including required review fixes. Actual orchestrator is installed `gpt-6.1-sol/high`; no new model assignment, no Jev call for an unchanged assignment.

The unchanged original is `/Users/admin/.codex/safety/state_check.py`, SHA-256 `f6b8df58b956919d6afcba6670f1395ef5c6e13fb31bef6ab85d1131dc4a4f97`. Its `_safe_relative()` applies `SECRET_PART` to every path component and rejects ordinary reviewed Python modules such as `credential_admission.py`. This is a consistency-validator source-classification correction. It does not grant filesystem permissions, authenticate reviewers, issue approval, disable hooks, install globally, accept ULS, or authorize commit/push.

Candidate files live only under `scripts/review_evidence_checker_candidate/`; private evidence under `.insane-review/checker-path-20261003/`. Original checker, hooks/config, ULS sources/tests, actual secret files and existing unrelated changes remain untouched. No actual dotenv, Keychain, provider, environment collection or foreign-workspace index.

## Independent pins

Embed an immutable mapping in candidate code of the nine exact paths and SHA-256 values in the separately reviewed `source_pins.json` inventory. That JSON is a review artifact ONLY; the candidate must never load it, accept pins/root from a task record, add a CLI bypass flag, or regenerate pins at runtime. The task record's hash remains untrusted comparison metadata. The eventual human-approved installed candidate bytes supply the independent expected pins.

The inventory is selected from the parent's already-reviewed whole-source Syllva snapshot, filtered to the nine ordinary Python filenames rejected by the original classifier, and checked against actual current bytes. It includes the two Notion/Google admission/service direct tests and direct CLI test as well as the six implementation modules. Exact hashes, sizes and the root path/device/inode are provided in the inventory. These metadata are not an approval token or a secret-file classification registry.

Bind the exception to the exact canonical root `/Users/admin/Project/Syllva` and the descriptor's pinned device/inode. A copied repository, replacement root directory, alias, symlink, stale source hash or newly added source name cannot inherit the exception. Changes require a newly reviewed candidate and separate application decision; no auto-refresh. Unexceptional paths continue with the original checker behavior.

## Minimal implementation boundary

Keep all original validation functions, command arguments, error text and exit status. Add a separate pinned-source reader invoked ONLY from `_verify_sources` for the exact independently pinned paths that the original classifier rejects. Every artifact read — record, package, manifests, selection evidence, responses and Gemini evidence — continues to use the original strict `_read_relative()` with the original `_safe_relative()`. A pinned source used as a package/evidence/response/record path is still rejected.

Before any pinned file open, verify exact known path, exact compiled pin versus the record's syntactically valid expected digest, canonical relative spelling, and `.py` basename. Preserve absolute/drive/backslash/NUL/dot-dot rejection. Directory components may not have secret-like names. Actual dotenv names, key/data paths and prohibited directory components remain hard-denied; only the specifically reviewed semantic basename is excepted. Unknown/forged pin or forbidden path fails before file open.

Require the POSIX secure-open capabilities (`O_NOFOLLOW`, `O_DIRECTORY`, `O_NONBLOCK` and descriptor-relative open support); otherwise this exception fails closed. Walk the absolute root components and then relative source parents with directory descriptors and no-follow opens. Check the opened root's device/inode against the independent root pin, not a fresh caller-supplied identity. Open the final source with no-follow and nonblocking flags, inspect with `fstat`, require a regular file with one link, and enforce a fixed 1 MiB cap and the exact pinned byte size. This prevents FIFO blocking and rejects symlink/hardlink/special-file aliases before content read. Close all descriptors on every failure.

Read the bounded bytes from that same descriptor and require SHA-256 equal both to the independent code pin and the record digest. Detect unstable identity/size metadata across the read as an additional fail-closed condition. Do not re-open the path to compare a different file, normalize/strip bytes, or change a mismatched record to match. Source read succeeds only on the exact pinned bytes. No source exception reaches `_verify_native_review`, `_verify_gemini_review` or artifact-path validation.

SHA-256 verification necessarily reads the authorized source bytes. It prevents successful acceptance of changed content; it does not guarantee that an adversary's replacement at an authorized source path contains no secret before read. Hard-denied paths and links are refused before read, but no new sandbox/isolation guarantee is claimed.

## Preserved gates

Preserve ordered source list/current hashes; package hash; native schema-2 original run/project/user/assistant/baseline identities; exact harvest argv; response wrapper and body hashes; selected model and actual effort; Pro-unavailable or quota fallback evidence; Gemini required/N/A shape and evidence; generic failure output. Never create a human approval, promote verification flags, or treat `ok:true` as reviewer authenticity or overall task acceptance.

## Meaningful checks

Use synthetic temporary files and public fixture bytes only. Fixture pins may be injected only by tests, never by a production input surface. Reproduce original rejection of a normal source basename and show intended pinned source-only success. Include full synthetic valid Native record through `validate` and CLI, followed by independent mutations of required gates:

- Exact current pins succeed; stale/forged record SHA, unknown source, wrong/copied/replaced root, changed source bytes/size and unsupported secure capabilities fail. Wrong declared digest and forbidden paths must not open the source.
- Absolute/traversal/drive/backslash/NUL/dotenv/key/data/secret-directory shapes fail before open. Symlink source/root/ancestor, hardlink, FIFO and nonregular sources fail without unsafe content read or hang. Include deterministic path-replacement/race observations at the descriptor boundary.
- A known pinned source supplied as an artifact still fails. Existing strict artifact paths and symlink rejection retain their behavior.
- Record/schema/ordered source/current/package hashes; native original-run/project/identity/body/wrapper/harvest argv/actual model/effort/max slider/fallback evidence; Gemini required/N/A mismatches remain fail-closed. Exercise original and candidate for gates unrelated to the intended source-name correction.
- Compare the baseline byte-for-byte with the unchanged global original. Verify changes are confined to the new source reader/compiled pins and `_verify_sources` dispatch. Owning lint/compile and a meaningful focused synthetic suite; no repeated ULS product suite needed because product sources are unchanged.

After synthetic checks, an explicitly diagnostic local candidate run may inspect the already authorized nonsecret current Syllva source/evidence paths in the current CLI and peer records. Candidate consistency success is NOT the original gate or installation; record the unchanged original result separately. Never regenerate or alter the earlier evidence to make a candidate pass.

## Review and delivery

Installed insane-review Native workflow, existing Syllva project, complete baseline/plan/inventory for PLAN; complete baseline/candidate/pin inventory/direct tests/plan for FINAL. Latest(`최신`) and maximum available linked slider must be verified pre/post attachment, including Chat and Pro-effort absence evidence. Original identity-bound canonical harvest only, no compression, force answer or concurrent Chrome operation. Gemini N/A: backend validator correction with unchanged generic CLI errors and no material UI/design/flow change.

Implement only after Native PLAN GO and parent disposition. Same domain owner handles every REQUIRED correction; preserve fixed actual model/effort. Parent verifies current source/package/test hashes, checks and complete opinions, then runs the unchanged original task-record consistency checker on the candidate's own review evidence. The independently reviewed local candidate and exact diff are the final deliverable. Ask for separate explicit global application approval only after this reviewable result is ready; do not replace the old ULS gate or claim overall GUI acceptance in this task.
