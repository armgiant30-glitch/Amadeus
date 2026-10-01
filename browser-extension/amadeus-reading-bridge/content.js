let lastSelection = "";

function currentSelection() {
  const selection = window.getSelection();
  const text = selection ? String(selection).trim() : "";
  if (text) lastSelection = text;
  return text || lastSelection;
}

function readingChapter() {
  const heading = document.querySelector("h1, h2, h3, [role='heading']");
  return heading?.textContent?.trim() || document.title || "";
}

document.addEventListener("mouseup", () => { currentSelection(); });
document.addEventListener("keyup", () => { currentSelection(); });

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "AMADEUS_CAPTURE_SELECTION") return false;
  sendResponse({
    text: currentSelection(),
    url: location.href,
    chapter: readingChapter(),
    // No page number here. The old value was window.scrollY, which is a pixel
    // offset, not a page; the adapter reads page: null as "unknown" and leaves
    // the stored page alone. A reader-specific client can supply a real one.
    page: null
  });
  return true;
});
