if (globalThis.__AMADEUS_READING_BRIDGE_LOADED__) {
  throw new Error("Amadeus Reading Bridge already loaded");
}
globalThis.__AMADEUS_READING_BRIDGE_LOADED__ = true;

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

function absoluteUrl(value, baseUrl = location.href) {
  try {
    const url = new URL(String(value || "").trim(), baseUrl);
    return /^https?:/i.test(url.href) ? url.href : "";
  } catch (_) {
    return "";
  }
}

function uniqueUrls(values) {
  return [...new Set(values.filter(Boolean))];
}

const wenku8Adapter = {
  id: "wenku8",
  contentRoot(root) {
    return root.querySelector("#content");
  },
  extraStripSelectors: "#contentdp, .ad, .ads, .advertisement",
  isIndexPage(_root, url) {
    try {
      return /\/index\.htm(?:[?#]|$)/i.test(new URL(url).pathname);
    } catch (_) {
      return false;
    }
  },
  chapterUrls(root, baseUrl) {
    const urls = [];
    for (const anchor of root.querySelectorAll("a[href]") || []) {
      const href = String(anchor.getAttribute("href") || "").trim();
      if (!href || !/(?:^|\/)\d+\.htm(?:[?#].*)?$/i.test(href)) continue;
      const label = cleanInline(anchor.textContent || "");
      if (!label) continue;
      urls.push(absoluteUrl(href, baseUrl));
    }
    return uniqueUrls(urls);
  },
  nextChapterUrl(root, baseUrl) {
    const scripts = [...root.querySelectorAll("script") || []]
      .map(script => script.textContent || "")
      .join("\n");
    const match = scripts.match(/var\s+next_page\s*=\s*["']([^"']+)["']/i);
    const candidate = match?.[1] ? absoluteUrl(match[1], baseUrl) : "";
    return candidate && !/\/index\.htm(?:[?#]|$)/i.test(new URL(candidate).pathname)
      ? candidate
      : "";
  },
  bookKey(url) {
    const parsed = new URL(url);
    const match = parsed.pathname.match(/^\/novel\/([^/]+)\/([^/]+)(?:\/|$)/i);
    return match ? `${parsed.origin}/novel/${match[1]}/${match[2]}` : "";
  }
};

const linovelibAdapter = {
  id: "linovelib",
  contentRoot(root) {
    return root.querySelector("#acontent, #chaptercontent, .read-content, .box_con");
  },
  extraStripSelectors: ".ad, .ads, .advertisement, .chapter-nav, .readpage, .read-page",
  isIndexPage(_root, url) {
    try {
      const path = new URL(url).pathname;
      return /^\/novel\/\d+\.html$/i.test(path) || /^\/novel\/\d+\/?$/i.test(path);
    } catch (_) {
      return false;
    }
  },
  chapterUrls(root, baseUrl) {
    const parsed = new URL(baseUrl);
    const book = parsed.pathname.match(/^\/novel\/(\d+)/i)?.[1] || "";
    if (!book) return [];
    const pattern = new RegExp(`^/novel/${book}/\\d+\\.html$`, "i");
    const urls = [];
    for (const anchor of root.querySelectorAll("a[href]") || []) {
      const href = absoluteUrl(anchor.getAttribute("href"), baseUrl);
      if (!href) continue;
      try {
        if (pattern.test(new URL(href).pathname)) urls.push(href);
      } catch (_) {
        // Ignore malformed links.
      }
    }
    return uniqueUrls(urls);
  },
  nextChapterUrl(root, baseUrl) {
    const next = root.querySelector("#next_url, a#next_url, a[rel='next']");
    return next ? absoluteUrl(next.getAttribute("href"), baseUrl) : "";
  },
  bookKey(url) {
    const parsed = new URL(url);
    const match = parsed.pathname.match(/^\/novel\/(\d+)/i);
    return match ? `${parsed.origin}/novel/${match[1]}` : "";
  }
};

function siteAdapterFor(_root = document, url = location.href) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host === "wenku8.net" || host.endsWith(".wenku8.net") || host.endsWith(".wenku8.com")) {
      return wenku8Adapter;
    }
    if (host === "linovelib.com" || host.endsWith(".linovelib.com")) {
      return linovelibAdapter;
    }
  } catch (_) {
    // Fall through to the generic adapter.
  }
  return null;
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
  "#acontent",
  "#chaptercontent",
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

function contentRoot(root = document, url = location.href) {
  const adapter = siteAdapterFor(root, url);
  const preferred = adapter?.contentRoot?.(root);
  if (preferred) return preferred;
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
  const adapter = siteAdapterFor(root, baseUrl);
  const adapted = adapter?.nextChapterUrl?.(root, baseUrl);
  if (adapted) return adapted;

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
    const candidate = absoluteUrl(element.getAttribute("href"), baseUrl);
    if (candidate && candidate !== baseUrl) return candidate;
  }
  return "";
}

function extractChapter(root = document, url = location.href) {
  const rootElement = contentRoot(root, url);
  const adapter = siteAdapterFor(root, url);
  const clone = rootElement.cloneNode(true);
  const selectors = adapter?.extraStripSelectors
    ? `${STRIP_SELECTORS}, ${adapter.extraStripSelectors}`
    : STRIP_SELECTORS;
  clone.querySelectorAll?.(selectors).forEach(element => element.remove());
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
  const parsed = new URL(value);
  const adapter = siteAdapterFor(document, value);
  const override = adapter?.bookKey?.(value);
  if (override) return override;
  for (const key of ["book", "book_id", "bookid", "novel", "novel_id", "novelid", "bid"]) {
    const item = parsed.searchParams.get(key);
    if (item) return `${parsed.origin}?${key}=${item}`;
  }
  const parts = parsed.pathname.split("/").filter(Boolean);
  const markers = new Set(["book", "novel", "story", "read", "article"]);
  const marker = parts.findIndex(part => markers.has(part.toLowerCase()));
  if (marker >= 0) return `${parsed.origin}/${parts.slice(0, marker + 2).join("/")}`;
  if (parts.length >= 2) {
    const last = parts[parts.length - 1];
    if (/\d{1,6}/.test(last) || /chapter|chap|read|page|episode|ep/i.test(last)) {
      return `${parsed.origin}/${parts.slice(0, -1).join("/")}`;
    }
  }
  return `${parsed.origin}${parsed.pathname}`;
}

async function fetchChapter(url) {
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) throw new Error(`章节抓取失败：HTTP ${response.status}`);
  const html = await response.text();
  const parsed = new DOMParser().parseFromString(html, "text/html");
  return extractChapter(parsed, url);
}

async function captureReading(maxChapters = 2) {
  const limit = Math.max(1, Math.min(2, Number(maxChapters) || 2));
  const adapter = siteAdapterFor(document, location.href);
  let first;
  if (adapter?.isIndexPage?.(document, location.href)) {
    const urls = adapter.chapterUrls?.(document, location.href) || [];
    if (!urls.length) throw new Error("目录页没有找到章节链接。");
    first = await fetchChapter(urls[0]);
  } else {
    first = extractChapter(document, location.href);
  }
  if (!first.text || first.text.length < 40) {
    throw new Error("没有识别到正文，请确认当前页是小说、文章或 EPUB 阅读页。");
  }
  const chapters = [first];
  let nextUrl = first.next_url;
  while (chapters.length < limit && nextUrl) {
    try {
      const target = new URL(nextUrl);
      if (target.origin !== location.origin) break;
      const next = await fetchChapter(target.href);
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

function comicImageSource(element) {
  const direct = [
    element.dataset?.src,
    element.dataset?.original,
    element.dataset?.lazySrc,
    element.dataset?.url,
    element.dataset?.imagesrc,
    element.getAttribute("data-src"),
    element.getAttribute("data-original"),
    element.getAttribute("data-lazy-src"),
    element.getAttribute("data-url"),
    element.getAttribute("data-imagesrc"),
    element.getAttribute("data-cfsrc"),
    element.currentSrc,
    element.src
  ];
  for (const candidate of direct) {
    const value = String(candidate || "").trim();
    if (!value) continue;
    // Lazy-loaders commonly leave a 1px GIF/PNG in src before data-src is applied.
    if (/^data:image\/(?:gif|png);base64,(?:R0lGOD|iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB)/i.test(value)) {
      continue;
    }
    return value;
  }
  const srcset = String(element.getAttribute("srcset") || element.dataset?.srcset || "").trim();
  if (srcset) {
    const parts = srcset
      .split(",")
      .map(part => part.trim().split(/\s+/)[0])
      .filter(Boolean);
    if (parts.length) return parts[parts.length - 1];
  }
  try {
    const view = element.ownerDocument?.defaultView || window;
    const background = String(view.getComputedStyle(element).backgroundImage || "");
    const match = background.match(/url\(["']?(.*?)["']?\)/i);
    return match?.[1] ? String(match[1]).trim() : "";
  } catch (_) {
    return "";
  }
}

function comicImageCandidate(element, source, order) {
  const rawSource = String(source || "").trim();
  if (!rawSource) return null;
  const baseUrl = element.ownerDocument?.location?.href || location.href;
  const url = /^(?:data|blob):/i.test(rawSource)
    ? rawSource
    : absoluteUrl(rawSource, baseUrl);
  if (!url) return null;
  if (/(?:^|[\/._-])(?:logo|icon|avatar|sprite|emoji|button|pixel|spacer|loading|advert|banner)(?:[\/._-]|$)/i.test(url)) {
    return null;
  }
  if (/^data:image\/gif;base64,R0lGOD/i.test(url)) return null;
  const rect = element.getBoundingClientRect?.() || { top: 0, width: 0, height: 0 };
  const naturalWidth = Number(
    element.naturalWidth || element.videoWidth || element.width ||
    element.getAttribute?.("width") || rect.width || 0
  );
  const naturalHeight = Number(
    element.naturalHeight || element.videoHeight || element.height ||
    element.getAttribute?.("height") || rect.height || 0
  );
  const width = Math.round(naturalWidth || rect.width || 0);
  const height = Math.round(naturalHeight || rect.height || 0);
  if (width < 240 && height < 240) return null;
  return {
    url,
    width,
    height,
    top: Number(rect.top || 0),
    order,
    area: Math.max(width, height)
  };
}

function accessibleComicDocuments(root = document) {
  const documents = [];
  const seen = new Set();
  const visit = (candidate) => {
    if (!candidate || seen.has(candidate)) return;
    seen.add(candidate);
    documents.push(candidate);
    let elements = [];
    try {
      elements = candidate.querySelectorAll("*") || [];
    } catch (_) {
      return;
    }
    for (const element of elements) {
      if (element.shadowRoot) visit(element.shadowRoot);
      if (String(element.tagName || "").toUpperCase() === "IFRAME") {
        try {
          if (element.contentDocument) visit(element.contentDocument);
        } catch (_) {
          // Cross-origin iframe.
        }
      }
    }
  };
  visit(root);
  return documents;
}

async function collectComicImages(maxPages = 40) {
  const limit = Math.max(1, Math.min(40, Number(maxPages) || 40));
  const found = new Map();
  let order = 0;

  const add = (element, source) => {
    const candidate = comicImageCandidate(element, source, order++);
    if (!candidate) return;
    const existing = found.get(candidate.url);
    if (!existing || candidate.area > existing.area) found.set(candidate.url, candidate);
  };

  const scanResources = () => {
    try {
      for (const entry of performance.getEntriesByType("resource") || []) {
        const url = String(entry.name || "").trim();
        if (!/\.(?:png|jpe?g|webp|avif|gif)(?:[?#]|$)/i.test(url)) continue;
        if (/(?:^|[\/._-])(?:logo|icon|avatar|sprite|emoji|button|pixel|spacer|loading|advert|banner)(?:[\/._-]|$)/i.test(url)) {
          continue;
        }
        if (entry.initiatorType && entry.initiatorType !== "img" && !/(comic|manga|chapter|page|image|pic|cdn)/i.test(url)) {
          continue;
        }
        if (!found.has(url)) {
          found.set(url, { url, width: 0, height: 0, top: 0, order: order++, area: 0 });
        }
      }
    } catch (_) {
      // Resource timing is best-effort.
    }
  };

  const scan = () => {
    for (const doc of accessibleComicDocuments()) {
      for (const image of doc.images || []) {
        const source = comicImageSource(image);
        if (source) add(image, source);
      }
      for (const sourceElement of doc.querySelectorAll("picture source[srcset]") || []) {
        const source = comicImageSource(sourceElement);
        if (source) add(sourceElement, source);
      }
      for (const element of doc.querySelectorAll(
        "[style*='background-image'], [data-src], [data-original], [data-lazy-src], [data-imagesrc]"
      ) || []) {
        if (String(element.tagName || "").toUpperCase() === "IMG") continue;
        const source = comicImageSource(element);
        if (source) add(element, source);
      }
      for (const canvas of doc.querySelectorAll("canvas") || []) {
        try {
          const dataUrl = canvas.toDataURL("image/jpeg", 0.78);
          if (dataUrl && dataUrl.length > 200) add(canvas, dataUrl);
        } catch (_) {
          // Cross-origin canvas cannot be exported.
        }
      }
    }
    scanResources();
  };

  scan();
  const originalX = window.scrollX;
  const originalY = window.scrollY;
  let totalHeight = Math.max(
    document.body?.scrollHeight || 0,
    document.documentElement?.scrollHeight || 0
  );
  const step = Math.max(320, Math.floor(window.innerHeight * 0.8));
  if (found.size < limit && totalHeight > window.innerHeight) {
    for (let y = 0, steps = 0; y < totalHeight && found.size < limit && steps < 60; y += step, steps++) {
      window.scrollTo(0, y);
      await new Promise(resolve => setTimeout(resolve, 140));
      scan();
      totalHeight = Math.max(
        totalHeight,
        document.body?.scrollHeight || 0,
        document.documentElement?.scrollHeight || 0
      );
    }
    window.scrollTo(originalX, originalY);
    await new Promise(resolve => setTimeout(resolve, 60));
  }

  return [...found.values()]
    .sort((a, b) => a.order - b.order || a.top - b.top || b.area - a.area)
    .slice(0, limit);
}

async function imageToDataUrl(url) {
  if (!url) return "";
  if (String(url).startsWith("data:")) return String(url);
  try {
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) return "";
    const blob = await response.blob();
    if (!blob.size || blob.size > 12 * 1024 * 1024) return "";
    try {
      const bitmap = await createImageBitmap(blob);
      const scale = Math.min(1, 720 / Math.max(1, bitmap.width));
      const width = Math.max(1, Math.round(bitmap.width * scale));
      const height = Math.max(1, Math.round(bitmap.height * scale));
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { alpha: false });
      if (context) {
        context.fillStyle = "#fff";
        context.fillRect(0, 0, width, height);
        context.drawImage(bitmap, 0, 0, width, height);
        bitmap.close?.();
        return canvas.toDataURL("image/jpeg", 0.78);
      }
      bitmap.close?.();
    } catch (_) {
      // Fall back to the original bytes when the browser cannot decode/resize.
    }
    return await new Promise(resolve => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => resolve("");
      reader.readAsDataURL(blob);
    });
  } catch (_) {
    return "";
  }
}

function comicNextUrl(root = document, baseUrl = location.href) {
  const selectors = ["a[rel='next']", "a.next", ".next a", ".next_page", "#next_url", "a[href*='next']", "a[href*='chapter']"];
  for (const selector of selectors) {
    for (const element of root.querySelectorAll(selector) || []) {
      const candidate = absoluteUrl(element.getAttribute("href"), baseUrl);
      if (candidate && candidate !== baseUrl) return candidate;
    }
  }
  return "";
}

async function captureComicChapter(maxPages = 40) {
  const limit = Math.max(1, Math.min(40, Number(maxPages) || 40));
  const candidates = await collectComicImages(limit);
  if (!candidates.length) throw new Error("没有识别到漫画页面图片，请确认当前页是漫画阅读页。");
  const images = [];
  let payloadChars = 0;
  const maxPayloadChars = 48 * 1024 * 1024;
  for (const item of candidates) {
    const dataUrl = item.data_url || await imageToDataUrl(item.url);
    if (!dataUrl) continue;
    if (payloadChars + dataUrl.length > maxPayloadChars) continue;
    images.push({
      url: item.url || "",
      data_url: dataUrl,
      width: item.width,
      height: item.height
    });
    payloadChars += dataUrl.length;
  }
  if (!images.length) throw new Error("漫画图片读取失败，请检查图片是否需要登录或跨域权限。");
  return {
    url: location.href,
    book_key: bookKeyForUrl(location.href),
    chapter: chapterTitle(document),
    page: null,
    images,
    next_url: comicNextUrl(document, location.href)
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
  if (message?.type === "AMADEUS_CAPTURE_COMIC_CHAPTER") {
    captureComicChapter(message.maxPages)
      .then(result => sendResponse(result))
      .catch(error => sendResponse({ error: String(error) }));
    return true;
  }
  return false;
});
