# ---- The shop browser ------------------------------------------------------
# One Chrome for all agents on the PC, so that logins made in it persist. A
# lock file serializes starting and quitting it; a JSON file in the same
# runtime directory maps each tab to the process (kernel) that opened it, so
# shop_close() knows which tabs are its own and whether others still work.


def _shop_json(path, method="GET", timeout=3):
    req = _urlrequest.Request(f"http://127.0.0.1:{_SHOP_PORT}{path}", method=method)
    with _urlrequest.urlopen(req, timeout=timeout) as r:
        body = r.read()
    try:
        return _json.loads(body)
    except ValueError:
        return body.decode()


def _shop_running(timeout=5):
    """Whether the browser answers on its DevTools port (one retry: under
    load a single answer can take seconds). False doesn't mean it is gone:
    see _shop_pid()."""
    for i in range(2):
        try:
            _shop_json("/json/version", timeout=timeout)
            return True
        except OSError as e:
            if isinstance(getattr(e, "reason", None), ConnectionRefusedError) or isinstance(e, ConnectionRefusedError):
                return False  # nothing listens: no need to retry
    return False


def _shop_pid():
    """The pid of the shop Chrome on this kernel's port and profile while its
    process lives (whatever its DevTools port answers), else None."""
    try:
        pid = int(_os.readlink(_os.path.join(_AGENT_PROFILE, "SingletonLock")).rsplit("-", 1)[1])
    except (OSError, ValueError, IndexError):
        return None
    return pid if _pid_alive(pid) and _is_shop_chrome(pid) else None


def _shop_pages():
    """The browser's page targets; one retry, 5 s each (OSError when it
    doesn't answer)."""
    for i in range(2):
        try:
            return [t for t in _shop_json("/json/list", timeout=5) if t["type"] == "page"]
        except OSError:
            if i:
                raise


def _pid_alive(pid):
    try:
        _os.kill(pid, 0)
    except (OSError, ValueError, TypeError):
        return False
    # A zombie still answers kill(0) but holds nothing: an agent kernel that
    # started Chrome and exited without reaping it left one that blocked the
    # profile lock (2026-11-03).
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _xvfb_pid():
    try:
        with open(f"/tmp/.X{_SHOP_DISPLAY}-lock") as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def _shop_file(ext):
    return _os.path.join(_ENV["XDG_RUNTIME_DIR"], f"omp-shop-{_SHOP_PORT}.{ext}")


@_contextlib.asynccontextmanager
async def _shop_lock(timeout=90):
    """Held while a kernel starts or quits the browser or changes the tab
    owners. Not reentrant."""
    fd = _os.open(_shop_file("lock"), _os.O_RDWR | _os.O_CREAT, 0o600)
    try:
        deadline = _time.monotonic() + timeout
        while True:
            try:
                _fcntl.flock(fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if _time.monotonic() > deadline:
                    raise TimeoutError("another agent kept the shop browser's lock") from None
                await _asyncio.sleep(0.1)
        yield
    finally:
        _os.close(fd)  # releases the lock


def _shop_owners():
    """{tab id: pid of the kernel that opened it}."""
    try:
        with open(_shop_file("tabs")) as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {}


def _shop_set_owners(owners):
    tmp = _shop_file(f"tabs.{_os.getpid()}")
    with open(_os.open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600), "w") as f:
        _json.dump(owners, f)
    _os.replace(tmp, _shop_file("tabs"))


