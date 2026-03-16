"""
页面元素标注与定位系统

核心设计：
1. 给每个可交互元素打上 data-skyvern-id 属性
2. 绘制 bounding box + 编号标签（带避让算法）
3. 构建 CSS selector 映射，支持多级回退定位
4. annotate 和 execute 共享同一份元素表，消除 DOM 查询不一致

参考 Skyvern 的 domUtils.js 设计，增强了：
- 交互元素检测（cursor:pointer、role 属性、contenteditable、Angular/React 组件）
- 标签避让算法（检测重叠，自动调整位置）
- 元素定位回退链（data-skyvern-id → CSS selector → XPath → 坐标）
"""

import asyncio
import base64


# ── JS: 交互元素检测 + 标注 ──────────────────────────────────────────────────

_ANNOTATE_JS = """() => {
    // 清理上一轮的标注和 ID
    document.querySelectorAll('.skyvern-label').forEach(el => el.remove());
    document.querySelectorAll('[data-skyvern-id]').forEach(el => {
        el.removeAttribute('data-skyvern-id');
    });

    // ── 交互性判断（参考 Skyvern isInteractable） ──

    const INTERACTIVE_TAGS = new Set([
        'input', 'textarea', 'select', 'button', 'details', 'summary'
    ]);

    const INTERACTIVE_ROLES = new Set([
        'button', 'link', 'tab', 'switch', 'checkbox', 'radio',
        'menuitem', 'option', 'combobox', 'textbox', 'searchbox',
        'slider', 'spinbutton', 'listbox', 'menu', 'menubar',
        'tablist', 'tree', 'treeitem', 'gridcell', 'dialog'
    ]);

    const EVENT_ATTRS = [
        'onclick', 'ng-click', 'v-on:click', '@click',
        'jsaction', 'data-action', 'data-onclick'
    ];

    function isVisible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) return false;
        if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
        if (rect.right < 0 || rect.left > window.innerWidth) return false;

        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity) < 0.1) return false;
        return true;
    }

    function isInteractable(el) {
        const tag = el.tagName.toLowerCase();

        // 排除 hidden input 和 script/style
        if (tag === 'input' && el.type === 'hidden') return false;
        if (['script', 'style', 'noscript', 'meta', 'head'].includes(tag)) return false;

        // 1. 原生交互标签
        if (INTERACTIVE_TAGS.has(tag)) return true;

        // 2. 带 href 的链接
        if (tag === 'a' && el.hasAttribute('href')) return true;

        // 3. ARIA role
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (INTERACTIVE_ROLES.has(role)) return true;

        // 4. contenteditable
        if (el.isContentEditable) return true;

        // 5. 事件属性
        for (const attr of EVENT_ATTRS) {
            if (el.hasAttribute(attr)) return true;
        }

        // 6. tabindex（可聚焦）
        if (el.hasAttribute('tabindex') && el.tabIndex >= 0) return true;

        // 7. cursor:pointer（样式暗示可点击）
        try {
            const style = window.getComputedStyle(el);
            if (style.cursor === 'pointer') {
                // 排除大容器（宽度超过视口 80% 的通常是布局元素）
                const rect = el.getBoundingClientRect();
                if (rect.width < window.innerWidth * 0.8) return true;
            }
        } catch(e) {}

        return false;
    }

    // ── 收集所有可交互元素 ──

    const seen = new Set();
    const interactables = [];

    // 递归遍历 DOM（包括 Shadow DOM）
    function walk(root) {
        const walker = document.createTreeWalker(
            root, NodeFilter.SHOW_ELEMENT, null
        );
        let node = walker.currentNode;
        while (node) {
            if (!seen.has(node) && isInteractable(node) && isVisible(node)) {
                seen.add(node);
                interactables.push(node);
            }
            // 进入 Shadow DOM
            if (node.shadowRoot) {
                walk(node.shadowRoot);
            }
            node = walker.nextNode();
        }
    }
    walk(document.body);

    // 按 DOM 顺序排序（从上到下、从左到右）
    interactables.sort((a, b) => {
        const ra = a.getBoundingClientRect();
        const rb = b.getBoundingClientRect();
        if (Math.abs(ra.top - rb.top) > 10) return ra.top - rb.top;
        return ra.left - rb.left;
    });

    // ── 标注 + 构建元素信息 ──

    const elements = [];
    const labelRects = [];  // 已放置的标签矩形，用于避让

    // 构建 CSS selector（用于回退定位）
    function buildSelector(el) {
        // 优先用 data-skyvern-id
        const skyId = el.getAttribute('data-skyvern-id');
        if (skyId !== null) return '[data-skyvern-id="' + skyId + '"]';

        // 用 id
        if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) {
            return '#' + CSS.escape(el.id);
        }

        // 用 name + tag
        if (el.name) {
            const sel = el.tagName.toLowerCase() + '[name="' + CSS.escape(el.name) + '"]';
            if (document.querySelectorAll(sel).length === 1) return sel;
        }

        // 用 aria-label + tag
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel) {
            const sel = el.tagName.toLowerCase() + '[aria-label="' + CSS.escape(ariaLabel) + '"]';
            if (document.querySelectorAll(sel).length === 1) return sel;
        }

        return null;
    }

    // 构建 XPath
    function buildXPath(el) {
        const parts = [];
        let current = el;
        while (current && current !== document.body && current !== document.documentElement) {
            let tag = current.tagName.toLowerCase();
            let idx = 1;
            let sibling = current.previousElementSibling;
            while (sibling) {
                if (sibling.tagName.toLowerCase() === tag) idx++;
                sibling = sibling.previousElementSibling;
            }
            parts.unshift(tag + '[' + idx + ']');
            current = current.parentElement;
        }
        return '//' + parts.join('/');
    }

    // 标签避让：找到不重叠的位置
    function findLabelPosition(rect, labelW, labelH) {
        // 候选位置：右上角、左上角、右下角、左下角、正上方居中
        const candidates = [
            { x: Math.min(rect.right - labelW, window.innerWidth - labelW - 2), y: Math.max(rect.top - labelH - 2, 2) },
            { x: Math.max(rect.left, 2), y: Math.max(rect.top - labelH - 2, 2) },
            { x: Math.min(rect.right - labelW, window.innerWidth - labelW - 2), y: Math.min(rect.bottom + 2, window.innerHeight - labelH - 2) },
            { x: Math.max(rect.left, 2), y: Math.min(rect.bottom + 2, window.innerHeight - labelH - 2) },
            { x: Math.max(rect.left + (rect.width - labelW) / 2, 2), y: Math.max(rect.top - labelH - 2, 2) },
        ];

        for (const pos of candidates) {
            const newRect = { left: pos.x, top: pos.y, right: pos.x + labelW, bottom: pos.y + labelH };
            let overlaps = false;
            for (const existing of labelRects) {
                if (!(newRect.right < existing.left || newRect.left > existing.right ||
                      newRect.bottom < existing.top || newRect.top > existing.bottom)) {
                    overlaps = true;
                    break;
                }
            }
            if (!overlaps) {
                labelRects.push(newRect);
                return pos;
            }
        }

        // 所有候选都重叠，用第一个（右上角）
        const fallback = candidates[0];
        labelRects.push({ left: fallback.x, top: fallback.y, right: fallback.x + labelW, bottom: fallback.y + labelH });
        return fallback;
    }

    interactables.forEach((el, index) => {
        const rect = el.getBoundingClientRect();

        // 打上稳定 ID
        el.setAttribute('data-skyvern-id', String(index));

        // 蓝色边框（参考 Skyvern 风格）
        const box = document.createElement('div');
        box.className = 'skyvern-label';
        box.style.cssText = `
            position: fixed;
            left: ${rect.left}px; top: ${rect.top}px;
            width: ${rect.width}px; height: ${rect.height}px;
            border: 2px solid rgba(30, 100, 255, 0.7);
            pointer-events: none;
            z-index: 2147483646;
            box-sizing: border-box;
            border-radius: 2px;
        `;

        // 编号标签（带避让）
        const labelText = String(index);
        const labelW = Math.max(18, labelText.length * 8 + 8);
        const labelH = 18;
        const pos = findLabelPosition(rect, labelW, labelH);

        const label = document.createElement('div');
        label.className = 'skyvern-label';
        label.textContent = index;
        label.style.cssText = `
            position: fixed;
            left: ${pos.x}px; top: ${pos.y}px;
            background: rgba(30, 100, 255, 0.85);
            color: white;
            padding: 1px 4px;
            font-size: 11px;
            font-weight: bold;
            border-radius: 3px;
            pointer-events: none;
            z-index: 2147483647;
            font-family: monospace;
            line-height: 16px;
            min-width: 16px;
            text-align: center;
            white-space: nowrap;
        `;

        document.body.appendChild(box);
        document.body.appendChild(label);

        // 构建元素信息
        const tag = el.tagName.toLowerCase();
        const info = {
            index: index,
            tag: tag,
            type: el.type || '',
            text: (el.textContent || el.value || '').trim().substring(0, 50),
            placeholder: el.placeholder || el.getAttribute('data-placeholder') || '',
            name: el.name || '',
            id: el.id || '',
            href: (tag === 'a' ? el.href : '') || '',
            aria_label: el.getAttribute('aria-label') || '',
            role: el.getAttribute('role') || '',
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
            w: Math.round(rect.width),
            h: Math.round(rect.height),
            css_selector: buildSelector(el),
            xpath: buildXPath(el),
        };

        elements.push(info);
    });

    return elements;
}"""


