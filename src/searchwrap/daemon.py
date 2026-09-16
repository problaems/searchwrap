import socket, threading, subprocess, json, sys, os, time, shutil, uuid, ctypes
from collections import OrderedDict

PORT = 47611
BUDGET_MS = 2000
LIMIT = 50
MAX_TREES = 3
RAM_LIMIT = 1_500_000_000
HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
_INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
_ES_CANDIDATES = [
    os.path.join(os.path.dirname(sys.executable), "es.exe"),
    os.path.join(HOME, "bin", "es.exe"),
    os.path.join(HOME, ".local", "share", "searchwrap", "bin", "es.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "searchwrap", "bin", "es.exe") if os.name == "nt" else None,
    r"C:\Program Files\Everything\es.exe",
]
_RG_CANDIDATES = [
    os.path.join(HOME, "scoop", "shims", "rg.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "searchwrap", "bin", "rg.exe") if os.name == "nt" else None,
    "/usr/bin/rg", "/usr/local/bin/rg", "/opt/homebrew/bin/rg",
]

def es_path():
    exe = os.environ.get("SEARCHWRAP_ES")
    if exe and os.path.exists(exe): return exe
    w = shutil.which("es")
    if w: return w
    for c in _ES_CANDIDATES:
        if c and os.path.exists(c): return c
    return None

def rg_path():
    w = shutil.which("rg")
    if w: return w
    for c in _RG_CANDIDATES:
        if c and os.path.exists(c): return c
    return "rg"

class FFFPool:
    def __init__(self):
        self.lock = threading.Lock()
        self.finders = OrderedDict()
        self.available = True
        self.error = None

    def flags_for(self, tree):
        n = os.path.normcase(os.path.normpath(tree))
        kw = {}
        if n == os.path.normcase(HOME):
            kw["enable_home_dir_scanning"] = True
        if os.path.splitdrive(n)[1] == "\\":
            kw["enable_fs_root_scanning"] = True
        return kw

    def get(self, tree):
        n = os.path.normpath(tree)
        with self.lock:
            if n in self.finders:
                self.finders.move_to_end(n)
                return self.finders[n], 0.0
            try:
                from fff import FileFinder
            except Exception as e:
                self.available = False
                self.error = str(e)
                raise
            t0 = time.perf_counter()
            ff = FileFinder(n, **self.flags_for(n))
            self.finders[n] = ff
            self.finders.move_to_end(n)
            ff.wait_for_scan_blocking()
            while len(self.finders) > MAX_TREES:
                k, v = self.finders.popitem(last=False)
                if not v.is_scanning():
                    try: v.close()
                    except Exception: pass
            return ff, (time.perf_counter() - t0) * 1000

    def close_all(self):
        with self.lock:
            for v in self.finders.values():
                try: v.close()
                except Exception: pass
            self.finders.clear()

POOL = FFFPool()
QUERY_COUNT = [0]

def rss_bytes():
    try:
        import ctypes.wintypes as wt
        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        k32 = ctypes.windll.kernel32
        k32.K32GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.wintypes.DWORD]
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        pmc = PMC(); pmc.cb = ctypes.sizeof(PMC)
        h = k32.GetCurrentProcess()
        if not k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), ctypes.sizeof(PMC)):
            return -1
        return pmc.WorkingSetSize
    except Exception:
        return -1

