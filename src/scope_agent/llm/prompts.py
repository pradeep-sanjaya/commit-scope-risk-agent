from jinja2 import Environment, PackageLoader, StrictUndefined

from scope_agent.llm.schemas import RISK_CATEGORIES

_env = Environment(
    loader=PackageLoader("scope_agent.llm", "prompts"),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
)


def render_prompt(name: str, **ctx) -> str:
    return _env.get_template(f"{name}.j2").render(categories=RISK_CATEGORIES, **ctx)
