import json
import re
from typing import Any
from pydantic import BaseModel, Field
from app.config import settings
from app.observability import trace


class RoutingDecision(BaseModel):
     provider: str = Field(description="Selected LLM provider name")
     model: str = Field(description="Selected model identifier")
     reason: str = Field(description="Brief justification for the routing decision")


ROUTING_SYSTEM_PROMPT = """You are Atlas Router, an intelligent LLM query routing classifier.
Your ONLY role is to analyze an incoming user query, context requirements, and evidence, then select the optimal LLM provider and model to generate the final answer.

DO NOT answer the user's question.
You must output ONLY a valid JSON object matching this schema:
{
  "provider": "<provider_name>",
  "model": "<model_name>",
  "reason": "<brief 1-sentence reason>"
}

Selection Criteria:
1. Query complexity:
   - Simple, factual questions, direct lookups, short definitions -> Fast, low-latency, cost-effective models.
   - Multi-step reasoning, cross-document synthesis, policy reconciliation, edge-case analysis -> High-capability reasoning models.
2. Context requirements:
   - Large or dense evidence passages -> Models with strong long-context comprehension.
3. Speed and cost:
   - Default to efficient models for routine tasks; reserve high-tier models for challenging queries.

Available Providers and Models:
{available_providers_spec}
"""


class LLMRouter:

    def __init__(self, default_provider: str = "bedrock", default_model: str = ""):
        self.default_provider = default_provider
        self.default_model = default_model

    def _build_provider_spec(self, providers: dict[str, Any]) -> str:
        lines = []
        for name, p in providers.items():
            models = getattr(p, "supported_models", [])
            default = getattr(p, "default_model", "default")
            model_list = ", ".join(models) if models else default
            lines.append(f"- Provider '{name}': default model '{default}', available models: [{model_list}]")
        return "\n".join(lines) if lines else "- Provider 'bedrock': amazon.nova-lite-v1:0"


    def _parse_decision(self, raw_output: str) -> dict:
        text = raw_output.strip()
        # Strip markdown code blocks if present
        if "```" in text:
            match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            if match:
                text = match.group(1).strip()

        # Find first JSON object
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start != -1 and brace_end != -1:
            text = text[brace_start:brace_end + 1]

        return json.loads(text)



    def validate_decision(self, raw_decision: dict, registered_providers: dict[str, Any]) -> RoutingDecision:

        provider = str(raw_decision.get("provider", "")).strip().lower()
        model = str(raw_decision.get("model", "")).strip()
        reason = str(raw_decision.get("reason", "Intelligent LLM routing selection.")).strip()

        # 1. Validate provider against registered providers
        if provider not in registered_providers:
            if self.default_provider in registered_providers:
                fallback_provider = self.default_provider
            else:
                fallback_provider = next(iter(registered_providers))

            p_obj = registered_providers[fallback_provider]
            fallback_model = getattr(p_obj, "default_model", settings().bedrock_chat_model)
            return RoutingDecision(
                provider=fallback_provider,
                model=fallback_model,
                reason=f"Selected provider '{provider}' is not registered; routed to default '{fallback_provider}'."
            )

        provider_obj = registered_providers[provider]
        supported = getattr(provider_obj, "supported_models", [])
        provider_default = getattr(provider_obj, "default_model", "")

        # 2. Validate model
        if not model:
            model = provider_default or settings().bedrock_chat_model
        elif supported and model not in supported:
            # If the model isn't in supported list, fall back to provider default
            model = provider_default or supported[0]
            reason = f"{reason} (Model adjusted to supported default '{model}')"

        return RoutingDecision(provider=provider, model=model, reason=reason)




    @trace("route_query")
    def route(self,query: str, evidence: str,registered_providers: dict[str, Any],routing_executor=None,history: list = None ) -> RoutingDecision:
        """
        Invokes the dedicated Routing LLM to inspect the query and choose a provider and model.
        Falls back safely if routing execution fails.
        """
        if not registered_providers:
            return RoutingDecision(
                provider=self.default_provider,
                model=self.default_model or settings().bedrock_chat_model,
                reason="No providers registered; using system default."
            )

        spec = self._build_provider_spec(registered_providers)
        system_prompt = ROUTING_SYSTEM_PROMPT.format(available_providers_spec=spec)

        evidence_snippet = (evidence[:800] + "...") if len(evidence) > 800 else evidence
        routing_prompt = (
            f"USER QUERY: {query}\n\n"
            f"EVIDENCE SUMMARY LENGTH: {len(evidence)} characters\n"
            f"EVIDENCE PREVIEW:\n{evidence_snippet}\n\n"
            "Analyze complexity, reasoning, and context. Output the JSON routing decision:"
        )

        try:
            if routing_executor is None:
                # Default routing executor: use default provider directly for the lightweight routing decision
                routing_provider_name = settings().routing_provider or self.default_provider
                if routing_provider_name not in registered_providers:
                    routing_provider_name = next(iter(registered_providers))

                provider_instance = registered_providers[routing_provider_name]
                routing_model = settings().routing_model or getattr(provider_instance, "default_model", settings().bedrock_chat_model)

                raw_output = provider_instance.chat(
                    messages=[{"role": "user", "content": routing_prompt}],
                    system=system_prompt,
                    model=routing_model,
                    max_tokens=200,
                    temperature=0.0
                )
            else:
                raw_output = routing_executor(system_prompt, routing_prompt)

            raw_dict = self._parse_decision(raw_output)
            return self.validate_decision(raw_dict, registered_providers)

        except Exception as exc:
            # Safe fallback if router execution or parsing fails
            fallback_provider = self.default_provider if self.default_provider in registered_providers else next(iter(registered_providers))
            p_obj = registered_providers[fallback_provider]
            fallback_model = getattr(p_obj, "default_model", settings().bedrock_chat_model)
            return RoutingDecision(
                provider=fallback_provider,
                model=fallback_model,
                reason=f"Routing evaluation encountered an error ({type(exc).__name__}); using reliable default."
            )
