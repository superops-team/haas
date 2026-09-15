import re
from pathlib import Path


def test_desktop_initial_window_is_shown_and_focused_after_page_load() -> None:
    source = (
        Path(__file__).parents[1] / "surfaces" / "gui" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")

    finished = source.index("PageLoadEvent::Finished")
    one_shot = source.index(".compare_exchange(", finished)
    shown = source.index("window.show()", finished)
    focused = source.index("window.set_focus()", shown)
    built = source.index("let win = builder.build()?;", focused)
    hidden = source.index(".visible(false)", finished, built)
    close_to_tray = source.index("win.on_window_event", built)

    assert finished < one_shot < shown < focused < hidden < built < close_to_tray


def test_desktop_dev_server_ignores_tauri_build_outputs() -> None:
    vite_config = (Path(__file__).parents[1] / "surfaces" / "gui" / "vite.config.ts").read_text(
        encoding="utf-8"
    )

    assert 'ignored: ["**/src-tauri/target/**"]' in vite_config


def test_desktop_dev_server_prefers_repo_venv_over_staged_release_sidecar() -> None:
    source = (
        Path(__file__).parents[1] / "surfaces" / "gui" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")

    start = source.index("fn server_bin()")
    end = source.index("fn state_dir()", start)
    resolver = source[start:end]

    assert resolver.index("cfg!(debug_assertions)") < resolver.index("current_exe()")
    assert resolver.index("../../../.venv/bin/openworker-server") < resolver.index("current_exe()")


def test_macos_dock_reopen_restores_only_when_no_window_is_visible() -> None:
    source = (
        Path(__file__).parents[1] / "surfaces" / "gui" / "src-tauri" / "src" / "lib.rs"
    ).read_text(encoding="utf-8")

    reopen_handler = re.search(
        r'#\[cfg\(target_os = "macos"\)\]\s*'
        r"if let RunEvent::Reopen \{\s*"
        r"has_visible_windows: false,\s*"
        r"\.\.\s*"
        r"\} = event\s*"
        r"\{\s*show_main\(app\);\s*return;\s*\}",
        source,
    )

    assert reopen_handler is not None
