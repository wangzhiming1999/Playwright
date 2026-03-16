# Skyvern - AI 驱动的浏览器自动化平台

基于 GPT-4o 视觉能力的智能浏览器 Agent，用 AI 理解网页代替传统 CSS 选择器，实现复杂的 Web 自动化任务。

## ✨ 核心特性

- **视觉驱动操作** - GPT-4o 直接"看"网页截图，通过视觉理解定位元素
- **智能任务分解** - 自动将复杂任务拆解为可执行步骤
- **失败自愈** - 操作失败时 AI 自动分析原因并调整策略重试
- **Human-in-the-loop** - 执行中可暂停向用户提问，获取必要信息
- **多浏览器模式** - 当前支持内置 Chromium，用户 Chrome / CDP 远程调试模式开发中
- **实时监控** - Web Dashboard + SSE 实时推送任务状态和日志
- **Cookie 持久化** - 自动保存登录状态，跨会话复用
- **网站探索** - 自动爬取网站并生成营销内容（截图策展 + 文案生成）

## 🚀 快速开始

### 1. 安装依赖

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 OpenAI API Key：

```env
OPENAI_API_KEY=sk-your-openai-api-key

# 可选：网站登录凭证（用于需要登录的任务）
# EXAMPLE_COM_EMAIL=your@email.com
# EXAMPLE_COM_PASSWORD=your_password
```

### 3. 启动服务

```bash
uvicorn app:app --reload --port 8000
```

