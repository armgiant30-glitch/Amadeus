let lastSelection = "";

function cleanInline(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function normalizeText(value) {
  return String(value || "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function currentSelection() {
  const selection = window.getSelection();
  const text = selection ? String(selection).trim() : "";
  if (text) lastSelection = text;
  return text || lastSelection;
}

function chapterTitle(root = document) {
  const selectors = [
    "meta[property='og:novel:chapter_name']",
    "meta[property='og:title']",
    "h1",
    "h2",
    "[role='heading']"
  ];
  for (const selector of selectors) {
    const element = root.querySelector?.(selector);
    const value = element?.tagName === "META"
      ? element.getAttribute("content")
      : element?.textContent;
    const title = cleanInline(value);
    if (title) return title;
  }
  return cleanInline(root.title || document.title || "");
}

const CONTENT_SELECTORS = [
  "article",
  "main",
  "[role='main']",
  ".chapter-content",
  ".read-content",
  ".reader-content",
  ".article-content",
  ".post-content",
  ".entry-content",
  "#chapter-content",
  "#content",
  ".content"
].join(",");

const STRIP_SELECTORS = [
  "script", "style", "noscript", "nav", "header", "footer", "aside", "form",
  "button", ".comments", "#comments", ".comment", ".ads", ".advertisement",
  ".toolbar", ".navigation", ".menu", ".metadata"
].join(",");

function textLength(element) {
  return String(element?.textContent || "").replace(/\s+/g, "").length;
}

function contentRoot(root = document) {
  const candidates = [];
  if (root.body) candidates.push(root.body);
  if (root.nodeType === 1) candidates.push(root);
  for (const element of root.querySelectorAll?.(CONTENT_SELECTORS) || []) candidates.push(element);
  let best = null;
  let bestScore = 0;
  for (const element of candidates) {
    const length = textLength(element);
    if (length < 200) continue;
    const paragraphs = element.querySelectorAll?.("p").length || 0;
    const links = element.querySelectorAll?.("a").length || 0;
    const score = length + Math.min(paragraphs, 80) * 120 - Math.min(links, 200) * 50;
    if (score > bestScore) {
      best = element;
      bestScore = score;
    }
  }
  return best || root.body || root.documentElement;
}

function findNextChapterUrl(root = document, baseUrl = location.href) {
  const chapterPatterns = [/下一[章篇节]/i, /下章/i, /next\s*chapter/i, /continue\s+reading/i];
  const pagePatterns = [/下一页/i, /next\s*page/i, /^next$/i];
  const chapterLinks = [];
  const pageLinks = [];
  const relNext = root.querySelector?.("link[rel='next'], a[rel='next']");
  if (relNext) pageLinks.push(relNext);
  for (const anchor of root.querySelectorAll?.("a[href]") || []) {
    const label = cleanInline(
      anchor.textContent || anchor.getAttribute("aria-label") || anchor.getAttribute("title") || ""
    );
    if (chapterPatterns.some(pattern => pattern.test(label))) chapterLinks.push(anchor);
    else if (pagePatterns.some(pattern => pattern.test(label))) pageLinks.push(anchor);
  }
  for (const element of [...chapterLinks, ...pageLinks]) {
    const href = element.getAttribute("href");
    if (!href) continue;
    try {
      const url = new URL(href, baseUrl);
      if (/^https?:/i.test(url.href) && url.href !== baseUrl) return url.href;
    } catch (_) {
      // Ignore malformed or javascript links.
    }
  }
  return "";
}

function extractChapter(root = document, url = location.href) {
  const rootElement = contentRoot(root);
  const clone = rootElement.cloneNode(true);
  clone.querySelectorAll?.(STRIP_SELECTORS).forEach(element => element.remove());
  const paragraphNodes = clone.querySelectorAll ? [...clone.querySelectorAll("p")] : [];
  const paragraphs = paragraphNodes
    .map(paragraph => cleanInline(paragraph.textContent))
    .filter(text => text.length >= 2);
  let text = paragraphs.length >= 2
    ? paragraphs.join("\n\n")
    : normalizeText(clone.innerText || clone.textContent || "");
  if (text.length > 120000) text = text.slice(0, 120000);
  return {
    url,
    title: chapterTitle(root),
    text,
    next_url: findNextChapterUrl(root, url)
  };
}

function bookKeyForUrl(value) {
  const url = new URL(value);
  for (const key of ["book", "book_id", "bookid", "novel", "novel_id", "novelid", "bid"]) {
    const item = url.searchParams.get(key);
    if (item) return `${url.origin}?${key}=${item}`;
  }
  const parts = url.pathname.split("/").filter(Boolean);
  const markers = new Set(["book", "novel", "story", "read", "article"]);
  const marker = parts.findIndex(part => markers.has(part.toLowerCase()));
  if (marker >= 0) return `${url.origin}/${parts.slice(0, marker + 2).join("/")}`;
  if (parts.length >= 2) {
    const last = parts[parts.length - 1];
    if (/\d{1,6}/.test(last) || /chapter|chap|read|page|episode|ep/i.test(last)) {
      return `${url.origin}/${parts.slice(0, -1).join("/")}`;
    }
  }
  return `${url.origin}${url.pathname}`;
}

async function captureReading(maxChapters = 2) {
  const limit = Math.max(1, Math.min(2, Number(maxChapters) || 2));
  const first = extractChapter(document, location.href);
  if (!first.text || first.text.length < 40) {
    throw new Error("没有识别到正文，请确认当前页是小说、文章或 EPUB 阅读页。");
  }
  const chapters = [first];
  let nextUrl = first.next_url;
  while (chapters.length < limit && nextUrl) {
    try {
      const target = new URL(nextUrl);
      if (target.origin !== location.origin) break;
      const response = await fetch(target.href, { credentials: "include" });
      if (!response.ok) break;
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const next = extractChapter(parsed, target.href);
      if (!next.text || next.text.length < 40) break;
      chapters.push(next);
      nextUrl = next.next_url;
    } catch (_) {
      break;
    }
  }
  let offset = 0;
  const chunks = chapters.map((chapter, index) => {
    const start = offset;
    offset += chapter.text.length;
    return {
      id: `chapter-${index}-${start}-${offset}`,
      chapter: chapter.title || first.title || `Chapter ${index + 1}`,
      start_offset: start,
      end_offset: offset,
      text: chapter.text,
      page: null
    };
  });
  return {
    url: first.url,
    book_key: bookKeyForUrl(first.url),
    kind: "web",
    chapter: first.title,
    cursor: first.text.length,
    spoiler_cursor: first.text.length,
    selected_start: 0,
    selected_end: first.text.length,
    text: first.text,
    chunks
  };
}

document.addEventListener("mouseup", () => { currentSelection(); });
document.addEventListener("keyup", () => { currentSelection(); });

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "AMADEUS_CAPTURE_SELECTION") {
    sendResponse({
      text: currentSelection(),
      url: location.href,
      book_key: bookKeyForUrl(location.href),
      chapter: chapterTitle(document),
      // No page number here. The old value was window.scrollY, which is a pixel
      // offset, not a page; the adapter reads page: null as "unknown" and leaves
      // the stored page alone. A reader-specific client can supply a real one.
      page: null
    });
    return true;
  }
  if (message?.type === "AMADEUS_CAPTURE_READING") {
    captureReading(message.maxChapters)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ error: String(error) }));
    return true;
  }
  return false;
});