def _is_shop_chrome(pid):
    """Whether process `pid` is a shop browser on this kernel's port."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = f.read().split(b"\0")
    except OSError:
        return False
    flag = f"--remote-debugging-port={_SHOP_PORT}".encode()
    # Chrome may rewrite its title into one space-joined string (it did in a
    # systemd unit with a session bus, 2026-11-04).
    return flag in args or (len(args) <= 2 and flag in args[0].split(b" "))


async def _shop_start(timeout):
    """Start the browser unless it runs; True if it was started. Call with
    the lock held."""
    if _shop_running():
        return False
    lock = _os.path.join(_AGENT_PROFILE, "SingletonLock")  # a symlink to "host-pid"
    try:
        pid = int(_os.readlink(lock).rsplit("-", 1)[1])
    except (OSError, ValueError, IndexError):
        pid = None
    if pid and _pid_alive(pid) and _is_shop_chrome(pid):
        # The shop browser itself, starting (another kernel's, or slow to
        # answer) or quitting (its port closes before the profile is free).
        deadline = _time.monotonic() + max(timeout, 20)
        while _pid_alive(pid) and _time.monotonic() < deadline:
            if _shop_running():
                return False
            await _asyncio.sleep(0.25)
        if _pid_alive(pid) and _is_shop_chrome(pid):
            raise RuntimeError(f"the shop browser runs but its DevTools port didn't answer for {max(timeout, 20)} s"
                               " (overloaded?): try again in a minute; never start or quit it by hand")
    if pid and _pid_alive(pid):
        raise RuntimeError(
            "another Chrome has the agents' profile (an agent_tab?): "
            "`await browser.close(all=True, kill=True)` first"
        )
    # No session bus for Chrome, as no agent kernel ever had one: with one it
    # moves its browser process into a systemd scope of its own (leaving its
    # helpers behind) and may keep its cookie key elsewhere.
    env = {k: v for k, v in _ENV.items() if k not in ("WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
    env["DISPLAY"] = f":{_SHOP_DISPLAY}"
    xvfb = [_XVFB, f":{_SHOP_DISPLAY}", "-screen", "0", "1920x1080x24", "-nolisten", "tcp"]  # unless it runs
    chrome = [
        _CHROME,
        f"--user-data-dir={_AGENT_PROFILE}",
        "--no-first-run",
        "--disable-extensions",  # see agent_tab
        "--ozone-platform=x11",
        f"--remote-debugging-port={_SHOP_PORT}",
        "--window-position=0,0",
        "--window-size=1920,1080",
        # WebGL on the CPU (Xvfb has no GPU): without it map widgets (Müller's store
        # finder, outage maps) fail with "Failed to initialize WebGL" (B-82).
        "--use-angle=swiftshader",
        "--enable-unsafe-swiftshader",
        "about:blank",
    ]
    unit = await _shop_unit_start(xvfb, chrome, env)
    if not unit:
        await _shop_spawn(xvfb, chrome, env)
    deadline, n = _time.monotonic() + timeout, 0
    while not _shop_running():
        n += 1
        state = await _asyncio.to_thread(_shop_unit_state) if unit and n % 8 == 0 else None
        if state in ("inactive", "failed") or _time.monotonic() > deadline:
            raise RuntimeError("the shop browser did not open its DevTools port"
                               + (f" (its unit {_shop_unit()} is {state or await _asyncio.to_thread(_shop_unit_state)})"
                                  if unit else ""))
        await _asyncio.sleep(0.25)
    return True


async def _shop_spawn(xvfb, chrome, env):
    """Start the display (`xvfb`: its command; skipped while one runs) and
    the browser detached from this kernel: the fallback without a systemd
    user manager. Both stay in this kernel's cgroup."""
    quiet = {"stdin": _subprocess.DEVNULL, "stdout": _subprocess.DEVNULL, "stderr": _subprocess.DEVNULL}

    def spawn(args, **kw):
        # Reap the child when it exits, or it stays a zombie for the life of
        # this kernel and its pid keeps looking alive to the profile lock.
        proc = _subprocess.Popen(args, start_new_session=True, **quiet, **kw)
        _threading.Thread(target=proc.wait, daemon=True).start()

    if _xvfb_pid() is None:
        spawn(xvfb)
        for _ in range(40):
            if _os.path.exists(f"/tmp/.X11-unix/X{_SHOP_DISPLAY}"):
                break
            await _asyncio.sleep(0.25)
        else:
            raise RuntimeError(f"Xvfb :{_SHOP_DISPLAY} did not start")
    spawn(chrome, env=env)


# The browser and its display run in a transient systemd user unit of their
# own, shop-browser-<display>, never in the cgroup of the agent that happened
# to start them: in an agent's unit (MemoryMax 6 G, no swap) the OOM killer
# took the whole browser 7 times in W1, and 3 times it went down with the
# unit when that agent finished. The unit's main process is Chrome itself
# (the launcher below execs it; Xvfb is its child), so when Chrome quits,
# systemd stops the display too, and the SingletonLock pid is the unit's
# MainPID. OOMPolicy=continue: an OOM kill of one renderer costs that tab,
# never the browser. The launcher reads its spec (with the kernel's
# environment, as the detached start passes it) from a mode-600 file it
# deletes, so no variable shows in the unit's properties.
_SHOP_MEM_HIGH, _SHOP_MEM_MAX = "7G", "10G"
_SHOP_LAUNCH_PY = r'''
import json, os, socket, subprocess, sys, time
with open(sys.argv[1]) as f:
    spec = json.load(f)
os.unlink(sys.argv[1])
if spec["xvfb"]:
    x, n = subprocess.Popen(spec["xvfb"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL), spec["display"]
    for _ in range(80):  # ready: its lock names it and its socket takes a connection
        time.sleep(0.125)
        if x.poll() is not None:
            sys.exit(f"Xvfb :{n} exited with {x.returncode}")
        try:
            with open(f"/tmp/.X{n}-lock") as f:
                ours = int(f.read().strip()) == x.pid
        except (OSError, ValueError):
            ours = False
        if ours:
            s = socket.socket(socket.AF_UNIX)
            try:
                s.connect(f"/tmp/.X11-unix/X{n}")
                break
            except OSError:
                pass
            finally:
                s.close()
    else:
        sys.exit(f"Xvfb :{n} did not start")
null = os.open(os.devnull, os.O_RDWR)  # Chrome's chatter; the launcher's own errors stay in the journal
for fd in (0, 1, 2):
    os.dup2(null, fd)
os.execve(spec["chrome"][0], spec["chrome"], spec["env"])
'''


