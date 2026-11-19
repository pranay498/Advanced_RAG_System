from abc import ABC, abstractmethod


class LLMProvider(ABC):

    @abstractmethod
    def chat(self, messages: list,system: str,model: str, max_tokens: int = 750, temperature: float = 0.1) -> str:
        pass


class EmbeddingProvider(ABC):

    @abstractmethod
    def embed(self,text: str,model: str) -> list[float]:
        pass