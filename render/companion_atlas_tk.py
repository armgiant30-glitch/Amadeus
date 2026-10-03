"""Native portrait-only adapter for the same Companion Lite atlas/timeline contract."""
from __future__ import annotations

from collections import OrderedDict
import json
import math
from pathlib import Path
import time

from PIL import Image, ImageDraw

from render.companion_pack import BYTE_LIMIT, CompanionPackError, load_companion_pack, validate_clip


DEFAULT_EMOTION_FALLBACKS = {
    "surprised": ("surprised", "sided_surprised", "normal"),
    "shy": ("shy", "blush", "normal"),
    "excited": ("excited", "happy", "normal"),
    "confused": ("confused", "sided_thinking", "normal"),
    "sleepy": ("sleepy", "normal"),
    "smug": ("smug", "happy", "normal"),
    "worried": ("worried", "sad", "normal"),
    "crying": ("sad", "disappointed", "normal"),
}


class AtlasPlayer:
    """At most two decoded atlases/16 MiB, plus the caller's one displayed tile.

    Tk owns its window. This class owns no widget, whole-window style or speech source.
    Selection/timing matches render/web/companion_atlas.js; no interpolation or recrop.
    """

    def __init__(self, root: Path, *, clock=time.monotonic):
        self.root = Path(root)
        load_companion_pack(self.root)
        manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        self.emotions = manifest["emotions"]
        self.mouth_config = manifest.get("mouth") or {}
        self.aliases = manifest.get("aliases") or {}
        self.fallbacks = manifest.get("fallbacks") or {}
        self.mouth_drive = manifest.get("mouthDrive") or {}
        self.speaking_pool = [str(item) for item in (manifest.get("speakingPool") or []) if str(item)]
        self._speaking_pool_index = 0
        self._pending_select = None
        self.clock = clock
        self.entries: OrderedDict[str, Image.Image] = OrderedDict()
        self.resident_bytes = 0
        self.speech_counts: dict[str, int] = {}
        self.last_speaking = False
        self.last_emotion = ""
        self.spec = None
        self.mouth_spec = None
        self.mouth_entry = None
        self.mouth_entry_url = ""
        self.mouth_value = 1.0
        self.started = clock()
        self.elapsed = 0.0
        self.paused = False
        self.last_tile = None
        self.draws = 0

    def resolve_emotion(self, emotion: str) -> str:
        key = str(emotion or "").strip().lower()
        key = str(self.aliases.get(key) or key)
        if key in self.emotions:
            return key
        for candidate in self.fallbacks.get(key, DEFAULT_EMOTION_FALLBACKS.get(key, ())):
            if candidate in self.emotions:
                return candidate
        return "normal" if "normal" in self.emotions else next(iter(self.emotions))

    def request_select(
        self,
        emotion: str,
        speaking: bool,
        static_idle: bool = False,
        *,
        advance_variant: bool = False,
    ) -> None:
        key = self.resolve_emotion(emotion)
        if speaking and self.last_speaking and key != self.last_emotion and self.spec is not None:
            self._pending_select = (key, speaking, static_idle, advance_variant)
            return
        self._pending_select = None
        self.select(key, speaking, static_idle, advance_variant=advance_variant)

    def choose_speaking_emotion(self, emotion: str, *, advance_variant: bool = False) -> str:
        requested = self.resolve_emotion(emotion)
        pool = [self.resolve_emotion(item) for item in self.speaking_pool]
        pool = [item for item in pool if item in self.emotions]
        if not pool:
            return requested
        if requested in pool:
            return requested
        if advance_variant:
            self._speaking_pool_index = (self._speaking_pool_index + 1) % len(pool)
        return pool[self._speaking_pool_index]

    def select(self, emotion: str, speaking: bool, static_idle: bool = False, *, advance_variant: bool = False):
        self._pending_select = None
        key = self.resolve_emotion(emotion)
        if speaking and (advance_variant or not self.last_speaking or key != self.last_emotion):
            self.speech_counts[key] = self.speech_counts.get(key, 0) + 1
        self.last_speaking, self.last_emotion = speaking, key
        self.mouth_spec = self.mouth_config.get(key) if speaking else None
        if self.mouth_spec:
            mouth_url = str(self.mouth_spec.get("url") or "")
            if mouth_url and mouth_url != self.mouth_entry_url:
                if self.mouth_entry is not None:
                    self.mouth_entry.close()
                with Image.open(self.root / mouth_url) as image:
                    self.mouth_entry = image.convert("RGBA")
                self.mouth_entry_url = mouth_url
        states = self.emotions[key]
        alternate = "speakingAlternate" in states and self.speech_counts.get(key, 1) % 2 == 0
        spec = (states["speakingAlternate"] if alternate else states["speaking"]) if speaking else (
            states.get("idleStatic", states["idle"]) if static_idle else states["idle"])
        if spec is self.spec:
            return
        url = spec["url"]
        dimensions = validate_clip(spec)
        if url not in self.entries:
            while len(self.entries) >= 2 or self.resident_bytes + spec["decodedBytes"] > BYTE_LIMIT:
                _, old = self.entries.popitem(last=False)
                self.resident_bytes -= old.width * old.height * 4
                old.close()
            with Image.open(self.root / url) as image:
                if image.format != "WEBP" or image.size != dimensions:
                    raise CompanionPackError("Portrait dimensions differ from manifest")
                self.entries[url] = image.convert("RGBA")
            self.resident_bytes += spec["decodedBytes"]
        elif self.entries[url].size != dimensions:
            raise CompanionPackError("Portrait dimensions differ from manifest")
        self.entries.move_to_end(url)
        self.spec = spec
        self.started, self.elapsed = self.clock(), 0.0
        self.last_tile = None

    def set_mouth_value(self, value: float) -> None:
        try:
            self.mouth_value = max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            self.mouth_value = 1.0

    def _close_mouth(self, image: Image.Image) -> Image.Image:
        spec = self.mouth_spec or {}
        source = self.mouth_entry
        if source is None:
            return image
        roi = spec.get("roi") if isinstance(spec.get("roi"), dict) else {}
        try:
            width = float(roi.get("width", 0) or 0)
            height = float(roi.get("height", 0) or 0)
            cx = image.width / 2 + float(roi.get("cx", 0) or 0)
            cy = image.height / 2 + float(roi.get("cy", 0) or 0)
        except (TypeError, ValueError):
            return image
        if width <= 0 or height <= 0:
            return image
        mask = Image.new("L", image.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2), fill=255)
        result = image.copy()
        result.paste(source, (0, 0), mask)
        return result

    def frame(self) -> tuple[Image.Image | None, int | None]:
        """Return a changed tile and milliseconds to its next change (None = no timer)."""
        if self.spec is None:
            return None, None
        if self._pending_select is not None and not self.paused:
            elapsed_pending = self.elapsed if self.paused else (self.clock() - self.started) * 1000
            position_pending = (elapsed_pending % self.spec["durationMs"]) / self.spec["durationMs"] * len(self.spec["sequence"])
            index_pending = math.floor(position_pending)
            if index_pending >= len(self.spec["sequence"]) - 1:
                pending = self._pending_select
                self._pending_select = None
                emotion, speaking, static_idle, advance_variant = pending
                self.select(emotion, speaking, static_idle, advance_variant=advance_variant)
        spec = self.spec
        elapsed = self.elapsed if self.paused else (self.clock() - self.started) * 1000
        position = (elapsed % spec["durationMs"]) / spec["durationMs"] * len(spec["sequence"])
        index = math.floor(position)
        tile = spec["sequence"][index]
        mouth_driven = bool(self.last_speaking and self.mouth_drive)
        if mouth_driven:
            threshold = float((self.mouth_spec or {}).get("threshold", 0.08))
            closed = int(self.mouth_drive.get("closedFrame", 0) or 0)
            max_open = max(1, int(self.mouth_drive.get("maxOpenFrame", 13) or 13))
            if self.mouth_value <= threshold:
                tile = spec["sequence"][closed % len(spec["sequence"])]
            else:
                level = max(0.0, min(1.0, (self.mouth_value - threshold) / max(1e-6, 1.0 - threshold)))
                center = int(round(level * max_open))
                speed = float(self.mouth_drive.get("speed", 8.0) or 8.0)
                wobble = float(self.mouth_drive.get("wobble", 1.0) or 0.0)
                offset = int(round(math.sin((elapsed / 1000.0) * speed * math.tau) * wobble))
                frame_index = max(1, min(max_open, center + offset))
                tile = spec["sequence"][frame_index % len(spec["sequence"])]
        image = None
        if tile != self.last_tile:
            size, columns = spec["size"], spec["columns"]
            left, top = tile % columns * size, tile // columns * size
            image = self.entries[spec["url"]].crop((left, top, left + size, top + size))
            threshold = float((self.mouth_spec or {}).get("threshold", 0.08))
            if self.last_speaking and self.mouth_value <= threshold:
                image = self._close_mouth(image)
            self.last_tile = tile
            self.draws += 1
        if self.paused:
            return image, None
        if mouth_driven:
            return image, 40
        if all(value == tile for value in spec["sequence"]):
            return image, None
        steps = 1
        while steps < len(spec["sequence"]) and spec["sequence"][(index + steps) % len(spec["sequence"])] == tile:
            steps += 1
        return image, max(1, math.ceil((index + steps - position) * spec["durationMs"] / len(spec["sequence"])))

    def set_paused(self, paused: bool):
        if paused == self.paused:
            return
        if paused:
            self.elapsed = (self.clock() - self.started) * 1000
        else:
            self.started = self.clock() - self.elapsed / 1000
        self.paused = paused

    def close(self):
        for image in self.entries.values():
            image.close()
        if self.mouth_entry is not None:
            self.mouth_entry.close()
            self.mouth_entry = None
        self.mouth_entry_url = ""
        self.entries.clear()
        self.resident_bytes = 0
        self.spec = None
        self._pending_select = None
