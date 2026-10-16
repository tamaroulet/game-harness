"""Job Object（KILL_ON_JOB_CLOSE）で木ごと止める。非 Windows では何もしない。CREATE_BREAKAWAY_FROM_JOB は渡さない。"""
import ctypes as C
import functools
import subprocess
import sys

JobObjectExtendedLimitInformation, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 9, 0x2000
PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001


class _Basic(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                ("LimitFlags", C.c_uint32), ("MinimumWorkingSetSize", C.c_size_t),
                ("MaximumWorkingSetSize", C.c_size_t), ("ActiveProcessLimit", C.c_uint32),
                ("Affinity", C.c_size_t), ("PriorityClass", C.c_uint32), ("SchedulingClass", C.c_uint32)]


class _Extended(C.Structure):
    _fields_ = [("Basic", _Basic), ("IoInfo", C.c_uint64 * 6)] + [(n, C.c_size_t) for n in (
        "ProcessMemoryLimit", "JobMemoryLimit", "PeakProcessMemoryUsed", "PeakJobMemoryUsed")]


_k32_cache = None


def _win(default):
    """非 Windows と API の失敗では、例外を出さず default を返す。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a):
            try:
                return fn(*a) if sys.platform == "win32" else default
            except Exception:
                return default
        return wrapper
    return deco


def _k32():
    global _k32_cache
    if _k32_cache is None:
        k, v, i, u = C.WinDLL("kernel32", use_last_error=True), C.c_void_p, C.c_int, C.c_uint32
        for name, res, args in (("CreateJobObjectW", v, [v, C.c_wchar_p]), ("CloseHandle", i, [v]),
                                ("SetInformationJobObject", i, [v, i, v, u]), ("OpenProcess", v, [u, i, u]),
                                ("AssignProcessToJobObject", i, [v, v]), ("GetCurrentProcess", v, [])):
            fn = getattr(k, name)
            fn.restype, fn.argtypes = res, args
        _k32_cache = k
    return _k32_cache


@_win(False)
def supported():
    return bool(_k32())


@_win(None)
def open_job():
    k = _k32()
    job, info = k.CreateJobObjectW(None, None), _Extended()  # 属性 NULL = 握りを子に継承させない
    info.Basic.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if job and not k.SetInformationJobObject(job, JobObjectExtendedLimitInformation, C.byref(info), C.sizeof(info)):
        k.CloseHandle(job)
        job = None
    return job or None


@_win(False)
def assign(job, pid):
    k = _k32()
    handle = k.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
    try:
        return bool(handle and job and k.AssignProcessToJobObject(job, handle))
    finally:
        k.CloseHandle(handle)


@_win(None)
def close(job):
    _k32().CloseHandle(job)  # None（NULL）は失敗を返すだけ


@_win(None)
def _self_handle():
    job, k = open_job(), _k32()
    if job and not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
        k.CloseHandle(job)
        return None
    return job


@functools.lru_cache(maxsize=None)
def ensure_self_job():  # 初回の結果を返し続ける。握りは閉じない（閉じると自分ごと止まる）
    return _self_handle() is not None


def exited(proc, grace):  # Job を閉じたあとの終了は非同期なので、grace 秒まで待つ
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    return proc.poll() is not None


class Child:  # with Child(args, **popen_kwargs) as proc: …（proc は subprocess.Popen）
    def __init__(self, args, **popen_kwargs):
        self._args, self._kwargs, self._job, self._proc = args, popen_kwargs, None, None

    def __enter__(self):
        self._job = open_job()
        try:
            self._proc = subprocess.Popen(self._args, **self._kwargs)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        assign(self._job, self._proc.pid)
        return self._proc

    def kill_tree(self):
        proc, win = self._proc, sys.platform == "win32"
        try:
            self.__exit__(None, None, None)
            if proc is not None and not exited(proc, 3 if win else 0):
                if win:
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, stdin=subprocess.DEVNULL,
                                   timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if not exited(proc, 3 if win else 0):
                    proc.kill()
                proc.wait(timeout=30)
        except Exception:
            pass

    def __exit__(self, exc_type, exc, tb):
        job, self._job = self._job, None
        close(job)
