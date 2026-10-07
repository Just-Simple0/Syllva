# Syllva Local Settings — interaction mock

Date: 2026-09-27. Status: **PLAN interaction evidence**.

This artifact freezes the minimum user-facing flows for PLAN review. It is not final visual styling.
Implementation may change spacing, typography, or component library, but must preserve the states,
actions, prerequisite order, destructive-action wording, and secret-handling behavior below.

## 1. First launch / resumable setup

```text
┌ Syllva Settings ────────────────────────────────────────────────────────────┐
│ Setup progress  1 Storage  2 Canvas  3 Academic  4 Automation  5 Remote  6 Check │
│                                                                            │
│ Storage roots & Notion parent                                  Not checked │
│                                                                            │
│ Google Drive retrieval credential        [Not configured]  [Configure]     │
│ Google Drive worker credential           [Not configured]  [Configure]     │
│                                         required if intake is enabled      │
│ University/root folder                   [ Select folder… ]                  │
│ Upload root                              [ Select folder… ]                  │
│                                                                            │
│ Notion retrieval credential              [Not configured]  [Configure]     │
│ Notion worker credential                 [Not configured]  [Configure]     │
│                                         required if write automation is on │
│ Semester parent/workspace                 [ Select… ]                         │
│                                                                            │
│ Course-specific folders/portals are configured after Canvas course choice. │
│                                                      [Save & continue →]    │
└────────────────────────────────────────────────────────────────────────────┘
```

Step 1 never asks for a course mapping. If the user closes Settings after saving, the next launch
derives the completed step from durable bindings and explicit feature choices and resumes at the first
incomplete step. A missing worker credential blocks completion only when the corresponding
write/intake feature is enabled. A later provider outage changes the current live-check state but does
not erase a previously satisfied durable step predicate. Secret inputs are always empty on relaunch.

The durable step predicates presented to the user are:

```text
1 Storage   configured values and verification are shown separately; legacy retrieval stays usable
2 Canvas    verified profile + term + selected-course registry saved
3 Academic  exact course joins saved; required mappings may be Configured/Unverified, not Ready
4 Automation each feature explicitly Enabled or Disabled; enabled features need their own readiness
5 Remote    configured, or explicitly [Skip / keep Remote MCP disabled]
6 Check     structural config, verification, process state, and live health remain distinct
```

If the user runs `uls setup` while Settings is already open, the launcher invalidates the existing
session, stops its process, and waits for its exit before creating or opening the new session:

```text
$ uls setup
Existing Settings session is being replaced…
Previous Settings process exited. Opening a fresh session…
```

The new Settings page displays:

```text
Previous Settings session ended because a new session was launched.
Saved settings are unchanged. Unsaved entries from the previous tab were not carried over.
```

When the old tab receives the replacement response before the old process exits, it displays:

```text
Settings session moved
A new Settings window was opened. This window can no longer save.
Unsaved non-secret entries are kept below for reference or copying into the new window.
Secret fields were cleared and are never retained or shown here.

Unsaved entries (read-only; selectable)
Time zone                 Asia/Seoul
Context limit             24

Use the new Settings window to continue.
```

If the old port is already closed or otherwise unreachable, the old tab uses the same read-only
layout with this safe message instead:

```text
Settings session closed
This window can no longer connect to Syllva Settings or save changes.
Unsaved non-secret entries are kept below for reference or copying.
Secret fields were cleared and are never retained or shown here.
```

Both states immediately remove the CSRF value, make all non-secret entries read-only/selectable,
disable Save/Next/Apply/Restart, and announce the heading with an alert/live region while moving focus
to it. A before-unload warning is active only while unsaved non-secret entries remain. Reloading or
closing after the warning discards those in-memory entries.

The old port is closed and its session cookie no longer authenticates after replacement completes. If
the old process cannot be authenticated or does not exit within the wait limit, the launcher displays:

```text
The current Settings session did not stop. A new session was not started.
Close Settings, then run uls setup again.
```

Using `Back` from a later setup step retains entered values. Dependent steps become `Partial` until
their durable predicates are rechecked; their data is not silently cleared.

## 2. Canvas connect and course selection

Initial card:

```text
Canvas LMS                                                    [Not checked]
Canvas URL   [ https://canvas.example.edu ]
                                              [Test & connect]
```

