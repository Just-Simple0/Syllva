# Connecting Google Drive with your own Google account (personal OAuth)

Syllva can read and organize your Drive files with **your own Google account**
instead of a service account. You create one Google Cloud *Desktop* OAuth
client, put its ID and secret into your local `config.yaml`, and then approve
two separate consents from Local Settings:

| Connection | Google permission requested | Used by |
|---|---|---|
| Retrieval (MCP) | `https://www.googleapis.com/auth/drive.readonly` | the read-only MCP search surface |
| Worker | `https://www.googleapis.com/auth/drive` | the local intake worker |

Each consent produces its own refresh credential, stored in Syllva's protected
secrets folder. The two connections must use the **same** Google account.

This guide is for a single person running Syllva on their own computer. It does
not cover publishing a shared OAuth app or Google's app verification.

## 1. Create a Desktop OAuth client

1. Open the [Google Cloud console](https://console.cloud.google.com/) and create
   (or pick) a project that only you use.
2. Enable the **Google Drive API** for that project.
3. Configure the OAuth consent screen. For a personal project, choose
   **External** and add your own Google account under *Test users*. Leave the
   app in **Testing**; you do not need to submit it for verification.
4. Create credentials → **OAuth client ID** → application type **Desktop app**.
5. Copy the client ID and the client secret.

Note: while the consent screen stays in *Testing*, Google may expire refresh
tokens after about seven days. When that happens Syllva reports
`RECONNECT_REQUIRED` and you simply run the connection again from Settings.

## 2. Put the client in your local config

Add this section to your `config.yaml` (never to the repository or to any
shared file):

```yaml
google_oauth:
  client_id: "1234567890-abc.apps.googleusercontent.com"
  client_secret: "GOCSPX-your-desktop-client-secret"
```

Both values are required together. Because the file now holds a secret, it
must be readable only by you:

```bash
chmod 600 config.yaml
```

Settings refuses to start a Google connection (`OAUTH_APP_NOT_READY`) while the
section is missing, incomplete, or the file is readable by other users.

If a Google credential is still supplied through `GOOGLE_*_CREDENTIALS_FILE`
environment variables or an external file path, detach it first; Settings does
not overwrite an external source (`CREDENTIAL_SOURCE_EXTERNAL`).

## 3. Connect from Local Settings

1. Run `uls setup` and open the Settings page.
2. For the connection you want (Retrieval or Worker), start the Google
   sign-in. Your browser opens Google's consent page.
3. Approve exactly the permission shown. Google redirects back to a page on
   `127.0.0.1` that says the sign-in finished; you can close that tab.
4. Back in Settings, confirm the connection. Only this confirmation saves the
   credential.

Repeat for the other connection with the same Google account. If you approve a
different account, Settings reports `ACCOUNT_MISMATCH` and saves nothing.

Other fixed outcomes you may see: `OAUTH_ACCESS_DENIED` (you declined),
`OAUTH_GRANT_MISMATCH` (Google granted a different permission than requested),
`FLOW_EXPIRED` (the sign-in took longer than five minutes), `FLOW_BUSY`
(a sign-in for that connection is already in progress).

## 4. What is and is not stored

- Stored locally, protected: one `authorized_user` refresh credential per
  connection, in the same secrets folder used for other Syllva credentials.
- Stored in `config.yaml`: the client ID and secret you entered, plus the path
  of each managed credential file.
- Never stored or logged: authorization codes, access tokens, your Google
  account identifier, or provider error text. API responses carry fixed codes
  only.

## 5. Running

Before every worker run, Syllva refreshes the grant and checks that it is still
the same client, the same single permission and the same Google account. If
any of these changed, the worker stops before touching Drive or Notion and
reports `RECONNECT_REQUIRED`.

Removing a connection from Settings ("forget") deletes only Syllva's local
credential for that role. It does not revoke anything at Google and does not
touch the other connection. To revoke access at Google, use your Google
account's *Third-party access* page.