def _shop_unit():
    return f"shop-browser-{_SHOP_DISPLAY}.service"


def _systemd_bin(name):
    path = _shutil.which(name, path=_ENV.get("PATH")) or f"/run/current-system/sw/bin/{name}"
    return path if _os.access(path, _os.X_OK) else None


def _systemd_user(*args, timeout=30):
    """Run `systemctl --user …` / `systemd-run --user …` (args[0] names
    which) against Kirill's user manager; None when there is none to ask."""
    rt = _ENV["XDG_RUNTIME_DIR"]
    exe = _systemd_bin(args[0])
    if exe is None or not _os.path.exists(_os.path.join(rt, "systemd", "private")):
        return None
    env = {**_ENV, "DBUS_SESSION_BUS_ADDRESS": f"unix:path={rt}/bus"}
    try:
        return _subprocess.run([exe, "--user", *args[1:]], env=env, stdin=_subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=timeout)
    except (OSError, _subprocess.TimeoutExpired):
        return None


def _shop_unit_state():
    """The unit's ActiveState ("inactive" when it isn't loaded), or None
    without a systemd user manager."""
    r = _systemd_user("systemctl", "show", "-p", "ActiveState", "--value", _shop_unit(), timeout=10)
    return r.stdout.strip() or None if r is not None and r.returncode == 0 else None


async def _shop_unit_stop():
    """Stop what is left of the browser's unit (its display, a failed
    start; after Browser.close usually nothing) and wait for it."""
    state = await _asyncio.to_thread(_shop_unit_state)
    if state in (None, "inactive"):
        return
    await _asyncio.to_thread(_systemd_user, "systemctl", "stop", _shop_unit(), timeout=40)
    await _asyncio.to_thread(_systemd_user, "systemctl", "reset-failed", _shop_unit(), timeout=10)


async def _shop_unit_start(xvfb, chrome, env):
    """Start display + browser in the unit; True if systemd took it, False
    (after a note when it refused) for the detached fallback."""
    if await _asyncio.to_thread(_shop_unit_state) is None:
        return False
    await _shop_unit_stop()  # an earlier one's display or failed start holds the name
    spec = _shop_file(f"launch.{_os.getpid()}")
    with open(_os.open(spec, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600), "w") as f:
        _json.dump({"xvfb": xvfb if _xvfb_pid() is None else None, "display": _SHOP_DISPLAY, "chrome": chrome,
                    "env": env}, f)
    r = await _asyncio.to_thread(
        _systemd_user, "systemd-run", "--unit", _shop_unit(), "--collect", "--quiet",
        f"--description=omp shop browser (display :{_SHOP_DISPLAY}, DevTools {_SHOP_PORT})",
        "-p", f"MemoryHigh={_SHOP_MEM_HIGH}", "-p", f"MemoryMax={_SHOP_MEM_MAX}", "-p", "OOMPolicy=continue",
        "-p", "TimeoutStopSec=20",
        "--", _sys.executable, "-c", _SHOP_LAUNCH_PY, spec)
    if r is not None and r.returncode == 0:
        return True
    try:
        _os.unlink(spec)
    except OSError:
        pass
    why = (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1] if r is not None else "no answer"
    print(f"[shop browser: systemd-run refused ({why[:160]}); started it detached in this agent's cgroup instead]")
    return False


async def _shop_browser_cdp():
    for i in range(2):
        try:
            return await _CDP.connect(_shop_json("/json/version", timeout=5)["webSocketDebuggerUrl"])
        except OSError:
            if i:
                raise


class BrowserRestarted(ConnectionError):
    """The shared shop browser restarted or quit under this tab, or the tab
    itself is gone: open it again with shop_tab(url); logins persist."""


_SHOP_SEEN = globals().get("_SHOP_SEEN") or {}  # {tab id: the browser's pid when this kernel attached it}


