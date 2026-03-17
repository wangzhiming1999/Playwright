# Skyvern - AI 驱动的浏览器自动化平台

基于 GPT-4o / Claude 视觉能力的智能浏览器 Agent，用 AI 理解网页代替传统 CSS 选择器，实现复杂的 Web 自动化任务。

## ✨ 核心特性

- **视觉驱动操作** - LLM 直接"看"网页截图，通过视觉理解定位元素
- **多 LLM 后端** - 支持 OpenAI (GPT-4o)、Anthropic (Claude)、litellm（Gemini / Ollama / Azure 等 100+ 模型）
- **智能任务分解** - 自动将复杂任务拆解为可执行步骤
- **失败自愈** - 操作失败时 AI 自动分析原因并调整策略重试
- **Human-in-the-loop** - 执行中可暂停向用户提问，获取必要信息
- **2FA / 验证码** - 支持 TOTP 两步验证和图形验证码识别
- **文件操作** - 文件上传、下载、拖拽
- **YAML 工作流** - 声明式多步骤工作流，可视化编辑与运行
- **模板市场** - 预置模板（登录、数据提取、表单填写、监控等），一键实例化与运行
- **实时监控** - Web Dashboard + SSE 实时推送任务状态和日志
- **Webhook 回调** - 任务完成/失败时自动通知外部系统
- **断点续跑** - 从检查点恢复中断的任务
- **Cookie 持久化** - 自动保存登录状态，跨会话复用
- **网站探索** - 自动爬取网站并生成营销内容（截图策展 + 文案生成）
- **Docker 支持** - 开箱即用的容器化部署

## 🚀 快速开始

### 方式一：本地运行

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 安装 Playwright 浏览器
playwright install chromium

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key（详见 .env.example 中的注释）

# 4. 启动服务
uvicorn app:app --reload --port 8000
```

打开浏览器访问 [http://localhost:8000](http://localhost:8000)

如需修改前端界面：进入 `frontend` 目录执行 `npm install` 与 `npm run build`，构建产物会输出到 `static/`。

### 方式二：Docker

```bash
# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key

# 启动
docker-compose up
```

### 交互式 API 文档

启动服务后访问 [http://localhost:8000/docs](http://localhost:8000/docs) 查看 Swagger UI，可直接在线调试所有 API。

## 📖 使用示例

### Web UI 模式（推荐）

在浏览器中打开 `http://localhost:8000`，主要页面：

| 页面 | 说明 |
|------|------|
| **Dashboard** | 概览与快捷入口 |
| **任务** | 提交自然语言任务，实时查看执行日志和截图 |
| **网站探索** | 爬取网站、策展截图、生成营销内容 |
| **工作流** | 编辑 YAML 工作流、运行并查看历史 |
| **模板市场** | 浏览/运行预置模板（登录、数据提取、表单、监控等） |
| **设置** | 主题、API 等配置 |

任务示例：`打开 https://example.com 并截图`、`打开 GitHub Trending 页面截图前 5 个项目`

### API 模式

```bash
# 提交任务
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"task": "打开 https://example.com 并截图"}'

# 查看所有任务
curl http://localhost:8000/tasks

# SSE 实时订阅（浏览器 EventSource）
const es = new EventSource('http://localhost:8000/tasks/stream');
es.onmessage = (e) => console.log(JSON.parse(e.data));
```

## 🛠️ Agent 工具集

Agent 拥有 20+ 个工具来操作浏览器：

| 工具 | 功能 | 示例 |
|------|------|------|
| `navigate` | 打开 URL | `navigate(url="https://example.com")` |
| `click` | 点击元素 | `click(index=5)` 或 `click(text="登录")` |
| `type_text` | 输入文本 | `type_text(index=3, text="hello", press_enter=true)` |
| `scroll` | 滚动页面 | `scroll(direction="down", amount=500)` |
| `wait` | 等待内容变化 | `wait(wait_for_content_change=true, timeout=120)` |
| `screenshot` | 保存截图 | `screenshot(filename="result.png")` |
| `get_page_html` | 获取 HTML | `get_page_html()` |
| `press_key` | 按键 | `press_key(key="Escape")` |
| `hover` | 鼠标悬停 | `hover(index=3)` |
| `select_option` | 下拉选择 | `select_option(index=5, value="option1")` |
| `switch_tab` | 切换标签页 | `switch_tab(tab_index=1)` |
| `upload_file` | 上传文件 | `upload_file(index=2, file_path="/path/to/file")` |
| `download_file` | 下载文件 | `download_file(index=3)` |
| `drag_drop` | 拖拽操作 | `drag_drop(from_index=1, to_index=5)` |
| `solve_captcha` | 识别验证码 | `solve_captcha(input_index=3)` |
| `get_totp_code` | 生成 TOTP | `get_totp_code(site_key="github")` |
| `get_credentials` | 获取凭证 | `get_credentials(site_key="github")` |
| `ask_user` | 向用户提问 | `ask_user(question="请输入验证码")` |
| `dismiss_overlay` | 关闭弹窗 | `dismiss_overlay()` |
| `done` | 标记完成 | `done(summary="任务已完成")` |

