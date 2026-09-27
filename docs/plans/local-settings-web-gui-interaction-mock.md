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
│ University/root folder                   [ Select folder… ]                  │
│ Upload root                              [ Select folder… ]                  │
│                                                                            │
│ Notion retrieval credential              [Not configured]  [Configure]     │
│ Notion worker credential                 [Not configured]  [Configure]     │
│ Semester parent/workspace                 [ Select… ]                         │
│                                                                            │
│ Course-specific folders/portals are configured after Canvas course choice. │
│                                                      [Save & continue →]    │
└────────────────────────────────────────────────────────────────────────────┘
```

Step 1 never asks for a course mapping. If the user closes Settings after saving, the next launch
derives the completed step from durable config/readiness and resumes at the first incomplete step.
Secret inputs are always empty on relaunch.

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

## 6. Overview and restart-required state

```text
┌ Overview ───────────────────────────────────────────────────────────────────┐
│ Restart required: Remote MCP settings changed. Saved configuration is safe. │
│ [Restart Remote MCP]  [Later]                                               │
│                                                                             │
│ Canvas                 Ready        checked 19:20                           │
│ Drive retrieval        Ready        checked 19:20                           │
│ Drive worker           Blocked      credential missing                      │
│ Notion retrieval       Ready                                               │
│ Notion worker          Ready                                               │
│ Academic scope         Partial      1 course mapping incomplete             │
│ Intake worker          Disabled                                            │
│ Remote MCP             Ready        https://mcp.example.dev/mcp             │
│ AI client E2E          Not checked                                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

`Save` never silently restarts a service. `Restart required` is a separate banner/modifier rather
than a connection status.

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

No tunnel token field exists in the normal flow. Resetting local OAuth grants is under an explicit
advanced destructive action and states that it removes only local authorization codes/tokens from
the identified Syllva OAuth database, not the Cloudflare tunnel or Google OAuth client.

## 8. Session expiry and close

Before inactivity expiry, the UI shows an accessible warning dialog with `Stay signed in locally`
and `Close settings`. If expiry wins, the current app becomes:

```text
Settings session expired
For your security, this local setup session ended after inactivity.
Run/open Syllva Settings again to continue. Saved configuration was not changed.
```

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
