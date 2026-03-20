"""
Accessibility Tree 提取：轻量级页面结构表示。

用 ~500-1000 tokens 的纯文本描述页面结构，替代截图（~1100 tokens）。
仅在关键时刻（首步、导航后、视觉定位）才发截图，其余步骤用 a11y tree。

设计参考 Browser-Use 的 DOM 提取策略：
- 只提取可交互元素和关键文本节点
- 树形缩进表示层级关系
- 每个元素一行，包含 index、类型、文本
"""

# JS: 提取 Accessibility Tree 的精简表示
# MAX_LINES 由 Python 侧通过参数传入
_A11Y_TREE_JS_TEMPLATE = """(maxLines) => {
    const INTERACTIVE_TAGS = new Set([
        'input', 'textarea', 'select', 'button', 'a', 'details', 'summary'
    ]);
    const INTERACTIVE_ROLES = new Set([
        'button', 'link', 'tab', 'switch', 'checkbox', 'radio',
        'menuitem', 'option', 'combobox', 'textbox', 'searchbox',
        'slider', 'spinbutton', 'listbox', 'menu', 'menubar',
        'tablist', 'tree', 'treeitem', 'dialog'
    ]);

    const lines = [];
    const MAX_LINES = maxLines || 150;
    let lineCount = 0;

    // 获取页面基本信息
    lines.push('Page: ' + document.title);
    lines.push('URL: ' + location.href);
    lines.push('---');
    lineCount += 3;

    const vMid = window.innerHeight / 2;

    function isVisible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) return false;
        if (rect.bottom < -300 || rect.top > window.innerHeight + 300) return false;
        if (rect.right < 0 || rect.left > window.innerWidth) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity) < 0.1) return false;
        return true;
    }

    function getNodeText(el) {
        // 只取直接文本子节点，不递归
        let text = '';
        for (const child of el.childNodes) {
            if (child.nodeType === 3) { // TEXT_NODE
                text += child.textContent.trim() + ' ';
            }
        }
        return text.trim().substring(0, 80);
    }

    function getPositionMark(el) {
        try {
            const rect = el.getBoundingClientRect();
            if (rect.top < vMid * 0.4) return ' ↑';
            if (rect.top > vMid * 1.6) return ' ↓';
        } catch(e) {}
        return '';
    }

    function isInteractive(el) {
        const tag = el.tagName.toLowerCase();
        if (tag === 'input' && el.type === 'hidden') return false;
        if (['script', 'style', 'noscript', 'meta', 'head', 'svg'].includes(tag)) return false;
        if (el.getAttribute('aria-hidden') === 'true') return false;
        if (INTERACTIVE_TAGS.has(tag)) return true;
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (INTERACTIVE_ROLES.has(role)) return true;
        if (el.isContentEditable) return true;
        if (el.hasAttribute('onclick') || el.hasAttribute('ng-click') ||
            el.hasAttribute('@click') || el.hasAttribute('v-on:click')) return true;
        if (el.hasAttribute('tabindex') && el.tabIndex >= 0) return true;
        try {
            if (window.getComputedStyle(el).cursor === 'pointer') {
                const rect = el.getBoundingClientRect();
                if (rect.width < window.innerWidth * 0.8) return true;
            }
        } catch(e) {}
        return false;
    }

    // 判断是否是有意义的文本容器（标题、段落等）
    function isTextLandmark(el) {
        const tag = el.tagName.toLowerCase();
        if (['h1','h2','h3','h4','h5','h6','p','label','legend','caption','figcaption'].includes(tag)) return true;
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (['heading', 'banner', 'navigation', 'main', 'complementary', 'contentinfo', 'alert', 'status'].includes(role)) return true;
        return false;
    }

    // 判断是否是结构容器（nav, main, form 等）
    function isStructural(el) {
        const tag = el.tagName.toLowerCase();
        return ['nav', 'main', 'header', 'footer', 'form', 'section', 'article', 'aside', 'dialog'].includes(tag);
    }

    function walk(el, depth) {
        if (lineCount >= MAX_LINES) return;
        if (!el || el.nodeType !== 1) return;

        const tag = el.tagName.toLowerCase();
        if (['script', 'style', 'noscript', 'meta', 'head', 'svg', 'path'].includes(tag)) return;
        if (!isVisible(el)) return;

        const indent = '  '.repeat(Math.min(depth, 6));
        const skyId = el.getAttribute('data-skyvern-id');
        const interactive = isInteractive(el);
        const textLandmark = isTextLandmark(el);
        const structural = isStructural(el);

        if (interactive && skyId !== null) {
            // 可交互元素：显示 index + 类型 + 关键属性
            let desc = `${indent}[${skyId}] <${tag}`;
            if (el.type && el.type !== tag) desc += ` type="${el.type}"`;
            if (el.name) desc += ` name="${el.name}"`;
            const role = el.getAttribute('role');
            if (role) desc += ` role="${role}"`;
            desc += '>';

            // 值/文本
            const text = getNodeText(el) || el.value || el.placeholder || el.getAttribute('aria-label') || '';
            if (text) desc += ' ' + text.substring(0, 60);
            if (el.disabled) desc += ' [disabled]';
            if (tag === 'input' && el.checked) desc += ' [checked]';
            desc += getPositionMark(el);

            lines.push(desc);
            lineCount++;
        } else if (textLandmark) {
            // 文本地标：标题、段落等
            const text = getNodeText(el);
            if (text) {
                lines.push(`${indent}<${tag}> ${text}`);
                lineCount++;
            }
        } else if (structural) {
            // 结构容器：只输出标签
            const ariaLabel = el.getAttribute('aria-label');
            let desc = `${indent}<${tag}`;
            if (ariaLabel) desc += ` "${ariaLabel}"`;
            desc += '>';
            lines.push(desc);
            lineCount++;
        }

        // 递归子元素
        for (const child of el.children) {
            if (lineCount >= MAX_LINES) break;
            walk(child, depth + (structural || textLandmark ? 1 : 0));
        }
    }

    walk(document.body, 0);

    if (lineCount >= MAX_LINES) {
        lines.push('... (truncated)');
    }

    return lines.join('\\n');
}"""


