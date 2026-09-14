# LMS Sync

[한국어](lms-sync.ko.md) · [Operator Guide](README.md)

LMS support is optional and should be treated as a separately gated sidecar, not as a prerequisite for core Syllva retrieval.

The repository includes KNU/Canvas-oriented probe/sync helpers under `scripts/` and corresponding engineering/acceptance records under `docs/plans/`.

## Safety rule: paused by default

Keep LMS scheduling **paused** until all of the following are true:

- the user has explicitly authorized the credential use;
- the expected active account/course scope is verified;
- the credential is current and valid for the intended time window;
- a read-only connection/probe succeeds for the configured scope;
- the target Notion/application mapping has been validated;
- the operator has reviewed the first result before enabling recurrence.

Do not enroll credentials or activate a heartbeat merely because repository scripts exist.

## Credentials

The token-based sidecar branch does not read a Canvas token from an environment variable, argument, file, or browser session. `scripts/knu_lms_sync.py enroll --confirm yes` prompts once for the token in a local interactive terminal (no-echo) and stores it through the OS-native credential store for the current user: the explicit `keyring.backends.macOS.Keyring` class on macOS, or the explicit `keyring.backends.Windows.WinVaultKeyring` class on Windows (Windows Credential Manager). No other platform or fallback backend is supported. A later run reads the enrolled token back from that same OS-native store; it is never written to a repository file or log. The preferred Aside browser-session branch avoids this token path entirely and does not read or enroll any credential.

Credential/config scope must be validated **before** retrieving a secret from secure storage or making provider calls. A changed/foreign config must not be allowed to reuse a secret under the previous active-owner scope.

## Data behavior

- Keep course identity explicit; course names are discovery aids, not sufficient durable bindings.
- Preserve partial/failed course results rather than turning one incomplete course into a successful semester.
- Do not invent dates for assignments that have no date.
- Do not silently change user completion/submission state unless an explicitly authorized integration owns that field.

## Operational evidence

Detailed connection-validation and hourly-sync plans/evidence live under `docs/plans/`. They are engineering records; follow their current accepted state when operating that lane.