def run_es(q, tree=None, limit=LIMIT, deadline=None, want_total=False):
    t0 = time.perf_counter()
    es = es_path()
    if not es and os.environ.get("SEARCHWRAP_AUTOBOOT") == "1" and not _BOOT_TRIED["es"]:
        _BOOT_TRIED["es"] = True
        try: bootstrap_engine("es")
        except Exception: pass
        es = es_path()
    if not es:
        return {"engine": "es", "ms": 0.0, "hits": [], "total": 0, "status": "unavailable:es-not-found"}
    try:
        if want_total:
            args = [es, "-get-result-count"]
            if tree: args += ["-path", tree]
            args.append(q)
            p = subprocess.run(args, capture_output=True, text=True, timeout=max(0.2, deadline), creationflags=0x08000000)
            ms = (time.perf_counter() - t0) * 1000
            try: total = int(p.stdout.strip())
            except Exception: total = 0
            return {"engine": "es", "ms": round(ms, 1), "hits": [], "total": total, "status": "ok"}
        args = [es]
        if tree: args += ["-path", tree]
        args += ["-n", str(max(limit, 1)), q]
        p = subprocess.run(args, capture_output=True, text=True, timeout=max(0.2, deadline), creationflags=0x08000000)
        ms = (time.perf_counter() - t0) * 1000
        hits = [l for l in p.stdout.splitlines() if l.strip()][:limit]
        return {"engine": "es", "ms": round(ms, 1), "hits": [{"path": h} for h in hits], "total": len(hits), "status": "ok"}
    except Exception as e:
        return {"engine": "es", "ms": (time.perf_counter() - t0) * 1000, "hits": [], "total": 0, "status": f"error:{type(e).__name__}"}

def run_rg_name(pattern, tree, limit, deadline):
    t0 = time.perf_counter()
    g = pattern if any(c in pattern for c in "*?") else f"*{pattern}*"
    args = [rg_path(), "--files", tree or HOME, "-g", g, "--max-count", "1"]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=max(0.2, deadline), creationflags=0x08000000)
        hits = [l for l in p.stdout.splitlines() if l.strip()][:limit]
        return {"engine": "rg", "ms": (time.perf_counter() - t0) * 1000, "hits": [{"path": h} for h in hits], "total": len(hits), "status": "ok"}
    except Exception as e:
        return {"engine": "rg", "ms": (time.perf_counter() - t0) * 1000, "hits": [], "total": 0, "status": f"error:{type(e).__name__}"}

def run_rg_content(q, tree, limit, deadline):
    t0 = time.perf_counter()
    args = [rg_path(), "-n", "--no-heading", "-m", "20", q, tree or HOME]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=max(0.2, deadline), creationflags=0x08000000)
        hits = []
        for l in p.stdout.splitlines():
            m = None
            i = line_split(l)
            if i: hits.append(i)
            if len(hits) >= limit: break
        return {"engine": "rg", "ms": (time.perf_counter() - t0) * 1000, "hits": hits, "total": len(hits), "status": "ok"}
    except Exception as e:
        return {"engine": "rg", "ms": (time.perf_counter() - t0) * 1000, "hits": [], "total": 0, "status": f"error:{type(e).__name__}"}

def line_split(l):
    i = l.find(":", 2)
    if i < 0: return None
    j = l.find(":", i + 1)
    if j < 0: return None
    try:
        n = int(l[i + 1:j])
    except Exception:
        return None
    return {"path": l[:i], "line": n, "line_content": l[j + 1:][:200]}

def run_fff(tree, q, mode, limit, deadline, fuzzy=False):
    t0 = time.perf_counter()
    try:
        ff, scan_ms = POOL.get(tree)
    except Exception as e:
        return {"engine": "fff", "ms": (time.perf_counter() - t0) * 1000, "hits": [], "total": 0, "status": f"error:{type(e).__name__}", "scan_ms": 0}
    try:
        if mode == "content":
            r = ff.grep(q, max_matches_per_file=5)
            items = list(r.items)[:limit]
            hits = [{"path": os.path.join(tree, m.file_name), "line": m.line_number, "line_content": (m.line_content or "")[:200]} for m in items]
            total = r.total_matched
        else:
            pat = q if any(c in q for c in "*?") else (q if fuzzy else f"*{q}*")
            r = ff.glob(pat, page_size=limit)
            items = list(r.items)
            hits = [{"path": os.path.join(tree, i.relative_path), "score": getattr(i, "total_frecency_score", 0)} for i in items]
            total = getattr(r, "total_matched", len(hits))
        return {"engine": "fff", "ms": (time.perf_counter() - t0) * 1000, "hits": hits, "total": total, "status": "ok", "scan_ms": round(scan_ms, 1)}
    except Exception as e:
        return {"engine": "fff", "ms": (time.perf_counter() - t0) * 1000, "hits": [], "total": 0, "status": f"error:{type(e).__name__}", "scan_ms": 0}