def _proc_start(pid):
    """The wall-clock time process `pid` started, or None."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat") as f:
            btime = next(int(line.split()[1]) for line in f if line.startswith("btime"))
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    return btime + ticks / _os.sysconf("SC_CLK_TCK")


def _restart_error(tid, old_pid):
    """BrowserRestarted when the browser that held tab `tid` (pid `old_pid`
    then) is gone or another one, or the tab is gone; None when both live."""
    pid = _shop_pid()
    try:
        live = {p["id"] for p in _shop_pages()} if pid else set()
    except OSError:
        live = None  # it doesn't answer: can't tell about the tab
    same = pid is not None and (old_pid is None or pid == old_pid)
    if same and (live is None or tid in live):
        return None
    if same:
        at = _time.strftime("%H:%M:%S")
        return BrowserRestarted(f"this tab ({tid[:8]}…) is gone (closed by now, {at}: by the page, another agent or a"
                                " crash) though the browser runs: open it again with shop_tab(url); logins persist")
    if pid is not None:
        at = _time.strftime("%H:%M:%S", _time.localtime(_proc_start(pid) or _time.time()))
        return BrowserRestarted(f"the shared browser restarted at {at} and this tab is gone: open it again with"
                                " shop_tab(url); logins persist")
    return BrowserRestarted(f"the shared browser quit (by {_time.strftime('%H:%M:%S')}) and this tab is gone: open it"
                            " again with shop_tab(url), which starts it; logins persist")


# Hard rules the browser itself enforces, for every tab, popup and frame of
# every agent: navigations to these URLs push or text Kirill's phone, or are
# never allowed. Each kernel holds a browser-wide DevTools Fetch interception
# (documents only, these patterns only) while it uses the browser; it fails
# them as BlockedByClient before any request leaves (page scripts can't tell).
_BLOCK_PATTERNS = ["*://wallet.google.com/*", "*://myaccount.google.com/device-activity*",
                   "*://*amazon.*/ap/cvf/accountrecovery*"]
# Google's "confirm on your phone" (push, app, SMS) challenges: the push is
# out once the page shows; goto/click/wait_for raise at once.
_PHONE_RE = _re.compile(r"^https://accounts\.google\.com/[^?#]*challenge/(dp|az|ipp)(?![\w-])", _re.I)
_GUARDS = globals().get("_GUARDS") or {}  # {DevTools port: this kernel's guard connection}
_GUARD_BLOCKS = globals().get("_GUARD_BLOCKS") or []  # [(monotonic time, url, frame id)], the last 50


class HardRule(RuntimeError):
    """A navigation the hard rules forbid was blocked, or Google asks for
    Kirill's phone: stop that path, never retry it."""


def _blocked_msg(url):
    return f"blocked: {url[:200]} would push or text Kirill's phone (hard rule); go back"


def _phone_msg(url):
    return (f"Google asks to confirm on Kirill's phone ({url[:120]}): stop (hard rule), don't answer, "
            "navigate away")


async def _close_blocked(tid):
    """Close a window whose page the guard blocked; when it is still open
    after 1 s, it becomes this kernel's tab, so that shop_close() closes it."""
    for _ in range(10):
        try:
            if tid not in {p["id"] for p in _shop_pages()}:
                return
            _shop_json(f"/json/close/{tid}")
        except (OSError, ValueError):
            pass
        await _asyncio.sleep(0.1)
    async with _shop_lock():
        owners = _shop_owners()
        owners[tid] = _os.getpid()
        _shop_set_owners(owners)


async def _guard_on():
    """This kernel's browser-wide block of _BLOCK_PATTERNS (again after the
    browser restarted)."""
    g = _GUARDS.get(_SHOP_PORT)
    if g is not None and not g._task.done():
        return
    cdp = await _shop_browser_cdp()

    async def fail(rid, entry):
        try:
            await cdp.send("Fetch.failRequest", {"requestId": rid, "errorReason": "BlockedByClient"}, 5)
        except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
            pass  # another kernel's guard failed it first
        # A window whose page was blocked (a popup's first page) is of no use:
        # close it unless a live agent's kernel holds it. Its opener goes into
        # the entry, so _rules still finds it once the window is gone.
        for _ in range(3):
            await _asyncio.sleep(0.2)
            try:
                infos = (await cdp.send("Target.getTargets", timeout=5))["targetInfos"]
            except (RuntimeError, ConnectionError, KeyError, _asyncio.TimeoutError):
                return
            t = next((t for t in infos if t["targetId"] == entry[2] and t.get("type") == "page"), None)
            if t is None or not (t.get("openerId") or t.get("openerFrameId")):
                return  # gone, a frame, or a tab's own navigation (its goto raises)
            entry[3] = t.get("openerFrameId") or t.get("openerId")
            owner = _shop_owners().get(entry[2])
            if owner and _pid_alive(owner):
                return
            try:
                await cdp.send("Target.closeTarget", {"targetId": entry[2]}, 5)
            except (RuntimeError, ConnectionError, _asyncio.TimeoutError):
                pass

    def on(msg):
        if msg.get("method") == "Fetch.requestPaused":
            p = msg["params"]
            entry = [_time.monotonic(), p["request"]["url"], p.get("frameId"), None]
            _GUARD_BLOCKS.append(entry)
            del _GUARD_BLOCKS[:-50]
            _asyncio.ensure_future(fail(p["requestId"], entry))

    cdp._listeners.append(on)
    try:
        await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": u, "resourceType": "Document", "requestStage": "Request"}
                                                     for u in _BLOCK_PATTERNS]}, 5)
    except BaseException:
        cdp.close()
        raise
    _GUARDS[_SHOP_PORT] = cdp


