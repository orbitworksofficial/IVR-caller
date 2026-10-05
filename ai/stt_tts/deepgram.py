import asyncio
import logging
import os
from typing import AsyncGenerator, Tuple

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)

logger = logging.getLogger(__name__)

class DeepgramSTT:
    """
    Async streaming Speech-to-Text adapter using Deepgram Nova 2.
    Optimized for 8kHz µ-law telephony audio.
    """
    def __init__(self):
        self.api_key = os.getenv("DEEPGRAM_API_KEY")
        if not self.api_key:
            logger.warning("DEEPGRAM_API_KEY environment variable is not set.")
            
        self.model = os.getenv("DEEPGRAM_MODEL", "nova-2-phonecall")
        
        # Initialize Deepgram client
        self.deepgram = DeepgramClient(self.api_key)

    async def stream(self, audio_generator: AsyncGenerator[bytes, None]) -> AsyncGenerator[Tuple[str, bool], None]:
        """
        Connects to Deepgram WebSocket and yields (transcript, is_final) pairs.
        Re-establishes connection with exponential backoff on failure.
        """
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                # We use a queue to yield items from the Deepgram event handlers
                result_queue = asyncio.Queue()
                
                # Setup Live connection
                dg_connection = self.deepgram.listen.asyncwebsocket.v("1")
                
                async def on_message(self_param, result, **kwargs):
                    sentence = result.channel.alternatives[0].transcript
                    if len(sentence) == 0:
                        return
                    is_final = result.is_final
                    await result_queue.put((sentence, is_final))
                
                async def on_error(self_param, error, **kwargs):
                    logger.error(f"Deepgram STT Error: {error}")
                    await result_queue.put(Exception(f"Deepgram STT Error: {error}"))
                    
                async def on_close(self_param, close, **kwargs):
                    logger.info("Deepgram connection closed.")
                    await result_queue.put(None)

                dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
                dg_connection.on(LiveTranscriptionEvents.Error, on_error)
                dg_connection.on(LiveTranscriptionEvents.Close, on_close)
                
                options = LiveOptions(
                    model=self.model,
                    language="en",
                    encoding="mulaw",
                    sample_rate=8000,
                    channels=1,
                    interim_results=True,
                    endpointing=300,
                    utterance_end_ms="1000",
                    vad_events=True
                )
                
                if not await dg_connection.start(options):
                    raise Exception("Failed to start Deepgram connection")

                logger.info("Deepgram Live STT started successfully.")
                
                # Task to send audio from generator
                async def send_audio():
                    try:
                        async for chunk in audio_generator:
                            await dg_connection.send(chunk)
                        await dg_connection.finish()
                    except Exception as e:
                        logger.error(f"Error sending audio: {e}")
                        
                send_task = asyncio.create_task(send_audio())
                
                while True:
                    item = await result_queue.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item
                    yield item
                
                await send_task
                return # Clean exit

            except Exception as e:
                logger.error(f"Deepgram streaming error (attempt {attempt+1}/{max_retries+1}): {e}")
                if attempt == max_retries:
                    raise e
                await asyncio.sleep(retry_delay)
                retry_delay *= 2

    async def transcribe_audio_chunk(self, chunk: bytes) -> str:
        """
        One-shot chunk transcription (not recommended for streaming telephony but useful for one-offs)
        """
        try:
            payload = {"buffer": chunk}
            options = {
                "model": self.model,
                "encoding": "mulaw",
                "sample_rate": 8000,
            }
            response = await self.deepgram.listen.asyncrest.v("1").transcribe_file(payload, options)
            return response.results.channels[0].alternatives[0].transcript
        except Exception as e:
            logger.error(f"Error transcribing chunk: {e}")
            return ""