## 🏗️ 项目架构

```
skyvern/
├── agent/                  # 核心 Agent 包
│   ├── __init__.py         # 包导出
│   ├── core.py             # BrowserAgent 类（截图、点击、输入、工具执行）
│   ├── runner.py           # run_agent() 主函数（LLM 决策循环）
│   ├── tools.py            # 工具定义（LLM 可调用的 20+ 操作）
│   ├── llm_helpers.py      # 任务分解、步骤验证、上下文压缩
│   ├── page_utils.py       # 页面就绪等待、安全打印
│   ├── error_recovery.py   # 失败分析与重试
│   ├── circuit_breaker.py  # 调用熔断
│   └── chrome_detector.py  # Chrome/Edge 用户数据目录检测
├── workflow/               # YAML 工作流引擎
│   ├── models.py           # 工作流/Block/参数 Pydantic 模型
│   ├── parser.py           # YAML 解析与校验
│   ├── engine.py           # WorkflowEngine 顺序执行
│   ├── blocks.py           # 12 种 block 执行器
│   ├── context.py          # 参数与 Jinja2 上下文
│   ├── db.py / loader.py   # 工作流持久化与目录扫描
├── templates/              # 模板市场 YAML（按分类存放，20+ 模板）
│   ├── login-session/      # 登录、会话检查、会话保活
│   ├── data-extraction/    # 表格/商品/文章/列表数据提取
│   ├── form-filling/       # 表单填写、多步表单、申请提交
│   ├── monitoring/         # 价格监控、页面变更、可用性检测、关键词监控
│   ├── file-operations/    # 文件下载、上传并提交
│   ├── search-research/    # 搜索汇总、收集链接、多商品比价
│   └── integration/       # 集成通知（如执行任务后 Webhook）
├── workflows/              # 用户工作流示例（_examples 等）
├── app.py                  # FastAPI 后端服务
├── db.py                   # SQLite 数据持久化
├── utils.py                # LLM 路由（OpenAI/Anthropic/litellm）、URL 验证
├── page_annotator.py       # 页面元素标注（红框+编号）
├── explorer.py             # 网站探索爬虫
├── curator.py              # 截图策展（去重+打分）
├── content_gen.py          # 营销内容生成
├── site_understanding.py   # 网站结构分析
├── template_loader.py       # 扫描 templates/ 加载模板列表
├── frontend/               # Web Dashboard（React + Vite → static/）
├── static/                 # 前端构建产物
├── tests/                  # 测试套件
├── data/                   # tasks.db、工作流 DB 等
├── screenshots/             # 任务截图
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── requirements.txt
```

## 🔧 高级配置

### 环境变量

详见 `.env.example`，主要配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API Key | - |
| `ANTHROPIC_API_KEY` | Anthropic API Key | - |
| `LLM_BACKEND` | LLM 后端：`openai` / `anthropic` / `litellm` | `openai` |
| `API_KEY` | 服务端 API 认证密钥（可选） | - |
| `HEADLESS` | 浏览器无头模式 | `false` |
| `USE_PROXY` | 是否使用代理 | `false` |
| `CORS_ORIGINS` | CORS 允许的源（逗号分隔） | `http://localhost:8000` |
| `MAX_QUEUE_SIZE` | 最大并发任务数 | `20` |
| `MAX_TASKS_KEEP` | 保留的历史任务数 | `50` |

### 浏览器模式

| 模式 | 状态 | 说明 |
|------|------|------|
| `builtin` | ✅ 可用 | 内置 Chromium，开箱即用 |
| `user_chrome` | 🚧 开发中 | 使用用户 Chrome 配置文件，保留登录态 |
| `cdp` | 🚧 开发中 | CDP 远程调试，连接已打开的 Chrome |

### 网站凭证配置

在 `.env` 中添加站点凭证（站点域名用下划线替换点和连字符）：

```env
# 示例：github.com 的凭证
GITHUB_COM_EMAIL=your@email.com
GITHUB_COM_PASSWORD=your_password

# 可选：TOTP 两步验证密钥
GITHUB_COM_TOTP_SECRET=JBSWY3DPEHPK3PXP
```

Agent 会在需要时通过 `get_credentials` / `get_totp_code` 工具自动获取。

## 🧪 运行测试