@_both
def shop_tabs():
    """The shop browser's open tabs: id, title, url, and owner (this kernel,
    another agent, or none); [] when it isn't running."""
    try:
        pages = _shop_pages()
    except (OSError, ValueError):
        return []
    owners, me = _shop_owners(), _os.getpid()
    who = lambda pid: "this kernel" if pid == me else "another agent" if _pid_alive(pid) else "none"  # noqa: E731
    return [{"id": t["id"], "title": t["title"], "url": _mask_url(t["url"]), "owner": who(owners.get(t["id"]))} for t in pages]


async def _shop_open(new, timeout):
    """Start the browser unless it runs → (its pages, the id of the window
    opened for a new tab when `new`, else None). Call with the lock held."""
    started = await _shop_start(timeout)
    pages = _shop_pages()
    for _ in range(20 if started and not pages else 0):
        await _asyncio.sleep(0.1)  # the first window shows up a moment later
        pages = _shop_pages()
    if not new:
        return pages, None
    if started and len(pages) == 1 and pages[0]["url"] == "about:blank":
        return pages, pages[0]["id"]  # the window Chrome starts with
    # A window per tab, not a background tab: each stays visible to Chrome,
    # which throttles pages it thinks hidden.
    cdp = await _shop_browser_cdp()
    try:
        r = await cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True, "width": 1920, "height": 1080})
    finally:
        cdp.close()
    return pages, r["targetId"]


async def shop_tab(url="about:blank", *, target=None, wait=None, timeout=None):
    """A new tab of the shop browser (started on first use), in a window of
    its own, or with `target` (an id, or a piece of its title or URL) one
    already open. Other agents may use the browser at the same time; it keeps
    running between cells and turns until the last of them calls
    shop_close(). `wait`, `timeout`: as goto's (text to wait for after the
    load; None: 30 s, and a text-only wait ends early on a quiet page)."""
    me = _os.getpid()
    async with _shop_lock():
        for attempt in (0, 1):
            old = _shop_pid()
            try:
                pages, tid = await _shop_open(target is None, timeout or 30)
                break
            except (_asyncio.IncompleteReadError, OSError):
                if attempt or old is None:
                    raise
                # Its last window just closed: Chrome quits ~0.3 s later while its port still
                # answers (p10D2). Once that pid is gone, _shop_start starts a fresh browser.
                end = _time.monotonic() + 3
                while _pid_alive(old) and _time.monotonic() < end:
                    await _asyncio.sleep(0.1)
        live = {p["id"] for p in pages}
        owners = {k: v for k, v in _shop_owners().items() if k in live}
        if target is None:
            owners[tid] = me
        else:
            found = [t for t in pages if target == t["id"]] or [
                t for t in pages if target in t["title"] or target in t["url"] or target in _mask_url(t["url"])
            ]
            if not found:
                if target in _SHOP_SEEN:  # a tab this kernel had: say why it is gone
                    raise _restart_error(target, _SHOP_SEEN[target]) or RuntimeError(
                        f"the shop tab {target} is gone; open: {shop_tabs()}")
                raise RuntimeError(f"no shop tab matches {target!r}; open: {shop_tabs()}")
            free = [t for t in found if owners.get(t["id"]) in (None, me) or not _pid_alive(owners[t["id"]])]
            if not free:
                raise RuntimeError(
                    f"the shop tab {found[0]['title']!r} ({_mask_url(found[0]['url'])}) belongs to another agent "
                    "that is still running: open your own with shop_tab(url)"
                )
            tid = free[0]["id"]
            owners[tid] = me  # ours already, a popup, or left by a kernel that is gone
        _shop_set_owners(owners)
    tab = await _shop_attach(tid)
    if target is None and url != "about:blank":
        try:
            await tab.goto(url, wait=wait, timeout=timeout)
        except BaseException as e:
            if isinstance(e, TimeoutError):
                try:
                    shows = await tab._ev('(document.body?.innerText ?? "").trim().length', 5)
                except Exception:
                    shows = 0
                if shows:  # it never finished loading, but it can be read (DG-13)
                    print(f"[shop_tab: {e}; returning the tab with what it shows]")
                    return tab
            # The caller never gets this tab: don't leave it behind (DIGI-19).
            try:
                await tab.close()
            except Exception:
                pass
            raise
    elif target is not None:
        await tab._after_load()
    return tab