def route(req):
    if req.get("type") == "meta": return "META", ["es"]
    if req.get("type") == "content": return ("CONTENT_TREE", ["fff", "rg"]) if req.get("tree") else ("CONTENT", ["rg"])
    if req.get("tree"): return "NAME_TREE", ["fff", "es"]
    if any(k in req.get("q", "") for k in ("dm:", "size:", "dupe:", "count")): return "META", ["es"]
    return "NAME_MACHINE", ["es", "rg"]

def guarded_fff(req, lane):
    if req.get("fresh") or req.get("hidden"): return False
    if any(w in req.get("q", "").lower() for w in ("appdata", "hidden", "system32")): return False
    return POOL.available and req.get("tree")

def race(engines, req, deadline):
    runners = {"es": lambda: run_es(req["q"], req.get("tree"), req.get("limit", LIMIT), deadline), "rg": lambda: run_rg_name(req["q"], req.get("tree"), req.get("limit", LIMIT), deadline)}
    results = {}
    threads = {}
    for e in engines:
        if e == "fff": continue
        threads[e] = threading.Thread(target=lambda e=e: results.update({e: runners[e]()}), daemon=True)
        threads[e].start()
    fff_res = [None]
    if "fff" in engines:
        def fffjob():
            mode = "content" if req.get("type") == "content" else "name"
            fff_res[0] = run_fff(req["tree"], req["q"], mode, req.get("limit", LIMIT), deadline, fuzzy=req.get("fuzzy", False))
        threads["fff"] = threading.Thread(target=fffjob, daemon=True)
        threads["fff"].start()
    t0 = time.perf_counter()
    verdict, winner, cancelled = None, None, []
    while time.perf_counter() - t0 < deadline:
        for e in engines:
            r = fff_res[0] if e == "fff" else results.get(e)
            if r is None: continue
            if r["status"] != "ok":
                verdict = r; continue
            ok = bool(r["hits"])
            if e == "fff" and not ok: continue
            if ok:
                winner = e; verdict = r; break
            results[e] = r
        if verdict: break
        time.sleep(0.005)
    elapsed = (time.perf_counter() - t0) * 1000
    if not verdict:
        for e in engines:
            r = fff_res[0] if e == "fff" else results.get(e)
            if r and r["hits"] and (e != "fff" or not req.get("fresh")):
                verdict = r; winner = e; break
    if not verdict:
        verdict = {"engine": "none", "ms": elapsed, "hits": [], "total": 0, "status": "empty"}
        cancelled = [e for e in engines if (fff_res[0] if e == "fff" else results.get(e)) is None]
    else:
        cancelled = [e for e in engines if e != verdict["engine"] and (fff_res[0] if e == "fff" else results.get(e)) is None]
    return verdict, winner, cancelled, elapsed, fff_res[0], results

