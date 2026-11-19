import httpx
from app.llm.base import LLMProvider
from app.config import settings


class GeminiProvider(LLMProvider):

    def __init__(self, api_key: str | None = None, default_model: str | None = None):
        self.api_key = api_key or settings().gemini_api_key
        self.default_model = default_model or settings().gemini_chat_model or "gemini-2.5-flash"
        self.supported_models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-1.5-flash",
            "gemini-1.5-pro"
        ]

    def chat(self, messages: list, system: str, model: str, max_tokens: int = 750, temperature: float = 0.1) -> str:
        api_key = self.api_key or settings().gemini_api_key
        if not api_key:
            raise ValueError("Gemini API key is not configured. Set GEMINI_API_KEY in .env")

        target_model = model or self.default_model
        # Strip any prefix like 'models/' if provided
        if target_model.startswith("models/"):
            target_model = target_model[len("models/"):]

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={api_key}"

        formatted_contents = []
        for msg in messages:
            role = "model" if msg["role"] == "assistant" else "user"
            formatted_contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        payload = {
            "contents": formatted_contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature
            }
        }

        if system:
            payload["system_instruction"] = {
                "parts": [{"text": system}]
            }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini API returned error {resp.status_code}: {resp.text}")

            data = resp.json()

        try:
            candidates = data.get("candidates", [])
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        except Exception as exc:
            raise RuntimeError(f"Failed to parse Gemini response: {exc}") from exc
