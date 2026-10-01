const ENDPOINT = "http://127.0.0.1:17878/reading/event";

function stableBookId(url) {
  let hash = 2166136261;
  for (const ch of String(url || "")) {
    hash ^= ch.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `browser:${(hash >>> 0).toString(16)}`;
}

async function captureSelection(tabId, fallbackUrl = "", fallbackText = "") {
  let captured = { text: fallbackText, url: fallbackUrl, chapter: "", page: 0 };
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "AMADEUS_CAPTURE_SELECTION" });
    if (response?.text) captured = { ...captured, ...response };
  } catch (_) {
    // Restricted browser pages cannot run content scripts; use selectionText if available.
  }
  return captured;
}

async function sendSelection(tab, fallbackText = "") {
  const url = tab?.url || "";
  const captured = await captureSelection(tab?.id, url, fallbackText);
  const text = String(captured.text || "").trim();
  if (!text) throw new Error("请先选中要发送的文字。");

  const bookId = stableBookId(captured.url || url);
  const storageKey = `cursor:${bookId}`;
  const prior = await chrome.storage.local.get(storageKey);
  const start = Number(prior[storageKey] || 0);
  const end = start + text.length;

  const payload = {
    type: "reading.selection",
    app: "browser",
    book_id: bookId,
    chapter: String(captured.chapter || ""),
    // Omitted, not zero: the adapter reads a missing/null page as "unknown" and
    // keeps the stored page, while 0 would claim the reader is on page zero.
    ...(Number(captured.page) > 0 ? { page: Number(captured.page) } : {}),
    cursor: end,
    selected_start: start,
    selected_end: end,
    text
  };

  const response = await fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (!response.ok) throw new Error(`Amadeus adapter HTTP ${response.status}`);
  await chrome.storage.local.set({
    [storageKey]: end,
    lastReadingStatus: { ok: true, chars: text.length, bookId, at: Date.now() }
  });
  return { ok: true, chars: text.length };
}

async function sendActiveTab(fallbackText = "") {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("未找到当前网页。");
  return await sendSelection(tab, fallbackText);
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "amadeus-send-selection",
    title: "发送选中文字到 Amadeus",
    contexts: ["selection"]
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "amadeus-send-selection") return;
  try {
    await sendSelection(tab, info.selectionText || "");
  } catch (error) {
    await chrome.storage.local.set({ lastReadingStatus: { ok: false, error: String(error), at: Date.now() } });
  }
});

chrome.commands.onCommand.addListener(async command => {
  if (command !== "send-selection") return;
  try {
    await sendActiveTab();
  } catch (error) {
    await chrome.storage.local.set({ lastReadingStatus: { ok: false, error: String(error), at: Date.now() } });
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "AMADEUS_SEND_ACTIVE_SELECTION") return false;
  sendActiveTab()
    .then(result => sendResponse(result))
    .catch(error => sendResponse({ ok: false, error: String(error) }));
  return true;
});
