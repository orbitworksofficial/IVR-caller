from abc import ABC, abstractmethod
from typing import AsyncGenerator, Dict, Any, List

class LLMProvider(ABC):
    """Abstract base class for LLM Provider implementations (e.g. OpenAI, Anthropic)."""

    @abstractmethod
    async def generate(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """
        Send a completion request to the provider.

        Args:
            messages: List of message dictionaries containing 'role' and 'content'.
            **kwargs: Additional parameters (e.g. temperature, max_tokens, response_format).

        Returns:
            A dictionary containing the response text and usage/cost metadata.
        """
        pass

    @abstractmethod
    async def stream(self, messages: List[Dict[str, str]], **kwargs) -> AsyncGenerator[str, None]:
        """
        Stream responses using Server-Sent Events (SSE) / AsyncGenerator.

        Args:
            messages: List of message dictionaries.
            **kwargs: Additional parameters.

        Yields:
            Chunks of text as they arrive from the model.
        """
        pass

    @abstractmethod
    async def tool_call(self, messages: List[Dict[str, str]], tools: List[Dict[str, Any]], **kwargs) -> Dict[str, Any]:
        """
        Send a request requiring tool call / structured output resolution.

        Args:
            messages: List of message dictionaries.
            tools: List of tool schemas.
            **kwargs: Additional parameters.

        Returns:
            A dictionary with the resolved tool call arguments or content.
        """
        pass

    @abstractmethod
    async def embed(self, text: str, **kwargs) -> List[float]:
        """
        Generate embedding vector for a given text.

        Args:
            text: Input string.
            **kwargs: Additional parameters.

        Returns:
            List of floats representing the embedding vector.
        """
        pass