`Test & connect` opens a write-only token dialog:

```text
Connect Canvas
Access token  [••••••••••••••••••••••••••••]

The token is stored only on this computer after Canvas verifies your account.
It will not be shown again in Settings.

                                      [Cancel]  [Verify account]
```

Successful verification transitions to:

```text
Canvas LMS                                                        [Ready]
https://canvas.example.edu
Account: Student Name · Canvas user 12345
Access: configured · verified moments ago

Term       [ 2026 Fall ▼ ]
Courses    ☑ Database Systems
           ☑ Capstone Design
           ☐ Computer Networks

[Save selection]   [Replace token]   [Pause Canvas sync]
                                    [Forget Canvas token…]
```

`Pause Canvas sync` changes only the feature toggle. `Forget Canvas token…` is destructive and
opens a confirmation dialog that states: **the stored token and authorization lease are removed;
already imported academic/source history is preserved.** Provider availability is not required to
forget a revoked token; the dialog binds the action to the local Canvas origin/account profile.

Invalid submitted token is distinct from a provider outage:

```text
Canvas LMS                                                        [Failed]
Canvas URL   [ https://canvas.example.edu ]
Access token [                              ]   cleared after submit

Canvas rejected this access token. Existing saved Canvas connection was not changed.
[Try another token]
```

If Canvas is unreachable after a previously completed connection, the saved profile/course registry
remains durable and the card shows `Failed — provider unavailable, last successful check <time>` with
`[Retry check]`; it does not send the wizard back to an earlier incomplete step.

## 3. Academic scope after Canvas

```text
Academic scope                                                     [Partial]
Academic active semester: 2026 Fall
MCP retrieval scope: legacy_global (independent)                 [Change scope…]
Changing Academic semester does not change the retrieval selector.
Shared Drive university root       [ Select… ]                   Not checked
Drive semester folder              [ Select… ]                   Not checked
Drive upload root                  [ Select… ]                   Not checked
Notion semester parent             [ Select… ]                   Not checked

Database Systems
  Canvas                  course 41921                         Ready
  Drive course folder     [ Select… ]                         Configured/Unverified
  Drive upload folder     [ Select… ]                         Blocked
  Drive Recordings folder [ Select… ]                         Not checked
  Drive Materials folder  [ Select… ]                         Not checked
  Notion academic-courses [ Select… ]                         Configured/Unverified
  Notion sessions         [ Select… ]                         Not checked
  Notion materials        [ Select… ]                         Not checked
  Notion file-intake      [ Select… ]                         Not checked
  Notion input-requests   [ Select… ]                         Not checked
  Notion course portal    [ Select… ]                         Optional

Capstone Design
  Canvas                  course 41708                         Ready
  Required Drive/Notion mappings …                              Not checked

[Discover IDs] [Verify for retrieval] [Verify for intake worker]
Configured IDs can be saved as setup inputs. They do not become active verified bindings
until the matching server-side purpose check succeeds.
                                                 [Save as unverified]
```

The editor exposes every mapping required by the current resolver for each selected course, plus
shared semester roots. This compact sample abbreviates the second course's rows. A manual ID remains
Configured/Unverified; Save as unverified does not unblock semester retrieval, intake, or study-note
processing. Retrieval verification and intake-worker verification are separate read-only checks.
Display names help selection but never become the stored identity. Module/week labels never create
Session number/date/completion.

### Automation choices and interval

```text
Automation                                                        [Not checked]
Intake worker       [Not selected]  Current schema default is not your choice
                    [Choose Enabled] [Choose Disabled]
Study-note feature  [Not selected]  Current value is not an explicit choice
                    [Choose Enabled] [Choose Disabled]
Desired interval    [ 15 ] minutes  (desired value; scheduler use Not reported)
Actual schedule     Not reported
Next run            Not reported

Enabled intake/study processing needs its own mappings and worker-role checks.
Disabled preserves existing mappings and does not require worker credentials.
Save choices and interval     [Review changes]
```

Only an explicit choice is saved as choice evidence. Choosing Enabled does not start a worker,
scheduler, or provider operation. Existing unsupported positive interval values remain unchanged
when only a toggle changes; the GUI validates the interval when the user edits that field.

## 4. Credential card states

Google Drive and Notion always show retrieval and worker credentials as separate rows:

```text
Google Drive
Retrieval (read-only)     Configured   last check 19:20   [Test] [Replace] [Forget…]
Worker (intake writes)    Not configured                  [Configure]
```

Credential actions expose a write-only field only while configuring/replacing. After submit the
field is cleared. The card receives only readiness metadata and the new `config_generation`; it
never receives the stored value, service-account JSON, private key, or keyring content.

`Forget…` always names one exact role/store before confirmation. For Drive, forgetting retrieval or
worker credentials removes only that local credential after detaching that role; Drive files,
academic bindings, and imported/source history remain. For Notion, forgetting retrieval or worker
credentials removes only the named keyring/protected-file credential after detaching that role;
Notion pages/databases, academic bindings, and imported/source history remain.

A credential card's Verified result describes that credential check only; it does not verify an
Academic mapping, choose a semester, or make worker/study processing Ready.

Failed replacement preserves the previously active credential:

```text
Google Drive · Retrieval                                      [Failed]
Replacement credential was rejected. Current credential is still active.
The submitted secret was cleared. Folder ID: 1Abc… (unchanged)
[Retry replacement]   [Test current credential]
```

The same pattern applies to Canvas and Notion: non-secret fields stay populated, submitted secret
fields clear, a polite live region announces the fixed redacted error/remediation, and the current
known-good credential remains active/recoverable until replacement commits successfully.

GUI-2 clarifications (see `local-settings-gui-2-worker-plan.md`):

```text
Notion
Retrieval (read-only)     Provided by NOTION_MCP_TOKEN          [Test]
                          [Use a Syllva-managed credential instead]
                          Remove it from the environment to stop using it.
Worker (intake writes)    Not configured                        [Configure]

Google Drive · Retrieval  Key file you manage: …/my-key.json     [Test]
                          [Use a Syllva-managed credential instead]  [Stop using this credential…]

Google Drive · Worker                                             [Configure]
Service account key file  [Choose file…]   (JSON, up to 64 KB)
                                               [Cancel]  [Verify and save]

Google Drive · Worker                                             [Not saved]
Google could not be reached, so the key was not checked. Nothing changed.
The selected file was cleared.                     [Try again]

Notion (on Linux)
Not available on this computer: Syllva cannot store credentials securely here yet.
Use environment variables instead.                 [Test]
```

`Stop using this credential…` appears only for a key file named in Settings' configuration. It removes
only that setting; the key file you manage yourself is never read, changed, or deleted. A credential
supplied by an environment variable can be stopped only by removing that variable. Retrieval and worker must use
different credentials; a duplicate is refused before anything is saved. Tests are read-only and show
only `Verified`, `Invalid credential`, `Access missing`, or `Provider unavailable` with a check time.

## 5. Save review and concurrent/partial failure

Before apply, Settings shows a redacted semantic diff:

```text
Review changes
Academic active semester   2026-S1  →  2026-S2
MCP retrieval scope        legacy_global (no change)
Intake worker choice       Not selected → Enabled
Desired poll interval      15 → 30 minutes

Impact: show verification/readiness separately from any observed process restart impact
                                      [Back]  [Apply changes]
```

For a verified Academic binding, a successful Verify adds server-issued evidence to a new final
candidate and produces a new candidate hash and redacted diff. The user reviews that final diff
before Apply; Apply never adds a receipt or choice marker after the review. The separate
Configured/Unverified save path has its own diff and removes stale proof only if that candidate is
applied.

If another process edits config while the form is open:

```text
Configuration changed outside Settings.
Your unsaved entries are still here.
[Reload latest and reapply my edits]   [Cancel]
```

If a multi-store operation stops after storing a credential but before config apply:

```text
Canvas connection                                                [Partial]
Credential stored, configuration not applied.
No existing secret was deleted or rolled back.
[Resume repair]   [Leave as-is]
```

Relaunching Settings surfaces the same unfinished operation from the secret-free transaction
journal. No timeout silently repairs or deletes it.

Forget has a different safe partial state because configuration is detached before local deletion:

```text
Notion retrieval credential                                      [Partial]
Configuration detached. Old local credential is still present.
Academic bindings and Notion content are preserved.
[Retry local deletion]   [Leave as-is]
```

## 6. Overview and restart-required state