def handle(req):
    t0 = time.perf_counter()
    cpu0 = time.process_time()
    lane, engines = route(req)
    deadline = req.get("budget_ms", BUDGET_MS) / 1000.0
    resp = {"lane": lane, "engines": engines}
    if lane == "META":
        r = run_es(req["q"], None, req.get("limit", LIMIT), deadline, want_total=True)
        resp.update({"winner": "es", "ms": r["ms"], "hits": r["hits"], "total": r["total"], "verified_not_found": ["es"] if r["status"] == "ok" and not r["hits"] else None, "engines_detail": [r]})
    elif lane == "NAME_MACHINE":
        r = run_es(req["q"], None, req.get("limit", LIMIT), deadline)
        if r["hits"]:
            resp.update({"winner": "es", "ms": r["ms"], "hits": r["hits"], "total": r["total"], "verified_not_found": None, "engines_detail": [r]})
        else:
            r2 = run_rg_name(req["q"], None, req.get("limit", LIMIT), max(0.2, deadline - r["ms"] / 1000.0))
            nf = [e for e, x in (("es", r), ("rg", r2)) if x["status"] == "ok" and not x["hits"]]
            resp.update({"winner": "rg" if r2["hits"] else ("es" if not r2["hits"] and not nf else None), "ms": r["ms"] + r2["ms"], "hits": r2["hits"], "total": r2["total"], "verified_not_found": nf or None, "partial": bool(r2["status"].startswith("error") or (not r2["hits"] and r2["ms"] >= deadline * 1000 * 0.9)), "engines_detail": [r, r2]})
    elif lane in ("NAME_TREE", "CONTENT_TREE"):
        use = list(engines)
        if not guarded_fff(req, lane): use.remove("fff")
        verdict, winner, cancelled, elapsed, fff_res, all_res = race(use, req, deadline)
        detail = [all_res[e] for e in use if e in all_res] + ([fff_res] if fff_res else [])
        empties = [x["engine"] for x in detail if x and x["status"] == "ok" and not x["hits"] and x["engine"] != "fff"]
        resp.update({"winner": winner, "ms": round(elapsed, 1), "hits": verdict.get("hits", []), "total": verdict.get("total", 0), "verified_not_found": empties or None, "cancelled": cancelled or None, "engines_detail": detail})
    elif lane == "CONTENT":
        r = run_rg_content(req["q"], req.get("tree") or HOME, req.get("limit", LIMIT), deadline)
        resp.update({"winner": "rg", "ms": r["ms"], "hits": r["hits"], "total": r["total"], "verified_not_found": ["rg"] if r["status"] == "ok" and not r["hits"] else None, "engines_detail": [r]})
    QUERY_COUNT[0] += 1
    if QUERY_COUNT[0] % 20 == 0:
        m = rss_bytes()
        if m > RAM_LIMIT: POOL.close_all()
    resp["ms"] = round(time.perf_counter() - t0, 1) * 1000 if resp.get("ms") is None else resp["ms"]
    resp["cpu_ms"] = round((time.process_time() - cpu0) * 1000, 1)
    resp["fff_available"] = POOL.available
    if POOL.error: resp["fff_error"] = POOL.error
    return resp

