"""
E2E 测试场景定义。

分两组：
1. BASIC_SCENARIOS — 基础场景（mock 站点），验证核心工具链
2. REAL_SCENARIOS — 真实网站场景，验证 Agent 在野外的实战能力
"""

from .e2e_runner import E2EScenario


# ── 基础场景（mock 站点，稳定可控） ──────────────────────────────────────────

BASIC_SCENARIOS = [
    E2EScenario(
        name="simple_navigation",
        task="打开 https://example.com 并截图",
        max_steps=5,
        timeout_seconds=60,
        category="navigation",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="login_flow",
        task="打开 https://the-internet.herokuapp.com/login 用账号 tomsmith 密码 SuperSecretPassword! 登录，截图登录成功页面",
        max_steps=10,
        timeout_seconds=120,
        category="login",
        difficulty="basic",
        tags=["mock_site"],
        expected_result="登录成功，页面显示 Secure Area",
    ),
    E2EScenario(
        name="form_fill",
        task="打开 https://httpbin.org/forms/post 填写所有表单字段（Customer=Test User, Size=Medium, Topping=Cheese, Comments=E2E test）并提交，截图提交结果",
        max_steps=15,
        timeout_seconds=120,
        category="form",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="table_extract",
        task="打开 https://the-internet.herokuapp.com/tables 截图页面上的表格数据",
        max_steps=8,
        timeout_seconds=90,
        category="extract",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="multi_step",
        task="打开 https://the-internet.herokuapp.com/add_remove_elements/ 点击 Add Element 按钮 3 次，然后截图",
        max_steps=10,
        timeout_seconds=90,
        category="multi_step",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="dropdown_select",
        task="打开 https://the-internet.herokuapp.com/dropdown 选择 Option 2，截图确认选择结果",
        max_steps=8,
        timeout_seconds=90,
        category="form",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="file_download",
        task="打开 https://the-internet.herokuapp.com/download 下载页面上的第一个文件",
        max_steps=10,
        timeout_seconds=120,
        category="navigation",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="scroll_long_page",
        task="打开 https://the-internet.herokuapp.com/large 向下滚动 3 次并截图页面底部",
        max_steps=10,
        timeout_seconds=90,
        category="navigation",
        difficulty="basic",
        tags=["mock_site"],
    ),
    E2EScenario(
        name="checkbox_toggle",
        task="打开 https://the-internet.herokuapp.com/checkboxes 勾选第一个复选框，取消勾选第二个复选框，截图结果",
        max_steps=10,
        timeout_seconds=90,
        category="form",
        difficulty="basic",
        tags=["mock_site"],
    ),
]


# ── 真实网站场景 ──────────────────────────────────────────────────────────

