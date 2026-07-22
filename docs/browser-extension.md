# Local browser extension

The Chromium extension submits the active public page to a Python service on the same computer. The extension is intentionally thin: it does not download media, call model providers, read page cookies, or store DeepSeek/Qwen keys.

Current first-release support: Chrome, Edge, Brave, and other Chromium browsers with Manifest V3 unpacked-extension support. Firefox packaging is a future compatibility target, not part of this release.

## 1. Install ManuLoom locally

Follow the operating-system instructions in the main README, configure the text model, and choose an Obsidian Vault. Verify the CLI first:

```bash
scripts/manuloom doctor
```

## 2. Start the loopback service

```bash
scripts/manuloom serve --vault ~/ObsidianVault
```

The service binds only to `127.0.0.1:8766`; there is no option that exposes it to the LAN or Internet. Keep this terminal open. You can also open `http://127.0.0.1:8766` and use the built-in local page without installing the extension.

In a second terminal, display the browser pairing token:

```bash
scripts/manuloom browser-token
```

The token is separate from every model/platform credential and is stored at `~/.config/manuloom/browser-token` with mode `0600`. Rotate it at any time:

```bash
scripts/manuloom browser-token --rotate
```

## 3. Load the extension

1. Open the browser's extensions page (`chrome://extensions`, `edge://extensions`, or the equivalent).
2. Enable Developer mode.
3. Choose **Load unpacked** and select this repository's `browser-extension` directory.
4. Pin ManuLoom to the toolbar.
5. Open a supported video or article, click ManuLoom, paste the local pairing token, and submit.

The token is stored only in `chrome.storage.local`, not synchronized across browsers. Closing the popup does not cancel the Python job; use the CLI task list or reopen the popup while it is running.

## Permission model

| Permission | Why it is needed |
|---|---|
| `activeTab` | Read the current tab's URL and title only after the user clicks the extension |
| `storage` | Remember the loopback endpoint and pairing token on this browser profile |
| `http://127.0.0.1:8766/*` | Submit and poll jobs on the local ManuLoom bridge |

The manifest has no `<all_urls>`, content script, cookie permission, history permission, remote JavaScript, or model credential. The local API requires a Bearer pairing token, rejects non-HTTP(S) source URLs, bounds JSON bodies, and rejects cross-origin requests except Chromium/Firefox extension origins. The processing core separately applies its existing public-network and SSRF checks before acquisition.

## What “local” means

Files, temporary media, ASR, and the Vault remain on this computer. If an API model is configured, prompts and selected evidence are still sent to that model provider according to its terms. “Local-first” does not claim that provider-backed editing is offline.
