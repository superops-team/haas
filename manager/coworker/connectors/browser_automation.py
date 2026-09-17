"""Playwright-backed browser automation tools for Cowork.

The dependency is optional. If Playwright or its browser binaries are not installed, the
tools return a clear setup error instead of breaking engine construction.
"""

from __future__ import annotations

import base64
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

import aisuite as ai

from ..secrets import state_dir
from ..web.guard import check_url

try:  # Optional dependency: browser tools degrade cleanly when it is absent.
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - exercised in installs without [browser]
    sync_playwright = None  # type: ignore[assignment]


_BROWSER_PROFILE_DIR = "managed-browser-profile"
_BROWSER_DOWNLOAD_DIR = "managed-browser-downloads"
_BROWSER_KIND = "managed_chromium"


def _meta(name: str, *, approval: bool = False, capabilities: Optional[list[str]] = None):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities or ["browser"],
        requires_approval=approval,
    )


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _attach(fn: Callable[..., Any], schema: dict[str, Any], *, approval: bool = True):
    from .tool_defs import approval_for_tool

    name = schema["function"]["name"]
    # §36: the tool registry's read/write kind wins for registered tools — reads never gate.
    approval = approval_for_tool(name, default=approval)
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, approval=approval)
    fn.__doc__ = schema["function"]["description"]
    return fn


