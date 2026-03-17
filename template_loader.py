"""
Template marketplace loader.
Scans templates/ directory at startup, parses YAML files with template metadata.
"""

from __future__ import annotations
from pathlib import Path

import yaml

TEMPLATE_CATEGORIES = {
    "data-extraction": {"label": "数据提取", "icon": "database"},
    "form-filling":    {"label": "表单填写", "icon": "edit"},
    "monitoring":      {"label": "监控检测", "icon": "eye"},
    "login-session":   {"label": "登录会话", "icon": "lock"},
    "file-operations": {"label": "文件操作", "icon": "file"},
    "search-research": {"label": "搜索研究", "icon": "search"},
    "integration":     {"label": "集成通知", "icon": "webhook"},
}


def scan_templates(directory: str = "templates") -> dict[str, dict]:
    tpl_dir = Path(directory)
    if not tpl_dir.exists():
        tpl_dir.mkdir(exist_ok=True)
        return {}

    result = {}
    for yaml_file in sorted(tpl_dir.glob("**/*.y*ml")):
        if yaml_file.suffix not in (".yaml", ".yml"):
            continue
        try:
            yaml_str = yaml_file.read_text(encoding="utf-8")
            raw = yaml.safe_load(yaml_str)
        except Exception as e:
            print(f"[template_loader] 解析 {yaml_file} 失败: {e}")
            continue

        if not isinstance(raw, dict):
            continue

        meta = raw.get("template", {})
        tpl_id = meta.get("id", yaml_file.stem)

        params = []
        for p in raw.get("parameters", []):
            params.append({
                "key": p.get("key", ""),
                "type": p.get("type", "string"),
                "description": p.get("description", ""),
                "default": p.get("default"),
                "required": p.get("required", False),
            })

        result[tpl_id] = {
            "id": tpl_id,
            "title": raw.get("title", tpl_id),
            "description": raw.get("description", ""),
            "category": meta.get("category", ""),
            "tags": meta.get("tags", []),
            "icon": meta.get("icon", ""),
            "difficulty": meta.get("difficulty", "beginner"),
            "estimated_time": meta.get("estimated_time", ""),
            "parameters": params,
            "blocks": raw.get("blocks", []),
            "yaml_source": yaml_str,
        }
        print(f"[template_loader] 加载模板: {result[tpl_id]['title']} ({tpl_id})")

    return result