def serve():
    if os.name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            k32.CreateJobObjectW.restype = ctypes.c_void_p
            hjob = k32.CreateJobObjectW(None, None)
            class _BASIC_LIMIT(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                            ("LimitFlags", ctypes.c_ulong), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_ulong),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_ulong), ("SchedulingClass", ctypes.c_ulong)]
            class _IOC(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount","WriteOperationCount","OtherOperationCount","ReadTransferCount","WriteTransferCount","OtherTransferCount")]
            class _EXT_LIMIT(ctypes.Structure):
                _fields_ = [("Basic", _BASIC_LIMIT), ("Io", _IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
            el = _EXT_LIMIT(); el.Basic.LimitFlags = 0x00002000
            k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
            k32.SetInformationJobObject(ctypes.c_void_p(hjob), 9, ctypes.byref(el), ctypes.sizeof(el))
            k32.GetCurrentProcess.restype = ctypes.c_void_p
            k32.AssignProcessToJobObject(ctypes.c_void_p(hjob), ctypes.c_void_p(k32.GetCurrentProcess()))
        except Exception:
            pass
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        except (AttributeError, OSError):
            pass
    srv.bind(("127.0.0.1", PORT)); srv.listen(8)
    print(f"searchd up pid={os.getpid()} fff_import={'pending'}", flush=True)
    try:
        import fff; POOL.available = True
    except Exception as e:
        POOL.available = False; POOL.error = str(e)
    print(f"fff_available={POOL.available}", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=client, args=(c,), daemon=True).start()

_BOOT_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.join(HOME, ".local", "share")), "searchwrap", "bin")
_ES_URL = "https://www.voidtools.com/ES-1.1.0.38.x64.zip"
_RG_URL = "https://github.com/BurntSushi/ripgrep/releases/download/14.1.0/ripgrep-14.1.0-x86_64-pc-windows-msvc.zip"
_FFF_PKG = "fff-search==0.10.6"
_BOOT_TRIED = {"es": False}

def _service_alive():
    if os.name != "nt": return False
    try:
        adv = ctypes.windll.advapi32
        adv.OpenSCManagerW.restype = ctypes.c_void_p
        h = adv.OpenSCManagerW(None, None, 1)
        if not h: return False
        adv.OpenServiceW.restype = ctypes.c_void_p
        hs = adv.OpenServiceW(ctypes.c_void_p(h), "Everything", 0x0004)
        if not hs:
            adv.CloseServiceHandle(ctypes.c_void_p(h)); return False
        class _SS(ctypes.Structure):
            _fields_ = [("dwServiceType", ctypes.c_ulong), ("dwCurrentState", ctypes.c_ulong),
                        ("dwControlsAccepted", ctypes.c_ulong), ("dwWin32ExitCode", ctypes.c_ulong),
                        ("dwServiceSpecificExitCode", ctypes.c_ulong), ("dwCheckPoint", ctypes.c_ulong), ("dwWaitHint", ctypes.c_ulong)]
        st = _SS()
        ok = adv.QueryServiceStatus(ctypes.c_void_p(hs), ctypes.byref(st))
        adv.CloseServiceHandle(ctypes.c_void_p(hs)); adv.CloseServiceHandle(ctypes.c_void_p(h))
        return bool(ok and st.dwCurrentState == 4)
    except Exception:
        return False

_RG_URLS = [
    ("github", "https://github.com/BurntSushi/ripgrep/releases/download/14.1.0/ripgrep-14.1.0-x86_64-pc-windows-msvc.zip"),
    ("scoop-manifest", "https://raw.githubusercontent.com/ScoopInstaller/Main/master/bucket/ripgrep.json"),
    ("system", "cmd:scoop"),
]

def _bootstrap_rg(timeout=120):
    existing = rg_path()
    if existing and (os.path.isabs(existing) or existing != "rg"):
        return {"source": "already-installed", "path": existing, "skipped_download": True}
    dest = os.path.join(_BOOT_DIR, "rg.exe")
    if os.path.exists(dest):
        return {"source": "already-bootstrapped", "path": dest, "skipped_download": True}
    os.makedirs(_BOOT_DIR, exist_ok=True)
    errors = []
    import urllib.request, zipfile, io
    for label, url in _RG_URLS:
        try:
            if url.startswith("cmd:"):
                tool = url[4:]
                w = shutil.which(tool)
                if w:
                    return {"source": f"system:{tool}", "path": w}
                errors.append(f"{label}: {tool} not on PATH")
                continue
            req = urllib.request.Request(url, headers={"User-Agent": "searchwrap-bootstrap/1.0"})
            raw = urllib.request.urlopen(req, timeout=timeout).read()
            if url.endswith(".json"):
                import json as _json
                m = _json.loads(raw.decode("utf-8"))
                arch = "64bit" if os.environ.get("PROCESSOR_ARCHITECTURE", "").endswith("64") else "32bit"
                u = next((x for x in m.get("architecture", {}).get(arch, {}).get("url", []) if isinstance(x, str)), None)
                if not u:
                    errors.append(f"{label}: no url in manifest")
                    continue
                req2 = urllib.request.Request(u, headers={"User-Agent": "searchwrap-bootstrap/1.0"})
                raw = urllib.request.urlopen(req2, timeout=timeout).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                cands = [n for n in z.namelist() if n.endswith("rg.exe")]
                if not cands:
                    errors.append(f"{label}: no rg.exe in zip")
                    continue
                with z.open(cands[0]) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                return {"source": label, "path": dest, "bytes": len(raw)}
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}: {str(e)[:100]}")
    return {"error": "all rg sources failed", "attempts": errors,
            "hint": "cannot be resolved: check network/DNS/proxy; or install ripgrep manually (winget install BurntSushi.ripgrep | scoop install ripgrep | choco install ripgrep) and ensure rg.exe is on PATH"}

