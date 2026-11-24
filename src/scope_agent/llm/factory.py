from datetime import date, datetime
from zoneinfo import ZoneInfo

from scope_agent.llm.anthropic_client import AnthropicClient
from scope_agent.llm.base import LLMClient
from scope_agent.llm.budget import BudgetGuard
from scope_agent.persistence.usage import TokenUsageMeter


def build_llm(settings, meter: TokenUsageMeter) -> LLMClient:
    if settings.llm_provider != "anthropic":
        raise ValueError(f"unsupported llm provider: {settings.llm_provider}")
    tz = ZoneInfo(settings.timezone)

    def today() -> date:
        return datetime.now(tz).date()

    client = AnthropicClient(
        api_key=settings.llm_api_key.get_secret_value(), model=settings.llm_model,
        max_tokens=settings.llm_max_output_tokens, base_url=settings.llm_base_url,
    )
    return BudgetGuard(client, meter, settings.llm_daily_token_budget, today)