# ── JS: 元素定位回退链 ──────────────────────────────────────────────────────

_LOCATE_JS = """(skyvernId) => {
    // 1. data-skyvern-id 精确定位
    let el = document.querySelector('[data-skyvern-id="' + skyvernId + '"]');
    if (el) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0) {
            return {
                found: true, method: 'skyvern-id',
                x: Math.round(r.left + r.width / 2),
                y: Math.round(r.top + r.height / 2),
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                placeholder: el.placeholder || '',
            };
        }
    }
    return { found: false, method: 'none' };
}"""

_LOCATE_BY_CSS_JS = """(cssSelector) => {
    try {
        const el = document.querySelector(cssSelector);
        if (!el) return { found: false, method: 'css-not-found' };
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return { found: false, method: 'css-invisible' };
        return {
            found: true, method: 'css',
            x: Math.round(r.left + r.width / 2),
            y: Math.round(r.top + r.height / 2),
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            placeholder: el.placeholder || '',
        };
    } catch(e) {
        return { found: false, method: 'css-error', error: e.message };
    }
}"""

_LOCATE_BY_XPATH_JS = """(xpath) => {
    try {
        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
        const el = result.singleNodeValue;
        if (!el) return { found: false, method: 'xpath-not-found' };
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return { found: false, method: 'xpath-invisible' };
        return {
            found: true, method: 'xpath',
            x: Math.round(r.left + r.width / 2),
            y: Math.round(r.top + r.height / 2),
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            placeholder: el.placeholder || '',
        };
    } catch(e) {
        return { found: false, method: 'xpath-error', error: e.message };
    }
}"""


