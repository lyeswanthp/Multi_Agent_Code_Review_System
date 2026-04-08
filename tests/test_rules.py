"""Tests for rule file loading."""

from pathlib import Path

from code_review.rules.loader import load_rules, parse_rule_file

RULES_DIR = Path(__file__).resolve().parent.parent / "rules" / "default"


class TestParseRuleFile:
    def test_parses_frontmatter(self):
        rule = parse_rule_file(RULES_DIR / "security.md")
        assert rule.agent == "security"
        assert rule.trigger == "always"
        assert "**/*.py" in rule.globs
        assert rule.severity_default == "high"

    def test_parses_body(self):
        rule = parse_rule_file(RULES_DIR / "logic.md")
        assert "Reasoning Template" in rule.body

    def test_git_history_trigger(self):
        rule = parse_rule_file(RULES_DIR / "git_history.md")
        assert rule.trigger == "overlap_only"


class TestLoadRules:
    def test_loads_all_defaults(self):
        rules = load_rules(RULES_DIR)
        assert len(rules) == 5
        assert set(rules.keys()) == {"syntax", "logic", "security", "git_history", "orchestrator"}

    def test_glob_matching(self):
        rules = load_rules(RULES_DIR)
        assert rules["syntax"].matches_file("src/app.py")
        assert rules["syntax"].matches_file("src/app.ts")
        assert rules["git_history"].matches_file("anything.xyz")
