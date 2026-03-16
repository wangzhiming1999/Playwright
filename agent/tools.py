# ── 工具定义（GPT 可调用的操作） ──────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "打开一个 URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "完整的 URL，如 https://example.com"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "点击页面上的元素。优先用截图中的元素编号（index），也可以用可见文字（text）。不要猜 selector。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中元素的编号（红色数字标签），优先使用"},
                    "text": {"type": "string", "description": "元素的可见文字，当不确定 index 时使用"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "在输入框中输入文字。优先用截图中的元素编号（index）直接定位，比 description 更准确。密码框必须设 is_password: true。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中输入框的编号（红色数字标签），优先使用，比 description 更准确"},
                    "description": {"type": "string", "description": "输入框的描述，如'邮箱输入框'、'密码框'、'搜索框'，当不确定 index 时使用"},
                    "text": {"type": "string", "description": "要输入的内容"},
                    "press_enter": {"type": "boolean", "description": "输入后是否按 Enter"},
                    "is_password": {"type": "boolean", "description": "是否为密码，设为 true 时日志中不显示内容"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_credentials",
            "description": "从环境变量获取某站点的登录账号和密码，用于登录流程。站点 key 示例：felo_ai 对应 FELO_AI_EMAIL、FELO_AI_PASSWORD",
            "parameters": {
                "type": "object",
                "properties": {
                    "site_key": {"type": "string", "description": "站点标识，如 felo_ai、github，对应环境变量 FELO_AI_EMAIL/FELO_AI_PASSWORD、GITHUB_EMAIL/GITHUB_PASSWORD"},
                },
                "required": ["site_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "滚动页面",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["down", "up"], "description": "滚动方向"},
                    "amount": {"type": "integer", "description": "滚动像素数，默认 500"},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "等待页面内容稳定。提交搜索/AI生成任务后，必须用 wait_for_content_change=true 等待内容真正生成完毕，再截图。AI生成内容可能需要30-120秒，务必设置足够大的 timeout。",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "固定等待秒数，默认 2。内容变化场景请用 wait_for_content_change 代替"},
                    "selector": {"type": "string", "description": "等待某个元素出现（可选）"},
                    "wait_for_content_change": {"type": "boolean", "description": "等待页面主体内容开始变化并稳定（搜索结果加载、AI生成内容完成后用）。会先等内容开始出现，再等内容停止变化。"},
                    "timeout": {"type": "number", "description": "wait_for_content_change 的最长等待秒数，默认60。AI生成任务建议设为120。"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "截图并保存，任务完成时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "保存的文件名，如 result.png"},
                    "full_page": {"type": "boolean", "description": "是否截全页，默认 false"},
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_page_html",
            "description": "获取当前页面的 HTML 源码或某个元素的 outerHTML，用于分析页面结构、查找正确的 selector",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "可选，获取某个元素的 HTML；不填则返回整个 body 的 innerHTML"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_key",
            "description": "按下键盘按键，如 Enter、Tab、Escape 等",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "按键名称，如 Enter、Tab、Escape、ArrowDown"},
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "任务已完成，退出循环",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "任务完成的简短说明"},
                },
                "required": ["summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "当你缺少必要信息无法继续时，暂停并向用户提问。"
                "适用场景：需要登录但没有账号密码、任务描述不清楚、遇到验证码、"
                "需要用户做选择（如多个搜索结果）、需要确认敏感操作。"
                "不要用于可以自己判断的情况。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "向用户提出的问题，要具体说明缺少什么信息"},
                    "reason": {"type": "string", "description": "为什么需要这个信息，当前卡在哪一步"},
                },
                "required": ["question", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hover",
            "description": "鼠标悬停在元素上，用于触发下拉菜单、tooltip 等",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中元素的编号（红色数字标签），优先使用"},
                    "text": {"type": "string", "description": "元素的可见文字，当不确定 index 时使用"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "select_option",
            "description": "在下拉选择框中选择选项",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中 select 元素的编号（红色数字标签）"},
                    "value": {"type": "string", "description": "选项的 value 属性值或可见文字"},
                },
                "required": ["index", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_tab",
            "description": "切换到指定标签页",
            "parameters": {
                "type": "object",
                "properties": {
                    "tab_index": {"type": "integer", "description": "标签页序号，从 0 开始"},
                    "url_contains": {"type": "string", "description": "URL 包含的关键词，用于匹配目标标签页"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_file",
            "description": "上传文件到 input[type=file] 元素。先用 index 定位文件上传按钮，再指定本地文件路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中文件上传元素的编号"},
                    "file_path": {"type": "string", "description": "本地文件的绝对路径，如 C:/Users/xxx/photo.jpg"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "点击下载链接/按钮并等待下载完成。返回下载文件的路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "截图中下载按钮/链接的编号"},
                    "text": {"type": "string", "description": "下载按钮的可见文字，当不确定 index 时使用"},
                    "timeout": {"type": "integer", "description": "等待下载完成的超时秒数，默认 30"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drag_drop",
            "description": "拖拽操作：从一个元素拖到另一个元素或指定坐标",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_index": {"type": "integer", "description": "拖拽起点元素的编号"},
                    "to_index": {"type": "integer", "description": "拖拽终点元素的编号"},
                    "to_x": {"type": "integer", "description": "终点 X 坐标（当没有 to_index 时使用）"},
                    "to_y": {"type": "integer", "description": "终点 Y 坐标（当没有 to_index 时使用）"},
                },
                "required": ["from_index"],
            },
        },
    },
]