# ── 缓存：上一次标注的元素映射 ──────────────────────────────────────────────

_last_elements: list[dict] = []


# ── 公开 API ─────────────────────────────────────────────────────────────────

async def annotate_page(page):
    """
    在页面上给所有可交互元素打标签（蓝框 + 编号，带避让算法）
    返回: (标注后的截图 base64, 元素列表)

    元素列表每项包含：
      index, tag, type, text, placeholder, name, id, href,
      aria_label, role, x, y, w, h, css_selector, xpath
    """
    global _last_elements

    elements_info = await page.evaluate(_ANNOTATE_JS)
    _last_elements = elements_info or []

    await asyncio.sleep(0.3)

    screenshot = await page.screenshot(type="jpeg", quality=85)
    img_b64 = base64.b64encode(screenshot).decode()

    # 移除视觉标注，但保留 data-skyvern-id（execute 还要用）
    await page.evaluate("""() => {
        document.querySelectorAll('.skyvern-label').forEach(el => el.remove());
    }""")

    return img_b64, elements_info


async def get_element_coords(page, skyvern_id: int) -> dict | None:
    """
    通过多级回退链定位元素，返回坐标信息。

    回退策略：
    1. data-skyvern-id 属性精确定位
    2. CSS selector 回退（id、name、aria-label）
    3. XPath 回退
    4. 缓存坐标回退（使用上次标注时的坐标）

    返回 dict: {x, y, tag, type, placeholder, method} 或 None
    """
    # 1. data-skyvern-id 精确定位
    result = await page.evaluate(_LOCATE_JS, skyvern_id)
    if result and result.get("found"):
        return result

    # 2. CSS selector 回退
    element_info = _find_element_info(skyvern_id)
    if element_info and element_info.get("css_selector"):
        result = await page.evaluate(_LOCATE_BY_CSS_JS, element_info["css_selector"])
        if result and result.get("found"):
            return result

    # 3. XPath 回退
    if element_info and element_info.get("xpath"):
        result = await page.evaluate(_LOCATE_BY_XPATH_JS, element_info["xpath"])
        if result and result.get("found"):
            return result

    # 4. 缓存坐标回退（最后手段）
    if element_info and element_info.get("x") and element_info.get("y"):
        return {
            "found": True,
            "method": "cached-coords",
            "x": element_info["x"],
            "y": element_info["y"],
            "tag": element_info.get("tag", ""),
            "type": element_info.get("type", ""),
            "placeholder": element_info.get("placeholder", ""),
        }

    return None


def _find_element_info(skyvern_id: int) -> dict | None:
    """从缓存的元素列表中查找指定 ID 的元素信息"""
    for el in _last_elements:
        if el.get("index") == skyvern_id:
            return el
    return None
