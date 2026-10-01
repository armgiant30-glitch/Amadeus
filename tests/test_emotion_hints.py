from __future__ import annotations

from core.emotion_hints import infer_emotion_from_text


def test_emotion_hints_cover_clear_visible_signals() -> None:
    assert infer_emotion_from_text("我今天特别开心，快夸夸我") == "happy"
    assert infer_emotion_from_text("呜，今天真的很难过") == "sad"
    assert infer_emotion_from_text("喂，不许说我老！") == "angry"
    assert infer_emotion_from_text("欸？真的吗，没想到会这样") == "sided_surprised"
    assert infer_emotion_from_text("其实我有一点害羞") == "blush"
    assert infer_emotion_from_text("算了，反正也没办法") == "disappointed"
    assert infer_emotion_from_text("为什么这个会这样，我想想") == "sided_thinking"
    assert infer_emotion_from_text("今天天气不错") == ""