async def _shop_attach(tid):
    await _guard_on()  # before the tab does anything
    ws = f"ws://127.0.0.1:{_SHOP_PORT}/devtools/page/{tid}"
    tab = ShopTab(tid, ws, await _CDP.connect(ws))
    _SHOP_SEEN[tid] = tab._browser
    await tab._setup()
    return tab


def _target_family(infos, tid):
    """Target `tid` and its frames' own targets (cross-site iframes), from
    DevTools TargetInfos."""
    infos, fam, grew = list(infos), {tid}, True
    while grew:
        grew = False
        for t in infos:
            if t.get("type") == "iframe" and t["targetId"] not in fam and t.get("parentFrameId") in fam:
                fam.add(t["targetId"])
                grew = True
    return fam


async def _stop_xvfb():
    """Stop the invisible display and wait for it, so that a shop_tab right
    after doesn't take the dying one for running."""
    xvfb = _xvfb_pid()
    if xvfb is None:
        return
    _os.kill(xvfb, 15)
    for _ in range(40):
        try:
            _os.waitpid(xvfb, _os.WNOHANG)  # reap it if this kernel started it
        except ChildProcessError:
            pass  # another kernel's
        # Xvfb removes its lock file on exit; a zombie would still pass kill(0).
        if not _os.path.exists(f"/tmp/.X{_SHOP_DISPLAY}-lock"):
            return
        await _asyncio.sleep(0.1)


def _shop_close_tab(tid):
    """Close tab `tid` (two tries, 5 s each); False when the browser didn't answer."""
    for _ in range(2):
        try:
            _shop_json(f"/json/close/{tid}", timeout=5)
            return True
        except _urlerror.HTTPError:
            return True  # no such tab: closed already
        except OSError:
            pass
    return False


async def shop_close(timeout=15, *, all=False):
    """Close the tabs this kernel opened and delete its screenshots and
    captcha montages. When no other agent's tab is left (or with all=True),
    quit the shop browser cleanly, so its cookies (logins) are saved, then
    its invisible display and their systemd unit. While the browser's
    process lives but its DevTools port doesn't answer (overloaded), only
    this kernel's tabs go."""
    me = _os.getpid()
    _shots_drop()
    async with _shop_lock():
        if _shop_pid() is None and not _shop_running():
            await _shop_unit_stop()
            await _stop_xvfb()
            _shop_set_owners({})
            return "closed"
        owners = _shop_owners()

        def only_mine(tids, why, live=None):
            left = [t for t in tids if not _shop_close_tab(t)]
            _shop_set_owners({k: v for k, v in _shop_owners().items()
                              if (v != me or k in left) and (live is None or k in live or k in left)})
            return (f"closed this kernel's {len(tids) - len(left)} tab(s); {why}"
                    + (f"; {len(left)} of them didn't close (it didn't answer)" if left else ""))

        try:
            pages = _shop_pages()
        except OSError:
            # Never quit it, nor its display, nor forget others' tabs on a
            # missing answer: 16 agents lost the browser that way (2026-11-03).
            return only_mine([k for k, v in owners.items() if v == me],
                             "the shop browser runs but didn't list its tabs (busy?), so it stays open")
        mine = [p["id"] for p in pages if owners.get(p["id"]) == me]
        others = [p["id"] for p in pages if owners.get(p["id"]) not in (None, me) and _pid_alive(owners[p["id"]])]
        if others and not all:
            return only_mine(mine, f"{len(others)} tab(s) of other agents keep the browser open",
                             {p["id"] for p in pages})
        try:
            cdp = await _shop_browser_cdp()
        except _asyncio.IncompleteReadError:
            cdp = None  # its last window just closed: Chrome quits while its port still answers (p10D2)
        except OSError:
            return only_mine(mine, "the shop browser didn't answer, so it stays open")
        if cdp is not None:
            try:
                await cdp.send("Browser.close", timeout=10)
            except (ConnectionError, _asyncio.TimeoutError, RuntimeError):
                pass  # the browser went away before it answered
            cdp.close()
        deadline = _time.monotonic() + timeout
        while (_shop_pid() is not None or _shop_running(1)) and _time.monotonic() < deadline:
            await _asyncio.sleep(0.25)
        if _shop_pid() is not None:
            return f"asked the shop browser to quit; it still runs after {timeout} s (its display stays)"
        await _shop_unit_stop()  # Chrome was its main process: usually stopped already
        await _stop_xvfb()  # one outside the unit (the detached start's)
        _shop_set_owners({})
        return "closed"


