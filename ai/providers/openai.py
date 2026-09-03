import os
import asyncio
from typing import AsyncGenerator, Dict, Any, List
import httpx
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

class _IPv4Transport(httpx.AsyncHTTPTransport):
    """
    Async transport that resolves hostnames to IPv4 only.

    Docker's default bridge network does not route IPv6, but its resolver
    happily returns AAAA records on a dual-stack host. Any connection that
    picks one fails with APIConnectionError — intermittently, depending on
    which record the resolver returns first, which makes it look like a
    flaky network rather than a configuration problem.
    """

    async def handle_async_request(self, request):
        import socket
        loop = asyncio.get_running_loop()
        original = loop.getaddrinfo

        async def ipv4_only(host, port, *args, **kwargs):
            kwargs["family"] = socket.AF_INET
            return await original(host, port, *args, **kwargs)

        loop.getaddrinfo = ipv4_only
        try:
            return await super().handle_async_request(request)
        finally:
            loop.getaddrinfo = original


class OpenAIProvider(LLMProvider):
    """OpenAI API implementation. Supports Groq as a dev drop-in via OPENAI_BASE_URL."""

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

        # An EMPTY OPENAI_BASE_URL must be treated as "unset". Docker Compose
        # writes ${OPENAI_BASE_URL:-} as an empty string rather than omitting
        # the variable, and AsyncOpenAI(base_url="") builds requests against an
        # empty base — every call then dies with APIConnectionError even though
        # the network is fine.
        raw_base = base_url if base_url is not None else os.getenv("OPENAI_BASE_URL")
        self.base_url = raw_base.strip() if raw_base and raw_base.strip() else None

        # Passing base_url=None is NOT sufficient: the SDK falls back to reading
        # OPENAI_BASE_URL itself, and an empty value there still produces an
        # empty base. Clear it so the SDK uses its own default endpoint.
        if self.base_url is None and os.environ.get("OPENAI_BASE_URL", "").strip() == "":
            os.environ.pop("OPENAI_BASE_URL", None)

        self._is_groq = "groq.com" in (self.base_url or "")

        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            http_client=self._build_http_client(),
            max_retries=3,
        )

    @staticmethod
    def _build_http_client() -> httpx.AsyncClient:
        """
        Builds the HTTP client used for every API call.

        On a dual-stack host whose IPv6 is not routable — which is the default
        for Docker's bridge network — DNS can return IPv6-only records for
        api.openai.com and every request dies with APIConnectionError. Setting
        local_address to 0.0.0.0 binds the socket to IPv4, so resolution and
        connection stay on a route that works.

        Set OPENAI_ALLOW_IPV6=true to opt out on a host with working IPv6.
        """
        timeout = httpx.Timeout(60.0, connect=15.0)
        limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)

        if os.getenv("OPENAI_ALLOW_IPV6", "").lower() in ("1", "true", "yes"):
            return httpx.AsyncClient(timeout=timeout, limits=limits)

        # local_address alone is not enough: httpx still resolves the host
        # normally and may pick an AAAA record, then fail to route to it.
        # Patching getaddrinfo to request AF_INET makes IPv4 selection
        # deterministic for every connection this client opens.
        transport = _IPv4Transport(local_address="0.0.0.0", retries=2)
        return httpx.AsyncClient(timeout=timeout, limits=limits, transport=transport)

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
            # Set reasoning_effort to 'none' if we are making tool/function calls on reasoning models,
            # as required by OpenAI /v1/chat/completions endpoint.
            if "tools" in kwargs:
                params["reasoning_effort"] = "none"
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


