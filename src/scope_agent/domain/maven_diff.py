"""Diff dependencies between two pom.xml contents (pure: strings in, models out)."""

import re
import xml.etree.ElementTree as ET

from scope_agent.domain.models import DependencyChange
from scope_agent.domain.versions import change_kind

_PROP = re.compile(r"\$\{([^}]+)\}")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: ET.Element | None, name: str) -> ET.Element | None:
    for c in el if el is not None else []:
        if _local(c.tag) == name:
            return c
    return None


def _text(el: ET.Element | None, name: str) -> str | None:
    c = _child(el, name)
    return c.text.strip() if c is not None and c.text and c.text.strip() else None


def _children(el: ET.Element | None, name: str) -> list[ET.Element]:
    return [c for c in el if _local(c.tag) == name] if el is not None else []


class _Pom:
    def __init__(self, xml: str | None):
        self.deps: dict[str, str | None] = {}
        self.parent: tuple[str, str | None] | None = None
        self.boms: dict[str, str | None] = {}
        if not xml or not xml.strip():
            return
        root = ET.fromstring(xml)
        props: dict[str, str] = {}
        properties = _child(root, "properties")
        for p in properties if properties is not None else []:
            if p.text:
                props[_local(p.tag)] = p.text.strip()
        parent = _child(root, "parent")
        parent_version = _text(parent, "version") if parent is not None else None
        own_version = _text(root, "version") or parent_version
        if own_version:
            props["project.version"] = own_version
            props["version"] = own_version
        if parent_version:
            props["project.parent.version"] = parent_version
        self._props = props

        if parent is not None:
            self.parent = (self._name(parent), self._resolve(parent_version))
        for d in _children(_child(_child(root, "dependencyManagement"), "dependencies"), "dependency"):
            if _text(d, "type") == "pom" and _text(d, "scope") == "import":
                self.boms[self._name(d)] = self._resolve(_text(d, "version"))
        for d in _children(_child(root, "dependencies"), "dependency"):
            self.deps[self._name(d)] = self._resolve(_text(d, "version"))

    def _resolve(self, value: str | None) -> str | None:
        if value is None:
            return None
        for _ in range(5):  # bounded: tolerates property cycles
            new = _PROP.sub(lambda m: self._props.get(m.group(1), m.group(0)), value)
            if new == value:
                break
            value = new
        return value

    def _name(self, d: ET.Element) -> str:
        return f"{self._resolve(_text(d, 'groupId')) or ''}:{self._resolve(_text(d, 'artifactId')) or ''}"


def maven_changes(path: str, before: str | None, after: str | None) -> list[DependencyChange]:
    try:
        old, new = _Pom(before), _Pom(after)
    except ET.ParseError:
        return []

    out: list[DependencyChange] = []

    def add(name: str, o: str | None, n: str | None, kind: str) -> None:
        out.append(DependencyChange(ecosystem="maven", name=name, old_version=o, new_version=n, kind=kind, file=path))

    # parent
    op, np_ = old.parent, new.parent
    if op != np_:
        name = (np_ or op)[0]  # type: ignore[index]
        add(name, op[1] if op else None, np_[1] if np_ else None, "parent")

    # boms
    for name in sorted(old.boms.keys() | new.boms.keys()):
        o, n = old.boms.get(name), new.boms.get(name)
        if (name in old.boms) != (name in new.boms) or o != n:
            add(name, o, n, "bom")

    # direct dependencies
    for name in sorted(old.deps.keys() | new.deps.keys()):
        if name not in new.deps:
            add(name, old.deps[name], None, "removed")
        elif name not in old.deps:
            add(name, None, new.deps[name], "added")
        else:
            o, n = old.deps[name], new.deps[name]
            if o is None or n is None:
                continue  # version managed elsewhere
            kind = change_kind(o, n)
            if kind:
                add(name, o, n, kind)
    return out
