import asyncio
import logging
import os
import aiohttp
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

class ElevenLabsTTS:
    """
    Async streaming Text-to-Speech adapter using ElevenLabs Flash/Turbo.
    """
    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            logger.warning("ELEVENLABS_API_KEY environment variable is not set.")
            
        self.default_voice_id = os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
        self.model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
        
        self.base_url = "https://api.elevenlabs.io/v1/text-to-speech"
        self._cancel_event = asyncio.Event()

    def cancel(self):
        """Cancel ongoing stream."""
        self._cancel_event.set()
        
    def _reset_cancel(self):
        self._cancel_event.clear()

    async def stream_text(self, text: str, voice_id: str = None) -> AsyncGenerator[bytes, None]:
        """
        Stream text as audio chunks.
        """
        self._reset_cancel()
        voice_id = voice_id or self.default_voice_id
        url = f"{self.base_url}/{voice_id}/stream?output_format=pcm_8000"
        
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg" # Although pcm_8000 output is specified in url
        }
        
        data = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.0,
                "use_speaker_boost": True
            }
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    err_msg = await response.text()
                    logger.error(f"ElevenLabs TTS Error: {response.status} {err_msg}")
                    return
                
                async for chunk in response.content.iter_chunked(1024):
                    if self._cancel_event.is_set():
                        logger.info("ElevenLabs stream cancelled.")
                        break
                    yield chunk

    async def stream_sentences(self, text_generator: AsyncGenerator[str, None], voice_id: str = None) -> AsyncGenerator[bytes, None]:
        """
        Consumes LLM token stream, splits at sentence boundaries, streams TTS chunk-by-chunk.
        """
        self._reset_cancel()
        voice_id = voice_id or self.default_voice_id
        
        sentence_buffer = ""
        delimiters = [".", "!", "?"]
        
        async for token in text_generator:
            if self._cancel_event.is_set():
                break
                
            sentence_buffer += token
            
            # Very basic sentence splitting
            if any(d in token for d in delimiters):
                # We have a sentence, stream it
                sentence = sentence_buffer.strip()
                sentence_buffer = ""
                
                if sentence:
                    async for chunk in self.stream_text(sentence, voice_id):
                        if self._cancel_event.is_set():
                            break
                        yield chunk
                        
        # Flush remainder
        if sentence_buffer.strip() and not self._cancel_event.is_set():
            async for chunk in self.stream_text(sentence_buffer.strip(), voice_id):
                yield chunk

    async def synthesize(self, text: str, voice_id: str = None) -> bytes:
        """
        One-shot synthesis.
        """
        voice_id = voice_id or self.default_voice_id
        url = f"{self.base_url}/{voice_id}?output_format=pcm_8000"
        
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json"
        }
        
        data = {
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.0
            }
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    err_msg = await response.text()
                    logger.error(f"ElevenLabs synthesis error: {response.status} {err_msg}")
                    return b""
                return await response.read()
