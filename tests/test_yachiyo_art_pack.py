from __future__ import annotations

from pathlib import Path

from core.emotion_hints import infer_emotion_from_text
from render.companion_atlas_tk import AtlasPlayer
from render.companion_pack import load_companion_pack


PACK = Path(__file__).resolve().parents[1] / "assets" / "companion" / "yachiyo"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_yachiyo_pack_has_11_emotions_and_no_removed() -> None:
    manifest = __import__("json").loads((PACK / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["emotions"]) == 11
    assert all(name not in manifest["emotions"] for name in ("crying", "excited", "worried", "smug"))
    assert all(name not in manifest["mouth"] for name in ("crying", "excited", "worried", "smug"))
    assert all("idleStatic" in states for states in manifest["emotions"].values())
    assert all(not (value.startswith("C:") or value.startswith("D:")) for value in _strings(manifest))
    load_companion_pack(PACK)


def test_new_emotion_hints() -> None:
    assert infer_emotion_from_text("我好担心你") == "worried"
    assert infer_emotion_from_text("我好困啊") == "sleepy"
    assert infer_emotion_from_text("真的吗？不会吧") == "sided_surprised"
    assert infer_emotion_from_text("好耶！太棒了") == "excited"
    assert infer_emotion_from_text("我有点不好意思") == "shy"
    assert infer_emotion_from_text("这到底是什么意思") == "confused"
    assert infer_emotion_from_text("看我厉害吧，得意") == "smug"


def test_atlas_fallback_and_deferred_speaking_switch() -> None:
    now = [0.0]
    player = AtlasPlayer(PACK, clock=lambda: now[0])
    try:
        assert player.resolve_emotion("surprise") == "surprised"
        assert player.resolve_emotion("sided_surprised") == "surprised"
        assert player.resolve_emotion("crying") == "sad"
        assert player.resolve_emotion("excited") == "happy"
        assert player.resolve_emotion("worried") == "sad"
        assert player.resolve_emotion("smug") == "happy"
        assert player.resolve_emotion("excited") == "happy"
        assert player.resolve_emotion("worried") == "sad"
        assert player.resolve_emotion("smug") == "happy"
        assert player.resolve_emotion("unknown") == "normal"
        player.select("normal", True)
        player.request_select("happy", True)
        assert player.last_emotion == "normal"
        now[0] = 3.9
        player.frame()
        assert player.last_emotion == "happy"
        assert player.mouth_entry_url.endswith("happy/mouth-closed.webp")
    finally:
        player.close()


def test_yachiyo_mouth_follows_amplitude() -> None:
    now = [0.0]
    player = AtlasPlayer(PACK, clock=lambda: now[0])
    try:
        player.select("worried", True)
        player.set_mouth_value(0.0)
        frame, _delay = player.frame()
        assert player.last_tile == 0
        frame.close()

        player.set_mouth_value(0.9)
        now[0] = 0.1
        frame, _delay = player.frame()
        assert 1 <= player.last_tile <= 13
        frame.close()

        player.set_mouth_value(0.35)
        now[0] = 0.2
        frame, _delay = player.frame()
        assert 1 <= player.last_tile <= 13
        frame.close()
    finally:
        player.close()


def test_speaking_pool_alternates_surprised_and_happy() -> None:
    player = AtlasPlayer(PACK)
    try:
        first = player.choose_speaking_emotion("normal", advance_variant=True)
        second = player.choose_speaking_emotion("sad", advance_variant=True)
        assert {first, second} == {"surprised", "happy"}
        assert first != second
        assert player.choose_speaking_emotion("happy", advance_variant=False) == "happy"
    finally:
        player.close()
