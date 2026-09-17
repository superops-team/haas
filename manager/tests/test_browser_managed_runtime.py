from __future__ import annotations

from types import SimpleNamespace

from coworker.connectors import browser_automation as ba


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.closed = False
        self.screenshot_error: Exception | None = None

    def is_closed(self) -> bool:
        return self.closed

    def title(self) -> str:
        return "Fake"

    def evaluate(self, _script: str):
        return {"title": "Fake", "url": self.url, "text": "ready", "controls": []}

    def screenshot(self, *_, **__):
        if self.screenshot_error is not None:
            raise self.screenshot_error
        return b"png"


class _FakeContext:
    def __init__(self, page: _FakePage | None = None) -> None:
        self.pages = [page or _FakePage()]
        self.closed = False

    def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed = True
        for page in self.pages:
            page.closed = True


class _FakeBrowserType:
    def __init__(self) -> None:
        self.executable_path = ""
        self.launch_calls = []
        self.persistent_calls = []
        self.context_pages: list[_FakePage] = []

    def launch(self, **kwargs):  # pragma: no cover - must not be used
        self.launch_calls.append(kwargs)
        raise AssertionError("managed browser must not call chromium.launch")

    def launch_persistent_context(self, user_data_dir: str, **kwargs):
        self.persistent_calls.append({"user_data_dir": user_data_dir, **kwargs})
        page = self.context_pages.pop(0) if self.context_pages else None
        return _FakeContext(page)


def _fake_chromium_path(root):
    return (
        root
        / "chromium-1228"
        / "chrome-mac-arm64"
        / "Google Chrome for Testing.app"
        / "Contents"
        / "MacOS"
        / "Google Chrome for Testing"
    )


def _fake_headless_shell_path(root):
    return (
        root
        / "chromium_headless_shell-1228"
        / "chrome-headless-shell-mac-arm64"
        / "chrome-headless-shell"
    )


def _write_executable(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)


def test_managed_browser_uses_app_owned_profile_and_not_system_chrome(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("OPENHARNESS_BROWSER_EXECUTABLE", raising=False)
    monkeypatch.delenv("BROWSER_EXECUTABLE_PATH", raising=False)
    fake_type = _FakeBrowserType()
    executable = _fake_chromium_path(tmp_path / "ms-playwright")
    _write_executable(executable)
    fake_type.executable_path = str(executable)
    controller = ba._BrowserController()
    controller._playwright = SimpleNamespace(chromium=fake_type)

    page = controller._launch_context()

    assert page is not None
    assert fake_type.launch_calls == []
    call = fake_type.persistent_calls[0]
    assert call["headless"] is True
    assert call["executable_path"] == fake_type.executable_path
    assert call["user_data_dir"].endswith("browser-harness/managed-browser-profile")
    assert "/Applications/Google Chrome.app" not in call["user_data_dir"]
    state = controller.state()
    assert state["managed"] is True
    assert state["executable"] == "managed_runtime"
    assert state["headless"] is True


def test_managed_browser_rebuilds_once_after_closed_page(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    fake_type = _FakeBrowserType()
    controller = ba._BrowserController()

    class _FakePlaywrightFactory:
        chromium = fake_type

    monkeypatch.setattr(
        ba,
        "sync_playwright",
        lambda: SimpleNamespace(start=lambda: _FakePlaywrightFactory()),
        raising=False,
    )

    page, err = controller.page()
    assert err is None
    assert page is not None
    page.closed = True

    calls = 0

    def action(active_page):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("Target page, context or browser has been closed")
        return {"ok": True, "url": active_page.url}

    assert controller.call("read", action) == {"ok": True, "url": "about:blank"}
    assert calls == 2
    assert len(fake_type.persistent_calls) >= 2


def test_managed_browser_rebuilds_once_after_screenshot_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    fake_type = _FakeBrowserType()
    first_page = _FakePage()
    first_page.screenshot_error = RuntimeError(
        "Page.captureScreenshot failed at /Users/example/Library/Caches/ms-playwright"
    )
    fake_type.context_pages = [first_page, _FakePage()]
    controller = ba._BrowserController()

    class _FakePlaywrightFactory:
        chromium = fake_type

    monkeypatch.setattr(
        ba,
        "sync_playwright",
        lambda: SimpleNamespace(start=lambda: _FakePlaywrightFactory()),
        raising=False,
    )

    result = controller.screenshot()

    assert result["ok"] is True
    assert result["screenshot_data_url"].startswith("data:image/png;base64,")
    assert len(fake_type.persistent_calls) == 2


def test_managed_browser_falls_back_to_available_playwright_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "cache"))
    fake_type = _FakeBrowserType()
    missing_expected = _fake_chromium_path(tmp_path / "cache" / "chromium-missing-parent")
    installed = _fake_chromium_path(tmp_path / "cache")
    _write_executable(installed)
    fake_type.executable_path = str(missing_expected)
    controller = ba._BrowserController()
    controller._playwright = SimpleNamespace(chromium=fake_type)

    page = controller._launch_context()

    assert page is not None
    assert fake_type.persistent_calls[0]["executable_path"] == str(installed)


def test_headless_runtime_prefers_shell_next_to_expected_chromium(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    fake_type = _FakeBrowserType()
    browser = _fake_chromium_path(tmp_path / "cache")
    headless = _fake_headless_shell_path(tmp_path / "cache")
    _write_executable(browser)
    _write_executable(headless)
    fake_type.executable_path = str(browser)
    controller = ba._BrowserController()
    controller._playwright = SimpleNamespace(chromium=fake_type)

    page = controller._launch_context()

    assert page is not None
    assert fake_type.persistent_calls[0]["executable_path"] == str(headless)


def test_chromium_candidates_include_packaged_runtime(tmp_path):
    packaged = tmp_path / "playwright-browsers"
    browser = _fake_chromium_path(packaged)
    _write_executable(browser)

    assert ba._chromium_candidates_from_root(packaged) == [browser]


def test_chromium_candidates_prefer_headless_shell_for_headless_runtime(tmp_path):
    packaged = tmp_path / "playwright-browsers"
    browser = _fake_chromium_path(packaged)
    headless = _fake_headless_shell_path(packaged)
    _write_executable(browser)
    _write_executable(headless)

    assert ba._chromium_candidates_from_root(packaged)[0] == headless
    assert ba._chromium_candidates_from_root(packaged, prefer_headless=False)[0] == browser


def test_browser_state_marks_developer_override_without_leaking_path(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    override = tmp_path / "custom-browser"
    _write_executable(override)
    monkeypatch.setenv("OPENHARNESS_BROWSER_EXECUTABLE", str(override))
    fake_type = _FakeBrowserType()
    controller = ba._BrowserController()
    controller._playwright = SimpleNamespace(chromium=fake_type)

    page = controller._launch_context()

    assert page is not None
    assert fake_type.persistent_calls[0]["executable_path"] == str(override)
    state = controller.state()
    assert state["executable"] == "developer_override"
    assert str(override) not in str(state)


def test_browser_error_text_redacts_host_paths():
    error = ba._safe_error_text(
        "failed at /Users/bytedance/Library/Caches/ms-playwright/chromium"
    )

    assert "/Users/bytedance" not in error
    assert "[path]" in error
