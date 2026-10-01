"""Deterministic emotion hints for companion portrait selection.

This is deliberately a small lexical fallback, not a second language model. It
turns clear visible language into an atlas key when the main model omitted its
optional [EMO preset=...] tag.
"""

from __future__ import annotations


_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sided_surprised", ("诶", "欸", "咦", "哇", "真的吗", "不会吧", "没想到", "吓", "吃惊", "まさか", "びっくり", "うそ")),
    ("angry", ("生气", "讨厌", "不许", "笨蛋", "坏蛋", "气死", "别这样", "ムカ", "腹立", "ふざけ", "ばか")),
    ("blush", ("害羞", "不好意思", "脸红", "喜欢你", "告白", "可爱", "恥ずか", "照れ")),
    ("sad", ("难过", "伤心", "想哭", "呜", "寂寞", "撑不住", "失落", "难受", "抱歉", "对不起", "悲しい", "つらい", "寂しい", "疲れ")),
    ("disappointed", ("失望", "算了", "没办法", "无所谓", "随便", "落寞", "残念", "仕方ない")),
    ("happy", ("开心", "高兴", "太好了", "哈哈", "嘻嘻", "嘿嘿", "好耶", "真棒", "谢谢", "谢谢", "欢迎", "夸", "沾光", "愉快", "嬉しい", "楽しい", "よかった")),
    ("sided_thinking", ("为什么", "怎么", "思考", "想想", "或许", "应该", "说明", "解释", "等一下", "どうして", "なぜ", "考え", "たぶん")),
)


def infer_emotion_from_text(text: str) -> str:
    """Return an atlas emotion key for clear lexical emotion signals."""

    value = str(text or "")
    if not value.strip():
        return ""
    best_key = ""
    best_score = 0
    for key, words in _HINTS:
        score = sum(value.count(word) for word in words if word)
        if score > best_score:
            best_key, best_score = key, score
    return best_key
