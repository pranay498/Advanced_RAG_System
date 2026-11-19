import json

from app.llm.base import LLMProvider, EmbeddingProvider
from app.aws import client
from app.config import settings


class BedrockProvider(LLMProvider, EmbeddingProvider):

    def __init__(self, default_model: str | None = None):
        self.default_model = default_model or settings().bedrock_chat_model or "amazon.nova-lite-v1:0"
        self.supported_models = [
            "amazon.nova-lite-v1:0",
            "amazon.nova-pro-v1:0",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "amazon.titan-text-express-v1"
        ]

    def chat(self,messages: list,system: str,model: str,max_tokens: int = 750,temperature: float = 0.1) -> str:

        formatted_messages = []

        for message in messages:
            formatted_messages.append({
                "role": message["role"],
                "content": [{"text": message["content"]}]
            })

        response = client("bedrock-runtime").converse(
            modelId=model,
            system=[{"text": system}],
            messages=formatted_messages,
            inferenceConfig={
                "maxTokens": max_tokens,
                "temperature": temperature
            }
        )

        return "".join(
            item.get("text", "")
            for item in response["output"]["message"]["content"]
        )

    def embed(self, text: str, model: str) -> list[float]:

        body = json.dumps({
            "inputText": text,
            "dimensions": settings().embed_dim,
            "normalize": True
        })

        response = client("bedrock-runtime").invoke_model(
            modelId=model,
            body=body
        )

        return json.loads(
            response["body"].read()
        )["embedding"]