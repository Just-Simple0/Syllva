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
1 Storage   retrieval roots/bindings saved; enabled write features have worker credentials
2 Canvas    verified profile + term + selected-course registry saved
3 Academic  required mappings for selected courses saved
4 Automation each automation explicitly Enabled or Disabled; enabled writers are ready
5 Remote    configured, or explicitly [Skip / keep Remote MCP disabled]
6 Check     all required durable predicates complete; live failures remain diagnostics
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
Semester: 2026 Fall

Database Systems
  Canvas                  course 41921                         Ready
  Drive upload folder     [ Select… ]                         Blocked
  Notion course portal    [ Select… ]                         Blocked

Capstone Design
  Canvas                  course 41708                         Ready
  Drive upload folder     [ Select… ]                         Not checked
  Notion course portal    [ Select… ]                         Not checked

                                                 [Save mappings]
```

Only verified provider IDs become durable bindings. Display names help selection but never become
the stored identity. Module/week labels never create Session number/date/completion.

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
Academic semester          2026-S1  →  2026-S2
Canvas sync                Disabled → Enabled
Canvas access token        Not configured → Configured
Remote MCP                 no change

Impact: worker restart required
                                      [Back]  [Apply changes]
```

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
│ Intake worker          Disabled                                            │
│ Remote MCP             Ready        running previous settings               │
│ AI client E2E          Not checked                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

`Save` never silently restarts a service. `Restart required` is derived from the service's saved
desired fingerprint differing from its reported/durable applied fingerprint, so it survives closing
and reopening Settings. `Ready — running previous settings` describes the currently running instance,
not the newly saved configuration. A failed restart keeps the banner and adds a fixed redacted error;
an external restart onto the desired fingerprint clears it on the next status refresh.

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