def _site_note(url):
    """Print the agents' note on the site of `url`, the first time this
    kernel gets there."""
    try:
        u = _urlparse.urlsplit(url)
    except (ValueError, TypeError):
        return
    if u.scheme not in ("http", "https") or not u.hostname:
        return
    site = _site(u.hostname)
    if site in _SITES_SEEN:
        return
    _SITES_SEEN.add(site)
    try:
        with open(_os.path.join(_SITE_NOTES, f"{site}.md")) as f:
            note = f.read().strip()
    except OSError:
        return
    print(f"[site note: {_SITE_NOTES}/{site}.md]\n{note}\n")


@_both
def site_note(site):
    """The agents' note on `site` (a domain like "otto.de", or a URL), or ""
    when there is none: print(site_note("otto.de")) before choosing the
    first URL. goto() then doesn't print it again."""
    host = _urlparse.urlsplit(site).hostname if "://" in site else site.strip().strip("/")
    s = _site(host or "")
    _SITES_SEEN.add(s)
    try:
        with open(_os.path.join(_SITE_NOTES, f"{s}.md")) as f:
            return f.read().strip()
    except OSError:
        return ""


_ACCOUNTS_MD = _os.path.expanduser("~/Documents/agents/profile/registry/accounts.md")


@_both
def brief(site):
    """Everything to know before the first URL of a task on `site` (a
    domain or URL), in one printout (also returned): the site note, the
    vault entries for the site (name, user, folder, sites; no secrets), and
    the lines of the accounts registry (profile/registry/accounts.md) that
    mention it. A locked vault is reported, never unlocked from here."""
    host = (_urlparse.urlsplit(site).hostname if "://" in site else site.strip().strip("/")) or ""
    s = _site(host)
    label = s.split(".")[0]
    note = site_note(s)
    parts = [f"## site note {s}", note or f"(no site note for {s}: add one at the end)"]
    parts.append(f"## vault entries for {s}")
    if _rbw("unlocked").returncode != 0:
        parts.append("(the vault is locked: vault_unlock() asks Kirill on the PC's screen; only if a login is needed)")
    else:
        try:
            rows = [e for e in _vault_entries() if s in e["sites"] or any(s in x.lower() for x in e["sites"])
                    or _re.search(rf"(?<![a-z]){_re.escape(label)}(?![a-z])", e["name"].lower())]
        except RuntimeError as e:
            rows, parts = [], parts + [f"(vault: {e})"]
        parts += [f"- {e['name']!r} user {e['user']!r}" + (f" folder {e['folder']!r}" if e["folder"] else "")
                  + f" sites {', '.join(e['sites']) or '-'}" for e in rows] or [f"no vault entry for {s}"]
        names = [e["name"] for e in rows]
        for n in sorted({n for n in names if names.count(n) > 1}):
            users = ", ".join(repr(e["user"]) for e in rows if e["name"] == n)
            parts.append(f"{names.count(n)} entries named {n!r} (users {users}): vault_fill(…, user='…') picks one")
    parts.append(f"## accounts registry lines for {s}")
    try:
        with open(_ACCOUNTS_MD) as f:
            lines = [_mask_text(ln.rstrip()) for ln in f if s in ln.lower() or (len(label) > 3 and _re.search(
                rf"(?<![a-z]){_re.escape(label)}(?![a-z])", ln.lower()))]
    except OSError:
        lines = ["(no accounts registry file)"]
    parts += lines or [f"(no line mentions {s})"]
    text = "\n".join(parts)
    print(text)
    return text


_PROFILE = _os.path.expanduser("~/Documents/agents/profile")
# The profile's owner files: the only ones agents read (PROFILE.md: never
# sources/ or inbox/, which hold raw material).
_PROFILE_FILES = ("PROFILE.md", "open.md", "registry/*.md", "biography/*.md", "career/*.md")
# One row budget for all capped profile_grep calls of one eval cell, so a loop
# of regexes can't add up to a dump of other people's numbers (MONEY-0149: 4 ×
# 15 rows). A cell is the omp kernel's top-level code object ("<cell>"); outside
# a cell (a script, a worker thread) calls less than 60 s apart share one.
_PROFILE_CELL_BUDGET = 30
_PROFILE_WINDOW_S = 60
_pg_state = {"key": None, "seen": set(), "at": 0.0}
_pg_lock = _threading.Lock()


def _pg_cell():
    """The calling eval cell's top-level code object, or None."""
    f, cell = _sys._getframe(1), None
    while f is not None:
        if f.f_code.co_name == "<module>" and f.f_code.co_filename.startswith("<cell"):
            cell = f.f_code  # outermost one: a %load'ed file runs inside the cell too
        f = f.f_back
    return cell


