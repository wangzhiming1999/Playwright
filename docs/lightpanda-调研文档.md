# Lightpanda 调研文档

> 基于官方文档、博客与 agent-browser 集成资料整理，便于与当前 Playwright + Chromium Agent 项目对比与借鉴。  
> 官网：https://lightpanda.io · 文档：https://lightpanda.io/docs · 仓库：https://github.com/lightpanda-io/browser

---

## 1. 项目概述

**Lightpanda** 是一个**为机器和 AI 从头打造的无头浏览器引擎**（Zig 实现，非 Chrome/WebKit 分支），面向网页自动化、爬虫与 AI Agent 工作流。

| 项目 | 说明 |
|------|------|
| 仓库 | [lightpanda-io/browser](https://github.com/lightpanda-io/browser) |
| 星标 | 20k+ |
| 协议 | AGPL-3.0 |
| 云服务 | cloud.lightpanda.io（支持 Lightpanda 与 Chrome 双后端） |

**核心定位**：专为「无头、自动化、AI Agent」设计，无渲染开销，通过 **Chrome DevTools Protocol (CDP)** 与 Playwright / Puppeteer 兼容，可作为 Chromium 的轻量替代引擎。

---

## 2. 核心能力与架构

### 2.1 性能指标（官方口径）

| 维度 | 说明 |
|------|------|
| 执行速度 | 约 **10×** 比 Chrome 无头更快 |
| 内存占用 | 约 **10×** 更少（约 24MB vs 207MB 峰值） |
| 冷启动 | **&lt;100ms**，可嵌入 |

适合在资源受限的 CI、高并发自动化、云端批量任务中替代 Chrome。

### 2.2 技术架构

| 层级 | 技术选型 |
|------|----------|
| 语言 / DOM | Zig（系统层）+ 自研 DOM（zigdom） |
| JavaScript | V8，完整 Web API 支持 |
| 网络 | libcurl（HTTP/TLS） |
| 协议 | Chrome DevTools Protocol (CDP) over WebSocket |
| 部署 | 单二进制、单进程、多线程；支持多客户端并发 CDP 连接 |

### 2.3 主要能力

- **CDP 兼容**：与 Playwright、Puppeteer、chromedp 等通过 `connectOverCDP()` / `puppeteer.connect()` 连接。
- **请求拦截**：通过 CDP 暂停、修改、mock 或屏蔽 HTTP 请求。
- **robots.txt**：可选遵守，便于合规爬取。
- **多客户端**：单进程内支持多个并发 CDP 连接。

### 2.4 与 Chrome 的差异（agent-browser 文档）

| 能力 | Lightpanda 支持情况 |
|------|---------------------|
| 扩展 (`--extension`) | 不支持 |
| 持久 Profile (`--profile`) | 不支持 |
| Storage state (`--state`) | 不支持 |
| 本地文件访问 (`--allow-file-access`) | 不支持 |
| Headed 模式 | 不适用（仅无头） |
| 截图 | 依赖 Lightpanda CDP 实现 |

需要完整浏览器行为、扩展或用户 Profile 时，应继续使用 Chrome。

---

## 3. 与 Playwright 的集成方式

**连接方式与现有 CDP 模式一致**，无需改 Agent 逻辑：

```javascript
// Playwright 连接本地 Lightpanda
const browser = await chromium.connectOverCDP('ws://localhost:9222');
const page = await browser.newPage();
await page.goto('https://example.com');
```

```javascript
// Puppeteer 连接 Lightpanda Cloud
const browser = await puppeteer.connect({
  browserWSEndpoint: 'wss://cloud.lightpanda.io/ws?token=YOUR_TOKEN',
});
```

当前项目已支持 `browser_mode == "cdp"` 并通过 `pw.chromium.connect_over_cdp(cdp_url)` 连接，因此**只需在「CDP 模式」下指向 Lightpanda 的 endpoint**（本地或云端），即可无缝切换引擎。

---

## 4. 适用场景（官方建议）

| 场景 | 建议 |
|------|------|
| 高并发并行自动化 | 适合用 Lightpanda |
| CI/CD 资源受限 | 适合用 Lightpanda |
| AI Agent 工作流（速度与内存敏感） | 适合用 Lightpanda |
| 快速爬虫 / 数据抽取 | 适合用 Lightpanda |
| 需要完整浏览器保真度、扩展、持久 Profile | 用 Chrome |

---

## 5. 对本项目的启发与建议

### 5.1 多引擎架构：增加 Lightpanda 可选引擎

- **现状**：项目已有 `builtin`（内置 Chromium）、`cdp`、`user_chrome` 三种浏览器模式。
- **建议**：新增一种 **「Lightpanda」模式**（或通过环境变量/配置在 CDP 模式下选择引擎）：
  - **本地**：用户自行启动 Lightpanda 并暴露 CDP（如 `ws://localhost:9222`），用现有 `connect_over_cdp` 连接。
  - **云端**：CDP URL 配置为 `wss://cloud.lightpanda.io/ws?token=...`。
- **注意**：在文档或配置中标明 Lightpanda 不支持扩展、Profile、storage state 等，避免误用。

### 5.2 成本与规模

- 项目已有 **CostTracker** 统计 LLM token 与成本；Lightpanda 主要降低**浏览器侧**成本：
  - 单实例内存与启动时间更小 → 同机可跑更多并发 Agent（如并行 E2E 场景）。
- 可在文档或报告中区分：**总成本 = LLM 成本（CostTracker）+ 机器/并发成本**，并说明在选用 Lightpanda 时对后者的优化效果。

### 5.3 引擎与 Agent 解耦（参考 agent-browser）

- agent-browser 通过 `--engine lightpanda` 切换引擎，同一套命令（snapshot、click、fill、screenshot）走同一 CDP 路径。
- **启发**：将「启动/连接浏览器、获得 Page」抽象为一层，下层可接 Chromium / 用户 Chrome / Lightpanda；并维护**引擎 × 能力矩阵**（扩展、Profile、截图等），在配置或 UI 中标明当前引擎支持项。

### 5.4 合规与稳定性

- **robots.txt**：若任务涉及爬取或批量访问外站，可考虑在导航前增加可选的 robots.txt 检查。
- **请求拦截**：Lightpanda 支持通过 CDP 拦截请求；可用于屏蔽广告/统计、注入 mock、节流，提升 E2E 稳定性。项目已有 `_active_requests` 与网络等待逻辑，可在此基础上增加可选的「拦截规则」配置（如 CDP Fetch.enable）。

### 5.5 E2E / CI

- 现有 **E2ERunner + SCENARIOS** 在 CI 或本地批量跑时，可选用 Lightpanda 引擎以减少内存、加快启动，便于**并行更多场景**。
- 在 `conftest.py` 或运行脚本中通过环境变量选择引擎（如 `BROWSER_ENGINE=lightpanda`），实现同一套用例、双引擎（Chrome / Lightpanda）的对比或降本。

### 5.6 最小可行落地

- 在 `runner.py` 的浏览器模式分支中增加 **通过 CDP 连接 Lightpanda** 的选项（用户自启 Lightpanda 或 subprocess 启动），并标注为「实验性 / 无扩展无 Profile」。
- 先验证 Playwright + Lightpanda 在本项目栈上的兼容性（标注、截图、点击、输入等），再考虑文档化「何时用 Lightpanda / 何时用 Chrome」。

---

## 6. 参考链接

| 类型 | 链接 |
|------|------|
| 官网 | https://lightpanda.io |
| 文档索引 | https://lightpanda.io/docs |
| 文档全文（LLM） | https://lightpanda.io/docs/llms-full.txt |
| 博客全文（LLM） | https://lightpanda.io/blog/llms-full.txt |
| GitHub 仓库 | https://github.com/lightpanda-io/browser |
| Agent Skill（Openclaw 等） | https://github.com/lightpanda-io/agent-skill |
| agent-browser 集成说明 | https://agent-browser.dev/engines/lightpanda |
| npm 包 | https://www.npmjs.com/package/@lightpanda/browser |

---

## 7. 与 Browser-Use 调研的对照

| 维度 | Browser-Use | Lightpanda |
|------|-------------|------------|
| 层级 | Agent 框架（DOM 快照 + 可选截图） | 浏览器引擎（CDP 兼容） |
| 与本项目关系 | 可借鉴 DOM/快照、extract、按需截图等设计 | 可作 Playwright 的轻量引擎替代 |
| 落地 | 若引入 DOM 快照需做 downsampling 与 token 控制 | 增加 CDP 连接模式即可试验，不改 Agent 逻辑 |

两者互补：Browser-Use 偏「如何把页面状态交给 LLM」；Lightpanda 偏「用更轻的浏览器跑同一套自动化」。