REAL_SCENARIOS = [
    # ── 搜索类 ──
    E2EScenario(
        name="bing_search",
        task="在 Bing (https://www.bing.com) 搜索 'playwright browser automation'，等待搜索结果加载完成，截图搜索结果页面",
        max_steps=10,
        timeout_seconds=120,
        category="search",
        difficulty="basic",
        tags=["real_site", "needs_network"],
        expected_result="搜索结果页面显示 playwright 相关结果",
    ),
    E2EScenario(
        name="duckduckgo_search",
        task="打开 https://duckduckgo.com 搜索 'python web scraping'，等待结果加载，截图搜索结果",
        max_steps=10,
        timeout_seconds=120,
        category="search",
        difficulty="basic",
        tags=["real_site", "needs_network"],
        expected_result="DuckDuckGo 搜索结果页面",
    ),
    E2EScenario(
        name="wikipedia_search",
        task="打开 https://en.wikipedia.org 搜索 'Artificial intelligence'，进入词条页面，截图页面顶部内容",
        max_steps=12,
        timeout_seconds=120,
        category="search",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="Wikipedia AI 词条页面",
    ),

    # ── 数据提取类 ──
    E2EScenario(
        name="github_repo_info",
        task="打开 https://github.com/microsoft/playwright 提取仓库的 star 数、fork 数、最新 release 版本号，用 extract 工具提取这些信息，然后截图",
        max_steps=12,
        timeout_seconds=120,
        category="extract",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="提取到 star/fork/release 信息",
    ),
    E2EScenario(
        name="hackernews_top",
        task="打开 https://news.ycombinator.com 用 extract 工具提取前 5 条新闻的标题和链接，然后截图",
        max_steps=10,
        timeout_seconds=120,
        category="extract",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="提取到 HN 前5条新闻标题",
    ),
    E2EScenario(
        name="quotes_scrape",
        task="打开 https://quotes.toscrape.com 用 extract 工具提取页面上前 3 条名言的内容和作者，然后截图",
        max_steps=10,
        timeout_seconds=120,
        category="extract",
        difficulty="basic",
        tags=["real_site", "needs_network"],
        expected_result="提取到名言内容和作者",
    ),

    # ── 表单交互类 ──
    E2EScenario(
        name="demoqa_form",
        task="打开 https://demoqa.com/text-box 填写 Full Name 为 'Test User'，Email 为 'test@example.com'，Current Address 为 '123 Test Street'，点击 Submit 按钮，截图提交结果",
        max_steps=15,
        timeout_seconds=120,
        category="form",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="表单提交成功，底部显示填写的信息",
    ),
    E2EScenario(
        name="demoqa_checkbox",
        task="打开 https://demoqa.com/checkbox 展开 Home 目录树，勾选 Desktop 复选框，截图结果",
        max_steps=12,
        timeout_seconds=120,
        category="form",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="Desktop 被勾选，底部显示选中项",
    ),

    # ── 多步骤导航类 ──
    E2EScenario(
        name="wikipedia_navigate",
        task="打开 https://en.wikipedia.org/wiki/Python_(programming_language) 找到页面中的 'History' 章节链接并点击跳转到该章节，截图 History 章节内容",
        max_steps=10,
        timeout_seconds=120,
        category="navigation",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="页面滚动到 History 章节",
    ),
    E2EScenario(
        name="github_navigate_issues",
        task="打开 https://github.com/microsoft/playwright 点击 Issues 标签页，截图 Issues 列表",
        max_steps=10,
        timeout_seconds=120,
        category="navigation",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="显示 Issues 列表页面",
    ),

    # ── SPA / 动态页面类 ──
    E2EScenario(
        name="jsonplaceholder_api",
        task="打开 https://jsonplaceholder.typicode.com/todos/1 用 extract 工具提取 JSON 中的 title 和 completed 字段值，截图",
        max_steps=8,
        timeout_seconds=90,
        category="extract",
        difficulty="basic",
        tags=["real_site", "needs_network"],
        expected_result="提取到 todo 的 title 和 completed 值",
    ),
    E2EScenario(
        name="demoqa_sortable",
        task="打开 https://demoqa.com/sortable 将列表模式下的 'One' 拖拽到 'Three' 下方，截图结果",
        max_steps=12,
        timeout_seconds=120,
        category="multi_step",
        difficulty="advanced",
        tags=["real_site", "needs_network"],
        expected_result="列表顺序发生变化",
    ),

    # ── 复杂多步骤类 ──
    E2EScenario(
        name="quotes_pagination",
        task="打开 https://quotes.toscrape.com 点击底部的 Next 按钮翻到第 2 页，用 extract 提取第 2 页第一条名言的内容和作者，截图",
        max_steps=12,
        timeout_seconds=120,
        category="multi_step",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="翻到第2页并提取名言",
    ),
    E2EScenario(
        name="books_toscrape_filter",
        task="打开 https://books.toscrape.com 点击左侧分类中的 'Travel'，截图 Travel 分类下的书籍列表",
        max_steps=10,
        timeout_seconds=120,
        category="navigation",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="显示 Travel 分类的书籍",
    ),
    E2EScenario(
        name="demoqa_tabs",
        task="打开 https://demoqa.com/tabs 依次点击 Origin 和 Use 标签页，截图 Use 标签页的内容",
        max_steps=10,
        timeout_seconds=120,
        category="multi_step",
        difficulty="intermediate",
        tags=["real_site", "needs_network"],
        expected_result="Use 标签页内容可见",
    ),
]


# 全部场景合并
SCENARIOS = BASIC_SCENARIOS + REAL_SCENARIOS