```bash
# 安装测试依赖
pip install -r requirements-dev.txt

# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_app.py -v
pytest -k "test_validate_url"
```

## 📊 API 端点

### 任务管理

- `POST /run` - 提交新任务
  ```json
  {"task": "打开 https://example.com 并截图"}
  ```

- `GET /tasks` - 获取所有任务列表

- `GET /tasks/stream` - SSE 实时订阅任务更新

- `GET /tasks/{task_id}/logs` - 获取任务日志

- `POST /tasks/{task_id}/reply` - 回答 Agent 的提问
  ```json
  {"answer": "用户输入的答案"}
  ```

- `POST /tasks/{task_id}/cancel` - 取消运行中的任务

- `DELETE /tasks/{task_id}` - 删除指定任务

### 网站探索

- `POST /explore` - 启动网站探索
  ```json
  {
    "url": "https://example.com",
    "product_context": "这是一个 AI 搜索引擎"
  }
  ```

- `POST /curate` - 策展截图（去重+打分）
  ```json
  {"task_id": "explore_xxx"}
  ```

- `POST /generate` - 生成营销内容
  ```json
  {
    "task_id": "explore_xxx",
    "content_type": "landing_page",
    "language": "zh-CN"
  }
  ```

### 工作流

- `GET /workflows` - 列出所有工作流
- `GET /workflows/{wf_id}` - 获取工作流详情
- `POST /workflows` - 创建工作流（YAML）
- `PUT /workflows/{wf_id}` - 更新工作流
- `DELETE /workflows/{wf_id}` - 删除工作流
- `POST /workflows/{wf_id}/run` - 运行工作流
- `GET /workflows/{wf_id}/runs` - 工作流运行历史
- `GET /workflow-runs/{run_id}` - 获取单次运行详情

### 模板市场

- `GET /templates` - 列出模板（可选 `?category=xxx`）
- `GET /templates/categories` - 模板分类及数量
- `GET /templates/{template_id}` - 获取模板详情
- `POST /templates/{template_id}/instantiate` - 实例化为新工作流
- `POST /templates/{template_id}/run` - 直接运行模板

模板分类：登录会话、数据提取、表单填写、监控检测、文件操作、搜索研究、**集成通知**（Webhook 等）。

### 导出

- `GET /export/{source}/{source_id}/json` - 导出为 JSON
- `GET /export/{source}/{source_id}/zip` - 导出为 ZIP

### 静态资源

- `GET /screenshots/{task_id}/{filename}` - 获取截图文件

## 🔒 安全性

- ✅ 路径遍历防护（截图文件名验证）
- ✅ URL 白名单（阻止 localhost、内网 IP）
- ✅ 输入验证（selector、filename 等）
- ✅ 可选 API Key 认证
- ✅ CORS 配置
- ✅ 凭证脱敏（密码在日志中显示为 `***`）
- ✅ 敏感信息检测（自动模糊处理 PII）

## 🗺️ 功能路线图

### 已完成

- [x] 2FA / 验证码处理（TOTP、图形验证码）
- [x] 文件上传/下载
- [x] 更多 action 类型（hover、drag & drop、select、switch_tab）
- [x] Webhook 回调（任务完成/失败通知）
- [x] 任务取消 & 超时配置
- [x] 断点续跑（从检查点恢复）
- [x] 结构化 JSON 日志
- [x] Docker 支持
- [x] YAML 工作流定义与可视化编辑
- [x] 模板市场（预置模板一键运行/实例化）
- [x] 商用级稳定性加固（50+ 问题修复）
- [x] 并行任务执行（TaskPool 并发控制）
- [x] 插件系统（自定义 Action 注册 + 3 个示例插件）
- [x] 安全加固（路径遍历/时序攻击/输入校验/LLM 重试）
- [x] 键盘组合键、右键点击、iframe 切换、SPA 检测

### 计划中

- [ ] 多用户/多租户（API Key 隔离、用量统计）

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

开发前请：
1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 运行测试确保通过 (`pytest`)
4. 提交代码 (`git commit -m 'Add amazing feature'`)
5. 推送到分支 (`git push origin feature/amazing-feature`)
6. 创建 Pull Request

## 📄 许可证

[MIT License](LICENSE)

## 🙏 致谢

- [Playwright](https://playwright.dev/) - 浏览器自动化框架
- [OpenAI GPT-4o](https://openai.com/) - 视觉语言模型
- [Anthropic Claude](https://anthropic.com/) - 视觉语言模型
- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Web 框架
- [Skyvern](https://github.com/Skyvern-AI/skyvern) - 灵感来源

---

**注意**：本项目处于活跃开发中，API 可能会有变动。生产环境使用前请充分测试。
