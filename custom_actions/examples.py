"""
示例自定义 Action：展示 @action 装饰器的用法。
实际使用时可以删除此文件，或在此基础上修改。
"""

from agent.action_registry import action


@action(
    name="extract_text",
    description="提取当前页面的纯文本内容（去除 HTML 标签），返回前 N 个字符",
    parameters={
        "max_chars": {"type": "integer", "description": "最多返回的字符数，默认 2000"},
        "selector": {"type": "string", "description": "CSS 选择器，只提取匹配元素的文本（可选）"},
    },
)
async def extract_text(max_chars: int = 2000, selector: str = "", **ctx) -> str:
    page = ctx.get("page")
    if not page:
        return "操作失败: 无法获取页面对象"

    try:
        if selector:
            text = await page.eval_on_selector(selector, "el => el.innerText")
        else:
            text = await page.evaluate("() => document.body.innerText")
        text = (text or "").strip()
        if len(text) > max_chars:
            return text[:max_chars] + f"\n...(共 {len(text)} 字符，已截断)"
        return text or "页面无文本内容"
    except Exception as e:
        return f"操作失败: {e}"


@action(
    name="run_js",
    description="在当前页面执行一段 JavaScript 代码并返回结果。用于高级页面操作或数据提取。",
    parameters={
        "code": {"type": "string", "description": "要执行的 JavaScript 代码", "required": True},
    },
)
async def run_js(code: str, **ctx) -> str:
    page = ctx.get("page")
    if not page:
        return "操作失败: 无法获取页面对象"

    # 安全检查：禁止危险操作
    dangerous = ["document.cookie", "localStorage.clear", "indexedDB.deleteDatabase"]
    code_lower = code.lower()
    for d in dangerous:
        if d.lower() in code_lower:
            return f"操作失败: 禁止执行包含 '{d}' 的代码"

    try:
        result = await page.evaluate(code)
        if result is None:
            return "执行完成（无返回值）"
        import json
        if isinstance(result, (dict, list)):
            return json.dumps(result, ensure_ascii=False, indent=2)[:3000]
        return str(result)[:3000]
    except Exception as e:
        return f"操作失败: JS 执行错误 — {e}"


@action(
    name="fill_form",
    description="批量填写表单：一次性填写多个输入框，避免逐个调用 type_text。传入 JSON 格式的字段映射。",
    parameters={
        "fields": {
            "type": "string",
            "description": 'JSON 格式的字段映射，如 {"#name": "张三", "#email": "test@example.com"}，key 是 CSS 选择器，value 是要填入的值',
            "required": True,
        },
    },
)
async def fill_form(fields: str, **ctx) -> str:
    page = ctx.get("page")
    log_fn = ctx.get("log_fn")
    if not page:
        return "操作失败: 无法获取页面对象"

    import json
    try:
        field_map = json.loads(fields) if isinstance(fields, str) else fields
    except json.JSONDecodeError as e:
        return f"操作失败: fields 不是有效的 JSON — {e}"

    if not isinstance(field_map, dict):
        return "操作失败: fields 必须是 JSON 对象"

    results = []
    for selector, value in field_map.items():
        try:
            await page.fill(selector, str(value), timeout=5000)
            results.append(f"✓ {selector} = {str(value)[:30]}")
        except Exception as e:
            results.append(f"✗ {selector}: {e}")

    summary = "\n".join(results)
    success_count = sum(1 for r in results if r.startswith("✓"))
    return f"已填写 {success_count}/{len(field_map)} 个字段:\n{summary}"
