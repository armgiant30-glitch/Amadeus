(function () {
  "use strict";
  const api = window.companion;
  const caption = document.getElementById("caption");
  const status = document.getElementById("status");
  const portrait = document.getElementById("portrait");
  const fallback = document.getElementById("fallback");
  const portraitWell = portrait.parentElement;
  let frames = {};
  let state = { text: "", emotion: "normal", speaking: false };
  let connected = false;
  let frameIndex = 0;
  let source = null;

  // SpriteForge bridge methods forwarded verbatim to the shared renderApp, so the
  // panel animates with exactly the same engine and frame sets as the wallpaper.
  const SPRITE_METHODS = new Set([
    "loadSpriteFrames", "loadSpriteClipFrames", "loadTransitionFrames",
    "setSpriteClipConfig", "setIdleFrameIntervalMs", "setIdleAnimation",
    "loadMouthConfig", "setEmotion", "setSpeaking", "setMouth",
    "holdSpriteFrame", "holdSpriteClosedFrame", "clearSpriteHold",
    "loadSpriteForgeGraph", "triggerSpriteForgeIntent", "releaseSpriteForge",
  ]);
  let spriteLive = false;

  function spriteApp() {
    const app = window.renderApp;
    return app && typeof app === "object" ? app : null;
  }

  function applySpriteCall(call) {
    if (!call || !SPRITE_METHODS.has(call.method)) return;
    const app = spriteApp();
    if (!app) return;
    const fn = app[call.method];
    if (typeof fn !== "function") return;
    try {
      fn.apply(app, call.args || []);
    } catch (error) {
      console.warn("[companion] sprite call failed:", call.method, error);
      return;
    }
    // The animation surface only replaces the VN portrait once frames exist.
    // The canvas itself stays transparent until a frame renders, so the VN
    // portrait and the text avatar remain visible underneath until then.
    if (!spriteLive && call.method === "loadSpriteFrames"
      && Array.isArray(call.args && call.args[1]) && call.args[1].length) {
      spriteLive = true;
      portraitWell.classList.add("sprite-live");
    }
  }

  function paint() {
    const text = connected ? (state.text || "我在这里，继续吧。") : "连接已断开，正在重连…";
    if (caption.textContent !== text) { caption.textContent = text; caption.scrollTop = 0; }
    status.textContent = connected ? (state.speaking ? "VOICE" : "STANDBY") : "RECONNECTING";
    document.body.classList.toggle("speaking", connected && state.speaking);
  }
  document.getElementById("close").onclick = () => { void api?.close(); };
  document.getElementById("dock").onclick = async () => {
    const docked = await api?.dock();
    if (!docked) status.textContent = "请先用 W 打开游戏预览";
  };
  // VN portrait-cache fallback. Stays idle while the SpriteForge surface is live.
  const animation = setInterval(() => {
    if (spriteLive) return;
    const emotion = frames[state.emotion] || frames.normal;
    if (!emotion) return;
    const mode = connected && state.speaking ? "speaking" : "idle";
    const sequence = emotion[mode]?.length ? emotion[mode] : emotion.idle;
    if (!sequence?.length) return;
    portrait.src = sequence[frameIndex++ % sequence.length];
    portrait.hidden = false;
    fallback.hidden = true;
  }, 170);
  async function start() {
    frames = await api?.portraits() || {};
    const port = Number(new URLSearchParams(location.search).get("bridgePort"));
    if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Missing presentation bridge");
    source = new EventSource(`http://127.0.0.1:${port}/wallpaper/events?retainSubtitle=true`);
    source.onopen = () => {
      connected = true;
      paint();
      void api?.connected(true);
    };
    source.onerror = () => {
      connected = false;
      state = { text: "", emotion: "normal", speaking: false };
      paint();
      void api?.connected(false);
    };
    source.onmessage = event => {
      try {
        const call = JSON.parse(event.data);
        applySpriteCall(call);
        const next = window.CompanionPresentation.apply(state, call);
        if (next !== state) {
          if (next.emotion !== state.emotion || next.speaking !== state.speaking) frameIndex = 0;
          state = next;
          paint();
        }
      } catch (error) { console.warn("[companion] invalid presentation event", error); }
    };
  }
  window.addEventListener("beforeunload", () => { clearInterval(animation); source?.close(); });
  start().catch(error => { caption.textContent = "面板暂时无法连接，可以关闭后重新打开。"; console.error(error); });
})();
