# Security policy

Please do not open a public issue for a vulnerability that could expose API keys, Cookies, private notes, local files, or arbitrary command execution. Report it privately through GitHub's repository security advisory feature.

Include the affected version/commit, reproduction steps using non-sensitive fixtures, impact, and any proposed mitigation. Do not test against systems or accounts you do not own.

## Supported version

Security fixes target the latest release and the default branch.

## Important boundaries

- ManuLoom processes user-supplied URLs but rejects private/local network destinations in its acquisition layer.
- The browser bridge listens only on `127.0.0.1`, requires a local pairing token, and is not intended for Internet exposure.
- Platform Cookies and model keys belong in the protected interactive secret store, never chat, source code, issue logs, or exported notes.
- If a secret is posted or committed, revoke or rotate it first; deleting only the latest file does not remove it from Git history.
- Do not publish private server addresses, SSH keys, raw task artifacts, model weights, or downloaded source media in reports.