class _BrowserController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._error: Optional[str] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coworker-browser")
        self._state: dict[str, Any] = {
            "open": False,
            "url": "",
            "title": "",
            "status": "closed",
            "last_action": "",
            "last_result": "",
            "last_error": "",
            "screenshot_data_url": "",
            "updated_at": None,
            "controls": [],
            "browser": _BROWSER_KIND,
            "managed": True,
            "profile": "app_owned",
            "executable": "managed_runtime",
            "headless": True,
        }

    def _touch(self, **changes: Any) -> None:
        self._state.update(changes)
        self._state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _refresh_page_state(self) -> None:
        if self._page is None:
            self._touch(open=False, status="closed", url="", title="", controls=[])
            return
        try:
            snap = _snapshot(self._page, 2000)
            self._touch(
                open=True,
                status="open",
                url=self._page.url,
                title=self._page.title(),
                controls=snap.get("controls", [])[:30],
            )
        except Exception as exc:
            self._touch(open=True, status="error", last_error=_safe_error_text(exc))

    def _setup_error(self, exc: Exception) -> dict[str, str]:
        return {
            "error": (
                "Managed browser automation requires Playwright with its Chromium runtime. "
                "Install it with `pip install playwright` and "
                "`python -m playwright install chromium`."
            ),
            "details": _safe_error_text(exc),
        }

    def _teardown_locked(self) -> None:
        for resource in (self._context, self._browser):
            if resource is None:
                continue
            try:
                resource.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def _managed_paths(self) -> tuple[Path, Path]:
        root = state_dir() / "browser-harness"
        profile = root / _BROWSER_PROFILE_DIR
        downloads = root / _BROWSER_DOWNLOAD_DIR
        profile.mkdir(parents=True, exist_ok=True)
        downloads.mkdir(parents=True, exist_ok=True)
        return profile, downloads

    def _browser_executable(self, browser_type) -> tuple[str | None, str]:
        override = os.environ.get("OPENHARNESS_BROWSER_EXECUTABLE") or os.environ.get(
            "BROWSER_EXECUTABLE_PATH"
        )
        if override:
            return str(Path(override).expanduser()), "developer_override"
        for candidate in self._browser_executable_candidates(browser_type):
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate), "managed_runtime"
        return None, "unavailable"

    def _browser_executable_candidates(self, browser_type) -> list[Path]:
        candidates: list[Path] = []
        packaged_root = Path(sys.executable).resolve().parent
        candidates.extend(
            _chromium_candidates_from_root(
                packaged_root / "playwright-browsers", prefer_headless=self._headless()
            )
        )
        candidates.extend(
            _chromium_candidates_from_root(
                packaged_root / "_internal" / "ms-playwright", prefer_headless=self._headless()
            )
        )
        expected = getattr(browser_type, "executable_path", None)
        if expected:
            expected_path = Path(str(expected)).expanduser()
            if self._headless():
                expected_root = _playwright_browser_root(expected_path)
                if expected_root is not None:
                    candidates.extend(
                        _chromium_candidates_from_root(expected_root, prefer_headless=True)
                    )
            candidates.append(expected_path)
        env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if env_root and env_root != "0":
            candidates.extend(
                _chromium_candidates_from_root(
                    Path(env_root).expanduser(), prefer_headless=self._headless()
                )
            )
        if sys.platform == "darwin":
            candidates.extend(
                _chromium_candidates_from_root(
                    Path.home() / "Library/Caches/ms-playwright",
                    prefer_headless=self._headless(),
                )
            )
        else:
            candidates.extend(
                _chromium_candidates_from_root(
                    Path.home() / ".cache/ms-playwright", prefer_headless=self._headless()
                )
            )
        seen: set[str] = set()
        unique: list[Path] = []
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _headless(self) -> bool:
        value = os.environ.get("OPENHARNESS_BROWSER_HEADLESS", "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _launch_context(self):
        profile, downloads = self._managed_paths()
        browser_type = self._playwright.chromium
        executable_path, executable_source = self._browser_executable(browser_type)
        if not executable_path:
            raise RuntimeError(
                "managed browser executable unavailable: expected bundled or Playwright "
                "Chrome for Testing runtime, not user Chrome"
            )
        kwargs: dict[str, Any] = {
            "headless": self._headless(),
            "viewport": {"width": 1280, "height": 900},
            "accept_downloads": True,
            "downloads_path": str(downloads),
            "args": [
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        }
        if executable_path:
            kwargs["executable_path"] = executable_path
        context = browser_type.launch_persistent_context(str(profile), **kwargs)
        self._context = context
        self._browser = getattr(context, "browser", None)
        self._page = context.pages[0] if getattr(context, "pages", None) else context.new_page()
        self._touch(
            open=True,
            status="open",
            last_action="open managed browser",
            last_result="ok",
            last_error="",
            browser=_BROWSER_KIND,
            managed=True,
            profile="app_owned",
            executable=executable_source,
            headless=self._headless(),
        )
        return self._page

    @staticmethod
    def _is_closed_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "target page, context or browser has been closed",
                "browser has been closed",
                "target closed",
                "context closed",
                "page closed",
            )
        )

    def page(self):
        with self._lock:
            if self._error:
                return None, {"error": self._error}
            if self._page is not None:
                try:
                    if not self._page.is_closed():
                        return self._page, None
                except Exception:
                    pass
                self._teardown_locked()
            try:
                if sync_playwright is None:
                    raise RuntimeError("playwright is not installed")
                self._playwright = sync_playwright().start()
                return self._launch_context(), None
            except Exception as exc:
                self._touch(open=False, status="error", last_error=_safe_error_text(exc))
                self._teardown_locked()
                return None, self._setup_error(exc)

    def _submit(self, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        return self._executor.submit(fn).result()

    def close(self) -> dict[str, Any]:
        return self._submit(self._close_locked)

    def _close_locked(self) -> dict[str, Any]:
        with self._lock:
            try:
                self._teardown_locked()
            except Exception as exc:
                return {"error": str(exc)}
            finally:
                self._touch(open=False, status="closed", url="", title="", controls=[])
            return {"ok": True}

    def state(self) -> dict[str, Any]:
        return self._submit(self._state_locked)

    def _state_locked(self) -> dict[str, Any]:
        with self._lock:
            self._refresh_page_state()
            return dict(self._state)

    def screenshot(self) -> dict[str, Any]:
        return self._submit(self._screenshot_locked)

    def _screenshot_locked(self) -> dict[str, Any]:
        with self._lock:
            for attempt in range(2):
                page, err = self.page()
                if err:
                    return err
                try:
                    png = page.screenshot(full_page=False)
                    data_url = "data:image/png;base64," + base64.b64encode(png).decode(
                        "ascii"
                    )
                    self._touch(
                        screenshot_data_url=data_url,
                        last_action="screenshot",
                        last_result="ok",
                        last_error="",
                    )
                    self._refresh_page_state()
                    return {"ok": True, **dict(self._state)}
                except Exception as exc:
                    if attempt == 0 and self._should_rebuild_after_browser_error(exc):
                        self._touch(
                            last_action="screenshot",
                            last_result="restarting",
                            last_error="managed browser screenshot failed; rebuilding",
                        )
                        self._teardown_locked()
                        continue
                    error = _safe_error_text(exc)
                    self._touch(
                        last_action="screenshot", last_result="error", last_error=error
                    )
                    return {"error": error}
            return {"error": "browser screenshot failed after rebuild"}

    def call(self, action: str, fn: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            with self._lock:
                out: dict[str, Any] = {}
                for attempt in range(2):
                    page, err = self.page()
                    if err:
                        return err
                    self._touch(last_action=action, last_result="running", last_error="")
                    try:
                        out = fn(page)
                        break
                    except Exception as exc:
                        if attempt == 0 and self._should_rebuild_after_browser_error(exc):
                            self._touch(
                                last_action=action,
                                last_result="restarting",
                                last_error="managed browser closed; rebuilding",
                            )
                            self._teardown_locked()
                            continue
                        out = {"error": _safe_error_text(exc)}
                        break
                if "error" in out:
                    self._touch(
                        last_action=action,
                        last_result="error",
                        last_error=_safe_error_text(out["error"]),
                    )
                else:
                    self._refresh_page_state()
                    self._touch(last_action=action, last_result="ok", last_error="")
                return out

        return self._submit(run)

    @classmethod
    def _should_rebuild_after_browser_error(cls, exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            cls._is_closed_error(exc)
            or "capturescreenshot" in text.replace(" ", "")
        )


_BROWSER = _BrowserController()


def browser_state() -> dict[str, Any]:
    return _BROWSER.state()


def browser_take_screenshot() -> dict[str, Any]:
    return _BROWSER.screenshot()


def browser_close_session() -> dict[str, Any]:
    return _BROWSER.close()


def _chromium_candidates_from_root(root: Path, *, prefer_headless: bool = True) -> list[Path]:
    if not root.exists():
        return []
    headless_patterns = [
        "chromium_headless_shell-*/chrome-headless-shell-mac*/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-linux*/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-win*/chrome-headless-shell.exe",
    ]
    chromium_patterns = [
        (
            "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/"
            "Google Chrome for Testing"
        ),
        "chromium-*/chrome-linux*/chrome",
        "chromium-*/chrome-win*/chrome.exe",
    ]
    patterns = (
        headless_patterns + chromium_patterns
        if prefer_headless
        else chromium_patterns + headless_patterns
    )
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(_sort_playwright_candidates(root.glob(pattern)))
    return candidates


def _playwright_browser_root(path: Path) -> Path | None:
    for parent in path.parents:
        if parent.name.startswith("chromium-") or parent.name.startswith("chromium_headless_shell-"):
            return parent.parent
    return None


def _sort_playwright_candidates(paths) -> list[Path]:
    def version(path: Path) -> int:
        for part in path.parts:
            if part.startswith("chromium-") or part.startswith("chromium_headless_shell-"):
                return _trailing_int(part)
        return -1

    return sorted(paths, key=lambda path: (version(path), str(path)), reverse=True)


def _trailing_int(value: str) -> int:
    match = re.search(r"-(\d+)$", value)
    return int(match.group(1)) if match else -1


def _safe_error_text(value: object) -> str:
    text = str(value)
    text = re.sub(r"/(?:Users|Applications|private|tmp|var|Volumes)/[^\s\"']+", "[path]", text)
    text = re.sub(r"[A-Za-z]:\\[^\s\"']+", "[path]", text)
    return text


def _cap(value: int, default: int = 20000, upper: int = 100000) -> int:
    try:
        return max(1, min(int(value or default), upper))
    except Exception:
        return default


def _target_locator(page, target: str):
    target = target.strip()
    if target.startswith("text="):
        return page.get_by_text(target[5:], exact=False).first
    if target.startswith("role="):
        role_name = target[5:]
        role, _, name = role_name.partition(":")
        return page.get_by_role(role.strip(), name=name.strip() or None).first
    try:
        return page.locator(target).first
    except Exception:
        return page.get_by_text(target, exact=False).first


def _safe_call(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        return {"error": str(exc)}


def _browser_call(action: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    return _BROWSER.call(action, lambda _page: fn())


_SNAPSHOT_JS = """
() => {
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style && style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
  };
  const labelFor = (el) => {
    if (el.labels && el.labels.length) return Array.from(el.labels).map(l => l.innerText.trim()).filter(Boolean).join(' ');
    const id = el.getAttribute('id');
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label) return label.innerText.trim();
    }
    return '';
  };
  const describe = (el, i) => ({
    index: i,
    tag: el.tagName.toLowerCase(),
    type: el.getAttribute('type') || '',
    id: el.getAttribute('id') || '',
    name: el.getAttribute('name') || '',
    role: el.getAttribute('role') || '',
    aria: el.getAttribute('aria-label') || '',
    label: labelFor(el),
    placeholder: el.getAttribute('placeholder') || '',
    text: (el.innerText || el.value || '').trim().slice(0, 200),
    href: el.getAttribute('href') || '',
    selectorHint: el.getAttribute('id') ? `#${CSS.escape(el.getAttribute('id'))}` : (el.getAttribute('name') ? `[name="${el.getAttribute('name')}"]` : '')
  });
  const controls = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[contenteditable="true"]'))
    .filter(visible)
    .slice(0, 120)
    .map(describe);
  return {
    title: document.title,
    url: location.href,
    text: document.body ? document.body.innerText : '',
    controls
  };
}
"""


def _snapshot(page, max_chars: int) -> dict[str, Any]:
    data = page.evaluate(_SNAPSHOT_JS)
    text = re.sub(r"\n{3,}", "\n\n", str(data.get("text") or ""))
    cap = _cap(max_chars)
    return {
        "title": data.get("title"),
        "url": data.get("url"),
        "text": text[:cap],
        "truncated": len(text) > cap,
        "controls": data.get("controls") or [],
    }


def redirect_refusal(requested: str, final: str) -> Optional[str]:
    """A refusal reason if navigation LANDED somewhere the address guard would refuse.

    `check_url` vets the URL the model supplied; Playwright then follows redirects, and the
    hop that actually loads is a different address the guard never saw (OPE-124). A public
    shortener can land on the cloud metadata endpoint or a router admin page, and the
    approval the user gave was for the first URL, not this one.

    The request has already gone out by the time this runs — it cannot be prevented here.
    What it prevents is the agent READING the page or interacting with it. Later
    JavaScript- or meta-refresh-driven navigation is still unchecked; only a proxy that
    vets every hop closes that, which is the larger design this defers."""
    if not final or final == requested:
        return None
    return check_url(final)


def make_browser_automation_tools(*, roots: Optional[list[Any]] = None) -> list[Callable[..., Any]]:
    tools: list[Callable[..., Any]] = []

    def _readable_source(raw: str) -> tuple[Any, dict[str, Any] | None]:
        """A local file to upload, resolved inside a granted root (OPE-122).

        These tools touch the filesystem but classify EXTERNAL, so the permission engine's
        root scoping — which only runs for WRITE_LOCAL — never sees them. Without this
        check the only thing between `~/.ssh/id_rsa` and a web form is someone reading the
        approval card. Mirrors `email_send`'s attachment rule, which solves the same
        problem for outgoing mail."""
        allowed = [r.path for r in (roots or [])]
        if not allowed:
            return None, {"error": "no session directory is available to upload from"}
        path = Path(str(raw)).expanduser().resolve()
        if not any(path.is_relative_to(root) for root in allowed):
            return None, {"error": f"{path} is outside the session's directories"}
        return path, None

    def _writable_target(raw: str) -> tuple[Any, dict[str, Any] | None]:
        """Where a screenshot may land: inside a WRITABLE granted root. An unnamed target
        keeps the temp-file default, which is not a place the user asked us to protect."""
        writable = [r.path for r in (roots or []) if r.writable]
        if not writable:
            return None, {"error": "no writable session directory for the screenshot"}
        path = Path(str(raw)).expanduser().resolve()
        if not any(path.is_relative_to(root) for root in writable):
            return None, {"error": f"{path} is outside the session's writable directories"}
        return path, None

    def browser_open_url(url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        if not url.lower().startswith(("http://", "https://")):
            return {"error": "url must start with http:// or https://"}
        # Same address guard as web_fetch. This is approval gated, so it is defense in
        # depth, not the primary control. It checks the initial model supplied URL only;
        # redirects that the browser follows internally are not hop checked here.
        blocked = check_url(url)
        if blocked:
            return {"error": blocked}

        def _open(page):
            page.goto(url, wait_until=wait_until, timeout=30000)
            landed = redirect_refusal(url, page.url)
            if landed:
                # Leave nothing readable behind: the next snapshot/get_text must not be
                # able to lift content off a page we just refused.
                final = page.url
                page.goto("about:blank")
                return {"error": f"redirected to {final} — {landed}"}
            return {"ok": True, "url": page.url}

        return _BROWSER.call("open_url", _open)

    browser_open_url.__name__ = "browser_open_url"
    tools.append(
        _attach(
            browser_open_url,
            _schema(
                "browser_open_url",
                "Open a URL in the local Playwright browser session.",
                {"url": {"type": "string"}, "wait_until": {"type": "string"}},
                ["url"],
            ),
            approval=True,
        )
    )

    def browser_read_page(max_chars: int = 20000) -> dict[str, Any]:
        return _BROWSER.call("snapshot", lambda page: _snapshot(page, max_chars))

    browser_read_page.__name__ = "browser_read_page"
    tools.append(
        _attach(
            browser_read_page,
            _schema(
                "browser_read_page",
                "Read the current page: its text plus visible controls and selector "
                "hints (for browser_click/browser_type). Not an image — use "
                "browser_screenshot for pixels.",
                {"max_chars": {"type": "integer"}},
                [],
            ),
            approval=True,
        )
    )

    def browser_click(target: str) -> dict[str, Any]:
        return _BROWSER.call(
            "click",
            lambda page: (
                _target_locator(page, target).click(timeout=10000),
                {"ok": True, "url": page.url},
            )[1],
        )

    browser_click.__name__ = "browser_click"
    tools.append(
        _attach(
            browser_click,
            _schema(
                "browser_click",
                "Click a visible page element by CSS selector, text=label, role=button:Name, or text fallback. Requires approval.",
                {"target": {"type": "string"}},
                ["target"],
            ),
            approval=True,
        )
    )

    def browser_type(target: str, text: str, clear: bool = True) -> dict[str, Any]:
        def run(page):
            loc = _target_locator(page, target)
            if clear:
                loc.fill(text, timeout=10000)
            else:
                loc.type(text, timeout=10000)
            return {"ok": True, "url": page.url}

        return _BROWSER.call("type", run)

    browser_type.__name__ = "browser_type"
    tools.append(
        _attach(
            browser_type,
            _schema(
                "browser_type",
                "Fill or type into an input, textarea, or editable element. Requires approval.",
                {
                    "target": {"type": "string"},
                    "text": {"type": "string"},
                    "clear": {"type": "boolean"},
                },
                ["target", "text"],
            ),
            approval=True,
        )
    )

    def browser_select(target: str, value: str) -> dict[str, Any]:
        return _BROWSER.call(
            "select",
            lambda page: (
                _target_locator(page, target).select_option(value, timeout=10000),
                {"ok": True, "url": page.url},
            )[1],
        )

    browser_select.__name__ = "browser_select"
    tools.append(
        _attach(
            browser_select,
            _schema(
                "browser_select",
                "Select an option in a dropdown by selector and option value/label. Requires approval.",
                {"target": {"type": "string"}, "value": {"type": "string"}},
                ["target", "value"],
            ),
            approval=True,
        )
    )

    def browser_upload_file(target: str, path: str) -> dict[str, Any]:
        file_path, err = _readable_source(path)
        if err:
            return err
        if not file_path.exists():
            return {"error": f"file not found: {file_path}"}
        return _BROWSER.call(
            "upload_file",
            lambda page: (
                _target_locator(page, target).set_input_files(str(file_path), timeout=10000),
                {"ok": True, "path": str(file_path)},
            )[1],
        )

    browser_upload_file.__name__ = "browser_upload_file"
    tools.append(
        _attach(
            browser_upload_file,
            _schema(
                "browser_upload_file",
                "Upload a local file through a file input. Requires approval.",
                {"target": {"type": "string"}, "path": {"type": "string"}},
                ["target", "path"],
            ),
            approval=True,
        )
    )

    def browser_wait(milliseconds: int = 1000, target: str = "") -> dict[str, Any]:
        def run(page):
            if target:
                _target_locator(page, target).wait_for(timeout=max(1, int(milliseconds or 1000)))
            else:
                page.wait_for_timeout(max(1, min(int(milliseconds or 1000), 30000)))
            return {"ok": True, "url": page.url}

        return _BROWSER.call("wait", run)

    browser_wait.__name__ = "browser_wait"
    tools.append(
        _attach(
            browser_wait,
            _schema(
                "browser_wait",
                "Wait for a duration or for a target element to appear.",
                {"milliseconds": {"type": "integer"}, "target": {"type": "string"}},
                [],
            ),
            approval=True,
        )
    )

    def browser_screenshot(path: str = "") -> dict[str, Any]:
        if path:
            _target, target_err = _writable_target(path)
            if target_err:
                return target_err

        def run(page):
            out = (
                _target
                if path
                else (Path(tempfile.gettempdir()) / "coworker-browser-screenshot.png").resolve()
            )
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=True)
            return {"ok": True, "path": str(out), "url": page.url}

        return _BROWSER.call("screenshot", run)

    browser_screenshot.__name__ = "browser_screenshot"
    tools.append(
        _attach(
            browser_screenshot,
            _schema(
                "browser_screenshot",
                "Save a full-page screenshot of the current browser page and return the local path.",
                {"path": {"type": "string"}},
                [],
            ),
            approval=True,
        )
    )

    def browser_close() -> dict[str, Any]:
        return browser_close_session()

    browser_close.__name__ = "browser_close"
    tools.append(
        _attach(
            browser_close,
            _schema(
                "browser_close",
                "Close the local Playwright browser session.",
                {},
                [],
            ),
            approval=True,
        )
    )

    return tools