# 保持向后兼容的旧变量名
_A11Y_TREE_JS = _A11Y_TREE_JS_TEMPLATE


# JS: 提取页面摘要信息（用于判断是否需要截图）
_PAGE_SUMMARY_JS = """() => {
    const title = document.title || '';
    const url = location.href;
    const bodyText = (document.body.innerText || '').substring(0, 200);
    const forms = document.querySelectorAll('form').length;
    const inputs = document.querySelectorAll('input:not([type=hidden]), textarea, select').length;
    const images = document.querySelectorAll('img').length;
    const links = document.querySelectorAll('a[href]').length;
    const buttons = document.querySelectorAll('button, [role=button], input[type=submit]').length;
    const hasDialog = !!document.querySelector('dialog[open], [role=dialog], .modal.show, .modal.active');
    const hasCaptcha = !!(
        document.querySelector('[class*=captcha], [id*=captcha], [class*=recaptcha], iframe[src*=captcha]') ||
        document.body.innerHTML.toLowerCase().includes('captcha')
    );
    const scrollHeight = document.body.scrollHeight;
    const viewportHeight = window.innerHeight;

    return {
        title, url,
        text_preview: bodyText,
        forms, inputs, images, links, buttons,
        has_dialog: hasDialog,
        has_captcha: hasCaptcha,
        scroll_ratio: Math.round(scrollHeight / viewportHeight * 10) / 10,
        element_count: document.querySelectorAll('*').length,
    };
}"""


async def extract_a11y_tree(page, max_lines: int = 150) -> str:
    """
    提取页面的 Accessibility Tree 文本表示。
    返回纯文本字符串，约 500-1500 tokens。
    max_lines: 动态行数上限（简单页面 100 / 默认 150 / 复杂页面 250）
    """
    try:
        tree = await page.evaluate(_A11Y_TREE_JS_TEMPLATE, max_lines)
        return tree or "Page: (empty)\nURL: about:blank"
    except Exception as e:
        return f"Page: (error extracting a11y tree: {e})\nURL: {page.url}"


async def get_page_summary(page) -> dict:
    """
    获取页面摘要信息，用于判断是否需要截图。
    """
    try:
        return await page.evaluate(_PAGE_SUMMARY_JS)
    except Exception:
        return {"title": "", "url": page.url, "element_count": 0}


def should_use_screenshot(
    step: int,
    last_tool: str | None,
    page_summary: dict,
    consecutive_dom_steps: int,
) -> bool:
    """
    判断当前步骤是否需要截图（而非仅用 a11y tree）。

    截图时机：
    1. 首步（需要视觉理解页面布局）
    2. 导航/页面跳转后（新页面需要视觉理解）
    3. 检测到验证码/弹窗
    4. 连续 N 步纯 DOM 后（防止视觉信息丢失太久）
    5. 页面有大量图片（可能需要视觉定位）
    6. 执行 find_element / save_element / solve_captcha 后（视觉操作）
    """
    # 1. 首步
    if step == 0:
        return True

    # 2. 导航后
    if last_tool in ("navigate", "switch_tab", "switch_iframe"):
        return True

    # 3. 验证码/弹窗
    if page_summary.get("has_captcha") or page_summary.get("has_dialog"):
        return True

    # 4. 连续 DOM 步骤过多（每 6 步截一次图）
    if consecutive_dom_steps >= 6:
        return True

    # 5. 视觉操作工具
    if last_tool in ("find_element", "save_element", "solve_captcha", "screenshot", "drag_drop"):
        return True

    # 6. 页面图片较多（可能需要视觉定位）
    if page_summary.get("images", 0) > 10:
        return True

    return False


# JS: 提取页面布局摘要（nav/header/footer/main 的位置分布）
_LAYOUT_SUMMARY_JS = """() => {
    const vh = window.innerHeight;
    const regions = { top: [], middle: [], bottom: [] };
    const selectors = 'nav, header, footer, main, [role="banner"], [role="navigation"], [role="main"], [role="contentinfo"]';
    document.querySelectorAll(selectors).forEach(el => {
        const r = el.getBoundingClientRect();
        if (r.width < 10 || r.height < 10) return;
        const tag = el.tagName.toLowerCase();
        const label = el.getAttribute('aria-label') || '';
        const region = r.top < vh * 0.2 ? 'top' : r.top > vh * 0.7 ? 'bottom' : 'middle';
        regions[region].push(tag + (label ? '(' + label.substring(0, 30) + ')' : ''));
    });
    const parts = [];
    if (regions.top.length) parts.push('top=[' + regions.top.join(',') + ']');
    if (regions.middle.length) parts.push('mid=[' + regions.middle.join(',') + ']');
    if (regions.bottom.length) parts.push('bot=[' + regions.bottom.join(',') + ']');
    return parts.length ? 'Layout: ' + parts.join(' ') : '';
}"""


async def get_layout_summary(page) -> str:
    """
    提取页面布局摘要：nav/header/footer/main 的位置分布。
    返回约 50 tokens 的布局描述，用于 DOM 模式下帮助 LLM 理解页面结构。
    """
    try:
        result = await page.evaluate(_LAYOUT_SUMMARY_JS)
        return result or ""
    except Exception:
        return ""
