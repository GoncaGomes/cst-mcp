"""Windows dialog handler for CST Studio Suite.

Detects, reads, and dismisses CST modal dialogs that block automation.
Uses Win32 API via ctypes — no external dependencies beyond the stdlib.

**Detection strategy (two-tier):**

1. **Process-based** — find CST main-window PIDs, then catch *any* popup or
   dialog window belonging to those processes (regardless of title).
2. **Title-pattern fallback** — match known CST dialog titles even if the
   process-based detection misses them.

This ensures that *all* CST dialogs are caught, including frequency-range
dialogs, property editors, and any unexpected popups.

Only functional on Windows; on other platforms the public functions
return empty results gracefully.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"


_comtypes: Any = None

if _IS_WINDOWS:
    try:
        import comtypes as _comtypes
    except ImportError:
        _comtypes = None

# ---------------------------------------------------------------------------
# Win32 setup (Windows only)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    # Callback type for EnumWindows / EnumChildWindows
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)  # type: ignore[attr-defined]

    # Constants
    WM_CLOSE = 0x0010
    BM_CLICK = 0x00F5
    GW_OWNER = 4

    def _find_cst_startup_windows() -> list[tuple[int, int, str]]:
        """Find visible CST startup windows through Win32."""
        if not _IS_WINDOWS:
            return []

        windows: list[tuple[int, int, str]] = []

        @WNDENUMPROC
        def collect(hwnd: int, _: int) -> bool:
            handle = wintypes.HWND(hwnd)

            if user32.IsWindowVisible(handle):
                title = _get_window_text(handle)

                if title.strip().casefold() == "cst studio suite 2025":
                    windows.append((hwnd, _get_window_pid(handle), title))

            return True

        user32.EnumWindows(collect, 0)
        return windows

    def _get_education_ok_pattern(
        automation: Any,
        uia_module: Any,
        window: Any,
        expected_pid: int,
    ) -> Any:
        """Get the unique OK action for the expected education notice."""
        if window.CurrentProcessId != expected_pid:
            return None

        elements = window.FindAll(
            uia_module.TreeScope_Descendants,
            automation.CreateTrueCondition(),
        )
        buttons = []

        for index in range(elements.Length):
            element = elements.GetElement(index)

            if (
                element.CurrentControlType == uia_module.UIA_ButtonControlTypeId
                and (element.CurrentName or "").strip().casefold() == "ok"
            ):
                buttons.append(element)

        if len(buttons) != 1:
            return None

        button = buttons[0]
        if not button.CurrentIsEnabled or button.CurrentProcessId != expected_pid:
            return None

        pattern = button.GetCurrentPattern(uia_module.UIA_InvokePatternId)
        if not pattern:
            return None

        invoke_pattern = pattern.QueryInterface(uia_module.IUIAutomationInvokePattern)

        # Revalidate the notice after locating its button.
        title = window.CurrentName or ""
        full_text = _read_uia_text(automation, uia_module, window)

        if window.CurrentProcessId != expected_pid or not _is_education_notice(title, full_text):
            return None

        return invoke_pattern

    def _get_window_text(hwnd: int) -> str:
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _get_class_name(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _get_window_pid(hwnd: int) -> int:
        """Get the process ID that owns *hwnd*."""
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _get_child_texts(hwnd: int) -> list[str]:
        """Collect text from every child control of a dialog."""
        texts: list[str] = []

        @WNDENUMPROC
        def _cb(child_hwnd: int, _: int) -> bool:
            text = _get_window_text(child_hwnd)
            if text:
                texts.append(text)
            return True

        user32.EnumChildWindows(hwnd, _cb, 0)
        return texts

    def _click_button(hwnd: int, button_text: str = "OK") -> bool:
        """Find a button by label inside *hwnd* and click it.

        Searches child controls for a Button whose text matches
        *button_text* (case-insensitive, whitespace-trimmed, ignoring
        ``&`` accelerator prefixes).  Falls through a priority list:
        exact match → ``&``-stripped match → IDOK (dialog item 1).
        """
        found = [False]
        target = button_text.strip().lower()

        @WNDENUMPROC
        def _cb(child_hwnd: int, _: int) -> bool:
            cls = _get_class_name(child_hwnd)
            if "Button" not in cls:
                return True
            raw_text = _get_window_text(child_hwnd).strip()
            text = raw_text.lower()
            # Match with or without & accelerator prefix
            if text == target or text.lstrip("&") == target:
                user32.SendMessageW(child_hwnd, BM_CLICK, 0, 0)
                found[0] = True
                return False
            return True

        user32.EnumChildWindows(hwnd, _cb, 0)

        if not found[0] and target == "ok":
            # Fallback: try IDOK (standard dialog button ID = 1)
            ok_hwnd = user32.GetDlgItem(hwnd, 1)  # IDOK = 1
            if ok_hwnd:
                user32.SendMessageW(ok_hwnd, BM_CLICK, 0, 0)
                found[0] = True

        return found[0]

    def _try_dismiss(hwnd: int) -> str:
        """Try to dismiss a dialog window.  Returns the action taken."""
        # Try common accept buttons in priority order
        for label in ("OK", "Yes", "Continue", "Accept", "Close"):
            if _click_button(hwnd, label):
                return f"clicked_{label.lower()}"

        # Last resort: WM_CLOSE
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return "closed"


# ---------------------------------------------------------------------------
# CST main-window identification
# ---------------------------------------------------------------------------

# Substrings that identify a CST *main application* window title.
_CST_MAIN_WINDOW_KEYWORDS = [
    "cst studio suite",
    "cst design environment",
    "cst microwave studio -",  # Main window has " - project path"
    "cst em studio -",
]

# Known dialog title patterns (fallback when process detection fails).
_CST_DIALOG_PATTERNS = [
    "Results May Get Incompatible",
    "History Error",
    "Solver Error",
    "CST Error",
    "CST Warning",
    "Frequency Range",
    "Port Properties",
    "Mesh Properties",
    "Boundary Conditions",
    "Units",
]


def _is_cst_main_window(title: str) -> bool:
    """Return True if *title* looks like a CST main application window."""
    t = title.lower()
    return any(kw in t for kw in _CST_MAIN_WINDOW_KEYWORDS)


def _is_education_notice(title: str, full_text: str) -> bool:
    """Identify the CST education notice by its title and message."""
    normalized_title = title.strip().casefold()
    normalized_text = " ".join(full_text.casefold().split())

    return (
        normalized_title == "cst studio suite 2025"
        and "education version" in normalized_text
        and "this software is licensed for non-commercial use only" in normalized_text
    )


def _read_uia_text(
    automation: Any,
    uia_module: Any,
    window: Any,
) -> str:
    """Read text exposed through UI Automation text patterns."""
    elements = window.FindAll(
        uia_module.TreeScope_Descendants,
        automation.CreateTrueCondition(),
    )
    texts = []

    for index in range(elements.Length):
        element = elements.GetElement(index)

        if element.CurrentControlType != uia_module.UIA_TextControlTypeId:
            continue

        pattern = element.GetCurrentPattern(uia_module.UIA_TextPatternId)

        if not pattern:
            continue

        text_pattern = pattern.QueryInterface(uia_module.IUIAutomationTextPattern)
        text = text_pattern.DocumentRange.GetText(-1)

        if text:
            texts.append(text)

    return "\n".join(texts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def find_cst_dialogs() -> list[dict[str, Any]]:
    """Return info about all visible CST dialog/popup windows.

    Uses process-based detection (finds CST PIDs from main windows, then
    catches *all* other visible windows from those processes) with a
    title-pattern fallback.
    """
    if not _IS_WINDOWS:
        return []

    # --- Pass 1: identify CST process IDs from main windows ---------------
    cst_pids: set[int] = set()
    main_hwnds: set[int] = set()

    @WNDENUMPROC
    def _find_main(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True

        title = _get_window_text(hwnd)

        if _is_cst_main_window(title):
            cst_pids.add(_get_window_pid(hwnd))

            full_text = "\n".join(_get_child_texts(hwnd))

            if not _is_education_notice(title, full_text):
                main_hwnds.add(hwnd)

        return True

    user32.EnumWindows(_find_main, 0)

    # --- Pass 2: find dialog windows --------------------------------------
    dialogs: list[dict[str, Any]] = []
    seen: set[int] = set()

    @WNDENUMPROC
    def _find_dialogs(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if hwnd in main_hwnds or hwnd in seen:
            return True

        title = _get_window_text(hwnd)
        if not title:
            return True

        pid = _get_window_pid(hwnd)
        matched_by: str | None = None

        # Strategy 1: any non-main window from a CST process
        if pid in cst_pids:
            matched_by = "cst_process"

        # Strategy 2: title pattern match (catches CST dialogs even if
        # the process-based detection didn't find the main window)
        if matched_by is None:
            title_lower = title.lower()
            for pat in _CST_DIALOG_PATTERNS:
                if pat.lower() in title_lower:
                    matched_by = "title_pattern"
                    break

        if matched_by is not None:
            child_texts = _get_child_texts(hwnd)
            dialogs.append(
                {
                    "hwnd": hwnd,
                    "pid": pid,
                    "title": title,
                    "class": _get_class_name(hwnd),
                    "texts": child_texts,
                    "full_text": "\n".join(child_texts),
                    "match": matched_by,
                }
            )
            seen.add(hwnd)

        return True

    user32.EnumWindows(_find_dialogs, 0)
    return dialogs


def dismiss_cst_dialogs() -> list[dict[str, Any]]:
    """Find and dismiss all CST dialogs.  Returns details of each."""
    dialogs = find_cst_dialogs()
    dismissed: list[dict[str, Any]] = []

    for dialog in dialogs:
        hwnd = dialog["hwnd"]
        dialog["action"] = _try_dismiss(hwnd)
        dismissed.append(dialog)

    # Strip non-serialisable fields
    for d in dismissed:
        d.pop("hwnd", None)

    return dismissed


class DialogWatcher:
    """Background thread that auto-dismisses CST dialogs.

    Start before long-running operations (solves, optimization loops)
    and stop afterwards.  All dismissed dialogs are logged and
    retrievable via :meth:`get_log`.
    """

    def __init__(self, poll_interval: float = 0.5) -> None:
        self._poll_interval = poll_interval
        self._running = False
        self._thread: threading.Thread | None = None
        self._log: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # -- lifecycle --

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Dialog watcher started (poll=%.1fs)", self._poll_interval)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Dialog watcher stopped")

    @property
    def running(self) -> bool:
        return self._running

    # -- log access --

    def get_log(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log)

    def clear_log(self) -> None:
        with self._lock:
            self._log.clear()

    # -- internal --

    def _loop(self) -> None:
        while self._running:
            try:
                dismissed = dismiss_cst_dialogs()
                if dismissed:
                    with self._lock:
                        for d in dismissed:
                            d["timestamp"] = time.time()
                            self._log.append(d)
                            logger.info("Auto-dismissed: %s", d.get("title"))
            except Exception as e:  # noqa: BLE001
                logger.debug("Dialog watcher error: %s", e)
            time.sleep(self._poll_interval)


class EducationNoticeWatcher:
    """Observe the education notice during a single CST startup."""

    def __init__(
        self,
        poll_interval: float = 0.2,
        timeout_s: float = 60.0,
        *,
        target_pid: int | None = None,
        target_pid_getter: Callable[[], int | None] | None = None,
    ) -> None:
        if poll_interval <= 0 or timeout_s <= 0:
            raise ValueError("Polling interval and timeout must be positive")

        if target_pid is not None and target_pid <= 0:
            raise ValueError("Target PID must be positive")

        if target_pid is not None and target_pid_getter is not None:
            raise ValueError("Provide a target PID or a getter, not both")

        self._target_pid_getter = target_pid_getter

        self._target_pid = target_pid

        self._poll_interval = poll_interval
        self._timeout_s = timeout_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._deadline = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not _IS_WINDOWS or _comtypes is None:
            raise RuntimeError("UI Automation requires Windows and comtypes")

        if self._thread is not None or self._stop_event.is_set():
            raise RuntimeError("Create a new watcher for each startup")

        self._deadline = time.monotonic() + self._timeout_s
        self._thread = threading.Thread(
            target=self._loop,
            name="cst-education-notice",
            daemon=True,
        )
        self._thread.start()

    def _wait_for_target_pid(self) -> int | None:
        """Wait for the PID published by the current CST startup."""
        if self._target_pid_getter is None:
            return self._target_pid

        while not self._stop_event.is_set():
            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                return None

            pid = self._target_pid_getter()
            if pid is not None and pid > 0:
                return pid

            if self._stop_event.wait(timeout=min(self._poll_interval, remaining)):
                return None

        return None

    def stop(self) -> bool:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=2.0)

        return not self.running

    def _loop(self) -> None:
        initialized = False

        try:
            _comtypes.CoInitializeEx(_comtypes.COINIT_MULTITHREADED)
            initialized = True
            self._observe_uia()
        except Exception:
            logger.exception("Education notice observer failed")
        finally:
            if initialized:
                _comtypes.CoUninitialize()

    def _observe_uia(self) -> None:
        from comtypes.client import CreateObject, GetModule

        target_pid = self._wait_for_target_pid()

        if self._target_pid_getter is not None and target_pid is None:
            return

        if target_pid is not None:
            logger.info(
                "Observing education notice for startup PID %s",
                target_pid,
            )

        uia_module = GetModule("UIAutomationCore.dll")
        automation = CreateObject(
            uia_module.CUIAutomation,
            interface=uia_module.IUIAutomation,
        )
        reported: set[tuple[int, str]] = set()
        first_scan = True

        while not self._stop_event.is_set():
            if time.monotonic() >= self._deadline:
                return

            candidates = _find_cst_startup_windows()

            if first_scan:
                logger.info("Win32 CST candidates: %r", candidates)
                first_scan = False

            for hwnd, pid, title in candidates:
                if target_pid is not None and pid != target_pid:
                    continue
                if self._stop_event.is_set() or time.monotonic() >= self._deadline:
                    return

                try:
                    window = automation.ElementFromHandle(hwnd)
                    full_text = _read_uia_text(
                        automation,
                        uia_module,
                        window,
                    )
                    matched = _is_education_notice(title, full_text)

                    diagnostic = (hwnd, full_text)
                    if diagnostic not in reported:
                        reported.add(diagnostic)
                        logger.info(
                            "CST candidate: pid=%s hwnd=%s title=%r text=%r match=%s",
                            pid,
                            hwnd,
                            title,
                            full_text,
                            matched,
                        )

                    if matched:
                        logger.info("Education notice detected")

                        # Without a target PID, keep observation-only mode.
                        if target_pid is None:
                            return

                        pattern = _get_education_ok_pattern(
                            automation,
                            uia_module,
                            window,
                            target_pid,
                        )
                        if pattern is None:
                            logger.warning("Education notice has no unique enabled OK button")
                            return

                        if self._stop_event.is_set() or time.monotonic() >= self._deadline:
                            return

                        # End observation before invoking, preventing retries
                        # if the invocation raises an exception.
                        self._stop_event.set()
                        pattern.Invoke()
                        logger.info(
                            "Education notice OK invoked: pid=%s",
                            pid,
                        )
                        return
                except _comtypes.COMError as exc:
                    diagnostic = (hwnd, str(exc))
                    if diagnostic not in reported:
                        reported.add(diagnostic)
                        logger.warning(
                            "Could not read CST window: pid=%s hwnd=%s",
                            pid,
                            hwnd,
                            exc_info=True,
                        )

            remaining = self._deadline - time.monotonic()
            if remaining <= 0:
                return

            if self._stop_event.wait(timeout=min(self._poll_interval, remaining)):
                return
