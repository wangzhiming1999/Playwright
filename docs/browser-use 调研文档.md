# Browser-Use 开源项目调研文档

> 基于官方文档、博客与社区资料整理，便于与当前 Skyvern 风格项目对比与借鉴。  
> 文档索引：https://docs.browser-use.com/llms.txt

---

## 1. 项目概述

**Browser-Use** 是一个用 Python 编写的开源 AI 浏览器自动化库，让 LLM Agent 能够控制浏览器完成网页任务。

| 项目 | 说明 |
|------|------|
| 仓库 | [browser-use/browser-use](https://github.com/browser-use/browser-use) |
| 星标 | 80k+ |
| 文档 | https://docs.browser-use.com |
| 安装 | `pip install browser-use`，CLI：`uvx browser-use install` |

**核心定位**：把网站状态以「文本化快照」为主、截图为辅的方式交给 LLM，实现**以 DOM 为主、视觉可选**的自动化，在保证精度的同时显著降低延迟与 token 消耗。

---

## 2. 核心架构与设计

### 2.1 状态表示：DOM 快照 vs 截图

- **主要输入**：将页面表示为 **DOM 快照（Snapshot）** 的文本形式，LLM 直接与结构化文本交互。
- **截图**：**可选**。通过 `use_vision` 控制：
  - `"auto"`（默认）：提供 screenshot 工具，但仅在需要视觉确认时使用；
  - `True`：每步都带截图；
  - `False`：不用截图，也不暴露截图工具。

**Snapshot 特点**（参考 agent-browser / CLI 文档）：

- 输出为「可访问性树」风格的紧凑文本，带元素引用（如 `@e1`, `@e2`），便于点击、填写、提取。
- 可选参数示例：`--interactive`（仅交互元素）、`--compact`（去掉空结构）、`--depth`（限制深度）、`--selector`（限定范围）。

因此：**Browser-Use 是「DOM 优先、截图按需」**，与当前项目「每步截图 + 红框编号」的纯视觉方案形成对比。

### 2.2 DOM Downsampling

- 原始 DOM 动辄数十万 token，而一张截图的视觉编码约数千 token。
- **DOM Downsampling**：对 DOM 做「降采样」，保留层次与关键特征，将 token 量压到与截图同量级（约 1e3），同时保持任务成功率（文献中与 GUI snapshot 基线相当）。
- 启示：若我们引入 DOM/可访问性树，必须做类似的**裁剪与摘要**，不能整页 HTML 塞进 prompt。

### 2.3 按需提取：`extract` 工具

- **问题**：整页 dump 到 context 会导致 token 爆炸、推理变慢。
- **做法**：提供 **`extract`** 工具，用自然语言向页面「提问」：
  - 例如：「这款商品的价格是多少？」「这个 PR 是什么时候开的？」
  - 内部对页面 **Markdown/文本** 做一次**单独的 LLM 调用**，只取相关片段返回给主 Agent。
- **效果**：信息获取更精准、context 更小、无视觉编码开销，尤其适合长任务（>50 步）。
- 改进版 extract 支持多种模式：`auto`、`full_page`、`main_content`、`interactive`、`structured`，以及无 LLM 的 raw 模式（按元素索引做结构化输出）。

---

## 3. 速度与成本优化（官方博客：Speed Matters）

在 benchmark（如 OnlineMind2Web）中，Browser-Use 1.0 平均约 **68 秒**完成任务，对比其他模型：

- OpenAI Computer-Using Model: ~330s  
- Claude Sonnet 4: ~295s  
- Gemini 2.5 Computer Use: ~225s  

**主要优化手段**：

1. **KV Cache 利用**  
   - 将 **Agent 对话历史** 放在 prompt 前部，**当前浏览器状态** 放在后部。  
   - 历史部分可被缓存，仅新状态参与计算，显著降低延迟与成本。

2. **按需截图**  
   - 多数步骤仅靠 DOM 即可决策，不传图。  
   - 每张截图约增加 ~0.8s 推理（图像编码），减少截图即减少延迟。

3. **智能文本提取（extract）**  
   - 不把整页内容塞进主 Agent context，而是用 extract 做「问什么取什么」的二次调用，既省 token 又省时间。

4. **最小化输出 token**  
   - 输出 token 单位时间成本远高于输入（文中约 215x）。  
   - 将 **action 空间设计得极简**（动作名与参数尽量短），单步多在 10–15 token 内表达。

---

## 4. 支持的 LLM

| 提供商 | 类名 | 推荐模型 | 环境变量 |
|--------|------|----------|----------|
| Browser-Use 自研 | `ChatBrowserUse` | `bu-latest` / `bu-1-0`（默认）, `bu-2-0`（高级） | `BROWSER_USE_API_KEY` |
| OpenAI | `ChatOpenAI` | `o3`（精度优先） | `OPENAI_API_KEY` |
| Google | `ChatGoogle` | `gemini-flash-latest`, `gemini-2.5-flash` | `GOOGLE_API_KEY` |
| Anthropic | `ChatAnthropic` | `claude-sonnet-4-0` | `ANTHROPIC_API_KEY` |
| Azure OpenAI | `ChatAzureOpenAI` | 如 `o4-mini`, `gpt-5.1-codex-mini` | `AZURE_OPENAI_*` |
| AWS Bedrock | `ChatAWSBedrock` / `ChatAnthropicBedrock` | 多种 Bedrock 模型 | AWS 凭证 |
| Groq | `ChatGroq` | 如 `meta-llama/llama-4-maverick-*` | `GROQ_API_KEY` |
| Ollama | `ChatOllama` | 如 `llama3.1:8b` | 本地 Ollama |
| 其他 | `ChatOpenAI` + `base_url` | OpenRouter、DeepSeek、Novita、Qwen、ModelScope、Vercel AI Gateway 等 | 各服务 API Key |

**ChatBrowserUse** 针对浏览器任务优化，官方称在保持精度的前提下比通用模型快约 3–5 倍，并有按 token 的定价（含 cached 优惠）。

---

## 5. Agent 参数摘要

### 5.1 核心与视觉

| 参数 | 默认 | 说明 |
|------|------|------|
| `output_model_schema` | - | Pydantic 模型，用于结构化输出校验 |
| `browser` | - | 浏览器实例（见下节） |
| `tools` | 内置 | 工具注册表，可扩展自定义工具 |
| `skills` / `skill_ids` | - | 预置技能 ID 列表，需 `BROWSER_USE_API_KEY` |
| `use_vision` | `"auto"` | `"auto"` / `True` / `False`，控制是否带截图 |
| `vision_detail_level` | `'auto'` | 截图细节：`'low'` / `'high'` / `'auto'` |
| `page_extraction_llm` | 与主 LLM 同 | 用于页面内容提取的独立 LLM（可用更小、更快模型） |
| `fallback_llm` | - | 主 LLM 多次失败后切换的备用 LLM |

### 5.2 行为与容错

| 参数 | 默认 | 说明 |
|------|------|------|
| `flash_mode` | `False` | 为 True 时跳过评估/下一步目标/思考，仅用记忆，速度优先 |
| `use_thinking` | `True` | 是否使用模型内部「思考」字段 |
| `max_failures` | `3` | 单步最大重试次数 |
| `max_actions_per_step` | `4` | 每步最多执行动作数（如一次填多字段） |
| `final_response_after_failure` | `True` | 达到 max_failures 后是否再强制一次带中间结果的输出 |
| `initial_actions` | - | 主任务前自动执行的动作列表（无需 LLM） |

### 5.3 提示与数据

| 参数 | 说明 |
|------|------|
| `extend_system_message` | 在默认 system prompt 后追加内容 |
| `override_system_message` | 完全替换默认 system prompt |
| `sensitive_data` | 敏感数据字典，供谨慎处理 |
| `available_file_paths` | Agent 可访问的文件路径列表 |
| `save_conversation_path` | 保存完整对话历史的路径 |

### 5.4 性能与限制

| 参数 | 默认 | 说明 |
|------|------|------|
| `directly_open_url` | `True` | 任务中检测到 URL 时是否直接打开 |
| `step_timeout` | `120` | 单步超时（秒） |
| `llm_timeout` | `90` | LLM 调用超时（秒） |
| `max_history_items` | `None` | 保留在记忆中的最近步数，`None` 表示全部保留 |

### 5.5 环境变量（超时类）

通过环境变量可调各类超时，便于调试或弱网/复杂页：

- 导航：`TIMEOUT_NavigateToUrlEvent`（默认 15）
- 点击/输入/滚动：`TIMEOUT_ClickElementEvent`、`TIMEOUT_TypeTextEvent`、`TIMEOUT_ScrollEvent` 等
- 浏览器状态：`TIMEOUT_BrowserStateRequestEvent`（默认 30）
- 标签页、存储、下载等均有对应 `TIMEOUT_*` 变量

---

## 6. 内置工具（Available Tools）

| 类别 | 工具 | 说明 |
|------|------|------|
| 导航与控制 | `navigate` | 打开 URL |
| | `go_back` | 后退 |
| | `search` | 搜索（DuckDuckGo / Google / Bing） |
| | `wait` | 等待指定秒数 |
| 页面交互 | `click` | 按元素索引点击 |
| | `input` | 在表单字段输入 |
| | `send_keys` | 发送特殊键（Enter、Escape 等） |
| | `scroll` | 上下滚动 |
| | `find_text` | 滚动到指定文本 |
| | `upload_file` | 上传文件 |
| 表单 | `select_dropdown` | 选择下拉项 |
| | `dropdown_options` | 获取下拉选项值 |
| 标签页 | `switch` | 切换标签 |
| | `close` | 关闭标签 |
| 内容提取 | **`extract`** | 用 LLM 从页面按需提取数据（核心差异化） |
| 视觉 | `screenshot` | 请求下一状态带截图（按需使用） |
| 脚本 | `evaluate` | 在页面执行 JavaScript（含 shadow DOM、自定义选择器等） |
| 文件 | `read_file` / `write_file` / `replace_file` | 读写与替换文件内容 |
| 结束 | `done` | 标记任务完成 |

---

## 7. 自定义工具（Add Tools）

通过 `Tools()` + `@tools.action()` 注册自定义函数，Agent 会根据描述与参数类型自动调用。

**基本写法**：

```python
from browser_use import Tools, Agent, ActionResult

tools = Tools()

@tools.action(description='向用户提问并等待回答')
async def ask_human(question: str) -> ActionResult:
    answer = input(f'{question} > ')
    return ActionResult(extracted_content=f'用户回答: {answer}')

agent = Agent(task='...', llm=llm, tools=tools)
```

**要点**：

- `description` 必填，供 LLM 决定何时调用。
- `allowed_domains` 可限制工具仅在指定域名下可用（如 `['https://mybank.com']`）。
- 参数名必须与注入对象**完全一致**，例如要用 **`browser_session: BrowserSession`**（不能用 `browser: Browser`）。
- 可注入对象：`browser_session`、`page_extraction_llm`、`file_system`、`cdp_client`、`available_file_paths`、`has_sensitive_data` 等（见官方 Available Objects 列表）。
- 支持 Pydantic 模型作为参数，便于复杂结构化输入。
- 推荐返回 `ActionResult(extracted_content=...)`，便于主 Agent 利用结果。

**与页面交互示例**（通过 `browser_session` 用 CSS 选择器）：

```python
@tools.action(description='用 CSS 选择器点击提交按钮')
async def click_submit_button(browser_session: BrowserSession):
    page = await browser_session.must_get_current_page()
    elements = await page.get_elements_by_css_selector('button[type="submit"]')
    if not elements:
        return ActionResult(extracted_content='未找到提交按钮')
    await elements[0].click()
    return ActionResult(extracted_content='已点击提交按钮')
```

`Page` 上还有 `get_element_by_prompt` / `get_elements_by_css_selector`，`Element` 有 `get_text()`、`type()`、`click()` 等（详见 `browser_use/actor/element.py`）。

---

## 8. 浏览器配置（Browser）

```python
from browser_use import Agent, Browser, ChatBrowserUse

browser = Browser(
    headless=False,       # 是否无头
    window_size={'width': 1000, 'height': 700},  # 视口
    # 还可传 Playwright 等启动参数
)
agent = Agent(task='...', browser=browser, llm=ChatBrowserUse())
```

可配置项包括无头模式、视口大小、代理等，与 Playwright 的 launch 选项兼容。

---

## 9. 生命周期钩子（Lifecycle Hooks）

在 `agent.run()` 时传入：

- **`on_step_start`**：每步开始前（Agent 尚未根据当前状态决策）。
- **`on_step_end`**：每步结束后（本步动作已执行完，下一步尚未开始）。

钩子为 `async (agent) -> None`，可访问例如：

- `agent.browser_session`：当前标签、URL、CDP 等；
- `agent.history`：`urls()`、`extracted_content()`、`model_actions()`、`model_thoughts()` 等；
- `agent.state`、`agent.settings`、`agent.task`；
- `agent.pause()` / `agent.resume()`、`agent.add_new_task(...)`。

适合做日志、存页面 HTML/截图、在特定 URL 暂停等人机协同逻辑。若逻辑较复杂，官方建议优先考虑用**自定义工具**实现。

---

## 10. 生产部署与 Cloud Sandbox

- **本地/自托管**：安装后直接 `Agent` + `llm` + 可选 `browser`/`tools` 即可 `agent.run()`。
- **Browser Use Cloud**：通过 `@sandbox(cloud_profile_id='...')` 将函数跑在云端沙箱，由官方处理浏览器、持久化、认证、Cookie、LLM 等；并支持将本地浏览器配置（如 Cookie）同步到 Cloud，便于已登录场景。
- 示例：

```python
from browser_use import Browser, sandbox, ChatBrowserUse
from browser_use.agent.service import Agent

@sandbox(cloud_profile_id='your-profile-id')
async def production_task(browser: Browser):
    agent = Agent(task='...', browser=browser, llm=ChatBrowserUse())
    await agent.run()
```

---

## 11. 与当前项目（Skyvern 风格）的对比与启发

| 维度 | Browser-Use | 当前项目（Skyvern 风格） | 可借鉴点 |
|------|-------------|---------------------------|----------|
| **主输入** | DOM 快照为主，截图按需 | 每步截图 + 红框编号（视觉驱动） | 引入「精简 DOM/可访问性树」作补充，减少误点、支持隐藏元素 |
| **页面内容** | `extract` 按问题抽取片段 | `get_page_html` 整页或选择器 | 新增「按需提取」工具（如按问题/选择器取文本），省 token、提速 |
| **截图策略** | 多数步仅 DOM，需要再截图 | 每步都截图 | 在能靠 DOM/selector 决策的步骤尝试「跳过截图」，失败再回退到截图 |
| **Prompt 结构** | 历史在前、当前状态在后（利于 KV cache） | 需确认当前实现 | 固定「历史 + 当前状态」顺序，便于后续缓存与长上下文优化 |
| **输出 token** | 极简 action 空间（10–15 token/步） | 工具名与参数较长 | 评估是否可缩短工具描述与参数名，降低输出成本 |
| **扩展** | `Tools()` + `@tools.action()` + 注入对象 | 计划中的插件系统 | 参考其「描述 + 参数名注入 + 域名限制」设计插件 API |

**建议落地顺序**：

1. **按需提取**：实现类似 `extract` 的「用自然语言/简单查询从当前页取一段文本」工具。  
2. **精简 DOM/可访问性**：在关键步骤或失败重试时，附带一份 downsampling 后的可交互元素列表（role/name/index），与截图一起给 LLM。  
3. **可选截图**：在 runner 中支持「本步仅用 DOM/selector，不截图」，仅在需要或失败时再截图。  
4. **Prompt 顺序与插件**：统一历史与状态的顺序；设计插件时参考 Browser-Use 的 tools 注册与注入约定。

---

## 12. 参考链接

- 官方文档索引：https://docs.browser-use.com/llms.txt  
- 快速开始：https://docs.browser-use.com/open-source/quickstart  
- 支持的模型：https://docs.browser-use.com/open-source/supported-models  
- Agent 全部参数：https://docs.browser-use.com/customize/agent/all-parameters  
- 内置工具：https://docs.browser-use.com/customize/tools/available  
- 自定义工具：https://docs.browser-use.com/customize/tools/add  
- 生命周期钩子：https://docs.browser-use.com/customize/hooks  
- 速度与优化博客：https://browser-use.com/posts/speed-matters  
- GitHub：https://github.com/browser-use/browser-use  

---

*文档最后更新：依据 2025–2026 年官方文档与博客整理，具体 API 以官方最新文档为准。*
