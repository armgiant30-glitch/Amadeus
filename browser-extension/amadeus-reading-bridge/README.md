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

The extension tracks a monotonic character cursor per page URL. It is an approximate reading cursor; a reader-specific extension can later provide exact chapter/page offsets.
