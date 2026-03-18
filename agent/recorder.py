"""
ActionRecorder — 通过 JS 注入捕获用户浏览器操作。

录制机制：
  1. 通过 page.expose_function() 注册回调
  2. 注入 JS 脚本监听 click/input/navigate/scroll 事件
  3. 每个事件序列化为 action dict，通过回调传回 Python
"""

import json
import time
from playwright.async_api import Page


# 注入到页面的录制脚本
RECORDER_JS = """
(() => {
  if (window.__recorderActive) return;
  window.__recorderActive = true;

  let inputTimer = null;
  let scrollTimer = null;
  let lastScrollY = window.scrollY;

  function buildSelector(el) {
    if (!el || !el.tagName) return '';
    if (el.id) return '#' + el.id;
    if (el.getAttribute('name')) return el.tagName.toLowerCase() + '[name="' + el.getAttribute('name') + '"]';
    if (el.getAttribute('data-testid')) return '[data-testid="' + el.getAttribute('data-testid') + '"]';
    if (el.getAttribute('aria-label')) return '[aria-label="' + el.getAttribute('aria-label') + '"]';
    const parts = [];
    let cur = el;
    for (let i = 0; i < 3 && cur && cur !== document.body; i++) {
      let seg = cur.tagName.toLowerCase();
      if (cur.className && typeof cur.className === 'string') {
        const cls = cur.className.trim().split(/\\s+/).slice(0, 2).join('.');
        if (cls) seg += '.' + cls;
      }
      parts.unshift(seg);
      cur = cur.parentElement;
    }
    return parts.join(' > ');
  }

  function getVisibleText(el) {
    return (el.innerText || el.textContent || el.value || el.placeholder || '').substring(0, 100).trim();
  }

  function onClick(e) {
    const el = e.target;
    window.__recordAction(JSON.stringify({
      type: 'click', timestamp: Date.now(), url: location.href,
      selector: buildSelector(el), text: getVisibleText(el), tag: el.tagName.toLowerCase(),
    }));
  }

  function onInput(e) {
    clearTimeout(inputTimer);
    const el = e.target;
    inputTimer = setTimeout(() => {
      window.__recordAction(JSON.stringify({
        type: 'type_text', timestamp: Date.now(), url: location.href,
        selector: buildSelector(el), text: el.value || '', tag: el.tagName.toLowerCase(),
        input_type: el.type || '',
      }));
    }, 500);
  }

  function onChange(e) {
    const el = e.target;
    if (el.tagName === 'SELECT') {
      window.__recordAction(JSON.stringify({
        type: 'select_option', timestamp: Date.now(), url: location.href,
        selector: buildSelector(el), text: el.options[el.selectedIndex]?.text || el.value,
        tag: 'select', meta: { value: el.value },
      }));
    }
  }

  function onKeydown(e) {
    if (['Enter', 'Escape', 'Tab'].includes(e.key)) {
      window.__recordAction(JSON.stringify({
        type: 'press_key', timestamp: Date.now(), url: location.href,
        selector: buildSelector(e.target), text: '',
        tag: (e.target.tagName || '').toLowerCase(), meta: { key: e.key },
      }));
    }
  }

  function onScroll() {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      const dir = window.scrollY > lastScrollY ? 'down' : 'up';
      const amount = Math.abs(window.scrollY - lastScrollY);
      if (amount > 100) {
        window.__recordAction(JSON.stringify({
          type: 'scroll', timestamp: Date.now(), url: location.href,
          selector: '', text: '', tag: '',
          meta: { direction: dir, amount: amount },
        }));
      }
      lastScrollY = window.scrollY;
    }, 300);
  }

  document.addEventListener('click', onClick, true);
  document.addEventListener('input', onInput, true);
  document.addEventListener('change', onChange, true);
  document.addEventListener('keydown', onKeydown, true);
  window.addEventListener('scroll', onScroll, true);

  const origPush = history.pushState;
  const origReplace = history.replaceState;
  history.pushState = function(...args) {
    origPush.apply(this, args);
    setTimeout(() => {
      window.__recordAction(JSON.stringify({
        type: 'navigate', timestamp: Date.now(), url: location.href,
        selector: '', text: document.title, tag: '',
      }));
    }, 100);
  };
  history.replaceState = function(...args) {
    origReplace.apply(this, args);
    setTimeout(() => {
      window.__recordAction(JSON.stringify({
        type: 'navigate', timestamp: Date.now(), url: location.href,
        selector: '', text: document.title, tag: '',
      }));
    }, 100);
  };

  function onPopstate() {
    window.__recordAction(JSON.stringify({
      type: 'navigate', timestamp: Date.now(), url: location.href,
      selector: '', text: document.title, tag: '',
    }));
  }
  window.addEventListener('popstate', onPopstate);

  window.__stopRecording = () => {
    window.__recorderActive = false;
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('input', onInput, true);
    document.removeEventListener('change', onChange, true);
    document.removeEventListener('keydown', onKeydown, true);
    window.removeEventListener('scroll', onScroll, true);
    window.removeEventListener('popstate', onPopstate);
    clearTimeout(inputTimer);
    clearTimeout(scrollTimer);
    history.pushState = origPush;
    history.replaceState = origReplace;
  };
})();
"""


class ActionRecorder:
    """录制用户浏览器操作。"""

    def __init__(self, page: Page, log_fn=None):
        self.page = page
        self._actions: list[dict] = []
        self._recording = False
        self._log_fn = log_fn

    async def start(self):
        """注入录制脚本，开始捕获用户操作。"""
        await self.page.expose_function("__recordAction", self._on_action)
        await self.page.add_init_script(RECORDER_JS)
        # 对当前页面也执行一次
        try:
            await self.page.evaluate(RECORDER_JS)
        except Exception:
            pass
        self._recording = True

    async def stop(self) -> list[dict]:
        """停止录制，返回操作列表。"""
        try:
            await self.page.evaluate("window.__stopRecording && window.__stopRecording()")
        except Exception:
            pass
        self._recording = False
        return self._actions

    def _on_action(self, action_json: str):
        """JS 回调：接收录制的操作。"""
        try:
            action = json.loads(action_json)
            self._actions.append(action)
            if self._log_fn:
                self._log_fn(f"  [录制] {action.get('type', '?')}: {action.get('text', '')[:50]}")
        except Exception:
            pass
