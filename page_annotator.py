"""
给页面元素打标签的辅助函数
用于 Skyvern 风格的视觉操作
"""

async def annotate_page(page):
    """
    在页面上给所有可交互元素打标签（红框 + 编号）
    返回: (标注后的截图 base64, 元素列表)
    """
    # 注入 JS：给所有可交互元素画红框 + 编号
    elements_info = await page.evaluate("""() => {
        // 移除之前的标注
        document.querySelectorAll('.skyvern-label').forEach(el => el.remove());

        const elements = [];
        let index = 0;

        // 获取所有可交互元素
        const selectors = [
            'input:not([type="hidden"])',
            'textarea',
            'button',
            'a[href]',
            'select',
            '[role="button"]',
            '[onclick]',
        ];

        const allElements = [];
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                if (!allElements.includes(el)) {
                    allElements.push(el);
                }
            });
        });

        allElements.forEach(el => {
            const rect = el.getBoundingClientRect();

            // 只标注可见元素
            if (rect.width === 0 || rect.height === 0 ||
                rect.top < 0 || rect.top > window.innerHeight ||
                rect.left < 0 || rect.left > window.innerWidth) {
                return;
            }

            // 创建红框
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
                z-index: 999999;
                box-sizing: border-box;
            `;

            // 创建编号标签
            const label = document.createElement('div');
            label.className = 'skyvern-label';
            label.textContent = index;
            label.style.cssText = `
                position: fixed;
                left: ${rect.left}px;
                top: ${rect.top - 20}px;
                background: red;
                color: white;
                padding: 2px 6px;
                font-size: 12px;
                font-weight: bold;
                border-radius: 3px;
                pointer-events: none;
                z-index: 999999;
                font-family: monospace;
            `;

            document.body.appendChild(box);
            document.body.appendChild(label);

            // 记录元素信息
            elements.push({
                index: index,
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                text: el.textContent?.trim().substring(0, 50) || '',
                placeholder: el.placeholder || '',
                name: el.name || '',
                id: el.id || '',
                href: el.href || '',
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
            });

            index++;
        });

        return elements;
    }""")

    # 等待标注渲染
    import asyncio
    await asyncio.sleep(0.3)

    # 截图
    import base64
    screenshot = await page.screenshot(type="jpeg", quality=85)
    img_b64 = base64.b64encode(screenshot).decode()

    # 移除标注
    await page.evaluate("""() => {
        document.querySelectorAll('.skyvern-label').forEach(el => el.remove());
    }""")

    return img_b64, elements_info
