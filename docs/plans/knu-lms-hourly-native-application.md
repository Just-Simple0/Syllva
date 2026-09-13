# Native Notion hourly pilot application

Final status: see [acceptance record](knu-lms-hourly-acceptance.md). Native application, 120 tests and final rereviews are accepted; the hourly heartbeat remains PAUSED pending credential authorization. The evidence below records the original application/review checkpoint.

The user chose native Notion and hourly updates. This pilot imports a verified second-semester course's assignment, announcement and week/item metadata. Existing first-semester content and the previously connected different second-semester course/PDF are preserved. Source identities and private connector receipts are in the ignored local runtime directory; no credentials belong in those receipts.

## Learner interface

The semester root remains ordered: **내 과목 → 이어서 공부 (at most 3) → To DO → 캘린더 → 파일 확인**.

One semester schedule datasource provides three linked views: root To DO, root Calendar, and course assignments. The primary datasource's default table is retained inside the closed native `전체 일정 목록` toggle under Calendar for viewing undated and completed items. The connector cannot convert an existing table's type using `CALENDAR BY`; a real calendar view was created and its `type=calendar` verified. There is one datasource and four total views, of which three are the learner's main linked views. Its exact containing toggle plus semester ancestor are recorded as the intended datasource parent binding.

The schema separates source fields (`이름`, `과목`, `유형`, `날짜`, `LMS 원문 URL`, `LMS 키`, `수집 범위`) from user-owned `내 상태`, `내 메모` and page body/comments. New rows initialize `내 상태=확인 전`; subsequent runs never write the user's state or notes. The exact source key encodes allowlisted origin/course/assignment IDs and is also the original assignment URL. Course filtering uses the exact assignment prefix including the trailing slash; display names are not identity. The connector renders rich-text URL keys as Markdown autolinks; normalization accepts only an exact wrapper whose label and target both equal the expected canonical key. Null date start/end may be omitted by readback and are normalized to null.

The To DO view includes unfinished personal tasks and assignments, including undated rows. The Calendar uses the same rows and only nonempty dates, preserving completed dated items. Its caption makes clear that missing dates are unknown. The verified pilot currently has two assignments with null LMS deadlines. Submission states were not collected, so the interface asks the user to consult the LMS original for submission status.

The course page has one course-level entry, a linked assignment view, retained collected announcements, fifteen native inline week toggles with seven original LMS launch links, and separate learning-session and user-memo sections. It creates no week/session subpages from module metadata. Empty observed weeks are labeled empty as of collection. Module completion flags and date-like item names are never promoted into learning completion or confirmed lecture dates.

LearningX PDF viewing was confirmed in the logged-in LMS, but the inspected viewer exposes no native original-download control. Those items remain LMS launch links; no new PDF is claimed to have been saved to Drive. The existing different course's verified Drive PDF remains linked.

## Safe connector application

Root owns connector writes. Before every run it obtains the sidecar's OS lock and durable active-owner reservation and retains them through source collection, fresh Notion readback, intent journal, every mutation and final readback. A lost keeper leaves a durable active reservation blocking competing writers and enrollment. It is not automatically expired or reclaimed. Clean release happens only after all requests have definitively ended and receipts are verified.

All components follow absent / exact verified one / ambiguous resolution: datasource, three views, pilot course page, source region and assignment rows. Persist an intent before creation, returned identifiers immediately, and accepted state only after readback. An indeterminate write is never blindly retried. Missing prior bindings or ambiguous discovery stops dependent writes. No deletion or arbitrary re-parenting is performed. Existing child tags and source datasource references remain intact during bounded section edits.

Source-owned values/regions must match the last accepted actual normalized Notion readback before changing them. Actual connector testing confirmed that Notion removes presentation escapes and blank lines. Separate hashes retain the normalized requested region and the normalized accepted actual region; a desired-hash match allows no-op only after the current actual hash passes the source-conflict guard. Any user edit or source-region comment yields reconciliation instead of overwrite. This is a cooperative single-writer protocol with fresh-read/conflict checks, not an atomic compare-and-swap guarantee against simultaneous external Notion edits.

## Hourly handoff and credential boundary

Use a current-task Codex heartbeat, configured hourly and initially paused. The run prompt is restricted to the verified pilot and the reviewed scripts/connector projection contract. It must not create tokens, change expiry, select other courses, download extra files, change sharing, modify implementation, or run external reviews containing private LMS data. Keep unchanged/non-actionable runs quiet; notify only meaningful changes or a new actionable failure. A repeated unchanged failure does not justify duplicate notifications. Interrupted reservations require an explicit actionable report and operator reconciliation.

No existing one-shot token is read, stored, reused persistently, or renewed. Prepared enrollment uses a new explicitly approved token, at most thirty days of local authorized use, and macOS Keychain through explicit keyring25.7.0 in the existing Python3.11 virtual environment. Enrollment has a durable pending/enrolled manifest; collection accepts only a matching enrolled, unexpired manifest. It does not infer authorization from a prepared configuration file. New credential creation and storage are a separate final user decision after the concrete independent work is completed.

Local project scheduling requires the computer to remain powered on with the desktop app running: [official scheduled-task documentation](https://learn.chatgpt.com/docs/automations?surface=app). The explicit macOS backend and credential API are documented by [Python keyring](https://keyring.readthedocs.io/en/latest/index.html).

## Acceptance evidence

- Plan: independent web ChatGPT Latest / Very high (user-authorized quota fallback), final plan R4 GO; independent Gemini 3.8 Flash / high R4 GO.
- Implementer verification: 117 focused unit tests passed across probe, sync and lock; Ruff and mypy passed. Exact independent final attachment audit/reviews pending. Actual sanitized probe snapshot CLI exits zero with 2 assignments, 1 announcement, 15 modules and 7 items. Actual assignment readbacks yield two no-op plans. Source region was aligned once to the renderer; fresh readback proves both the course prefix/linked view and USER sessions/memo suffix were preserved.
- Native root/course/datasource/view/row readbacks: verified. Exact proposed initial root and course bodies matched actual readback. Five headings remain in the required order; actual course has fifteen week toggles and seven LMS item links. Both To DO and the course view return the same two rows; the calendar returns zero because dates are null. Existing different course body is byte-for-byte unchanged in connector Markdown. After the bounded renderer alignment, replay against the actual normalized source readback and separately journaled request yields `complete`, two row `noop` operations and a course-region `noop`, with zero conflicts. This local replay calls no live credentials or provider and creates no further cloud changes.
- Current browser visual limitation: Notion IAB displays the workspace login wall; connector structural evidence is available. Do not claim authenticated pixel verification.
- Heartbeat configuration/readback: official automation `lms` named `경북대 LMS 매시간 동기화`, bound to the current task, hourly and PAUSED. Tool readback and persisted configuration confirm the exact prompt and cadence. Activation and live credential collection remain outside current approval. The complete execution contract is `knu-lms-hourly-driver.md`, and the exact user-visible prompt is `knu-lms-hourly-heartbeat-prompt.md`.
