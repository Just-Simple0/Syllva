# KNU LMS hourly pilot acceptance — 2026-09-13

The native Notion application and prepared hourly heartbeat are accepted. The heartbeat is **PAUSED**. Real credential enrollment, a fresh authenticated collection and activation remain pending separate human authorization. The implemented credential path passed final review; that review is not permission to create or store credentials.

## Delivered and verified

- The 2026-2 dashboard retains 내 과목 → 이어서 공부 (maximum3) → To DO → 캘린더 → 파일 확인. The different preexisting course and its verified Drive PDF are preserved.
- The pilot has two assignments, one collected announcement, fifteen inline week toggles and seven LMS item links. One semester datasource provides the three learner linked views; the primary table is retained in a closed 전체 일정 목록 toggle. Both assignments have unknown/null deadlines, so they appear in To DO and the course view and are excluded from Calendar. No LMS submission status or module completion is presented as the user's learning status.
- A bounded source-region alignment preserved the course prefix/linked view and the complete USER learning-session/memo suffix. Replay against actual Notion readback plus independently journaled request hashes returns two row no-ops and one source-region no-op, with no conflicts or duplicate writes.
- Official current-task automation `lms`, 경북대 LMS 매시간 동기화, was created hourly and PAUSED. Its exact prompt, target task, cadence and state were read back. Execution follows `knu-lms-hourly-driver.md` and the recorded heartbeat prompt.
- Original LearningX viewing is verified. A normal original-download route was not verified, so these items remain LMS links. No new pilot PDF is claimed to be stored on Drive. Notion browser login prevented pixel inspection; native connector structure and content were verified.

## Checks and review disposition

The implementer reports **120 focused unit tests passed**, covering the existing probe, new sync sidecar and durable lock. Ruff, production-script mypy and diff checks passed. Actual private probe input passes the snapshot CLI; actual Notion replay passes. No real Keychain or live credential operation was used by the tests.

Independent web review used **ChatGPT Latest / Very high**, the user's previously authorized quota-only fallback; this was not Pro. Both final attachment audits contain all eleven intended files, with no missing, extra or mismatched code. The first final review found two credential defects: using credentials before their issue time, and a second config load drifting away from the active reservation's scope. The same worker fixed both with regression tests. The final rereview returned **GO**, resolving both findings. Full serialization before stdout also prevents partial JSON on invalid local data.

Independent **Gemini 3.8 Flash / high** native, full flow and targeted authentication rereviews returned **GO**. Root checked report claims against actual code and corrected incidental error-code/date-boundary wording. No claim is made that credentials are hidden from process memory or that Notion updates are atomic against concurrent external USER edits.

The final web review noted one stale comment about an exclusive date boundary. It was corrected to the documented inclusive boundary without changing runtime code; root verified complete AST equality against the reviewed script. No further test run was needed for that comment-only change.

Prior probe/tests, transcript/tests, frozen specifications and pyproject hashes remained unchanged. Unrelated handoff/UX work is preserved. No commit or push was performed. Accepted hashes, cloud bindings, journals and review paths are in the ignored private runtime; it contains no actual credential config or enrolled auth manifest at delivery.

## Remaining user decision

The previous Canvas token was approved for one-shot validation and expires at 2026-09-14 00:00 KST. A new token with at most thirty days of authorized use, stored in this Mac's system Keychain for the single pilot, needs explicit approval. After user-controlled token entry/enrollment, perform a fresh complete authenticated run and verify its Notion result before enabling the existing hourly heartbeat. No automatic renewal, scope expansion or interrupted-reservation reclamation is authorized.
