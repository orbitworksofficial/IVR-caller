# ai/stt_tts/__init__.py
# Voice layer — streaming STT/TTS adapters and turn manager.
# SDK packages (deepgram-sdk, elevenlabs) are only required at runtime when the
# voice gateway is running. Import guards prevent startup failures if the packages
# are not yet installed in a chat-only environment.

try:
    from .deepgram import DeepgramSTT
except ImportError:
    DeepgramSTT = None  # type: ignore

try:
    from .elevenlabs import ElevenLabsTTS
except ImportError:
    ElevenLabsTTS = None  # type: ignore

from .turn_manager import TurnManager

__all__ = ["DeepgramSTT", "ElevenLabsTTS", "TurnManager"]
