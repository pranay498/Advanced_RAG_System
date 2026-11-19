import httpx
from app.llm.base import LLMProvider
from app.config import settings


class GrokProvider(LLMProvider):

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        self.api_key = api_key or settings().grok_api_key
        self.default_model = default_model or settings().grok_chat_model or "grok-2-latest"
        self.supported_models = [
            "grok-2-latest",
            "grok-2",
            "grok-beta"
        ]

    def chat(self, messages: list, system: str, model: str, max_tokens: int = 750, temperature: float = 0.1) -> str:
        api_key = self.api_key or settings().grok_api_key
        if not api_key:
            raise ValueError("Grok (xAI) API key is not configured. Set GROK_API_KEY in .env")

        target_model = model or self.default_model

        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        formatted_messages = []
        if system:
            formatted_messages.append({"role": "system", "content": system})

        for msg in messages:
            formatted_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        payload = {
            "model": target_model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Grok API returned error {resp.status_code}: {resp.text}")

            data = resp.json()

        try:
            choices = data.get("choices", [])
            if not choices:
                return ""
            return choices[0].get("message", {}).get("content", "")
        except Exception as exc:
            raise RuntimeError(f"Failed to parse Grok response: {exc}") from exc
