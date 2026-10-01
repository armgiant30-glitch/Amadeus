# Amadeus Reading Bridge

Chrome/Edge extension for sending selected text from web novels, browser PDFs, and online manga pages to Amadeus Companion.

## Load

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable Developer mode.
3. Choose **Load unpacked**.
4. Select this directory.
5. Start Amadeus Companion so `127.0.0.1:17878` is listening.

## Use

1. Select text in the page.
2. Click the extension button or press `Ctrl+Shift+Y`.
3. Amadeus receives a `reading.selection` event and can use it as reading context.

## What it sends, and what it cannot know

| Field | Source | Accuracy |
|---|---|---|
| `book_id` | FNV-1a hash of the page URL | Stable per URL, so two chapters of one book are two ids |
| `cursor` / `selected_start` / `selected_end` | Monotonic character count kept per URL in `chrome.storage.local` | Approximate: it counts characters received, not positions in the book |
| `chapter` | First `h1`/`h2`/`h3`/`[role=heading]`, falling back to the page title | Good on novel sites, wrong on sites that use a heading for the site name |
| `page` | Omitted | The generic client has no page number. Note the adapter treats an omitted page as "unknown" and clears any stored page, so a reader-specific client that knows the real page should send it |

A reader-specific client (Calibre, KOReader, an EPUB viewer) can send exact
chapter, page and offset values through the same `/reading/event` endpoint.

## Why this is an extension and not a bookmarklet

The adapter is a bare `BaseHTTPRequestHandler` on loopback: it sends no CORS
headers and answers no `OPTIONS` preflight, so a normal page `fetch` with a JSON
body cannot reach it. An extension works because its `host_permissions` entry for
`http://127.0.0.1:17878/*` exempts its service worker from CORS. Anything else
(a user script, a bookmarklet, another local tool) needs its own privileged
context, or the adapter needs CORS support.

See `docs/reading-bridge.md` for the full contract, verification steps and the
limitations above in context.
