"""
E2E 测试场景定义。

10 个典型场景覆盖：导航、搜索、表单、登录、数据提取、多步骤、下拉、下载、滚动、SPA。
"""

from .e2e_runner import E2EScenario

SCENARIOS = [
    E2EScenario(
        name="simple_navigation",
        task="打开 https://example.com 并截图",
        max_steps=5,
        timeout_seconds=60,
    ),
    E2EScenario(
        name="search_engine",
        task="在 Bing (https://www.bing.com) 搜索 'playwright automation' 并截图搜索结果",
        max_steps=10,
        timeout_seconds=120,
    ),
    E2EScenario(
        name="form_fill",
        task="打开 https://httpbin.org/forms/post 填写所有表单字段（Customer=Test User, Size=Medium, Topping=Cheese, Comments=E2E test）并提交，截图提交结果",
        max_steps=15,
        timeout_seconds=120,
    ),
    E2EScenario(
        name="login_flow",
        task="打开 https://the-internet.herokuapp.com/login 用账号 tomsmith 密码 SuperSecretPassword! 登录，截图登录成功页面",
        max_steps=10,
        timeout_seconds=120,
    ),
    E2EScenario(
        name="table_extract",
        task="打开 https://the-internet.herokuapp.com/tables 截图页面上的表格数据",
        max_steps=8,
        timeout_seconds=90,
    ),
    E2EScenario(
        name="multi_step",
        task="打开 https://the-internet.herokuapp.com/add_remove_elements/ 点击 Add Element 按钮 3 次，然后截图",
        max_steps=10,
        timeout_seconds=90,
    ),
    E2EScenario(
        name="dropdown_select",
        task="打开 https://the-internet.herokuapp.com/dropdown 选择 Option 2，截图确认选择结果",
        max_steps=8,
        timeout_seconds=90,
    ),
    E2EScenario(
        name="file_download",
        task="打开 https://the-internet.herokuapp.com/download 下载页面上的第一个文件",
        max_steps=10,
        timeout_seconds=120,
    ),
    E2EScenario(
        name="scroll_long_page",
        task="打开 https://the-internet.herokuapp.com/large 向下滚动 3 次并截图页面底部",
        max_steps=10,
        timeout_seconds=90,
    ),
    E2EScenario(
        name="checkbox_toggle",
        task="打开 https://the-internet.herokuapp.com/checkboxes 勾选第一个复选框，取消勾选第二个复选框，截图结果",
        max_steps=10,
        timeout_seconds=90,
    ),
]
