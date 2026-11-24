from email.message import EmailMessage

from jinja2 import PackageLoader, StrictUndefined, select_autoescape, Environment

_env = Environment(
    loader=PackageLoader("scope_agent.notify", "templates"),
    autoescape=select_autoescape(["html.j2"], default=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


def build_message(subject: str, template: str, context: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg.set_content(_env.get_template(f"{template}.txt.j2").render(**context))
    msg.add_alternative(_env.get_template(f"{template}.html.j2").render(**context), subtype="html")
    return msg
