import json, socket, subprocess, time, os, sys
HOME=os.environ.get("USERPROFILE") or os.path.expanduser("~")
PORT=47611
_VENVS=[os.path.join(HOME,".search-daemon","Scripts","python.exe"),os.path.join(HOME,".search-daemon","bin","python")]
def _conn():
    return socket.create_connection(("127.0.0.1",PORT),timeout=30)
def _up():
    try:
        s=_conn(); s.close(); return True
    except OSError:
        return False
def _spawn():
    py=next((p for p in _VENVS if os.path.exists(p)),sys.executable)
    script=os.path.join(HOME,"bin","search.py")
    kw={"creationflags":0x08000000} if os.name=="nt" else {}
    subprocess.Popen([py,script,"serve"],creationflags=0x08000000) if os.name=="nt" else subprocess.Popen([py,script,"serve"])
def _ensure():
    if _up(): return
    _spawn()
    for _ in range(40):
        time.sleep(0.25)
        if _up(): return
    raise RuntimeError("daemon did not start")
def _rt(req):
    s=_conn()
    try:
        s.sendall(json.dumps(req).encode()+b"\n")
        buf=b""
        while not buf.endswith(b"\n"):
            d=s.recv(1<<20)
            if not d:
                raise ConnectionError("daemon closed connection")
            buf+=d
        return json.loads(buf.decode())
    finally:
        try: s.close()
        except Exception: pass
def search(q,tree=None,type_=None,limit=50,budget_ms=2000,fresh=False,hidden=False,fuzzy=False):
    req={"q":q,"budget_ms":budget_ms,"limit":limit}
    if tree: req["tree"]=tree
    if type_: req["type"]=type_
    if fresh: req["fresh"]=True
    if hidden: req["hidden"]=True
    if fuzzy: req["fuzzy"]=True
    last=None
    for attempt in range(2):
        try:
            return _rt(req)
        except (OSError,ConnectionError) as e:
            last=e
            _ensure()
    raise last
