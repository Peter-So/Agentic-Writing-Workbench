from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from playwright.async_api import BrowserContext, Error as PlaywrightError, Page, async_playwright

from app.config import ROOT
from app.ai_providers import PROVIDERS


PROFILE_ROOT = ROOT / "data" / "browser-profiles"
# 每个 provider 固定会话 URL 的持久化文件，保证每次提问都续用同一段对话记录。
SESSION_STORE = PROFILE_ROOT / "conversation_urls.json"
RUN_TIMEOUT_MS = 120_000
# 抓取诊断日志：默认开启，记录候选数量/长度/文本片段，便于定位选择器漂移。
CAPTURE_DEBUG = True
# 专用诊断文件：无论 uvicorn 如何重定向 stdout/stderr 都能稳定读到，抓空时落盘完整快照。
CAPTURE_LOG = ROOT / "logs" / "capture_debug.log"
log = logging.getLogger("uvicorn.error")


def _capture_debug(provider_id: str, event: str, payload: Any = "") -> None:
    if not CAPTURE_DEBUG:
        return
    line = f"{datetime.now().isoformat(timespec='seconds')} provider={provider_id} {event} {payload}"
    log.info("%s", line)
    try:
        CAPTURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CAPTURE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# DOM -> Markdown 递归转换器（JS 函数体）：保留标题(#)、加粗(**)、列表(-)、有序列表、表格、代码块。
# 完整递归子节点，因此能抓到嵌套列表/分段内容，不会像"叶子块"剪枝那样丢失结构化正文。
MARKDOWN_WALKER_JS = r"""(element) => {
    const clone = element.cloneNode(true);
    ['sup','button','[role="button"]','[class*="citation"]','[class*="reference"]','[class*="footnote"]']
        .forEach((sel) => clone.querySelectorAll(sel).forEach((n) => n.remove()));
    const inline = (node) => {
        if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
        if (node.nodeType !== Node.ELEMENT_NODE) return '';
        const tag = node.tagName.toLowerCase();
        const value = Array.from(node.childNodes).map(inline).join('');
        if (tag === 'strong' || tag === 'b') return `**${value.trim()}**`;
        if (tag === 'em' || tag === 'i') return `*${value.trim()}*`;
        if (tag === 'code') return '`' + value.trim() + '`';
        if (tag === 'br') return '\n';
        return value;
    };
    const block = (el) => {
        const tag = el.tagName.toLowerCase();
        const hasBlockChildren = Array.from(el.children).some((child) =>
            /^(h[1-6]|p|div|section|article|ul|ol|li|table|tr|pre|blockquote)$/.test(child.tagName.toLowerCase())
        );
        if (/^h[1-6]$/.test(tag)) return `${'#'.repeat(Number(tag[1]))} ${inline(el).trim()}`;
        if (tag === 'li') {
            const nested = Array.from(el.children).filter((c) => /^(ul|ol)$/.test(c.tagName.toLowerCase()));
            // 父项自身文本要排除嵌套列表，否则嵌套项会被重复输出（先内联进父项、又作为子项）。
            const ownClone = el.cloneNode(true);
            Array.from(ownClone.children).forEach((c) => {
                if (/^(ul|ol)$/.test(c.tagName.toLowerCase())) c.remove();
            });
            const own = inline(ownClone).trim();
            const sub = nested.map(block).filter(Boolean).join('\n').split('\n')
                .map((s) => s ? '  ' + s : s).join('\n');
            return sub ? `- ${own}\n${sub}` : `- ${own}`;
        }
        if (tag === 'ul' || tag === 'ol') return Array.from(el.children).map(block).filter(Boolean).join('\n');
        if (tag === 'tr') return '| ' + Array.from(el.children).map((c) => inline(c).trim()).join(' | ') + ' |';
        if (tag === 'table') return Array.from(el.querySelectorAll('tr')).map(block).filter(Boolean).join('\n');
        if (tag === 'pre') return '\n```\n' + (el.innerText || '').trim() + '\n```\n';
        if (tag === 'blockquote') return '> ' + inline(el).trim();
        if (hasBlockChildren) return Array.from(el.children).map(block).filter(Boolean).join('\n\n');
        return inline(el).trim();
    };
    const result = Array.from(clone.children).map(block).filter(Boolean).join('\n\n');
    return result || inline(clone).trim() || clone.innerText || clone.textContent || '';
}"""


@dataclass
class ProviderSession:
    context: BrowserContext
    page: Page


