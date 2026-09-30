"""Input controls for the standalone Companion card."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from server.event_bus import bus
from server.protocol import Method
from server.ws_handler import RequestHandler


AsyncCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any] | None]]


class CompanionControlHandler(RequestHandler):
    methods = [Method.COMPANION_STATUS, Method.COMPANION_INPUT_SET]

    def __init__(self) -> None:
        self._asr_control: AsyncCall | None = None
        self._asr_state: Callable[[], dict[str, Any]] | None = None
        self._vision_enabled = False

    def configure(self, *, asr_control: AsyncCall, asr_state: Callable[[], dict[str, Any]]) -> None:
        self._asr_control = asr_control
        self._asr_state = asr_state

    @property
    def vision_enabled(self) -> bool:
        return self._vision_enabled

    def status(self) -> dict[str, Any]:
        asr = self._asr_state() if self._asr_state else {}
        listening = bool(asr.get("active") and asr.get("source") == "companion")
        voice_available = self._asr_control is not None
        return {
            "status": "active",
            "inputs": {
                "session_id": "companion",
                "kind": "ask",
                "voice": {
                    "enabled": listening,
                    "listening": listening,
                    "starting": False,
                    "available": voice_available,
                    "error": "",
                    "reason": "ready" if voice_available else "asr_unavailable",
                },
                "vision": {
                    "mode": "on_question" if self._vision_enabled else "off",
                    "enabled": self._vision_enabled,
                    "available": True,
                    "error": "",
                    "reason": "ready",
                },
            },
        }

    async def handle(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if method == Method.COMPANION_STATUS:
            return self.status()
        if method == Method.COMPANION_INPUT_SET:
            return await self._set_inputs(params)
        return None

    async def _set_inputs(self, params: dict[str, Any]) -> dict[str, Any]:
        if self._asr_control is None:
            raise RuntimeError("ASR is unavailable")
        voice = params.get("voice")
        if voice is not None and not isinstance(voice, bool):
            raise ValueError("Companion voice input must be boolean")
        vision_mode = params.get("vision_mode")
        if vision_mode is not None and vision_mode not in {"off", "on_question"}:
            raise ValueError("Unsupported Companion vision mode")
        if vision_mode is not None:
            self._vision_enabled = vision_mode == "on_question"
        if voice is not None:
            if voice:
                result = await self._asr_control(Method.ASR_START, {
                    "source": "companion",
                    "one_shot": False,
                    "finish_after_turn_complete": False,
                    "source_payload": {"companion": True},
                })
                if str((result or {}).get("status")) not in {"listening", "awake", "starting", "already_listening"}:
                    raise RuntimeError(str((result or {}).get("error") or "Microphone could not start."))
            else:
                await self._asr_control(Method.ASR_STOP, {"source": "companion"})
        result = self.status()
        await bus.emit(Method.COMPANION_STATUS, result)
        return result
