"""Rule file loader — parses YAML frontmatter + markdown body."""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# rules/loader.py → /packages/code_review/rules/loader.py
#   parents[0] = rules/       parents[1] = code_review/  parents[2] = packages/
#   parents[3] = project root
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = _PROJECT_ROOT / "rules" / "default"


@dataclass
class Rule:
    name: str
    agent: str
    trigger: str = "always"
    globs: list[str] = field(default_factory=list)
    severity_default: str = "medium"
    body: str = ""

    def matches_file(self, filepath: str) -> bool:
        """Check if a file path matches any of this rule's glob patterns."""
        if not self.globs:
            return True
        from pathlib import PurePosixPath
        path = PurePosixPath(filepath)
        return any(path.match(pattern) for pattern in self.globs)


def parse_rule_file(path: Path) -> Rule:
    """Parse a single rule file with YAML frontmatter and markdown body."""
    text = path.read_text(encoding="utf-8")

    if not text.startswith("---"):
        return Rule(name=path.stem, agent=path.stem, body=text)

    parts = text.split("---", 2)
    if len(parts) < 3:
        return Rule(name=path.stem, agent=path.stem, body=text)

    frontmatter = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()

    return Rule(
        name=frontmatter.get("name", path.stem),
        agent=frontmatter.get("agent", path.stem),
        trigger=frontmatter.get("trigger", "always"),
        globs=frontmatter.get("globs", []),
        severity_default=frontmatter.get("severity_default", "medium"),
        body=body,
    )


_rules_cache: dict[str, Rule] | None = None
_rules_cache_dir: Path | None = None


def load_rules(rules_dir: Path | None = None) -> dict[str, Rule]:
    """Load all rule files from the given directory. Returns {agent_name: Rule}.

    Results are cached per rules directory — repeated calls within a process
    return the same dict without re-reading disk.
    """
    global _rules_cache, _rules_cache_dir
    directory = rules_dir or RULES_DIR

    # Return cached result if the same directory was already loaded.
    if _rules_cache is not None and _rules_cache_dir == directory:
        return _rules_cache

    rules: dict[str, Rule] = {}

    if not directory.is_dir():
        logger.warning("Rules directory not found: %s", directory)
        return rules

    for path in sorted(directory.glob("*.md")):
        rule = parse_rule_file(path)
        rules[rule.agent] = rule
        logger.debug("Loaded rule: %s (agent=%s)", rule.name, rule.agent)

    _rules_cache = rules
    _rules_cache_dir = directory
    return rules