class AIWebBridge:
    def __init__(self) -> None:
        self._playwright = None
        self._sessions: dict[str, ProviderSession] = {}
        self._playwright_lock = asyncio.Lock()
        self._provider_locks: dict[str, asyncio.Lock] = {}
        self._conversation_urls: dict[str, str] = self._load_conversation_urls()

    def _load_conversation_urls(self) -> dict[str, str]:
        try:
            data = json.loads(SESSION_STORE.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if isinstance(v, str) and v}
        except Exception:
            return {}

    def _save_conversation_url(self, provider_id: str, url: str) -> None:
        if not url or url == self._conversation_urls.get(provider_id):
            return
        self._conversation_urls[provider_id] = url
        try:
            SESSION_STORE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_STORE.write_text(
                json.dumps(self._conversation_urls, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log.info("provider=%s saved conversation url=%s", provider_id, url)
        except Exception as exc:
            log.warning("provider=%s failed to persist conversation url: %s", provider_id, exc)

    def _is_conversation_url(self, provider_id: str, url: str) -> bool:
        """判断 URL 是否是一段具体对话（含会话 id），而非 provider 的新建对话首页。"""
        if not url:
            return False
        if provider_id == "doubao":
            return bool(re.search(r"/chat/\d+", url))
        if provider_id == "deepseek":
            return "/a/chat/s/" in url or bool(re.search(r"/chat/[0-9a-f-]{8,}", url))
        if provider_id == "qwen":
            # 千问会话 URL 形如 /chat/<32位hex>；新建对话停留在 /chat/ 不带 id。
            return bool(re.search(r"/chat/[0-9a-f]{16,}", url)) or bool(re.search(r"/c/[0-9a-f-]{6,}", url))
        return False

    async def _resolve_qwen_conversation_url(self, page: Page) -> str:
        """千问新建对话时地址栏停留在 /chat/ 不追加 id，需从 DOM 解析当前激活会话的链接。"""
        if self._is_conversation_url("qwen", page.url):
            return page.url
        script = """() => {
            const pick = (el) => {
                if (!el) return '';
                const href = el.getAttribute('href') || '';
                if (/\\/chat\\/[0-9a-f]{16,}/.test(href)) return href;
                return '';
            };
            // 优先取标记为激活/选中的历史项
            const active = document.querySelector(
                "a[aria-current], a[class*='active'], li[class*='active'] a, [class*='selected'] a[href*='/chat/']"
            );
            let href = pick(active);
            if (href) return href;
            // 退而求其次：第一条会话历史链接（最新的对话通常在最上）
            for (const a of document.querySelectorAll("a[href*='/chat/']")) {
                href = pick(a);
                if (href) return href;
            }
            return '';
        }"""
        for root in [page, *page.frames]:
            try:
                href = await root.evaluate(script)
            except Exception:
                continue
            if href:
                if href.startswith("/"):
                    origin = re.match(r"^https?://[^/]+", page.url)
                    href = (origin.group(0) if origin else "https://www.qianwen.com") + href
                if self._is_conversation_url("qwen", href):
                    return href
        return ""

    async def _ensure_playwright(self):
        if self._playwright is None:
            async with self._playwright_lock:
                if self._playwright is None:
                    self._playwright = await async_playwright().start()
        return self._playwright

    def provider(self, provider_id: str) -> dict[str, Any]:
        for provider in PROVIDERS:
            if provider["id"] == provider_id:
                return provider
        raise ValueError(f"unknown provider: {provider_id}")

    async def open_provider(self, provider_id: str) -> dict[str, Any]:
        async with self._provider_lock(provider_id):
            try:
                return await self._open_provider_once(provider_id)
            except PlaywrightError as exc:
                log.warning("provider=%s open failed, recreating session: %s", provider_id, exc)
                await self._drop_session(provider_id)
                try:
                    return await self._open_provider_once(provider_id)
                except Exception as retry_exc:
                    return self._provider_error(provider_id, retry_exc)
            except Exception as exc:
                return self._provider_error(provider_id, exc)

    async def _open_provider_once(self, provider_id: str) -> dict[str, Any]:
        provider = self.provider(provider_id)
        session = await self._session(provider_id)
        # 若已固定会话，直接打开该会话页，方便用户确认续用的是同一段对话。
        target = self._conversation_urls.get(provider_id) or provider["url"]
        await session.page.goto(target, wait_until="domcontentloaded", timeout=60_000)
        return {
            "ok": True,
            "provider": provider_id,
            "name": provider["name"],
            "url": session.page.url,
            "pinned": self._conversation_urls.get(provider_id) or "",
            "profile": str((PROFILE_ROOT / provider_id).relative_to(ROOT)).replace("\\", "/"),
        }

    async def _current_conversation_url(self, page: Page, provider_id: str) -> str:
        """解析当前激活会话的可续用 URL。千问需从 DOM 取，其余直接用地址栏。"""
        if provider_id == "qwen":
            return await self._resolve_qwen_conversation_url(page)
        return page.url if self._is_conversation_url(provider_id, page.url) else ""

    async def pin_current_conversation(self, provider_id: str) -> dict[str, Any]:
        """把浏览器当前所在的会话固定为该 provider 的默认会话。"""
        provider = self.provider(provider_id)
        session = self._sessions.get(provider_id)
        if not session or session.page.is_closed():
            return {"ok": False, "provider": provider_id, "name": provider["name"],
                    "result": "请先点击「打开」并停留在要固定的会话页。"}
        url = await self._current_conversation_url(session.page, provider_id)
        if not url:
            hint = (
                "当前页面不是具体会话页（缺少会话 id）。请先在该会话里发一句话，进入带 id 的对话再固定。"
                if provider_id != "qwen"
                else "未能解析到千问会话 id。请先在该会话里发一句话，让左侧出现这条对话历史后再固定。"
            )
            return {"ok": False, "provider": provider_id, "name": provider["name"], "url": session.page.url,
                    "result": hint}
        self._save_conversation_url(provider_id, url)
        return {"ok": True, "provider": provider_id, "name": provider["name"], "url": url,
                "result": f"已固定 {provider['name']} 的会话，后续提问都会续用这段对话。"}

    def reset_conversation(self, provider_id: str) -> dict[str, Any]:
        """清除固定会话，下次提问回到新建对话。"""
        provider = self.provider(provider_id)
        existed = self._conversation_urls.pop(provider_id, None)
        if existed is not None:
            try:
                SESSION_STORE.write_text(
                    json.dumps(self._conversation_urls, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:
                pass
        return {"ok": True, "provider": provider_id, "name": provider["name"],
                "result": "已清除固定会话。" if existed else "该 provider 当前没有固定会话。"}

    async def run_prompt(self, provider_id: str, prompt: str) -> dict[str, Any]:
        async with self._provider_lock(provider_id):
            provider = self.provider(provider_id)
            session = await self._session(provider_id)
            page = session.page
            await self._ensure_provider_page(page, provider_id, provider["url"])
            before = await self._conversation_snapshot(page, provider_id)
            before_copy_count = await self._copy_button_count(page)
            input_result = await self._fill_prompt(page, prompt, provider_id)
            log.info(
                "provider=%s input ok=%s selector=%s url=%s",
                provider_id,
                input_result.get("ok"),
                input_result.get("selector"),
                page.url,
            )
            if not input_result["ok"]:
                return {
                    "provider": provider_id,
                    "name": provider["name"],
                    "status": "failed",
                    "result": input_result["error"],
                    "url": page.url,
                }
            send_method = await self._send(page, provider_id)
            log.info("provider=%s send_method=%s url=%s", provider_id, send_method, page.url)
            # 发送确认：输入框已清空，或页面已进入/跳转到具体会话，或已出现新增回答。
            sent = await self._confirm_sent(page, provider_id, before, prompt)
            if not sent:
                return {
                    "provider": provider_id,
                    "name": provider["name"],
                    "status": "failed",
                    "result": "已输入内容，但未检测到发送成功；输入框仍保留文本，已停止抓取历史结果。",
                    "url": page.url,
                }
            # 首次在该会话发问后记录可续用 URL（千问需从 DOM 解析，其余取地址栏）。
            resolved = await self._current_conversation_url(page, provider_id)
            if resolved:
                self._save_conversation_url(provider_id, resolved)
            # 完成判定（统一）：等待本轮新增回答稳定，优先以新增复制按钮作为结束信号。
            result = await self._wait_until_copy_ready(page, provider_id, before, prompt, before_copy_count)
            # 等待回复结束后会话 id 可能才出现，再尝试记录一次。
            resolved = await self._current_conversation_url(page, provider_id)
            if resolved:
                self._save_conversation_url(provider_id, resolved)
            # 千问优先「复制为 Markdown」拿带标题的完整 markdown（与手动复制一致）。
            copied = ""
            if provider_id == "qwen":
                try:
                    copied = await asyncio.wait_for(
                        self._capture_qwen_markdown(page, prompt), timeout=10.0)
                except Exception as exc:
                    _capture_debug(provider_id, "copy_markdown", f"timeout/err: {type(exc).__name__}")
            # 普通复制按钮抓取（含 markdown，最权威）：优先点击本轮新增回答的复制按钮。
            # 这些站点的剪贴板拦截偶尔返回空，空则保留上面的抓取结果。
            if not copied:
                try:
                    copied = await asyncio.wait_for(
                        self._capture_via_copy_button(page, provider_id, before, prompt, before_copy_count),
                        timeout=14.0,
                    )
                except Exception as exc:
                    _capture_debug(provider_id, "copy_button", f"capture_timeout/err: {type(exc).__name__}")
            if copied and len(self._normalize_text(copied)) >= 20:
                _capture_debug(provider_id, "copy_button", f"len={len(self._normalize_text(copied))} used=1")
                result = copied
            else:
                _capture_debug(
                    provider_id, "copy_button",
                    f"copied_len={len(self._normalize_text(copied or ''))} scraped_len={len(self._normalize_text(result))} used=0",
                )
            result = self._clean_provider_result(provider_id, result)
            return {
                "provider": provider_id,
                "name": provider["name"],
                "status": "success" if result else "partial",
                "result": result or "已发送，但未在超时时间内抓取到新增回复文本。",
                "url": page.url,
            }

    # document-start 安装：在任何站点 JS 之前 patch 剪贴板写入，把内容存到 window.__capturedCopy。
    # 这样 provider 点"复制"调 writeText/execCommand 时必被捕获（运行时再 patch 会被 SPA 缓存的旧引用绕过）。
    _CLIPBOARD_INIT_SCRIPT = r"""
        (() => {
          window.__capturedCopy = "";
          try {
            const cb = navigator.clipboard;
            if (cb && cb.writeText) {
              const orig = cb.writeText.bind(cb);
              cb.writeText = (t) => { try { window.__capturedCopy = String(t || ""); } catch(e){} return orig(t); };
            }
          } catch(e){}
          try {
            const origSet = DataTransfer.prototype.setData;
            DataTransfer.prototype.setData = function(type, val){
              try { if (String(type).includes("text") && val) window.__capturedCopy = String(val); } catch(e){}
              return origSet.apply(this, arguments);
            };
          } catch(e){}
          document.addEventListener("copy", (e) => {
            try { const d = e.clipboardData; const t = d && d.getData ? d.getData("text/plain") : ""; if (t) window.__capturedCopy = t; } catch(err){}
          }, true);
        })();
    """

    async def _read_captured_copy(self, page: Page) -> str:
        """读取本次复制内容：优先 document-start 钩子捕获的 __capturedCopy，再退回真实剪贴板。"""
        for root in [page, *page.frames]:
            try:
                v = await root.evaluate("() => window.__capturedCopy || ''")
            except Exception:
                v = ""
            if v and v.strip():
                return v.replace("\r\n", "\n").strip()
        for root in [page, *page.frames]:
            try:
                v = await root.evaluate(
                    "async () => { try { return await navigator.clipboard.readText(); } catch(e) { return ''; } }")
            except Exception:
                continue
            if v and v.strip():
                return v.replace("\r\n", "\n").strip()
        return ""

    async def _reset_captured_copy(self, page: Page) -> None:
        try:
            await page.evaluate("() => { window.__capturedCopy = ''; }")
        except Exception:
            pass

    async def _capture_via_copy_button(
        self,
        page: Page,
        provider_id: str,
        before: list[str],
        prompt: str,
        before_copy_count: int = 0,
    ) -> str:
        """点击最后一条回答的「复制」按钮，读本次复制内容（最权威，含 markdown，不含推荐追问）。

        内容来自 document-start 钩子捕获的 provider 真实复制产物；不做字符过滤。
        """
        try:
            await self._reset_captured_copy(page)
            for selector in self._COPY_BUTTON_SELECTORS:
                try:
                    loc = page.locator(selector)
                    count = await loc.count()
                except Exception:
                    continue
                if not count or count <= before_copy_count:
                    continue
                btn = loc.nth(count - 1)  # 最后一个=本轮最新回答的复制按钮
                try:
                    await btn.scroll_into_view_if_needed(timeout=1_500)
                    await btn.hover(timeout=1_500)
                    await btn.click(timeout=2_500)
                except Exception:
                    continue
                await asyncio.sleep(0.5)
                captured = await self._read_captured_copy(page)
                if len(self._normalize_text(captured)) >= 8:
                    return captured
            # 有些站点不会增加 copy 按钮数量，只会复用最后一条消息工具栏；只在页面已有新增正文时兜底点最后一个。
            fallback_text = self._new_conversation_text(provider_id, before, await self._conversation_snapshot(page, provider_id), prompt)
            if len(self._normalize_text(fallback_text)) >= 80:
                for selector in self._COPY_BUTTON_SELECTORS:
                    try:
                        loc = page.locator(selector)
                        count = await loc.count()
                    except Exception:
                        continue
                    if not count:
                        continue
                    btn = loc.nth(count - 1)
                    try:
                        await btn.scroll_into_view_if_needed(timeout=1_500)
                        await btn.hover(timeout=1_500)
                        await btn.click(timeout=2_500)
                    except Exception:
                        continue
                    await asyncio.sleep(0.5)
                    captured = await self._read_captured_copy(page)
                    if len(self._normalize_text(captured)) >= 20:
                        return captured
        except Exception as exc:
            log.warning("provider=%s copy-button capture failed: %s", provider_id, exc)
        return ""

    # 千问「复制为 Markdown」菜单项的常见定位（文本/aria-label 含 Markdown）。
    _QWEN_MD_OPTION_SELECTORS = [
        "[role='menuitem']:has-text('Markdown')",
        "[role='menuitem']:has-text('markdown')",
        "li:has-text('复制为 Markdown')",
        "li:has-text('复制为Markdown')",
        "button:has-text('复制为 Markdown')",
        "div:has-text('复制为 Markdown')",
        "[aria-label*='Markdown' i]",
    ]
    # 触发复制菜单的入口。千问真实结构：复制按钮旁一个 aria-haspopup="menu" 的小箭头(chevron)按钮，
    # 无文本/无"复制"label，故优先按 haspopup 定位；再退回更多/下拉/箭头等通用形态。
    _QWEN_COPY_MENU_TRIGGERS = [
        "button[aria-haspopup='menu']",
        "[aria-haspopup='menu']",
        "button[aria-label*='复制']",
        "[class*='copy'] button",
        "button[aria-label*='更多' i]",
        "button[aria-label*='More' i]",
        "[class*='copy'] [class*='arrow']",
        "[class*='copy'] [class*='dropdown']",
    ]

    async def _capture_qwen_markdown(self, page: Page, prompt: str) -> str:
        """千问优先用「复制为 Markdown」拿带标题的完整 markdown。

        hover 出 chevron 菜单 → 点「复制为 Markdown」→ 读 document-start 钩子捕获的复制内容
        （千问自己写入的权威 markdown，本就不含推荐追问）。读不到返回空，由调用方回退。不做字符过滤。
        """
        try:
            await self._reset_captured_copy(page)
            for trig in self._QWEN_COPY_MENU_TRIGGERS:
                try:
                    loc = page.locator(trig)
                    n = await loc.count()
                    if not n:
                        continue
                    item = loc.nth(n - 1)
                    await item.scroll_into_view_if_needed(timeout=1_500)
                    await item.hover(timeout=1_500)
                    await item.click(timeout=2_000)
                except Exception:
                    continue
                await asyncio.sleep(0.4)
                # 菜单出现后点「复制为 Markdown」
                for opt in self._QWEN_MD_OPTION_SELECTORS:
                    try:
                        ol = page.locator(opt)
                        if not await ol.count():
                            continue
                        await ol.nth(await ol.count() - 1).click(timeout=2_000)
                    except Exception:
                        continue
                    await asyncio.sleep(0.5)
                    cap = await self._read_captured_copy(page)
                    if len(self._normalize_text(cap)) >= 20:  # 排除菜单标签等短串（非内容过滤）
                        _capture_debug("qwen", "copy_markdown", f"len={len(self._normalize_text(cap))} used=1")
                        return cap
        except Exception as exc:
            log.warning("provider=qwen copy-markdown capture failed: %s", exc)
        _capture_debug("qwen", "copy_markdown", "未取到 Markdown，回退")
        return ""

    async def _session(self, provider_id: str) -> ProviderSession:
        session = self._sessions.get(provider_id)
        if session and not session.page.is_closed():
            return session
        if session:
            await self._drop_session(provider_id)
        pw = await self._ensure_playwright()
        profile_dir = PROFILE_ROOT / provider_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
            permissions=["clipboard-read", "clipboard-write"],
        )
        # 在任何站点 JS 之前装剪贴板钩子（document-start），否则 SPA 已缓存原始
        # clipboard.writeText 引用，运行时再 patch 就拦不到。provider 点"复制"时即被捕获。
        await context.add_init_script(self._CLIPBOARD_INIT_SCRIPT)
        page = context.pages[0] if context.pages else await context.new_page()
        session = ProviderSession(context=context, page=page)
        self._sessions[provider_id] = session
        return session

    async def _ensure_provider_page(self, page: Page, provider_id: str, url: str) -> None:
        current = page.url or ""
        # 优先续用已保存的固定会话；已经在该会话页则无需跳转。
        saved = self._conversation_urls.get(provider_id)
        if saved:
            if self._is_conversation_url(provider_id, current):
                return
            try:
                await page.goto(saved, wait_until="domcontentloaded", timeout=60_000)
                return
            except Exception as exc:
                log.warning("provider=%s goto saved conversation failed, fallback to home: %s", provider_id, exc)
        should_goto = not current or current == "about:blank"
        if provider_id == "qwen" and "qianwen.com" not in current:
            should_goto = True
        if provider_id == "doubao" and "doubao.com" not in current:
            should_goto = True
        if provider_id == "deepseek" and "chat.deepseek.com" not in current:
            should_goto = True
        if should_goto:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    async def _drop_session(self, provider_id: str) -> None:
        session = self._sessions.pop(provider_id, None)
        if not session:
            return
        try:
            await session.context.close()
        except Exception:
            pass

    def _provider_error(self, provider_id: str, exc: Exception) -> dict[str, Any]:
        provider = self.provider(provider_id)
        return {
            "ok": False,
            "provider": provider_id,
            "name": provider["name"],
            "status": "failed",
            "result": f"{type(exc).__name__}: {exc}",
            "url": provider["url"],
            "profile": str((PROFILE_ROOT / provider_id).relative_to(ROOT)).replace("\\", "/"),
        }

    def _provider_lock(self, provider_id: str) -> asyncio.Lock:
        if provider_id not in self._provider_locks:
            self._provider_locks[provider_id] = asyncio.Lock()
        return self._provider_locks[provider_id]

    async def _fill_prompt(self, page: Page, prompt: str, provider_id: str) -> dict[str, Any]:
        provider_selectors = {
            "doubao": [
                "textarea[placeholder*='发消息']",
                "textarea.semi-input-textarea",
                "textarea[dir='ltr']",
            ],
            "qwen": [
                "textarea[placeholder*='输入']",
                "textarea[placeholder*='问']",
                "textarea[placeholder*='发']",
                "[contenteditable='true'][data-placeholder]",
                "[data-placeholder]",
                "[aria-label*='输入']",
                "[aria-label*='问']",
                "[class*='textarea']",
                ".ProseMirror",
                "[class*='ProseMirror']",
                "[class*='editor'] [contenteditable='true']",
                "[class*='input'] [contenteditable='true']",
                "[class*='chat-input'] textarea",
                "[class*='composer'] textarea",
                "[data-testid*='input'] textarea",
                "[data-testid*='input'] [contenteditable='true']",
                "textarea",
                "[contenteditable='true']",
                "div[role='textbox']",
            ],
            "deepseek": [
                "textarea",
                "[contenteditable='true']",
                "div[role='textbox']",
            ],
        }
        selectors = provider_selectors.get(provider_id, []) + [
            "textarea",
            "[contenteditable='true']",
            "div[role='textbox']",
            "input[type='text']",
        ]
        for selector in selectors:
            try:
                loc = await self._last_visible_locator(page, selector)
                if loc is None:
                    continue
                await loc.wait_for(state="visible", timeout=8_000)
                try:
                    await loc.scroll_into_view_if_needed(timeout=2_000)
                    await loc.click(timeout=5_000)
                    await loc.fill(prompt, timeout=5_000)
                except Exception:
                    await loc.click(timeout=5_000)
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(prompt, delay=1)
                return {"ok": True, "selector": selector}
            except Exception:
                continue
        return {"ok": False, "error": "未找到可输入的文本框；请确认该 provider 已登录并处于可聊天页面。"}

    async def _last_visible_locator(self, page: Page, selector: str):
        roots = [page, *page.frames]
        for root in roots:
            try:
                loc = root.locator(selector)
                count = await loc.count()
                for idx in range(count - 1, -1, -1):
                    item = loc.nth(idx)
                    try:
                        if await item.is_visible(timeout=500):
                            return item
                    except Exception:
                        continue
            except Exception:
                continue
        return None

    async def _send(self, page: Page, provider_id: str) -> str:
        if provider_id == "doubao":
            if await self._click_near_input_button(page):
                await asyncio.sleep(0.8)
                if not await self._input_still_has_text(page):
                    return "near_input_button"
        provider_selectors = {
            "qwen": [
                "button[type='submit']",
                "button[aria-label*='发送']",
                "button[aria-label*='send' i]",
                "button:has-text('发送')",
                "[data-testid*='send']",
                "[class*='send'] button",
                "[class*='Send'] button",
            ],
            "doubao": [
                "button[aria-label*='发送']",
                "button:has-text('发送')",
                "[class*='send'] button",
                "[class*='Send'] button",
            ],
        }
        button_selectors = [
            *provider_selectors.get(provider_id, []),
            "button[type='submit']",
            "button:has-text('发送')",
            "button:has-text('Send')",
            "[aria-label*='发送']",
            "[aria-label*='send' i]",
        ]
        clicked = await self._click_send_button(page, button_selectors)
        if clicked:
            await asyncio.sleep(0.8)
            if provider_id in {"qwen", "doubao"} and await self._input_still_has_text(page):
                if provider_id == "doubao" and await self._click_near_input_button(page):
                    await asyncio.sleep(0.8)
                    if not await self._input_still_has_text(page):
                        return "button_then_near_input_button"
                await page.keyboard.press("Enter")
                await asyncio.sleep(0.8)
                if await self._input_still_has_text(page):
                    await page.keyboard.press("Control+Enter")
                    return "button_then_enter_then_ctrl_enter"
                return "button_then_enter"
            return "button"
        if provider_id == "qwen":
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.8)
            if await self._input_still_has_text(page):
                await page.keyboard.press("Control+Enter")
                return "enter_then_ctrl_enter"
            return "enter"
        if provider_id == "doubao":
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.8)
            if await self._input_still_has_text(page):
                await page.keyboard.press("Control+Enter")
                return "enter_then_ctrl_enter"
            return "enter"
        await page.keyboard.press("Enter")
        return "enter"

    async def _click_near_input_button(self, page: Page) -> bool:
        script = """() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const inputs = Array.from(document.querySelectorAll('textarea, [contenteditable="true"], div[role="textbox"]')).filter(visible);
            const input = inputs[inputs.length - 1];
            if (!input) return false;
            const inputRect = input.getBoundingClientRect();
            const buttons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((button) => {
                if (!visible(button) || button.disabled || button.getAttribute('aria-disabled') === 'true') return false;
                const rect = button.getBoundingClientRect();
                if (rect.width < 16 || rect.height < 16) return false;
                const nearY = rect.top >= inputRect.top - 80 && rect.bottom <= inputRect.bottom + 120;
                const nearX = rect.left >= inputRect.left - 80 && rect.right <= inputRect.right + 160;
                return nearY && nearX;
            });
            if (!buttons.length) return false;
            buttons.sort((a, b) => {
                const ar = a.getBoundingClientRect();
                const br = b.getBoundingClientRect();
                return (br.right + br.bottom) - (ar.right + ar.bottom);
            });
            buttons[0].click();
            return true;
        }"""
        try:
            if await page.evaluate(script):
                return True
        except Exception:
            pass
        for frame in page.frames:
            try:
                if await frame.evaluate(script):
                    return True
            except Exception:
                continue
        return False

    async def _click_send_button(self, page: Page, selectors: list[str]) -> bool:
        for selector in selectors:
            try:
                roots = [page, *page.frames]
                for root in roots:
                    loc = root.locator(selector)
                    count = await loc.count()
                    if count:
                        button = loc.nth(count - 1)
                        await button.wait_for(state="visible", timeout=1_500)
                        if await button.is_disabled(timeout=500):
                            continue
                        await button.click(timeout=3_000)
                        return True
            except Exception:
                continue
        return False

    async def _wait_for_doubao_chat_url(self, page: Page) -> None:
        try:
            await page.wait_for_url("**/chat/*", timeout=8_000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        except Exception:
            pass

    async def _confirm_sent(self, page: Page, provider_id: str, before: list[str], prompt: str) -> bool:
        """确认提问已发送：轮询若干次，命中任一信号即视为成功。

        信号：输入框已清空 / URL 已变为具体会话 / 出现了 before 中没有的新增内容。
        替代旧的"输入框仍有文本即失败"判断——千问 ProseMirror 发送后常残留文本，导致误判。
        """
        before_set = {self._normalize_text(item) for item in before}
        for attempt in range(8):
            if not await self._input_still_has_text(page):
                return True
            if self._is_conversation_url(provider_id, page.url):
                return True
            try:
                current = await self._conversation_snapshot(page, provider_id)
            except Exception:
                current = []
            for item in current:
                normalized = self._normalize_text(item)
                if not normalized or normalized in before_set:
                    continue
                if self._is_provider_noise(provider_id, item):
                    continue
                # 出现了非提问回显的新增内容，说明已经发出并开始生成。
                if self._normalize_text(prompt) not in normalized and len(normalized) >= 8:
                    return True
            await asyncio.sleep(1)
        # deepseek 之前不做此校验，保持宽松：默认放行交给后续抓取判定。
        return provider_id == "deepseek"

    async def _input_still_has_text(self, page: Page) -> bool:
        selectors = [
            "textarea",
            "[contenteditable='true']",
            "div[role='textbox']",
        ]
        for selector in selectors:
            try:
                item = await self._last_visible_locator(page, selector)
                if item is None:
                    continue
                value = await item.input_value(timeout=500) if selector == "textarea" else await item.inner_text(timeout=500)
                if value.strip():
                    return True
            except Exception:
                continue
        return False

    async def _conversation_snapshot(self, page: Page, provider_id: str) -> list[str]:
        # 三个 provider 各用独立解析器，互不影响：调一个的查询/剪枝不会波及另一个。
        if provider_id == "deepseek":
            return await self._snapshot_deepseek(page)
        if provider_id == "qwen":
            return await self._snapshot_qwen(page)
        if provider_id == "doubao":
            return await self._snapshot_doubao(page)
        return await self._snapshot_default(page, provider_id)

    # ---- DeepSeek：选择器优先 + 短路兜底（沿用既有可用逻辑，逐字不变）----
    async def _snapshot_deepseek(self, page: Page) -> list[str]:
        selectors = [
            ".ds-markdown",
            "[class*='markdown']",
            "[class*='message'] [class*='content']",
            "[data-testid*='message']",
            "main article",
            "article",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                if count == 0:
                    continue
                texts = []
                for idx in range(max(0, count - 20), count):
                    node = loc.nth(idx)
                    text = await self._node_text(node, "deepseek")
                    if text:
                        texts.append(text)
                if texts:
                    log.info(
                        "provider=deepseek snapshot selector=%s count=%s texts=%s",
                        selector,
                        count,
                        [len(item) for item in texts[-5:]],
                    )
                    return texts
            except Exception:
                continue
        return await self._extract_text_blocks(page, "deepseek", self._DEFAULT_QUERY)

    # ---- 千问：独立解析器。回答渲染在零散 div 里，按元素抓取抓不到；
    #      改用对话区 innerText 整体线性化，按行切分，再交给前后 diff 区分提问/回答。----
    _QWEN_CHROME_LINES = {
        "新建对话", "我的空间", "智能体", "对话分组", "新分组", "最近对话",
        "任务助理", "思考", "研究", "千问高考", "PPT创作", "AI生视频", "AI生图",
        "代码", "翻译", "AI写作", "录音纪要", "更多", "引用", "复制", "分享",
        "内容由AI生成，可能不准确，请注意核实", "千问 - 阿里旗下全能AI助手",
        "你好，我是千问",
    }

    async def _snapshot_qwen(self, page: Page) -> list[str]:
        # 主路径：对话区 innerText 整体线性化按行抓取（=用户可见文本，与手动复制一致，
        # 不混入 HTML/代码序列化）。markdown walker 在千问会把内嵌 HTML/代码画布吞进来，
        # 产出上万字混 HTML 的块，故千问不用 walker，与豆包同走 innerText 路径。
        script = r"""() => {
            const main = document.querySelector('main') || document.body;
            return (main.innerText || main.textContent || '');
        }"""
        raw = ""
        for root in [page, *page.frames]:
            try:
                text = await root.evaluate(script)
            except Exception:
                continue
            if text and len(text) > len(raw):
                raw = text
        lines: list[str] = []
        for rawline in (raw or "").replace("﻿", "").splitlines():
            line = rawline.strip()
            if len(line) < 6:
                continue
            if line in self._QWEN_CHROME_LINES:
                continue
            if self._is_provider_noise("qwen", line):
                continue
            lines.append(line)
        # 行级去重保序：千问会把历史提问在底部再列一遍，去掉完全重复的行。
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            norm = self._normalize_text(line)
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(line)
        if CAPTURE_DEBUG:
            _capture_debug("qwen", "qwen_lines", [self._normalize_text(b)[:50] for b in deduped[-10:]])
        return deduped

    # ---- 豆包：与千问同思路。innerText 线性化按行抓取，前后 diff 区分提问/回答，
    #      结束后过滤提问与推荐问题，回传完整答案。----
    _DOUBAO_CHROME_LINES = {
        "新对话", "新建对话", "我的", "发现", "智能体", "默认", "深度思考", "联网搜索",
        "图片", "文件", "拍照", "电话", "实时通话", "深入研究", "帮我写作", "AI 编程",
        "图像生成", "更多", "发消息...", "发消息", "换一换", "换一批",
        "内容由 AI 生成，请仔细甄别", "内容由豆包 AI 生成，请仔细甄别",
        "复制", "重新生成", "分享", "赞", "踩", "朗读",
    }

    async def _snapshot_doubao(self, page: Page) -> list[str]:
        # 取对话主区域 innerText 整体线性化——和千问同样最稳，避免选择器漂移导致截断。
        script = r"""() => {
            const main = document.querySelector('main') || document.body;
            return (main.innerText || main.textContent || '');
        }"""
        raw = ""
        for root in [page, *page.frames]:
            try:
                text = await root.evaluate(script)
            except Exception:
                continue
            if text and len(text) > len(raw):
                raw = text
        lines: list[str] = []
        for rawline in (raw or "").replace("﻿", "").splitlines():
            line = rawline.strip()
            if len(line) < 4:
                continue
            if line in self._DOUBAO_CHROME_LINES:
                continue
            if self._is_provider_noise("doubao", line):
                continue
            lines.append(line)
        deduped: list[str] = []
        seen: set[str] = set()
        for line in lines:
            norm = self._normalize_text(line)
            if norm in seen:
                continue
            seen.add(norm)
            deduped.append(line)
        # 在快照层就剥离末尾的推荐追问行（短单句问句），避免合并阈值吃掉其中一条后计数失真。
        # 流式生成期间页面无推荐问题，此处只在回答结束后生效。
        while deduped and self._looks_like_doubao_suggestion(deduped[-1]):
            deduped.pop()
        if CAPTURE_DEBUG:
            _capture_debug("doubao", "doubao_lines", [self._normalize_text(b)[:50] for b in deduped[-12:]])
        return deduped

    # ---- 默认兜底（未知 provider）----
    async def _snapshot_default(self, page: Page, provider_id: str) -> list[str]:
        pooled = await self._extract_text_blocks(page, provider_id, self._DEFAULT_QUERY)
        return self._finish_snapshot(provider_id, pooled)

    def _finish_snapshot(self, provider_id: str, pooled: list[str]) -> list[str]:
        snapshot = self._dedupe_candidates(pooled)
        if CAPTURE_DEBUG:
            longest = sorted(snapshot, key=lambda item: len(self._normalize_text(item)), reverse=True)[:3]
            _capture_debug(
                provider_id,
                "snapshot",
                f"pooled={len(snapshot)} lengths={[len(self._normalize_text(item)) for item in longest]} "
                f"sample={[self._normalize_text(item)[:80] for item in longest]}",
            )
        return snapshot

    _DEFAULT_QUERY = (
        "main article, article, main section, main div, main p, main span, p, li, h1, h2, h3, h4, "
        "[class*=message], [class*=answer], [class*=response], [class*=markdown], [class*=paragraph], "
        "[data-testid*=message], [data-testid*=answer]"
    )

    async def _extract_text_blocks(
        self,
        page: Page,
        provider_id: str,
        query: str,
        skip_tags: list[str] | None = None,
        skip_closest: str = "button, nav, header, footer, form",
    ) -> list[str]:
        """通用文本块抽取引擎（机制共享，配置按 provider 独立传入）。

        广撒 query 命中的块级元素，靠"叶子块"剪枝（子文本覆盖 92% 即视为包裹层跳过）+
        可见性 + 长度过滤，按 y 排序返回。各 provider 自带 query / skip_tags / skip_closest，
        互不干扰。
        """
        tags = skip_tags or ["BUTTON", "TEXTAREA", "INPUT", "NAV", "HEADER", "FOOTER", "SCRIPT", "STYLE"]
        script = """(args) => {
            const { query, skipTags, skipClosest } = args;
            const skip = new Set(skipTags);
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const nodes = Array.from(document.querySelectorAll(query));
            const items = [];
            const seen = new Set();
            for (const el of nodes) {
                if (!visible(el) || skip.has(el.tagName)) continue;
                if (skipClosest && el.closest(skipClosest)) continue;
                const text = (el.innerText || el.textContent || '').trim();
                if (text.length < 8 || text.length > 12000) continue;
                const childText = Array.from(el.children).map((child) => (child.innerText || '').trim()).filter(Boolean).join('\\n');
                if (childText && childText.length > text.length * 0.92) continue;
                if (seen.has(text)) continue;
                seen.add(text);
                items.push({ text, y: el.getBoundingClientRect().top });
            }
            return items.sort((a, b) => a.y - b.y).map((item) => item.text).slice(-40);
        }"""
        args = {"query": query, "skipTags": tags, "skipClosest": skip_closest}
        candidates: list[str] = []
        for root in [page, *page.frames]:
            try:
                items = await root.evaluate(script, args)
                for item in items or []:
                    if item and not self._is_provider_noise(provider_id, item):
                        candidates.append(str(item).strip())
            except Exception:
                continue
        if candidates and CAPTURE_DEBUG:
            log.info("provider=%s extract_blocks=%s", provider_id, [len(item) for item in candidates[-5:]])
        return candidates

    async def _node_text(self, node, provider_id: str) -> str:
        if provider_id == "deepseek":
            try:
                text = await node.evaluate(MARKDOWN_WALKER_JS)
                return (text or "").strip()
            except Exception:
                pass
        try:
            return (await node.inner_text(timeout=1_000)).strip()
        except Exception:
            return ""

    # 复制按钮选择器（与抓取共用）：出现即视为答案已生成完毕。
    _COPY_BUTTON_SELECTORS = [
        "button[aria-label*='复制']",
        "[role='button'][aria-label*='复制']",
        "button[title*='复制']",
        "div[aria-label*='复制']",
        "button[aria-label*='Copy' i]",
        "button[title*='Copy' i]",
        "[data-testid*='copy']",
        "[class*='copy'] button",
        "button:has-text('复制')",
    ]

    async def _copy_button_count(self, page: Page) -> int:
        best = 0
        for selector in self._COPY_BUTTON_SELECTORS:
            try:
                best = max(best, await page.locator(selector).count())
            except Exception:
                continue
        return best

    async def _has_copy_button(self, page: Page) -> bool:
        return await self._copy_button_count(page) > 0

    async def _wait_until_copy_ready(
        self,
        page: Page,
        provider_id: str,
        before: list[str],
        prompt: str,
        before_copy_count: int = 0,
    ) -> str:
        """完成判定（统一）：等待本轮新增回答稳定后再抓取。

        (a) 本轮新增复制按钮出现，且内容稳定至少两轮；
        (b) 内容长时间稳定且不在生成中。
        二者皆未命中则继续，直到 RUN_TIMEOUT_MS 兜底。返回当前抓到的最长正文。
        """
        await asyncio.sleep(6)
        deadline = asyncio.get_event_loop().time() + RUN_TIMEOUT_MS / 1000
        best = ""
        best_norm = ""
        plateau = 0
        saw_generating = False
        composer_ready = False
        conservative_plateau = 10 if provider_id in {"qwen", "doubao"} else 4
        copy_ready_plateau = 2 if provider_id in {"qwen", "doubao"} else 1
        while asyncio.get_event_loop().time() < deadline:
            current = await self._conversation_snapshot(page, provider_id)
            new_text = self._new_conversation_text(provider_id, before, current, prompt)
            new_norm = self._normalize_text(new_text)
            if len(new_norm) > len(best_norm):
                best, best_norm, plateau = new_text, new_norm, 0
            elif best_norm:
                plateau += 1
            generating = await self._is_generating(page, provider_id)
            if generating:
                saw_generating = True
            if saw_generating and generating is False:
                composer_ready = True
            copy_count = await self._copy_button_count(page)
            has_new_copy = copy_count > before_copy_count
            if CAPTURE_DEBUG:
                _capture_debug(provider_id, "ready",
                               f"len={len(new_norm)} best={len(best_norm)} plateau={plateau} "
                               f"copy_count={copy_count} before_copy={before_copy_count} "
                               f"new_copy={has_new_copy} gen={generating} saw_gen={saw_generating} "
                               f"composer_ready={composer_ready}")
            # (a) 提问框附近的 Stop 已恢复为发送按钮，且正文稳定：这是最接近用户手动复制的时机。
            if best_norm and composer_ready and plateau >= copy_ready_plateau:
                _capture_debug(provider_id, "ready", "发送按钮恢复，返回")
                return self._trim_result(best)
            # (b) 本轮新增复制按钮出现，且确认不在生成中。旧回答复制按钮不再作为完成信号。
            if best_norm and has_new_copy and generating is False and plateau >= copy_ready_plateau:
                _capture_debug(provider_id, "ready", "本轮复制按钮就绪，返回")
                return self._trim_result(best)
            # (c) 内容长时间稳定且非生成中 → 已完成（不依赖 hover）。
            if best_norm and plateau >= conservative_plateau and generating is not True:
                _capture_debug(provider_id, "ready", "内容稳定，返回")
                return self._trim_result(best)
            # 豆包/千问有时页面会残留可见的“停止”类控件，导致 generating 长期误判为 True。
            # 如果正文已经长时间不再增长，优先保证用户及时拿到稳定回答。
            if provider_id in {"qwen", "doubao"} and best_norm and plateau >= max(18, conservative_plateau + 6):
                _capture_debug(provider_id, "ready", "内容长期稳定，生成状态疑似卡住，返回")
                return self._trim_result(best)
            await asyncio.sleep(1)
        _capture_debug(provider_id, "ready", "超时兜底返回")
        return self._trim_result(best)

    async def _composer_generation_state(self, page: Page, provider_id: str) -> bool | None:
        """Use the prompt-box button as the primary generation signal.

        During generation most providers turn the send button near the input into
        a Stop/Pause button. When that nearby button returns to a send button,
        the answer is ready for copy capture.
        """
        script = r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const norm = (value) => String(value || '').toLowerCase();
            const labelOf = (el) => norm([
                el.getAttribute && el.getAttribute('aria-label'),
                el.getAttribute && el.getAttribute('title'),
                el.getAttribute && el.getAttribute('data-testid'),
                el.getAttribute && el.getAttribute('class'),
                el.innerText,
                el.textContent,
            ].filter(Boolean).join(' '));
            const stopWords = ['停止生成', '停止响应', '停止', '暂停', '中止', 'stop generating', 'stop', 'pause'];
            const sendWords = ['发送', 'send', 'submit'];
            const inputs = Array.from(document.querySelectorAll(
                'textarea, [contenteditable="true"], div[role="textbox"], [data-testid*="input" i]'
            )).filter(visible);
            const input = inputs[inputs.length - 1];
            if (!input) return null;
            const ir = input.getBoundingClientRect();
            const buttons = Array.from(document.querySelectorAll('button, [role="button"], [aria-label]'))
                .filter((button) => {
                    if (!visible(button)) return false;
                    const rect = button.getBoundingClientRect();
                    if (rect.width < 12 || rect.height < 12) return false;
                    const nearY = rect.top >= ir.top - 120 && rect.bottom <= ir.bottom + 160;
                    const nearX = rect.left >= ir.left - 160 && rect.right <= ir.right + 220;
                    return nearY && nearX;
                });
            let sawSend = false;
            for (const button of buttons) {
                const label = labelOf(button);
                if (!label) continue;
                if (stopWords.some((word) => label.includes(word))) return true;
                if (sendWords.some((word) => label.includes(word))) sawSend = true;
            }
            return sawSend ? false : null;
        }"""
        results: list[bool | None] = []
        for root in [page, *page.frames]:
            try:
                value = await root.evaluate(script)
            except Exception:
                continue
            if value is True:
                _capture_debug(provider_id, "composer_state", "stop")
                return True
            results.append(value)
        if any(r is False for r in results):
            _capture_debug(provider_id, "composer_state", "send")
            return False
        return None

    async def _wait_for_response(self, page: Page, provider_id: str, before: list[str], prompt: str) -> str:
        deadline = asyncio.get_event_loop().time() + RUN_TIMEOUT_MS / 1000
        best = ""
        best_norm = ""
        prev_norm = None
        stable_after_done = 0   # 「已停止生成」后内容连续不变的轮数
        plateau = 0             # 长度连续不增长的轮数（仅在无法判定生成状态时作兜底）
        saw_generating = False  # 是否曾检测到「正在生成」，用于区分"还没开始"与"已结束"
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(2)
            current = await self._conversation_snapshot(page, provider_id)
            new_text = self._new_conversation_text(provider_id, before, current, prompt)
            new_norm = self._normalize_text(new_text)
            generating = await self._is_generating(page, provider_id)
            if generating:
                saw_generating = True
            if CAPTURE_DEBUG:
                _capture_debug(
                    provider_id,
                    "wait",
                    f"len={len(new_norm)} best={len(best_norm)} generating={generating} "
                    f"saw_gen={saw_generating} plateau={plateau} stable_done={stable_after_done}",
                )

            # keep-best：流式只增不减，保留见过的最长内容并跟踪是否还在增长。
            if len(new_norm) > len(best_norm):
                best = new_text
                best_norm = new_norm
                plateau = 0
            elif best_norm:
                plateau += 1

            # 内容是否与上一轮一致（用于"已停止生成"后的二次确认，防止刚停瞬间还在补字）。
            content_unchanged = bool(new_norm) and new_norm == prev_norm
            prev_norm = new_norm

            if best_norm:
                if generating is False and saw_generating:
                    # 明确检测到生成已结束：再确认 1~2 轮内容不变即返回，绝不提前截断。
                    stable_after_done = stable_after_done + 1 if (content_unchanged or plateau >= 1) else 0
                    if stable_after_done >= 2:
                        return self._trim_result(best)
                elif generating is None:
                    # 无法判定生成状态（找不到停止按钮等信号）：用更保守的长时间无增长兜底。
                    if plateau >= 6:
                        return self._trim_result(best)
                # generating is True：仍在生成，继续等待，不返回。
        if not best_norm:
            await self._dump_empty_capture(page, provider_id, before, prompt)
        return self._trim_result(best)

    async def _is_generating(self, page: Page, provider_id: str) -> bool | None:
        """判断页面是否仍在生成回答。

        返回 True=正在生成，False=已停止，None=无法判定（交给调用方走兜底）。
        依据：优先看提问框附近的按钮是否从 Stop 恢复为发送；无法识别时再扫全页停止按钮。
        """
        composer_state = await self._composer_generation_state(page, provider_id)
        if composer_state is not None:
            return composer_state
        script = r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
            };
            const texts = ['停止生成', '停止响应', '停止', 'Stop generating', 'Stop', '暂停'];
            const candidates = Array.from(document.querySelectorAll(
                "button, [role='button'], [aria-label], [class*='stop'], [class*='Stop']"
            ));
            let found = false;
            let sawAny = false;
            for (const el of candidates) {
                const label = ((el.getAttribute && (el.getAttribute('aria-label') || '')) + ' ' + (el.innerText || el.textContent || '')).trim();
                if (!label) continue;
                for (const t of texts) {
                    if (label.includes(t)) {
                        sawAny = true;
                        if (visible(el)) found = true;
                    }
                }
            }
            // 找到了停止按钮且可见 -> 正在生成；找到过但已不可见 -> 已结束。
            if (found) return true;
            if (sawAny) return false;
            return null;
        }"""
        results: list[bool | None] = []
        for root in [page, *page.frames]:
            try:
                value = await root.evaluate(script)
            except Exception:
                continue
            if value is True:
                return True
            results.append(value)
        if any(r is False for r in results):
            return False
        return None

    async def _dump_empty_capture(self, page: Page, provider_id: str, before: list[str], prompt: str) -> None:
        """抓空时把页面真实文本落盘，便于离线判断是"没抓到"还是"抓到被过滤"。"""
        try:
            current = await self._conversation_snapshot(page, provider_id)
        except Exception as exc:
            current = [f"<snapshot failed: {exc}>"]
        try:
            raw = await page.evaluate(
                "() => { const m = document.querySelector('main') || document.body; "
                "return (m.innerText || '').slice(-4000); }"
            )
        except Exception as exc:
            raw = f"<innerText failed: {exc}>"
        _capture_debug(
            provider_id,
            "EMPTY_CAPTURE",
            f"url={page.url} before={len(before)} current={len(current)}",
        )
        _capture_debug(provider_id, "EMPTY_CAPTURE current_samples", [item[:120] for item in current[-8:]])
        _capture_debug(provider_id, "EMPTY_CAPTURE page_innerText_tail", repr(raw))

    def _new_conversation_text(self, provider_id: str, before: list[str], current: list[str], prompt: str) -> str:
        before_set = {self._normalize_text(item) for item in before}
        norm_prompt = self._normalize_text(prompt)
        candidates = []
        dropped = {"before": 0, "prompt": 0, "noise": 0}
        for item in current:
            normalized = self._normalize_text(item)
            if not normalized or normalized in before_set:
                dropped["before"] += 1
                continue
            if self._is_provider_noise(provider_id, item):
                dropped["noise"] += 1
                continue
            # prompt 被回显进回答容器时，剥离 prompt 子串而非丢弃整条。
            stripped = item
            if norm_prompt and norm_prompt in normalized:
                stripped = item.replace(prompt, "").strip()
                if not self._normalize_text(stripped) or self._normalize_text(stripped) == norm_prompt:
                    dropped["prompt"] += 1
                    continue
            candidates.append(stripped)
        if candidates:
            if provider_id == "deepseek":
                return max(candidates, key=lambda item: len(self._normalize_text(item))).strip()
            return self._merge_candidates(candidates, provider_id)

        if before and current:
            old = before[-1]
            new = current[-1]
            if new.startswith(old):
                diff = new[len(old):].strip()
                if norm_prompt not in self._normalize_text(diff):
                    return diff
        if CAPTURE_DEBUG:
            _capture_debug(
                provider_id,
                "new_text_empty",
                f"before={len(before)} current={len(current)} dropped={dropped} "
                f"tail={[self._normalize_text(item)[:80] for item in current[-3:]]}",
            )
        return ""

    def _dedupe_candidates(self, candidates: list[str]) -> list[str]:
        """按 DOM 顺序双向包含式去重：丢弃属于已留块子串的候选，并用更长候选替换被包含的已留块。"""
        kept: list[str] = []
        kept_norm: list[str] = []
        for item in candidates:
            normalized = self._normalize_text(item)
            if not normalized:
                continue
            if any(normalized in existing for existing in kept_norm):
                continue
            survivors = [(k, kn) for k, kn in zip(kept, kept_norm) if kn not in normalized]
            kept = [k for k, _ in survivors]
            kept_norm = [kn for _, kn in survivors]
            kept.append(item.strip())
            kept_norm.append(normalized)
        return kept

    def _merge_candidates(self, candidates: list[str], provider_id: str) -> str:
        unique: list[str] = []
        for item in self._dedupe_candidates(candidates):
            if len(self._normalize_text(item)) < 12:
                continue
            unique.append(item)
        if not unique:
            return ""
        merged = "\n\n".join(unique[-12:])
        longest = max(unique, key=lambda item: len(self._normalize_text(item)))
        return merged if len(self._normalize_text(merged)) > len(self._normalize_text(longest)) else longest.strip()

    def _is_provider_noise(self, provider_id: str, text: str) -> bool:
        normalized = self._normalize_text(text)
        # 「思考中」占位符不算稳定回答，避免提前判完成（qwen/doubao 流式期间出现）。
        if provider_id in {"qwen", "doubao"} and len(normalized) <= 16:
            if re.fullmatch(r"(正在思考|思考中|深度思考中?|生成中|加载中|请稍候)[.。·…\s]*", normalized):
                return True
        if provider_id == "doubao":
            if len(normalized) <= 80 and "搜索" in normalized and "关键词" in normalized and "参考" in normalized:
                return True
            if re.fullmatch(r"搜索\s*\d+\s*个关键词\s*参考\s*\d+\s*篇资料.*", normalized):
                return True
        return False

    def _normalize_text(self, text: str) -> str:
        return " ".join((text or "").split())

    def _trim_result(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)[-30000:]

    def _clean_provider_result(self, provider_id: str, text: str) -> str:
        if provider_id == "deepseek":
            return self._clean_deepseek_result(text)
        if provider_id == "qwen":
            return self._clean_qwen_result(text)
        if provider_id != "doubao":
            return text
        return self._strip_doubao_suggestions(text)

    def _clean_qwen_result(self, text: str) -> str:
        """剥离千问回答末尾的「推荐提问/追问」块：结尾连续若干行短建议（问句或祈使追问）剥掉。

        千问会在正文下方追加可点击的推荐追问（如"推荐几个端午节祝福的金句""给这篇短文起几个吸引人的标题"），
        innerText 线性化会把它们当正文行抓进来。这里从末尾起连续剥（允许空行分隔）。
        """
        lines = [line.rstrip() for line in (text or "").splitlines()]
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return ""
        cutoff = len(lines)
        suggestion_count = 0
        for idx in range(len(lines) - 1, -1, -1):
            s = lines[idx].strip()
            if not s:
                continue  # 允许空行分隔，不中断
            if self._looks_like_qwen_suggestion(s):
                suggestion_count += 1
                cutoff = idx
                continue
            break
        if suggestion_count >= 2 and cutoff > 0:
            return "\n".join(lines[:cutoff]).strip()
        return "\n".join(lines).strip()

    # 千问推荐追问的祈使开头（这类行不以问号结尾，但是追问建议而非正文）。
    _QWEN_SUGGEST_PREFIXES = (
        "推荐", "给这篇", "给这", "增加一些", "增加几", "换一", "换个", "再写", "帮我写", "帮我",
        "写几个", "写一篇", "起几个", "列几个", "举几个", "扩展", "续写", "总结一下这",
    )

    def _looks_like_qwen_suggestion(self, line: str) -> bool:
        compact = "".join(line.split())
        if not compact or len(compact) > 36:
            return False
        # (a) 单句短问：问号结尾、无句中句号分段。
        if compact.endswith(("？", "?")):
            if compact.count("。") == 0 and compact.count("？") + compact.count("?") == 1:
                return True
        # (b) 祈使式追问：以推荐/给…起/增加…等开头的短建议行（不以句号结尾的命令式）。
        if compact.endswith("。"):
            return False
        if any(compact.startswith(p) for p in self._QWEN_SUGGEST_PREFIXES):
            return True
        return False

    def _clean_deepseek_result(self, text: str) -> str:
        lines = [line.rstrip() for line in (text or "").splitlines()]
        cleaned: list[str] = []
        idx = 0
        while idx < len(lines):
            line = lines[idx].strip()
            if (
                idx + 4 < len(lines)
                and line.endswith("-")
                and lines[idx + 1].strip().isdigit()
                and lines[idx + 2].strip() == "-"
                and lines[idx + 3].strip().isdigit()
                and lines[idx + 4].strip() in {"。", ".", "，", ",", "；", ";", ":", "："}
            ):
                cleaned.append(f"{line[:-1].rstrip()}{lines[idx + 4].strip()}")
                idx += 5
                continue
            if (
                idx + 4 < len(lines)
                and line.endswith("-")
                and lines[idx + 1].strip().isdigit()
                and lines[idx + 2].strip() == "-"
                and lines[idx + 3].strip().isdigit()
                and self._starts_with_punctuation(lines[idx + 4].strip())
            ):
                cleaned.append(f"{line[:-1].rstrip()}{lines[idx + 4].strip()}")
                idx += 5
                continue
            if (
                idx + 3 < len(lines)
                and line.endswith("-")
                and lines[idx + 1].strip().isdigit()
                and lines[idx + 2].strip() == "-"
                and lines[idx + 3].strip().isdigit()
            ):
                cleaned.append(line[:-1].rstrip())
                idx += 4
                continue
            if (
                idx + 2 < len(lines)
                and line.endswith("-")
                and lines[idx + 1].strip().isdigit()
                and lines[idx + 2].strip() in {"。", ".", "，", ",", "；", ";", ":", "："}
            ):
                cleaned.append(f"{line[:-1].rstrip()}{lines[idx + 2].strip()}")
                idx += 3
                continue
            if (
                idx + 2 < len(lines)
                and line.endswith("-")
                and lines[idx + 1].strip().isdigit()
                and self._starts_with_punctuation(lines[idx + 2].strip())
            ):
                cleaned.append(f"{line[:-1].rstrip()}{lines[idx + 2].strip()}")
                idx += 3
                continue
            if idx + 1 < len(lines) and line.endswith("-") and lines[idx + 1].strip().isdigit():
                cleaned.append(line[:-1].rstrip())
                idx += 2
                continue
            if line == "-" and idx + 2 < len(lines) and lines[idx + 1].strip().isdigit() and lines[idx + 2].strip() == "-":
                idx += 3
                continue
            if line.isdigit() and idx > 0 and idx + 1 < len(lines) and lines[idx + 1].strip() == "-":
                idx += 2
                continue
            cleaned.append(line)
            idx += 1
        text = "\n".join(item for item in cleaned if item)
        text = re.sub(r"(?<=[\u4e00-\u9fffA-Za-z0-9%）)])-\d+(?:-\d+)*(?=([。.,，、；;:：）)\n]|$))", "", text)
        text = re.sub(r"[（(]?(?:-\d+(?:-\d+)?)(?:[、,，]\s*-\d+(?:-\d+)?)+[）)]?", "", text)
        return text.strip()

    def _starts_with_punctuation(self, text: str) -> bool:
        return bool(text) and text[0] in {"。", ".", "，", ",", "；", ";", ":", "："}

    def _strip_doubao_suggestions(self, text: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        if not lines:
            return ""
        lines = self._strip_doubao_search_summary(lines)

        markers = {
            "猜你想问",
            "你可以继续问",
            "相关问题",
            "试试这样问",
            "大家还在问",
            "推荐问题",
            "换一批",
        }
        for idx, line in enumerate(lines):
            if any(marker in line for marker in markers):
                kept = lines[:idx]
                return "\n".join(kept).strip()

        cutoff = len(lines)
        suggestion_count = 0
        for idx in range(len(lines) - 1, -1, -1):
            line = lines[idx]
            if self._looks_like_doubao_suggestion(line):
                suggestion_count += 1
                cutoff = idx
                continue
            break
        if suggestion_count >= 2 and cutoff > 0:
            return "\n".join(lines[:cutoff]).strip()
        return "\n".join(lines).strip()

    def _strip_doubao_search_summary(self, lines: list[str]) -> list[str]:
        result = list(lines)
        summary_pattern = re.compile(
            r"^搜索\s*\d+\s*个关键词[，, ]*参考\s*\d+\s*篇资料(?:[，, ].{0,30}?\d+\s*处)?\s*"
        )
        while result:
            first = result[0].strip()
            match = summary_pattern.match(first)
            if match:
                trailing = first[match.end():].strip()
                if trailing:
                    result[0] = trailing
                    break
                result.pop(0)
                continue
            break
        return [line for line in result if line]

    def _looks_like_doubao_suggestion(self, line: str) -> bool:
        compact = "".join(line.split())
        if not compact:
            return False
        if len(compact) > 42:
            return False
        if compact.endswith(("？", "?", "。", ".")) and (
            "用" in compact
            or "如何" in compact
            or "怎么" in compact
            or "创作" in compact
            or "写" in compact
            or "一句" in compact
        ):
            return True
        if "用" in compact and "创作一句" in compact:
            return True
        # 通用：≤36 字的单句短问（问号结尾、无句号分段）几乎一定是推荐追问。
        if len(compact) <= 36 and compact.endswith(("？", "?")):
            if "。" not in compact and (compact.count("？") + compact.count("?")) == 1:
                return True
        return False


bridge = AIWebBridge()