@_both
def profile_grep(pattern, files=None, *, reveal=False, limit=15):
    """Profile facts: lines of the profile's owner files (PROFILE.md,
    open.md, registry/, biography/, career/) matching `pattern` (a regex,
    any case; a plain word works too): [{file, line_no, line}] (lines cut at
    400 chars). `files`: some of those globs only, e.g. ["registry/*.md"].
    Never sources/ or inbox/ (use this, never grep ~/Documents/agents/profile).
    IBANs show as DE••…••1234 and card numbers as ••••1234 unless
    reveal=True (only to type the full value into the task's own form). At
    most `limit` lines, and all calls of one eval cell together at most 30
    distinct lines (a line shown again costs nothing); it prints how many
    more matched, and where. limit=None: every match, no budget."""
    try:
        rx = _re.compile(pattern, _re.I)
    except _re.error:
        rx = _re.compile(_re.escape(pattern), _re.I)
    globs = list(files) if files else list(_PROFILE_FILES)
    out = []
    for g in globs:
        if _os.path.isabs(g) or ".." in g.split("/") or g.split("/")[0] in ("sources", "inbox"):
            raise ValueError(f"profile_grep reads only the owner files ({', '.join(_PROFILE_FILES)}), not {g!r}")
        for path in sorted(_glob.glob(_os.path.join(_PROFILE, g))):
            rel = _os.path.relpath(path, _PROFILE)
            if rel.split("/")[0] in ("sources", "inbox") or not any(_fnmatch.fnmatch(rel, p) for p in _PROFILE_FILES):
                continue
            try:
                with open(path, errors="replace") as f:
                    for n, line in enumerate(f, 1):
                        if rx.search(line):
                            line = line.rstrip("\n")
                            # masked before the cut, so a cut can't leave half an IBAN
                            out.append({"file": rel, "line_no": n, "line": (line if reveal else _mask_text(line))[:400]})
            except OSError:
                pass
    # a swapped profile root (the smoke tests' temp tree) gets a budget of its own
    cell, now = _pg_cell(), _time.monotonic()
    with _pg_lock:
        st = _pg_state
        # `is`: code objects compare by value, so two cells with the same text would be "equal"
        same = st["key"] is not None and st["key"][0] is cell and st["key"][1] == _PROFILE
        if not same or (cell is None and now - st["at"] > _PROFILE_WINDOW_S):
            st.update(key=(cell, _PROFILE), seen=set())
        st["at"] = now
        seen = st["seen"]
        if limit is None:
            seen.update((r["file"], r["line_no"]) for r in out)
            return out
        kept, rest, budget_hit = [], [], False
        for r in out:
            k = (r["file"], r["line_no"])
            if len(kept) >= limit:
                rest.append(r)
            elif k in seen or len(seen) < _PROFILE_CELL_BUDGET:
                seen.add(k)
                kept.append(r)
            else:
                budget_hit = True
                rest.append(r)
    if rest:
        where = {}
        for r in rest:
            where[r["file"]] = where.get(r["file"], 0) + 1
        top = max(where, key=where.get)
        where = ", ".join(f"{k} {v}" for k, v in sorted(where.items(), key=lambda kv: -kv[1]))
        why = f" beyond this cell's {_PROFILE_CELL_BUDGET}-line budget" if budget_hit else ""
        print(f"[profile_grep: {len(rest)} more lines match ({where}){why}: narrow the regex or pass"
              f" files=[{top!r}]]")
    return kept


def _note_logged_out(site):
    """The phrases of the site note's `- logged out: "…" | "…"` line."""
    try:
        with open(_os.path.join(_SITE_NOTES, f"{site}.md")) as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        m = _re.match(r"\s*[-*]\s*logged[ _-]?out\s*:(.*)", line, _re.I)
        if m:
            out += [p.strip().lower() for p in _re.findall(r'"([^"]+)"', m.group(1)) if p.strip()]
    return out


# Per-site rate cap of the bulk helpers (shop_map, fetch_json, pdf_text):
# {site: monotonic time the next request may start}. PayPal dropped the
# session after ~60 unthrottled detail fetches (LIFE-17).
_RATE_NEXT = globals().get("_RATE_NEXT") or {}
_RATE_INTERVAL = 1.0


async def _rate_wait(url):
    try:
        host = _urlparse.urlsplit(url).hostname
    except (ValueError, TypeError):
        host = None
    if not host:
        return
    site, now = _site(host), _time.monotonic()
    start = max(now, _RATE_NEXT.get(site, 0.0))
    _RATE_NEXT[site] = start + _RATE_INTERVAL
    if start > now:
        await _asyncio.sleep(start - now)


