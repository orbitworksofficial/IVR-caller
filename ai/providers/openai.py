import os
from typing import AsyncGenerator, Dict, Any, List
from openai import AsyncOpenAI
from ai.providers.base import LLMProvider

# Map production model names → Groq equivalents for dev
# Luna  → llama-3.1-8b-instant  (fast, cheap, simple queries)
# Terra → llama-3.3-70b-versatile (slower, smarter, complex queries + tool calls)
# This mirrors the Luna/Terra cost-vs-capability split in production.
# When real OpenAI keys are added, remove OPENAI_BASE_URL from .env and
# these mappings are bypassed automatically.
GROQ_MODEL_MAP = {
    "gpt-5.6-luna":  "llama-3.1-8b-instant",       # fast / low cost
    "gpt-5.6-terra": "llama-3.3-70b-versatile",     # capable / higher cost
}

class OpenAIProvider(LLMProvider):
    """OpenAI API implementation. Supports Groq as a dev drop-in via OPENAI_BASE_URL."""

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self._is_groq = "groq.com" in (self.base_url or "")

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    def _resolve_model(self, model: str) -> str:
        """Maps production model names to provider-compatible names."""
        if self._is_groq:
            return GROQ_MODEL_MAP.get(model, model)
        return model

    def _get_completion_params(self, model: str, temperature: float, max_tokens: int, **kwargs) -> Dict[str, Any]:
        """Prepares completion API parameters, mapping max_tokens to max_completion_tokens for newer models."""
        is_new_model = "gpt-5" in model or "o1" in model or "o3" in model
        
        params = {
            "model": model,
            **{k: v for k, v in kwargs.items() if k not in ["model", "temperature", "max_tokens", "max_completion_tokens"]}
        }
        
        # Newer models (gpt-5, o1, o3) do not support temperature parameters
        if is_new_model:
            pass
        else:
            params["temperature"] = temperature
            
        if is_new_model:
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
            
        return params

    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        model = self._resolve_model(kwargs.get("model", "gpt-5.6-luna"))
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        
        api_params = self._get_completion_params(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **{k: v for k, v in kwargs.items() if k not in ["model", "temperature", "max_tokens"]}
        )

        response = await self.client.chat.completions.create(**api_params)

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
        model = self._resolve_model(kwargs.get("model", "gpt-5.6-luna"))
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        
        api_params = self._get_completion_params(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            **{k: v for k, v in kwargs.items() if k not in ["model", "temperature", "max_tokens", "stream"]}
        )

        response_stream = await self.client.chat.completions.create(**api_params)

        async for chunk in response_stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def tool_call(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        model = self._resolve_model(kwargs.get("model", "gpt-5.6-luna"))
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", 1000)
        
        api_params = self._get_completion_params(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            tool_choice="auto",
            **{k: v for k, v in kwargs.items() if k not in ["model", "temperature", "max_tokens", "tools", "tool_choice"]}
        )

        response = await self.client.chat.completions.create(**api_params)

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


