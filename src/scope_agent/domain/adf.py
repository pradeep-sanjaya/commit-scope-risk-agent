import re


def _inline(node: dict) -> str:
    kind = node.get("type")
    if kind == "text":
        return node.get("text", "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "status"):
        return node.get("attrs", {}).get("text", "")
    if kind == "inlineCard":
        return node.get("attrs", {}).get("url", "")
    return "".join(_inline(child) for child in node.get("content", []))


def _blocks(node: dict, prefix: str = "") -> list[str]:
    kind = node.get("type")
    children = node.get("content", [])
    if kind == "heading":
        level = node.get("attrs", {}).get("level", 2)
        return [f"{'#' * level} {_inline(node)}"]
    if kind == "listItem":
        lines: list[str] = []
        for child in children:
            lines.extend(_blocks(child))
        if not lines:
            return []
        return [f"- {lines[0]}", *[f"  {line}" for line in lines[1:]]]
    if kind in ("bulletList", "orderedList", "doc", "blockquote", "panel", "table", "tableRow",
                "tableCell", "tableHeader", "expand"):
        lines = []
        for child in children:
            lines.extend(_blocks(child))
        return lines
    text = _inline(node)  # paragraph, codeBlock and anything else with inline content
    return [text] if text else []


def adf_to_text(doc: dict | None) -> str:
    if not doc:
        return ""
    return "\n".join(_blocks(doc))


def extract_ac(text: str, heading: str) -> str | None:
    label = re.compile(rf"^#*\s*{re.escape(heading)}\s*:?$", re.IGNORECASE)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if label.match(line.strip()):
            body: list[str] = []
            for following in lines[i + 1:]:
                if following.lstrip().startswith("#"):
                    break
                body.append(following)
            return "\n".join(body).strip() or None
    return None
