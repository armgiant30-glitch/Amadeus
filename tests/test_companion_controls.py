from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from server.handlers.companion_control_handler import CompanionControlHandler
from server.protocol import Method


class FakeAsr:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.active = False

    async def handle(self, method: str, params: dict):
        self.calls.append((method, params))
        if method == Method.ASR_START:
            self.active = str(params.get("source") or "") == "companion"
            return {"status": "listening"}
        if method == Method.ASR_STOP:
            self.active = False
            return {"status": "idle"}
        return None

    def state(self):
        return {"active": self.active, "source": "companion" if self.active else ""}


def test_companion_controls_start_voice_and_vision() -> None:
    async def run() -> None:
        asr = FakeAsr()
        handler = CompanionControlHandler()
        handler.configure(asr_control=asr.handle, asr_state=asr.state)
        with patch("server.handlers.companion_control_handler.bus.emit", new=AsyncMock()) as emit:
            result = await handler.handle(
                Method.COMPANION_INPUT_SET,
                {"voice": True, "vision_mode": "on_question"},
            )
        assert asr.calls[0][0] == Method.ASR_START
        assert asr.calls[0][1]["source"] == "companion"
        assert handler.vision_enabled is True
        assert result["inputs"]["voice"]["enabled"] is True
        assert result["inputs"]["vision"]["mode"] == "on_question"
        emit.assert_awaited_once()

    asyncio.run(run())


def test_companion_controls_stop_voice() -> None:
    async def run() -> None:
        asr = FakeAsr()
        handler = CompanionControlHandler()
        handler.configure(asr_control=asr.handle, asr_state=asr.state)
        with patch("server.handlers.companion_control_handler.bus.emit", new=AsyncMock()):
            await handler.handle(Method.COMPANION_INPUT_SET, {"voice": True})
            result = await handler.handle(Method.COMPANION_INPUT_SET, {"voice": False})
        assert asr.calls[-1][0] == Method.ASR_STOP
        assert result["inputs"]["voice"]["enabled"] is False

    asyncio.run(run())
