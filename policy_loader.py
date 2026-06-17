from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


# Filename (inside the policies folder) mapping a policy heading to its
# SharePoint page URL, e.g. {"Policy 2: Leave Policy": "https://...".}
LINKS_FILENAME = "policy_links.json"

# Fallback HR contacts if the policy frontmatter doesn't carry them.
DEFAULT_CONTACTS = {"hr": "HR@qubiqon.io", "posh": "posh@qubiqon.io"}


@dataclass
class PolicySection:
    file: str
    heading: str
    parent_heading: str
    content: str
    url: str = ""

    @property
    def title(self) -> str:
        if self.parent_heading and self.parent_heading != self.heading:
            return f"{self.parent_heading} > {self.heading}"
        return self.heading

    @property
    def full_text(self) -> str:
        return f"## {self.title}\n{self.content}".strip()


def _load_links(folder: Path) -> dict[str, str]:
    path = folder / LINKS_FILENAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    # Keep only entries that actually have a URL filled in.
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}


def load_contacts(folder: Path) -> dict[str, str]:
    """Read hr_contact / posh_contact from the first policy file's YAML
    frontmatter, falling back to DEFAULT_CONTACTS. Used so the assistant can
    hand off to a real, clickable email when an answer isn't in the docs."""
    contacts = dict(DEFAULT_CONTACTS)
    if not folder.exists():
        return contacts
    for md_path in sorted(folder.glob("*.md")):
        try:
            lines = md_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines or lines[0].strip() != "---":
            continue
        for line in lines[1:]:
            if line.strip() == "---":
                break
            m = re.match(r"^([A-Za-z_]+)\s*:\s*(.+?)\s*$", line)
            if not m:
                continue
            key, value = m.group(1).lower(), m.group(2).strip()
            if key == "hr_contact":
                contacts["hr"] = value
            elif key == "posh_contact":
                contacts["posh"] = value
        break  # only the first file carries the handbook frontmatter
    return contacts


def load_policies(folder: Path) -> list[PolicySection]:
    sections: list[PolicySection] = []
    if not folder.exists():
        return sections
    links = _load_links(folder)
    for md_path in sorted(folder.glob("*.md")):
        sections.extend(_split_file(md_path))
    # Attach the SharePoint URL: a subsection inherits its parent policy's link.
    for s in sections:
        s.url = links.get(s.heading) or links.get(s.parent_heading) or ""
    return sections


def _split_file(path: Path) -> list[PolicySection]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break

    sections: list[PolicySection] = []
    current_h1 = ""
    current_h2 = ""
    current_h3 = ""
    buffer: list[str] = []

    def flush() -> None:
        content = "\n".join(buffer).strip()
        if not content:
            return
        if current_h3:
            heading = current_h3
            parent = current_h2
        elif current_h2:
            heading = current_h2
            parent = ""
        elif current_h1:
            heading = current_h1
            parent = ""
        else:
            return
        sections.append(
            PolicySection(
                file=path.name,
                heading=heading,
                parent_heading=parent,
                content=content,
            )
        )

    for line in lines:
        h1 = re.match(r"^#\s+(.+)$", line)
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h1:
            flush()
            current_h1 = h1.group(1).strip()
            current_h2 = ""
            current_h3 = ""
            buffer = []
        elif h2:
            flush()
            current_h2 = h2.group(1).strip()
            current_h3 = ""
            buffer = []
        elif h3:
            flush()
            current_h3 = h3.group(1).strip()
            buffer = []
        else:
            buffer.append(line)
    flush()

    if not sections:
        sections.append(
            PolicySection(
                file=path.name,
                heading=path.stem.replace("_", " ").title(),
                parent_heading="",
                content=text.strip(),
            )
        )
    return sections