def bootstrap_engine(which, timeout=120):
    dest_dir = _BOOT_DIR
    if which == "es":
        existing = es_path()
        if existing and (os.path.isabs(existing) or existing != "es"):
            return {"source": "already-installed", "path": existing, "skipped_download": True}
    if which == "fff":
        try:
            import fff
            from importlib.metadata import distributions
            names = {d.metadata.get("Name", "").lower() for d in distributions()}
            if "fff-search" in names:
                return {"source": "already-installed", "skipped_install": True}
        except Exception:
            pass
    os.makedirs(dest_dir, exist_ok=True)
    try:
        import urllib.request, zipfile, io
        if which == "es":
            raw = urllib.request.urlopen(_ES_URL, timeout=timeout).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for n in z.namelist():
                    if n.lower().endswith("es.exe"):
                        open(os.path.join(dest_dir, "es.exe"), "wb").write(z.read(n)); break
            return os.path.join(dest_dir, "es.exe") if os.path.exists(os.path.join(dest_dir, "es.exe")) else {"error": "es.exe not found inside zip"}
        if which == "fff":
            p = subprocess.run([sys.executable, "-m", "pip", "install", _FFF_PKG], capture_output=True, text=True, timeout=timeout * 4)
            try:
                import fff
                return {"installed": _FFF_PKG}
            except Exception as e:
                return {"error": f"pip ok but import failed: {e}"}
        return {"error": f"unknown engine: {which}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}

def cmd_status():
    es = es_path(); fff_ok = True
    try:
        import fff
    except Exception:
        fff_ok = False
    svc = _service_alive() if os.name == "nt" else False
    es_live = False
    if es and svc:
        try:
            p = subprocess.run([es, "-get-result-count", "a"], capture_output=True, text=True, timeout=5, creationflags=0x08000000 if os.name == "nt" else 0)
            es_live = (p.returncode == 0)
        except Exception:
            es_live = False
    return {
        "es_exe": bool(es), "es_path": es,
        "everything_service": "running" if svc else ("not detected" if os.name == "nt" else "n/a"),
        "es_live_query": es_live,
        "rg": bool(rg_path()), "fff": fff_ok,
        "degraded_lanes": ([l for l, ok in (("name-machine", es and svc), ("meta", es and svc), ("name-tree-fff", fff_ok), ("content", True)) if not ok] or []),
        "bootstrap_available": {"es": os.name == "nt", "fff": True, "rg": "network permitting (github releases) or system package"},
    }

def bootstrap(which):
    if which not in ("es", "fff", "rg"):
        return {"error": "bootstrap target must be es|fff|rg"}
    if which == "rg":
        r = _bootstrap_rg()
    else:
        r = bootstrap_engine(which)
    return {"target": which, "result": r}

def client(c):
    try:
        buf = b""
        while not buf.endswith(b"\n"):
            d = c.recv(65536)
            if not d: return
            buf += d
        req = json.loads(buf.decode())
        if req.get("cmd") == "stop":
            c.sendall(b'{"bye":1}\n'); c.close(); os._exit(0)
        if req.get("cmd") == "health":
            out = {"pid": os.getpid(), "fff_available": POOL.available, "trees": list(POOL.finders.keys()), "queries": QUERY_COUNT[0], "rss": rss_bytes()}
        elif req.get("cmd") == "stats":
            out = {"pid": os.getpid(), "queries": QUERY_COUNT[0], "trees_warm": list(POOL.finders.keys()), "rss_kb": rss_bytes(), "cpu_total_ms": round(time.process_time() * 1000, 1), "fff_available": POOL.available}
        else:
            out = handle(req)
        c.sendall(json.dumps(out).encode() + b"\n")
    except Exception as e:
        try: c.sendall(json.dumps({"fatal": str(e)}).encode() + b"\n")
        except Exception: pass
    finally:
        try: c.close()
        except Exception: pass

def send(req, spawn=True):
    try:
        s = socket.create_connection(("127.0.0.1", PORT), timeout=10)
        s.sendall(json.dumps(req).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            d = s.recv(1 << 20)
            if not d: raise ConnectionError("closed")
            buf += d
        s.close()
        return json.loads(buf.decode())
    except OSError:
        if not spawn or req.get("cmd") in ("stop",): raise
        exe = os.environ.get("SEARCHWRAP_PYTHON") or sys.executable
        if not os.path.exists(exe):
            exe = os.path.join(HOME, ".search-daemon", "Scripts", "python.exe")
        if not os.path.exists(exe): exe = sys.executable
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        subprocess.Popen([exe, os.path.abspath(__file__), "serve"], **kw)
        for _ in range(60):
            time.sleep(0.25)
            try: return send(req, spawn=False)
            except OSError: continue
        raise RuntimeError("daemon did not start")

def main():
    if len(sys.argv) < 2:
        print("usage: search.py serve|query|stop|health|selftest [opts]"); return
    mode = sys.argv[1]
    if mode == "serve": serve(); return
    if mode == "stop":
        try: print(send({"cmd": "stop"}, spawn=False)); 
        except Exception as e: print("daemon not running")
        return
    if mode == "health":
        try: print(json.dumps(send({"cmd": "health"}), indent=1))
        except Exception: print("daemon not running")
        return
    if mode == "stats":
        try: print(json.dumps(send({"cmd": "stats"}), indent=1))
        except Exception: print("daemon not running")
        return
    if mode == "setup":
        es = es_path()
        rg = rg_path()
        try:
            import fff; fff_ok = True; fff_err = None
        except Exception as e:
            fff = None; fff_ok = False; fff_error = str(e)
        print(json.dumps({
            "es": {"found": bool(es), "path": es, "note": "requires Everything service running (GUI app, admin to install)" if os.name == "nt" else "es.exe is Windows-only"},
            "rg": {"found": bool(shutil.which("rg") or _RG_CANDIDATES[0] and os.path.exists(_RG_CANDIDATES[0])), "path": shutil.which("rg") or (_RG_CANDIDATES[0] if os.path.exists(_RG_CANDIDATES[0]) else None)},
            "fff": {"found": fff_ok, "note": None if fff_ok else "pip install fff-search into the daemon's python"},
            "env_overrides": {"SEARCHWRAP_ES": os.environ.get("SEARCHWRAP_ES"), "SEARCHWRAP_RG": os.environ.get("SEARCHWRAP_RG")},
            "missing_ok": True,
            "hint": "wrapper degrades per-lane; es lanes need es.exe + Everything service; fff needs pip install fff-search; rg is required for content lanes",
        }, indent=1))
        return
    if mode == "status":
        print(json.dumps(cmd_status(), indent=1))
        return
    if mode == "bootstrap":
        target = sys.argv[2] if len(sys.argv) > 2 else ""
        print(json.dumps(bootstrap(target), indent=1))
        return
    if mode == "query":
        args = sys.argv[2:]
        req = {"q": "", "budget_ms": BUDGET_MS, "limit": LIMIT}
        flags = {"--type": "type", "--tree": "tree", "--budget": "budget_ms", "--limit": "limit"}
        i = 0; show_json = "--json" in args
        args = [a for a in args if a != "--json"]
        while i < len(args):
            a = args[i]
            if a in flags: req[flags[a]] = args[i + 1]; i += 2; continue
            if a == "--fresh": req["fresh"] = True; i += 1; continue
            if a == "--hidden": req["hidden"] = True; i += 1; continue
            if a == "--fuzzy": req["fuzzy"] = True; i += 1; continue
            if a == "--raw":
                t = req.get("type") or ("content" if "--content" in args else "name")
                eng = es_path() if (t in ("name", "meta")) else rg_path()
                cmd = [eng] + ([req["tree"]] if req.get("tree") and eng == rg_path() else []) + args[i + 1:]
                p = subprocess.run(cmd, capture_output=True, text=True)
                sys.stdout.write(p.stdout[:20000]); return
            req["q"] = a; i += 1
        if "--content" in sys.argv: req["type"] = "content"
        out = send(req)
        if "--json" in sys.argv or show_json: pass
        print(json.dumps(out, indent=1)[:12000] if show_json else fmt(out))
        return
    if mode == "selftest":
        selftest(); return

def fmt(r):
    lines = [f"lane={r['lane']} winner={r.get('winner')} ms={r.get('ms')} hits={len(r.get('hits', []))} total={r.get('total')}"]
    for x in r.get("engines_detail", []):
        if x: lines.append(f"  {x['engine']:>4}: {x['ms']:.0f} ms status={x['status']} n={len(x['hits'])} scan_ms={x.get('scan_ms', '-')}")
    if r.get("verified_not_found"): lines.append(f"  verified_not_found={r['verified_not_found']}")
    if r.get("partial"): lines.append("  partial=true")
    if r.get("cancelled"): lines.append(f"  cancelled={r['cancelled']}")
    for h in r.get("hits", [])[:5]: lines.append(f"    {h.get('path')} {(':' + str(h.get('line'))) if h.get('line') else ''}")
    return "\n".join(lines)

def _tmp_tree():
    import tempfile, uuid
    d = tempfile.mkdtemp(prefix="searchwrap-test-")
    _TREE_TOKEN = "searchwrap_fixture_token"
    open(os.path.join(d, "alpha_fixture.txt"), "w").write(f"{_TREE_TOKEN} alpha line\n{_TREE_TOKEN} repeated\n")
    open(os.path.join(d, "beta_fixture.log"), "w").write(f"{_TREE_TOKEN} beta line\nplain filler text\n")
    open(os.path.join(d, "gamma.txt"), "w").write("plain filler text only\n")
    return d, _TREE_TOKEN

_MACHINE_NONCE = None

def _machine_fixture():
    global _MACHINE_NONCE
    if _MACHINE_NONCE is None:
        import tempfile, uuid, time
        _MACHINE_NONCE = "swx" + uuid.uuid4().hex[:12]
        d = tempfile.mkdtemp(prefix="searchwrap-home-")
        for j in range(3):
            open(os.path.join(d, f"{_MACHINE_NONCE}_file{j}.txt"), "w").write(f"{_MACHINE_NONCE} content\n")
        # machine-wide lane goes through es (Everything index) - wait until indexed (usually <1s)
        es = es_path()
        if es:
            for _ in range(20):
                time.sleep(0.25)
                try:
                    p = subprocess.run([es, "-n", "1", _MACHINE_NONCE], capture_output=True, text=True, timeout=10, creationflags=0x08000000 if os.name == "nt" else 0)
                    if p.returncode == 0 and p.stdout.strip():
                        break
                except Exception:
                    break
    return _MACHINE_NONCE

def selftest():
    tree, tree_token = _tmp_tree()
    name_q = tree_token.split("_")[1]  # "fixture" - present in 2 fixture filenames
    mnonce = _machine_fixture()
    cases = [
        ("T1 meta count", {"q": "ext:blend", "type": "meta"}),
        ("T2 name machine fixture", {"q": mnonce}),
        ("T3 name tree race token", {"q": name_q, "tree": tree}),
        ("T4 content one-shot", {"q": tree_token, "type": "content", "tree": tree}),
        ("T5 genuine miss", {"q": "zzznotexist12345xyz"}),
        ("T6 hidden dir guard", {"q": "*.ipynb", "hidden": True}),
        ("T7 fresh guard", {"q": mnonce, "fresh": True}),
    ]
    print(f"{'case':22s} {'ms':>8s} {'winner':6s} {'hits':>5s}  detail")
    solo = {}
    for name, req in cases:
        r = send(req)
        d = " | ".join(f"{x['engine']}={x['ms']:.0f}ms/n={len(x['hits'])}/{x['status']}" for x in r.get("engines_detail", []) if x)
        print(f"{name:22s} {r.get('ms', 0):8.1f} {str(r.get('winner')):6s} {len(r.get('hits', [])):5d}  {d}")
        if r.get("verified_not_found"): print(f"   verified_not_found={r['verified_not_found']}")
        if r.get("partial"): print("   partial=true")
    print("\nsolo baselines (direct in-proc, no daemon):")
    t0 = time.perf_counter(); r = run_es(mnonce, None, 25, 30); print(f"  es solo fixture: {r['ms']:.0f} ms n={len(r['hits'])} status={r['status']}")
    t0 = time.perf_counter(); r = run_rg_content(tree_token, tree, 30, 30); print(f"  rg solo content: {r['ms']:.0f} ms n={len(r['hits'])}")

if __name__ == "__main__":
    main()
