"""
Tests for template_loader.py — template scanning and parsing.
"""

import pytest
from pathlib import Path

from template_loader import scan_templates, TEMPLATE_CATEGORIES


class TestTemplateCategories:
    def test_has_expected_categories(self):
        expected = {"data-extraction", "form-filling", "monitoring", "login-session", "file-operations", "search-research", "integration"}
        assert set(TEMPLATE_CATEGORIES.keys()) == expected

    def test_category_has_label_and_icon(self):
        for cat_id, cat in TEMPLATE_CATEGORIES.items():
            assert "label" in cat
            assert "icon" in cat


class TestScanTemplates:
    def test_loads_yaml_files(self, tmp_path):
        tpl = tmp_path / "test.yaml"
        tpl.write_text("""
title: Test Template
description: A test
template:
  id: test_tpl
  category: monitoring
  tags: [test]
  difficulty: beginner
parameters:
  - key: url
    type: string
    description: Target URL
blocks:
  - block_type: navigation
    label: step1
    url: "{{ url }}"
""", encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert "test_tpl" in result
        assert result["test_tpl"]["title"] == "Test Template"
        assert result["test_tpl"]["category"] == "monitoring"
        assert len(result["test_tpl"]["parameters"]) == 1
        assert result["test_tpl"]["parameters"][0]["key"] == "url"

    def test_uses_stem_as_fallback_id(self, tmp_path):
        tpl = tmp_path / "my_template.yaml"
        tpl.write_text("title: No ID\nblocks: []", encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert "my_template" in result

    def test_skips_non_yaml(self, tmp_path):
        (tmp_path / "readme.txt").write_text("not yaml")
        (tmp_path / "valid.yaml").write_text("title: Valid\nblocks: []", encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert len(result) == 1

    def test_skips_malformed_yaml(self, tmp_path):
        (tmp_path / "bad.yaml").write_text("{{invalid yaml: [", encoding="utf-8")
        (tmp_path / "good.yaml").write_text("title: Good\nblocks: []", encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert len(result) == 1

    def test_empty_directory(self, tmp_path):
        result = scan_templates(str(tmp_path))
        assert result == {}

    def test_missing_directory_creates_it(self, tmp_path):
        new_dir = tmp_path / "nonexistent"
        result = scan_templates(str(new_dir))
        assert result == {}
        assert new_dir.exists()

    def test_yaml_source_preserved(self, tmp_path):
        content = "title: Source Test\nblocks: []"
        (tmp_path / "src.yaml").write_text(content, encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert "yaml_source" in result["src"]
        assert "Source Test" in result["src"]["yaml_source"]

    def test_subdirectory_scanning(self, tmp_path):
        sub = tmp_path / "monitoring"
        sub.mkdir()
        (sub / "price_check.yaml").write_text("""
title: Price Check
template:
  id: price_check
  category: monitoring
blocks: []
""", encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert "price_check" in result

    def test_yml_extension_supported(self, tmp_path):
        (tmp_path / "test.yml").write_text("title: YML\nblocks: []", encoding="utf-8")
        result = scan_templates(str(tmp_path))
        assert len(result) == 1
