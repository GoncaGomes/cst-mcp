"""PID-scoped connection notice handling, using only fake CST and UI Automation."""

from __future__ import annotations

import sys
import threading
from types import SimpleNamespace

import pytest

from cst_mcp import dialog_handler as dialogs
from cst_mcp import session as sessions

MESSAGE = "Education Version\nThis software is licensed for non-commercial use only"
TITLE = "CST Studio Suite 2026"
PID = 4242


@pytest.mark.parametrize("year", ["2024", "2025", "2026", "2031"])
@pytest.mark.parametrize("transform", [str.lower, str.upper, str.title])
def test_notice_recognition_normalizes_case_and_whitespace(year, transform):
    assert dialogs._is_education_notice(
        transform(f"  CST \t Studio Suite {year}\n"),
        transform(" Education \n version\tThis software is licensed\nfor non-commercial use only "),
    )


@pytest.mark.parametrize(
    ("title", "message"),
    [
        ("Solver Error", MESSAGE),
        ("License Selection", MESSAGE),
        ("Other Studio Suite 2026", MESSAGE),
        ("CST Studio Suite 2026 - Solver Error", MESSAGE),
        ("CST Studio Suite 202", MESSAGE),
        ("CST Studio Suite 20260", MESSAGE),
        ("CST Studio Suite", MESSAGE),
        (TITLE, "Education Version"),
        (TITLE, "This software is licensed for non-commercial use only"),
        (TITLE, "Select a license"),
        (TITLE, ""),
    ],
)
def test_notice_requires_exact_versioned_title_and_both_messages(title, message):
    assert not dialogs._is_education_notice(title, message)


def test_discovery_accepts_multiple_versions_without_reading_real_windows(monkeypatch):
    titles = {
        1: "CST Studio Suite 2024",
        2: "cst studio suite 2026",
        3: "CST Studio Suite 2025 - Solver Error",
        4: "CST Studio Suite 2025",
    }
    monkeypatch.setattr(dialogs, "_IS_WINDOWS", True)
    monkeypatch.setattr(dialogs, "WNDENUMPROC", lambda cb: cb, raising=False)
    monkeypatch.setattr(dialogs, "wintypes", SimpleNamespace(HWND=int), raising=False)
    monkeypatch.setattr(dialogs, "_get_window_text", titles.__getitem__, raising=False)
    monkeypatch.setattr(dialogs, "_get_window_pid", lambda hwnd: PID, raising=False)
    monkeypatch.setattr(
        dialogs,
        "user32",
        SimpleNamespace(
            IsWindowVisible=lambda hwnd: hwnd != 4,
            EnumWindows=lambda cb, data: [cb(hwnd, data) for hwnd in titles],
        ),
        raising=False,
    )
    assert dialogs._find_cst_startup_windows() == [
        (1, PID, titles[1]),
        (2, PID, titles[2]),
    ]


class FakeCOMError(Exception):
    pass


class FakePattern:
    def __init__(self):
        self.calls = 0
        self.on_query = lambda: None
        self.on_invoke = lambda: None
        self.error = None

    def QueryInterface(self, interface):
        self.on_query()
        return self

    def Invoke(self):
        self.calls += 1
        self.on_invoke()
        if self.error:
            raise self.error


class FakeWindow:
    def __init__(self, pattern):
        self.CurrentName = TITLE
        self.CurrentProcessId = PID
        self.message = MESSAGE
        self.button = SimpleNamespace(
            CurrentName=" oK ",
            CurrentControlType=1,
            CurrentIsEnabled=True,
            CurrentProcessId=PID,
            GetCurrentPattern=lambda _: pattern,
        )
        text_pattern = SimpleNamespace(
            DocumentRange=SimpleNamespace(GetText=lambda _: self.message)
        )
        self.text = SimpleNamespace(
            CurrentControlType=2,
            GetCurrentPattern=lambda _: SimpleNamespace(QueryInterface=lambda _: text_pattern),
        )
        self.elements = [self.text, self.button]

    def FindAll(self, scope, condition):
        return SimpleNamespace(Length=len(self.elements), GetElement=self.elements.__getitem__)


