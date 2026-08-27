import os
from typing import AsyncGenerator, Dict, Any, List
from openai import AsyncOpenAI
from ai.providers.base import LLMProvider

class OpenAIProvider(LLMProvider):
    """OpenAI API implementation for GPT-5.6 Luna / Terra models."""

    def __init__(self, api_key: str = None, base_url: str = None):
        # Fall back to environment variable if not provided
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        model = kwargs.get("model", "gpt-5.6-luna")
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)

        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **{k: v for k, v in kwargs.items() if k not in ["model", "temperature", "max_tokens"]}
        )

        return {
            "text": response.choices[0].message.content,
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            },
            "model": model
        }

    async def stream(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        model = kwargs.get("model", "gpt-5.6-luna")
        temperature = kwargs.get("temperature", 0.7)

        response_stream = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
            **{k: v for k, v in kwargs.items() if k not in ["model", "temperature", "stream"]}
        )

        async for chunk in response_stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def tool_call(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        model = kwargs.get("model", "gpt-5.6-luna")
        
        response = await self.client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            **{k: v for k, v in kwargs.items() if k not in ["model", "tools", "tool_choice"]}
        )

        choice = response.choices[0]
        return {
            "message": choice.message,
            "tool_calls": choice.message.tool_calls or [],
            "usage": {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }
        }

    async def embed(self, text: str, **kwargs) -> List[float]:
        """
        Generates an embedding vector for the given text.
        - Production: uses text-embedding-3-small via OpenAI API.
        - Development: set EMBEDDING_PROVIDER=local in .env to use a free
          local sentence-transformers model (no API key needed).
        """
        if os.getenv("EMBEDDING_PROVIDER") == "local":
            return await self._embed_local(text)

        model = kwargs.get("model", "text-embedding-3-small")
        response = await self.client.embeddings.create(
            model=model,
            input=text,
            **{k: v for k, v in kwargs.items() if k not in ["model"]}
        )
        return response.data[0].embedding

    async def _embed_local(self, text: str) -> List[float]:
        """
        Local embedding using sentence-transformers (all-MiniLM-L6-v2).
        Produces 384-dimensional vectors — sufficient for dev/testing.
        Install with: pip install sentence-transformers
        Note: Production uses text-embedding-3-small (1536-dim) via OpenAI.
        """
        import asyncio
        from sentence_transformers import SentenceTransformer

        # Cache the model so it only loads once per process
        if not hasattr(self, "_local_model"):
            self._local_model = SentenceTransformer("all-MiniLM-L6-v2")

        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None, lambda: self._local_model.encode(text).tolist()
        )
        return embedding


