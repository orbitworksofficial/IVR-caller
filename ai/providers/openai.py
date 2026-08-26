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
        model = kwargs.get("model", "text-embedding-3-small")
        
        # If using Groq (which doesn't host embeddings), generate a deterministic mock vector to prevent dev crashes
        if self.base_url and "groq.com" in self.base_url:
            import hashlib
            import random
            hash_val = int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)
            random.seed(hash_val)
            vector = [random.uniform(-1, 1) for _ in range(1536)]
            norm = sum(x**2 for x in vector)**0.5
            return [x / norm for x in vector]

        response = await self.client.embeddings.create(
            model=model,
            input=text,
            **{k: v for k, v in kwargs.items() if k not in ["model"]}
        )
        return response.data[0].embedding