@pytest.fixture
def fake_uia(monkeypatch):
    pattern = FakePattern()
    window = FakeWindow(pattern)
    uia = SimpleNamespace(
        TreeScope_Descendants=1,
        UIA_ButtonControlTypeId=1,
        UIA_TextControlTypeId=2,
        UIA_InvokePatternId=3,
        UIA_TextPatternId=4,
        IUIAutomationInvokePattern=object(),
        IUIAutomationTextPattern=object(),
        CUIAutomation=object(),
        IUIAutomation=object(),
    )
    handles = []
    com_calls = []

    def element_from_handle(hwnd):
        handles.append(hwnd)
        return window

    automation = SimpleNamespace(
        CreateTrueCondition=lambda: True, ElementFromHandle=element_from_handle
    )
    monkeypatch.setattr(dialogs, "_IS_WINDOWS", True)
    monkeypatch.setattr(
        dialogs,
        "_comtypes",
        SimpleNamespace(
            COMError=FakeCOMError,
            COINIT_MULTITHREADED=0,
            CoInitializeEx=lambda _: com_calls.append("init"),
            CoUninitialize=lambda: com_calls.append("uninit"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "comtypes.client",
        SimpleNamespace(GetModule=lambda _: uia, CreateObject=lambda *a, **kw: automation),
    )
    monkeypatch.setattr(
        dialogs, "_find_cst_startup_windows", lambda: [(10, PID, window.CurrentName)]
    )
    return SimpleNamespace(pattern=pattern, window=window, handles=handles, com_calls=com_calls)


class ControlledEvent:
    """Advance observation time on waits, without sleeping."""

    def __init__(self, clock):
        self.clock = clock
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waits.append(timeout)
        self.clock[0] += timeout
        return self.stopped


@pytest.fixture
def observer(monkeypatch, fake_uia):
    clock = [0.0]
    monkeypatch.setattr(dialogs, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    watcher = dialogs.EducationNoticeWatcher(target_pid=PID, timeout_s=1)
    watcher._deadline = 1
    watcher._stop_event = ControlledEvent(clock)
    return watcher, clock


@pytest.mark.parametrize(
    "unsafe",
    [
        "other_pid",
        "unknown_pid",
        "uia_pid",
        "unrelated",
        "incomplete",
        "missing",
        "disabled",
        "ambiguous",
        "button_pid",
        "no_pattern",
    ],
)
def test_no_click_without_all_safety_conditions(monkeypatch, fake_uia, observer, unsafe):
    watcher, _ = observer
    window = fake_uia.window
    if unsafe == "other_pid":
        monkeypatch.setattr(dialogs, "_find_cst_startup_windows", lambda: [(10, PID + 1, TITLE)])
    elif unsafe == "unknown_pid":
        watcher._target_pid = None
    elif unsafe == "uia_pid":
        window.CurrentProcessId += 1
    elif unsafe == "unrelated":
        window.CurrentName = "Solver Error"
    elif unsafe == "incomplete":
        window.message = "Education Version"
    elif unsafe == "missing":
        window.elements = [window.text]
    elif unsafe == "disabled":
        window.button.CurrentIsEnabled = False
    elif unsafe == "ambiguous":
        window.elements.append(window.button)
    elif unsafe == "button_pid":
        window.button.CurrentProcessId += 1
    elif unsafe == "no_pattern":
        window.button.GetCurrentPattern = lambda _: None
    watcher._observe_uia()
    assert fake_uia.pattern.calls == 0
    if unsafe in ("unknown_pid", "other_pid"):
        assert fake_uia.handles == []


@pytest.mark.parametrize("error_type", [None, FakeCOMError, RuntimeError])
def test_invoke_at_most_once_even_when_it_raises(monkeypatch, fake_uia, observer, error_type):
    watcher, _ = observer
    monkeypatch.setattr(
        dialogs, "_find_cst_startup_windows", lambda: [(10, PID, TITLE), (11, PID, TITLE)]
    )
    if error_type:
        fake_uia.pattern.error = error_type("Invoke may already have taken effect")
    watcher._observe_uia()
    assert fake_uia.pattern.calls == 1
    assert watcher._stop_event.is_set()
    assert fake_uia.handles == [10]


@pytest.mark.parametrize(
    "change", ["title", "message", "window_pid", "enabled", "button_pid", "stop", "timeout"]
)
def test_revalidation_and_cancellation_before_invoke(fake_uia, observer, change):
    watcher, clock = observer

    def mutate():
        if change == "title":
            fake_uia.window.CurrentName = "License Selection"
        elif change == "message":
            fake_uia.window.message = "Education Version"
        elif change == "window_pid":
            fake_uia.window.CurrentProcessId += 1
        elif change == "enabled":
            fake_uia.window.button.CurrentIsEnabled = False
        elif change == "button_pid":
            fake_uia.window.button.CurrentProcessId += 1
        elif change == "stop":
            watcher.stop()
        else:
            clock[0] = 1

    fake_uia.pattern.on_query = mutate
    watcher._observe_uia()
    assert fake_uia.pattern.calls == 0


@pytest.mark.parametrize("cancel", [False, True])
def test_no_notice_observation_ends_on_cancel_or_deadline(monkeypatch, fake_uia, observer, cancel):
    watcher, clock = observer
    scans = []

    def discover():
        scans.append(True)
        if cancel:
            watcher.stop()
        return []

    monkeypatch.setattr(dialogs, "_find_cst_startup_windows", discover)
    watcher._observe_uia()
    assert scans
    assert watcher._stop_event.is_set() if cancel else clock[0] >= watcher._deadline
    assert fake_uia.pattern.calls == 0
    assert all(wait <= watcher._poll_interval for wait in watcher._stop_event.waits)


def test_startup_pid_wait_is_cancellable_and_deadline_bounded(observer):
    watcher, clock = observer
    watcher._target_pid_getter = lambda: None
    assert watcher._wait_for_target_pid() is None
    assert clock[0] >= watcher._deadline
    watcher.stop()
    watcher._target_pid_getter = lambda: pytest.fail("Must not poll after cancellation")
    assert watcher._wait_for_target_pid() is None


def install_interface(monkeypatch, factory, running):
    interface = SimpleNamespace(DesignEnvironment=factory)
    if running is not None:
        interface.running_design_environments = lambda: running
    monkeypatch.setitem(sys.modules, "cst", SimpleNamespace(interface=interface))
    monkeypatch.setitem(sys.modules, "cst.interface", interface)
    monkeypatch.setattr(sessions.sys, "platform", "win32")
    config = SimpleNamespace(cst_available=True, cst_path=None, python_lib_path=None)
    return sessions.CSTSession(config=config)


@pytest.mark.parametrize(("mode", "existing"), [("new", True), ("any", True), ("any", False)])
@pytest.mark.parametrize("vendor_fails", [False, True])
def test_watcher_armed_before_exact_vendor_call_and_always_stopped(
    monkeypatch, mode, existing, vendor_fails
):
    events = []
    project = SimpleNamespace(filename=lambda: "active.cst")
    de = SimpleNamespace(
        pid=lambda: PID,
        get_open_projects=lambda: [project],
        has_active_project=lambda: True,
        active_project=lambda: project,
    )
    watchers = []

    class Watcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            watchers.append(self)
            assert kwargs["timeout_s"] == 60

        def start(self):
            events.append("armed")
            getter = self.kwargs["target_pid_getter"]
            assert getter() is None if getter else self.kwargs["target_pid"] == PID

        def stop(self):
            events.append("stopped")
            return True

    def vendor(process_info=None, selected=None):
        assert events == ["armed"]
        if process_info is not None:
            assert selected is None
            process_info.pid = PID
            assert watchers[0].kwargs["target_pid_getter"]() == PID
        else:
            assert selected == PID
        events.append("vendor")
        if vendor_fails:
            raise RuntimeError("vendor failed")
        return de

    factory = SimpleNamespace(
        ProcessInfo=lambda: SimpleNamespace(pid=0),
        new=lambda **kw: vendor(**kw),
        connect=lambda selected: vendor(selected=selected),
        connect_to_any_or_new=lambda: pytest.fail("Must choose an authoritative target"),
    )
    monkeypatch.setattr(sessions, "EducationNoticeWatcher", Watcher)
    session = install_interface(monkeypatch, factory, [PID, PID + 1] if existing else [])
    result = session.connect(mode=mode)
    assert events == ["armed", "vendor", "stopped"]
    assert result["status"] == ("offline" if vendor_fails else "connected")
    if not vendor_fails:
        attached = mode == "any" and existing
        assert result["de_pid"] == PID
        assert result["newly_started"] is not attached
        assert session._project is (project if mode == "any" else None)
        assert result["project_path"] == ("active.cst" if mode == "any" else None)
        assert result["open_project_paths"] == ["active.cst"]
        assert ("warning" in result) is attached


@pytest.mark.parametrize(("mode", "existing"), [("new", False), ("any", False), ("any", True)])
def test_fake_ok_releases_blocking_connection_before_return(monkeypatch, fake_uia, mode, existing):
    vendor_entered = threading.Event()
    invoked = threading.Event()
    watcher_created = []
    real_watcher = dialogs.EducationNoticeWatcher

    def make_watcher(**kwargs):
        watcher = real_watcher(**kwargs)
        watcher_created.append(watcher)
        return watcher

    def discover():
        assert vendor_entered.wait(2), "Vendor operation never entered"
        return [(10, PID, TITLE)]

    def vendor(process_info=None, selected=None):
        assert watcher_created[0]._thread is not None
        if process_info is not None:
            process_info.pid = PID
        else:
            assert selected == PID
        vendor_entered.set()
        assert invoked.wait(2), "Blocking vendor call was not released by fake OK"
        return SimpleNamespace(pid=lambda: PID, get_open_projects=list)

    fake_uia.pattern.on_invoke = invoked.set
    monkeypatch.setattr(dialogs, "_find_cst_startup_windows", discover)
    monkeypatch.setattr(sessions, "EducationNoticeWatcher", make_watcher)
    factory = SimpleNamespace(
        ProcessInfo=lambda: SimpleNamespace(pid=0),
        new=lambda **kw: vendor(**kw),
        connect=lambda selected: vendor(selected=selected),
    )
    session = install_interface(monkeypatch, factory, [PID] if existing else [])
    result = session.connect(mode=mode)
    assert result["status"] == "connected"
    assert fake_uia.pattern.calls == 1
    assert not watcher_created[0].running
    assert fake_uia.com_calls == ["init", "uninit"]


@pytest.mark.parametrize("failure", ["construct", "start", "stop", "still_running"])
def test_watcher_errors_do_not_fail_successful_connection(monkeypatch, caplog, failure):
    calls = []

    class Watcher:
        def __init__(self, **kwargs):
            if failure == "construct":
                raise RuntimeError("No UI Automation")

        def start(self):
            calls.append("start")
            if failure == "start":
                raise RuntimeError("No UI Automation")

        def stop(self):
            calls.append("stop")
            if failure == "stop":
                raise RuntimeError("Cleanup failed")
            return failure != "still_running"

    monkeypatch.setattr(sessions, "EducationNoticeWatcher", Watcher)
    factory = SimpleNamespace(
        ProcessInfo=lambda: SimpleNamespace(pid=0),
        new=lambda **kw: SimpleNamespace(pid=lambda: PID, get_open_projects=list),
    )
    result = install_interface(monkeypatch, factory, []).connect(mode="new")
    assert result["status"] == "connected"
    assert "stop" in calls if failure != "construct" else not calls
    assert "education notice observer" in caplog.text.lower()


@pytest.mark.parametrize("legacy", ["no_process_info", "no_enumeration", "no_connect"])
def test_legacy_connection_skips_unscoped_watcher(monkeypatch, caplog, legacy):
    de = SimpleNamespace(pid=lambda: PID, get_open_projects=list)
    factory = SimpleNamespace(new=lambda: de, connect_to_any_or_new=lambda: de)
    monkeypatch.setattr(
        sessions, "EducationNoticeWatcher", lambda **kw: pytest.fail("Unscoped watcher")
    )
    running = [] if legacy == "no_process_info" else None if legacy == "no_enumeration" else [PID]
    result = install_interface(monkeypatch, factory, running).connect()
    assert result["status"] == "connected"
    assert "authoritative target PID" in caplog.text


def test_missing_comtypes_preserves_connection(monkeypatch):
    monkeypatch.setattr(dialogs, "_comtypes", None)
    factory = SimpleNamespace(
        ProcessInfo=lambda: SimpleNamespace(pid=0),
        new=lambda **kw: SimpleNamespace(pid=lambda: PID, get_open_projects=list),
    )
    assert install_interface(monkeypatch, factory, []).connect(mode="new")["status"] == "connected"


def test_success_without_notice_cancels_long_observation_wait(monkeypatch, fake_uia):
    scanned = threading.Event()
    watchers = []

    def make_watcher(**kwargs):
        watcher = dialogs.EducationNoticeWatcher(poll_interval=60, **kwargs)
        watchers.append(watcher)
        return watcher

    def discover():
        scanned.set()
        return []

    def vendor(**kwargs):
        kwargs["process_info"].pid = PID
        assert scanned.wait(2)
        return SimpleNamespace(pid=lambda: PID, get_open_projects=list)

    # Publish the PID before the watcher polls so this test exercises only the
    # long observation wait, not the separate wait for ProcessInfo publication.
    factory = SimpleNamespace(ProcessInfo=lambda: SimpleNamespace(pid=PID), new=vendor)
    monkeypatch.setattr(dialogs, "_find_cst_startup_windows", discover)
    monkeypatch.setattr(sessions, "EducationNoticeWatcher", make_watcher)
    result = install_interface(monkeypatch, factory, []).connect(mode="new")
    assert result["status"] == "connected"
    assert watchers[0]._stop_event.is_set()
    assert not watchers[0].running
    assert fake_uia.pattern.calls == 0
    assert fake_uia.com_calls == ["init", "uninit"]


def test_actual_attempts_get_fresh_watchers_but_reconnect_does_not(monkeypatch):
    watchers = []

    class Watcher:
        def __init__(self, **kwargs):
            self.stopped = False
            watchers.append(self)

        def start(self):
            assert not self.stopped

        def stop(self):
            self.stopped = True
            return True

    factory = SimpleNamespace(
        ProcessInfo=lambda: SimpleNamespace(pid=0),
        new=lambda **kw: SimpleNamespace(pid=lambda: PID, get_open_projects=list),
    )
    monkeypatch.setattr(sessions, "EducationNoticeWatcher", Watcher)
    session = install_interface(monkeypatch, factory, [])
    assert session.connect(mode="new")["status"] == "connected"
    assert session.connect()["message"] == "Already connected"
    assert session.connect(mode="new")["status"] == "error"
    assert len(watchers) == 1
    session.disconnect()
    assert session.connect(mode="new")["status"] == "connected"
    assert len(watchers) == 2
    assert all(watcher.stopped for watcher in watchers)


@pytest.mark.parametrize("stage", ["init", "load", "cleanup"])
def test_uia_thread_errors_are_logged_without_escaping(
    monkeypatch, fake_uia, observer, caplog, stage
):
    watcher, _ = observer

    def fail(*args):
        raise RuntimeError(f"{stage} failed")

    if stage == "init":
        monkeypatch.setattr(dialogs._comtypes, "CoInitializeEx", fail)
    elif stage == "cleanup":
        monkeypatch.setattr(dialogs._comtypes, "CoUninitialize", fail)
    else:
        monkeypatch.setattr(sys.modules["comtypes.client"], "GetModule", fail)
    watcher._loop()
    assert f"{stage} failed" in caplog.text
    expected_calls = {"init": [], "load": ["init", "uninit"], "cleanup": ["init"]}
    assert fake_uia.com_calls == expected_calls[stage]


def test_failed_process_info_preserves_startup_without_clicking(monkeypatch, caplog):
    def fail():
        raise RuntimeError("ProcessInfo unsupported")

    factory = SimpleNamespace(
        ProcessInfo=fail,
        new=lambda: SimpleNamespace(pid=lambda: PID, get_open_projects=list),
    )
    monkeypatch.setattr(
        sessions, "EducationNoticeWatcher", lambda **kw: pytest.fail("No authoritative PID")
    )
    assert install_interface(monkeypatch, factory, []).connect(mode="new")["status"] == "connected"
    assert "ProcessInfo unavailable" in caplog.text


def test_stop_uses_bounded_join_and_reports_live_thread():
    joins = []
    watcher = dialogs.EducationNoticeWatcher(target_pid=PID)
    watcher._thread = SimpleNamespace(join=lambda **kw: joins.append(kw), is_alive=lambda: True)
    assert watcher.stop() is False
    assert watcher._stop_event.is_set()
    assert joins == [{"timeout": 2.0}]


def test_non_windows_connection_never_starts_watcher(monkeypatch):
    factory = SimpleNamespace(new=lambda: SimpleNamespace(get_open_projects=list))
    session = install_interface(monkeypatch, factory, [])
    monkeypatch.setattr(sessions.sys, "platform", "linux")
    monkeypatch.setattr(
        sessions, "EducationNoticeWatcher", lambda **kw: pytest.fail("Windows only")
    )
    assert session.connect(mode="new")["status"] == "connected"