```text
┌ Overview ───────────────────────────────────────────────────────────────────┐
│ Restart required: saved Remote MCP settings are not active yet.              │
│ [Restart Remote MCP]  [Later]                                               │
│                                                                             │
│ Canvas                 Ready        checked 19:20                           │
│ Drive retrieval        Ready        checked 19:20                           │
│ Drive worker           Blocked      credential missing                      │
│ Notion retrieval       Ready                                               │
│ Notion worker          Ready                                               │
│ Academic scope         Partial      1 course mapping incomplete             │
│ Retrieval scope        legacy_global  unchanged by Academic selection       │
│ Intake worker          Not selected  schema default is not a choice         │
│ Study-note processing  Disabled      explicit choice                        │
│ Desired poll interval  15 min       scheduler/next run Not reported          │
│ Local study-note MCP   Not reported  no successful transport observation    │
│ Remote MCP             Running with previous settings                        │
│ AI client E2E          Not checked                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

`Save` never silently restarts a service. Desired fingerprints come from saved configuration;
applied fingerprints come from the actual process only after successful load and start. An intake
one-shot reports only after acquiring its worker lock; an MCP server reports only after its transport
starts. Object construction, credential presence, and Save never report applied or Running.
`Not reported`, `Stopped`, `Running with current settings`, and `Running with previous settings` are
separate runtime states; Running with previous settings is never Ready. A stopped one-shot shows its
last successful load, and its next invocation uses desired settings. Readiness is a separate
dependency result. A failed restart keeps the redacted error and prior successful observation; an
unobservable start remains Not reported.

If Apply is interrupted, exact current bytes equal to the reviewed final candidate mean committed and
continue readback; bytes still at the original generation mean not committed; any third hash is
Partial and needs manual review. Settings does not promise rollback after replacement.

## 7. Remote Access

```text
Remote Access
Remote MCP                         [Enabled]
Mode                               mcp_oauth
Public URL                         [ https://mcp.example.dev/mcp ]
Google OAuth client ID             [ …apps.googleusercontent.com ]
Authorized owner email             [ user@example.com ]
Google OAuth client secret         Configured       [Replace]
Redirect URI                       https://mcp.example.dev/oauth/callback [Copy]

[Check local health] [Check public discovery]
Cloudflare tunnel/account creation is completed outside Syllva Settings.
```

For first-run setup this step also offers `[Skip / keep Remote MCP disabled]`. Choosing it durably
completes the optional Remote step without creating public access, credentials, or a tunnel. The user
can configure Remote Access later from normal navigation.

No tunnel token field exists in the normal flow. Resetting local OAuth grants is under an explicit
advanced destructive action and states that it removes only local authorization codes/tokens from
the identified Syllva OAuth database, not the Cloudflare tunnel or Google OAuth client.

## 8. Session expiry and close

Before inactivity expiry, the UI shows an accessible warning dialog with `Stay signed in locally`
and `Close settings`. Passive Overview/status polling never extends the timer; only explicit user
activity such as `Stay signed in locally` renews it. If expiry wins, the current app becomes:

```text
Settings session expired
For your security, this local setup session ended after inactivity.
Unsaved non-secret entries remain in this tab's memory until it is closed or reloaded.
Run/open Syllva Settings again to continue. Saved configuration was not changed.
```

An API `401 SESSION_EXPIRED` clears the in-memory CSRF value and any secret input. Dirty non-secret
fields remain only in ephemeral page/JavaScript memory for re-entry; nothing is persisted or retried
against the expired session.

`Close settings` invalidates the session first, shows `Settings session ended`, and then allows the
local settings process to shut down gracefully. Opening the old localhost URL afterward cannot
restore access; a fresh launcher bootstrap is required.

## 9. Accessibility evidence

- Setup steps, cards, and status text are keyboard reachable and never rely on color alone.
- Async save/test messages use a polite live status region and do not steal focus.
- Destructive confirmation dialogs trap focus, identify the affected credential/local store, and
  explicitly state what academic/source data is preserved.
- Validation errors remain associated with their input. Non-secret fields remain populated after
  errors; submitted secret fields are cleared.
- Invalid credential, provider-unavailable, and failed-replacement states use fixed redacted errors,
  expose a retry action, and announce the outcome without moving focus. A failed replacement states
  explicitly that the previously active credential is preserved.
