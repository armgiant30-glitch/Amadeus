var AmadeusZoteroBridge = {
  id: "amadeus-zotero-bridge@example.com",
  endpoint: "http://127.0.0.1:17878/zotero/sync",
  windows: new Set(),
  _listener: null,

  startup({ id, rootURI }, reason) {
    this.id = id || this.id;
    this.rootURI = rootURI;
    this._listener = {
      onOpenWindow: (window) => {
        const win = window.docShell?.domWindow;
        if (win) win.addEventListener("load", () => this.loadWindow(win), { once: true });
      },
      onCloseWindow: (window) => this.unloadWindow(window),
      onWindowTitleChange: () => {},
    };
    try { Services.wm.addListener(this._listener); } catch (_) {}
    const win = Zotero.getMainWindow();
    if (win) this.loadWindow(win);
  },

  shutdown() {
    try { Services.wm.removeListener(this._listener); } catch (_) {}
    for (const win of Array.from(this.windows)) this.unloadWindow(win);
    this.windows.clear();
  },

  loadWindow(win) {
    if (!win || win.closed || this.windows.has(win)) return;
    const doc = win.document;
    if (!doc || doc.documentElement?.getAttribute("windowtype") !== "navigator:browser") return;
    const popup = doc.getElementById("zotero-itemmenu");
    if (!popup || doc.getElementById(this.id)) return;
    const item = doc.createXULElement("menuitem");
    item.id = this.id;
    item.setAttribute("label", "发送到 Amadeus Companion");
    item.addEventListener("command", () => this.sendSelected(win));
    popup.appendChild(item);
    this.windows.add(win);
  },

  unloadWindow(win) {
    if (!win) return;
    try { win.document?.getElementById(this.id)?.remove(); } catch (_) {}
    this.windows.delete(win);
  },

  selectedRegularItem() {
    const pane = Zotero.getActiveZoteroPane();
    const selected = pane?.getSelectedItems?.() || [];
    for (const item of selected) {
      if (item?.isRegularItem?.() || item?.isRegularItem) return item;
    }
    return null;
  },

  async sendSelected(win) {
    const item = this.selectedRegularItem();
    if (!item) {
      win.alert("请先选择一条 Zotero 文献条目。");
      return;
    }
    try {
      const response = await Zotero.HTTP.request("POST", this.endpoint, {
        body: JSON.stringify({ item_key: String(item.key || "") }),
        headers: { "Content-Type": "application/json" },
        responseType: "json",
        timeout: 30000,
        successCodes: [200],
      });
      const result = response.response || {};
      const title = result?.item?.title || item.getField?.("title") || "当前条目";
      const accepted = Number(result?.chars || 0);
      const source = Number(result?.source_chars ?? accepted);
      const suffix = result?.truncated ? `（从 ${source} 字符截断）` : "";
      win.alert(`Amadeus 已接收《${title}》，共 ${accepted} 字符${suffix}。`);
    } catch (error) {
      win.alert(`发送到 Amadeus 失败：${error?.message || error}`);
    }
  },
};

function install() {}
function uninstall() {}
async function startup(data, reason) {
  await Zotero.initializationPromise;
  AmadeusZoteroBridge.startup(data, reason);
}
function onMainWindowLoad({ window }) {
  AmadeusZoteroBridge.loadWindow(window);
}
function onMainWindowUnload({ window }) {
  AmadeusZoteroBridge.unloadWindow(window);
}
function shutdown(data, reason) {
  AmadeusZoteroBridge.shutdown();
}
