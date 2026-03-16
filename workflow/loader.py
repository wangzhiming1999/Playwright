"""
启动时扫描 workflows/ 目录，加载 YAML 工作流文件到数据库。
"""

from __future__ import annotations
import hashlib
import uuid
from pathlib import Path

from .parser import parse_workflow
from .db import save_workflow, load_all_workflows


def scan_workflow_directory(directory: str = "workflows") -> list[dict]:
    """
    扫描目录下的 .yaml/.yml 文件，解析并存入数据库。
    返回新加载/更新的工作流列表。
    """
    wf_dir = Path(directory)
    if not wf_dir.exists():
        wf_dir.mkdir(exist_ok=True)
        return []

    # 加载已有的文件来源工作流，用 source_path 做去重
    existing = load_all_workflows()
    path_to_id = {}
    path_to_hash = {}
    for w in existing.values():
        if w.get("source_type") == "file" and w.get("source_path"):
            path_to_id[w["source_path"]] = w["id"]
            path_to_hash[w["source_path"]] = hashlib.md5(
                w.get("yaml_source", "").encode()
            ).hexdigest()

    loaded = []
    for yaml_file in sorted(wf_dir.glob("**/*.y*ml")):
        if yaml_file.suffix not in (".yaml", ".yml"):
            continue
        # 跳过 _examples 目录
        if "_examples" in yaml_file.parts:
            continue

        try:
            yaml_str = yaml_file.read_text(encoding="utf-8")
        except Exception:
            continue

        file_key = str(yaml_file)
        content_hash = hashlib.md5(yaml_str.encode()).hexdigest()

        # 如果内容没变，跳过
        if file_key in path_to_hash and path_to_hash[file_key] == content_hash:
            continue

        try:
            wf_def = parse_workflow(yaml_str)
        except Exception as e:
            print(f"[workflow/loader] 解析 {yaml_file} 失败: {e}")
            continue

        wf_id = path_to_id.get(file_key, uuid.uuid4().hex[:8])
        wf_dict = {
            "id": wf_id,
            "title": wf_def.title,
            "description": wf_def.description,
            "yaml_source": yaml_str,
            "parameters": [p.model_dump() for p in wf_def.parameters],
            "blocks": [b.model_dump() for b in wf_def.blocks],
            "source_type": "file",
            "source_path": file_key,
        }
        save_workflow(wf_dict)
        loaded.append(wf_dict)
        print(f"[workflow/loader] {'更新' if file_key in path_to_id else '加载'} 工作流: {wf_def.title or yaml_file.name} ({wf_id})")

    return loaded