打开浏览器访问 [http://localhost:8000](http://localhost:8000)

## 📖 使用示例

### Web UI 模式（推荐）

1. 在浏览器中打开 `http://localhost:8000`
2. 在输入框中输入任务，例如：
   - `打开 https://example.com 并截图`
   - `搜索 felo.ai 上关于 AI 的最新新闻`
   - `打开 GitHub，找到 Trending 页面，截图前 5 个项目`
3. 点击"提交任务"，实时查看执行日志和截图

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

### CLI 模式

```bash
python agent.py
# 按提示输入任务描述
```

## 🛠️ Agent 工具集

Agent 拥有 14 个工具来操作浏览器：

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
| `get_credentials` | 获取凭证 | `get_credentials(site="example.com")` |
| `ask_user` | 向用户提问 | `ask_user(question="请输入验证码")` |
| `dismiss_overlay` | 关闭弹窗 | `dismiss_overlay()` |
| `done` | 标记完成 | `done(reason="任务已完成")` |
| `_ai_validate` | AI 验证 | 内部工具，验证操作结果 |
| `_ai_act` | AI 操作 | 内部工具，视觉驱动操作 |

## 🏗️ 项目架构

```
playwright/
├── agent.py              # 核心 Agent 逻辑（1669 行）
├── app.py                # FastAPI 后端服务（699 行）
├── db.py                 # SQLite 数据库（118 行）
├── utils.py              # 工具函数（OpenAI 客户端等）
├── page_annotator.py     # 页面元素标注（红框+编号）
├── explorer.py           # 网站探索爬虫
├── curator.py            # 截图策展（去重+打分）
├── content_gen.py        # 营销内容生成
├── site_understanding.py # 网站结构分析
├── static/
│   └── index.html        # Web Dashboard
├── tests/                # 测试套件（8 个模块）
├── data/
│   └── tasks.db          # SQLite 数据库
├── screenshots/          # 任务截图存储
└── requirements.txt      # Python 依赖
```

## 🔧 高级配置

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API Key（必填） | - |
| `API_KEY` | 服务端 API 认证密钥（可选） | - |
| `USE_PROXY` | 是否使用代理 | `false` |
| `CORS_ORIGINS` | CORS 允许的源（逗号分隔） | `http://localhost:8000` |
| `MAX_QUEUE_SIZE` | 最大并发任务数 | `20` |
| `MAX_TASKS_KEEP` | 保留的历史任务数 | `50` |

### 浏览器模式

目前仅内置 Chromium 模式可用，其余两种模式尚在开发中：

| 模式 | 状态 | 说明 |
|------|------|------|
| `builtin` | ✅ 可用 | 内置 Chromium，开箱即用 |
| `user_chrome` | 🚧 开发中 | 使用用户 Chrome 配置文件，保留登录态 |
| `cdp` | 🚧 开发中 | CDP 远程调试，连接已打开的 Chrome |

### 网站凭证配置

在 `.env` 中添加站点凭证（站点域名用下划线替换点和连字符）：

```env
# 示例：felo.ai 的凭证
FELO_AI_EMAIL=your@email.com
FELO_AI_PASSWORD=your_password

# 示例：github.com 的凭证
GITHUB_COM_EMAIL=your@email.com
GITHUB_COM_PASSWORD=your_password
```

Agent 会在需要时通过 `get_credentials` 工具自动获取。

## 🧪 运行测试

```bash
# 安装测试依赖
pip install -r requirements-dev.txt

# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_agent.py
pytest tests/test_app.py -v
```

## 📊 API 端点

### 任务管理

- `POST /run` - 提交新任务
  ```json
  {"task": "打开 https://example.com 并截图"}
  ```

- `GET /tasks` - 获取所有任务列表

- `GET /tasks/stream` - SSE 实时订阅任务更新

- `POST /tasks/{task_id}/reply` - 回答 Agent 的提问
  ```json
  {"answer": "用户输入的答案"}
  ```

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

### 静态资源

- `GET /screenshots/{task_id}/{filename}` - 获取截图文件

## 🔒 安全性

- ✅ 路径遍历防护（截图文件名验证）
- ✅ URL 白名单（阻止 localhost、内网 IP）
- ✅ 输入验证（selector、filename 等）
- ✅ 可选 API Key 认证
- ✅ 敏感信息检测（自动模糊处理 PII）

## 🐛 已知问题与解决方案

### 问题：Agent 在 AI 生成内容完成前就截图

**原因**：`wait_for_content_change` 在内容开始变化前就返回了。

**解决方案**：使用两阶段等待（已修复）
```python
# 任务中明确指定等待时间
wait(wait_for_content_change=true, timeout=120)
```

### 问题：Windows 上 Playwright 报 NotImplementedError

**原因**：主事件循环不支持 Playwright 的子进程操作。

**解决方案**：已在 [app.py:75](app.py#L75) 中使用独立线程池 + ProactorEventLoop。

## 🗺️ 功能路线图

### 第一优先级（解锁真实场景）
- [ ] 2FA / 验证码处理（短信、TOTP、图形验证码）
- [ ] 文件上传/下载
- [ ] 更多 action 类型（hover、drag & drop、原生 select、日期选择器）

### 第二优先级（工程完整性）
- [ ] Webhook 回调（任务完成/失败通知）
- [ ] 任务取消 & 超时配置
- [ ] 断点续跑（从检查点恢复）

### 第三优先级（生产化）
- [ ] YAML 工作流定义（声明式多步骤）
- [ ] 结构化 JSON 日志
- [ ] Docker 支持

## 📝 开发日志

- **2026-03-14** - 完成商用级稳定性加固（修复 50+ 问题）
  - 数组越界保护、JSON 解析异常处理
  - Playwright 操作异常捕获、资源泄漏修复
  - 安全漏洞修复、防御性编程改进
  - 详见 [stability_plan.md](C:\Users\wangzhiming\.claude\projects\c--Users-wangzhiming-Desktop-playwright\memory\stability_plan.md)

- **2026-03-12** - 实现 Human-in-the-loop（ask_user 工具）

- **2026-03-10** - 优化内容稳定检测（两阶段等待）

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

MIT License

## 🙏 致谢

- [Playwright](https://playwright.dev/) - 浏览器自动化框架
- [OpenAI GPT-4o](https://openai.com/) - 视觉语言模型
- [FastAPI](https://fastapi.tiangolo.com/) - 现代 Web 框架
- [Skyvern](https://github.com/Skyvern-AI/skyvern) - 灵感来源

---

**注意**：本项目处于活跃开发中，API 可能会有变动。生产环境使用前请充分测试。
