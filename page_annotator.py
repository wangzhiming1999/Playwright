"""
给页面元素打标签的辅助函数
用于 Skyvern 风格的视觉操作

核心设计：给每个元素打上 data-skyvern-id 属性，
annotate 和 execute 共享同一份元素表，彻底消除两次 DOM 查询不一致。
"""


async def annotate_page(page):
    """
    在页面上给所有可交互元素打标签（红框 + 编号）
    返回: (标注后的截图 base64, 元素列表)

    每个元素会被打上 data-skyvern-id="<index>" 属性，
    execute 时可直接用 querySelector('[data-skyvern-id="N"]') 定位，
    不需要重新查 DOM，彻底避免编号漂移。
    """
    elements_info = await page.evaluate("""() => {
        // 清理上一轮的标注和 ID
        document.querySelectorAll('.skyvern-label').forEach(el => el.remove());
        document.querySelectorAll('[data-skyvern-id]').forEach(el => {
            el.removeAttribute('data-skyvern-id');
        });

        const elements = [];
        let index = 0;

        const selectors = [
            'input:not([type="hidden"])',
            'textarea',
            'button',
            'a[href]',
            'select',
            '[role="button"]',
            '[onclick]',
        ];

        // 去重收集
        const seen = new Set();
        const allElements = [];
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                if (!seen.has(el)) {
                    seen.add(el);
                    allElements.push(el);
                }
            });
        });

        allElements.forEach(el => {
            const rect = el.getBoundingClientRect();

            // 只标注视口内可见元素
            if (rect.width === 0 || rect.height === 0 ||
                rect.bottom < 0 || rect.top > window.innerHeight ||
                rect.right < 0 || rect.left > window.innerWidth) {
                return;
            }

            // 打上稳定 ID，execute 时用这个定位
            el.setAttribute('data-skyvern-id', String(index));

            // 红框
            const box = document.createElement('div');
            box.className = 'skyvern-label';
            box.style.cssText = `
                position: fixed;
                left: ${rect.left}px;
                top: ${rect.top}px;
                width: ${rect.width}px;
                height: ${rect.height}px;
                border: 2px solid red;
                pointer-events: none;
                z-index: 2147483646;
                box-sizing: border-box;
            `;

            // 编号标签：放右上角，避免遮挡元素内容
            const labelLeft = Math.min(rect.right - 22, window.innerWidth - 24);
            const labelTop = Math.max(rect.top - 18, 2);
            const label = document.createElement('div');
            label.className = 'skyvern-label';
            label.textContent = index;
            label.style.cssText = `
                position: fixed;
                left: ${labelLeft}px;
                top: ${labelTop}px;
                background: red;
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
            `;

            document.body.appendChild(box);
            document.body.appendChild(label);

            elements.push({
                index: index,
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                text: (el.textContent || el.value || '').trim().substring(0, 50),
                placeholder: el.placeholder || '',
                name: el.name || '',
                id: el.id || '',
                href: el.href || '',
                aria_label: el.getAttribute('aria-label') || '',
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
            });

            index++;
        });

        return elements;
    }""")

    import asyncio
    await asyncio.sleep(0.3)

    import base64
    screenshot = await page.screenshot(type="jpeg", quality=85)
    img_b64 = base64.b64encode(screenshot).decode()

    # 移除视觉标注，但保留 data-skyvern-id（execute 还要用）
    await page.evaluate("""() => {
        document.querySelectorAll('.skyvern-label').forEach(el => el.remove());
    }""")

    return img_b64, elements_info


async def get_element_coords(page, skyvern_id: int) -> tuple[int, int] | None:
    """
    通过 data-skyvern-id 获取元素的当前坐标（中心点）。
    如果元素不存在或不可见，返回 None。
    """
    result = await page.evaluate(f"""() => {{
        const el = document.querySelector('[data-skyvern-id="{skyvern_id}"]');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) return null;
        return {{
            x: Math.round(r.left + r.width / 2),
            y: Math.round(r.top + r.height / 2),
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            placeholder: el.placeholder || '',
        }};
    }}""")
    return result
