# -*- coding: utf-8 -*-
"""Kalıcı / yeniden bağlanabilir tarama iş yöneticisi.

V4.6: Streamlit oturumuna bağlı ThreadPoolExecutor yerine ayrı bir Python
worker prosesi kullanır. İş durumu ve sonuçlar /tmp altında atomik dosyalara
kaydedilir. Böylece mobil tarayıcı ekranı kapandığında WebSocket kopsa bile
worker Streamlit oturumundan bağımsız çalışmaya devam eder.

Not: Community Cloud konteynerinin kendisi reboot/redeploy edilirse /tmp
silinir ve çalışan prosesler durur. Normal ekran kapatma / uygulama değiştirme
senaryosunda ise worker ayrı proses olarak devam eder.
"""

from __future__ import annotations

import errno
import json
import os
import pickle
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
JOB_ROOT = Path(tempfile.gettempdir()) / "bist_vwap_scan_jobs_v46"
JOB_ROOT.mkdir(parents=True, exist_ok=True)

_STATE_LOCK = threading.RLock()
_MANAGER = None


def _now_ts() -> float:
    return time.time()


def _job_dir(job_id: str) -> Path:
    return JOB_ROOT / str(job_id)


def _state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "state.json"


def _result_path(job_id: str) -> Path:
    return _job_dir(job_id) / "results.pkl"


def _config_path(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _log_path(job_id: str) -> Path:
    return _job_dir(job_id) / "worker.log"


def _lock_path(job_id: str) -> Path:
    return _job_dir(job_id) / ".launch.lock"


def _atomic_write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write_pickle(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_pickle(path: Path, default=None):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return default


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    # Linux'ta sonlanmış fakat parent tarafından henüz reap edilmemiş zombie
    # proses için os.kill(pid, 0) hâlâ başarılı döner. /proc durumunu ayrıca kontrol et.
    if os.name != "nt":
        try:
            stat_path = Path(f"/proc/{pid}/stat")
            if stat_path.exists():
                parts = stat_path.read_text(encoding="utf-8", errors="ignore").split()
                if len(parts) >= 3 and parts[2] == "Z":
                    return False
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    except Exception:
        return False


def _job_snapshot_from_disk(job_id: str):
    state = _read_json(_state_path(job_id))
    if not state:
        return None
    payload = _read_pickle(_result_path(job_id), default={}) or {}
    out = dict(state)
    out["result_sets"] = dict(payload.get("result_sets") or {})
    out["result_meta"] = dict(payload.get("result_meta") or {})
    return out


def _acquire_launch_lock(job_id: str):
    p = _lock_path(job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("ascii", "ignore"))
        os.close(fd)
        return True
    except FileExistsError:
        # Eski kilit 60 saniyeden uzun kaldıysa temizle.
        try:
            if _now_ts() - p.stat().st_mtime > 60:
                p.unlink(missing_ok=True)
                return _acquire_launch_lock(job_id)
        except Exception:
            pass
        return False


def _release_launch_lock(job_id: str):
    try:
        _lock_path(job_id).unlink(missing_ok=True)
    except Exception:
        pass


def _spawn_worker(job_id: str) -> bool:
    """Worker'ı Streamlit script thread'inden bağımsız proses olarak başlat."""
    if not _acquire_launch_lock(job_id):
        return False
    try:
        state = _read_json(_state_path(job_id), {}) or {}
        if _pid_alive(state.get("pid")):
            return True

        log_path = _log_path(job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "ab", buffering=0)
        worker_script = Path(os.environ.get("BIST_SCAN_WORKER_SCRIPT") or (BASE_DIR / "scan_worker.py"))
        cmd = [sys.executable, str(worker_script), str(job_id)]
        kwargs = {
            "cwd": str(BASE_DIR),
            "stdin": subprocess.DEVNULL,
            "stdout": log_f,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
            "env": {**os.environ, "PYTHONUNBUFFERED": "1"},
        }
        if os.name == "nt":
            flags = 0
            flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(cmd, **kwargs)
        try:
            log_f.close()
        except Exception:
            pass
        state.update({
            "pid": int(proc.pid),
            "status": state.get("status") if state.get("status") in {"queued", "running"} else "queued",
            "detail": state.get("detail") or "Arka plan worker başlatıldı.",
            "updated_ts": _now_ts(),
            "heartbeat_ts": _now_ts(),
        })
        _atomic_write_json(_state_path(job_id), state)
        return True
    finally:
        _release_launch_lock(job_id)


class ScanJobManager:
    def __init__(self):
        JOB_ROOT.mkdir(parents=True, exist_ok=True)

    def _prune(self):
        now = _now_ts()
        dirs = []
        for d in JOB_ROOT.iterdir():
            if not d.is_dir():
                continue
            state = _read_json(d / "state.json", {}) or {}
            dirs.append((d, float(state.get("created_ts") or 0), state))
            if state.get("status") in {"completed", "failed", "cancelled"}:
                if now - float(state.get("updated_ts") or now) > 12 * 3600:
                    try:
                        import shutil
                        shutil.rmtree(d, ignore_errors=True)
                    except Exception:
                        pass
        # En fazla 10 iş klasörü tut.
        dirs.sort(key=lambda x: x[1], reverse=True)
        for d, _, state in dirs[10:]:
            if state.get("status") in {"completed", "failed", "cancelled"}:
                try:
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass

    def start(self, kind, symbols, cfg):
        kind = str(kind)
        symbols = list(symbols or [])
        cfg = dict(cfg or {})
        with _STATE_LOCK:
            self._prune()
            active = self.active_snapshot()
            if active:
                # Worker öldüyse aynı işi kaldığı checkpoint'ten canlandır.
                if not _pid_alive(active.get("pid")):
                    _spawn_worker(active["id"])
                    active = self.snapshot(active["id"]) or active
                return active["id"], False

            job_id = uuid.uuid4().hex[:12]
            d = _job_dir(job_id)
            d.mkdir(parents=True, exist_ok=True)
            created = _now_ts()
            spec = {
                "id": job_id,
                "kind": kind,
                "symbols": symbols,
                "cfg": cfg,
                "created_ts": created,
            }
            state = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "progress": 0.0,
                "detail": "Tarama sıraya alındı.",
                "current_symbol": "",
                "done": 0,
                "total": len(symbols),
                "created_ts": created,
                "updated_ts": created,
                "heartbeat_ts": created,
                "started_at": None,
                "finished_at": None,
                "error": None,
                "revision": 0,
                "pid": None,
                "phase_index": 0,
                "phase_name": "",
                "cursor": 0,
            }
            _atomic_write_json(_config_path(job_id), spec)
            _atomic_write_json(_state_path(job_id), state)
            _atomic_write_pickle(_result_path(job_id), {"result_sets": {}, "result_meta": {}})
            _spawn_worker(job_id)
            return job_id, True

    def snapshot(self, job_id):
        if not job_id:
            return None
        with _STATE_LOCK:
            state = _read_json(_state_path(job_id))
            if not state:
                return None
            status = state.get("status")
            if status in {"queued", "running"} and not _pid_alive(state.get("pid")):
                # Worker beklenmedik şekilde durmuşsa checkpoint'ten yeniden başlat.
                # 2 sn tolerans: worker PID'sini yazmadan önceki kısa yarış durumunu önler.
                if _now_ts() - float(state.get("updated_ts") or 0) > 2:
                    state["detail"] = "Worker bağlantısı yenileniyor; kayıtlı noktadan devam edecek..."
                    state["updated_ts"] = _now_ts()
                    _atomic_write_json(_state_path(job_id), state)
                    _spawn_worker(job_id)
                    state = _read_json(_state_path(job_id), state) or state
            payload = _read_pickle(_result_path(job_id), default={}) or {}
            out = dict(state)
            out["result_sets"] = dict(payload.get("result_sets") or {})
            out["result_meta"] = dict(payload.get("result_meta") or {})
            return out

    def latest_snapshot(self):
        candidates = []
        for d in JOB_ROOT.iterdir():
            if not d.is_dir():
                continue
            state = _read_json(d / "state.json")
            if state:
                candidates.append((float(state.get("created_ts") or 0), state.get("id")))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return self.snapshot(candidates[0][1])

    def active_snapshot(self):
        candidates = []
        for d in JOB_ROOT.iterdir():
            if not d.is_dir():
                continue
            state = _read_json(d / "state.json")
            if state and state.get("status") in {"queued", "running"}:
                candidates.append((float(state.get("created_ts") or 0), state.get("id")))
        if not candidates:
            return None
        candidates.sort(reverse=True)
        return self.snapshot(candidates[0][1])


def get_scan_job_manager():
    global _MANAGER
    if _MANAGER is None:
        with _STATE_LOCK:
            if _MANAGER is None:
                _MANAGER = ScanJobManager()
    return _MANAGER


# Worker'ın kullanacağı dar yardımcı API. Bunlar Streamlit import etmez.
def worker_paths(job_id: str):
    return {
        "job": _config_path(job_id),
        "state": _state_path(job_id),
        "results": _result_path(job_id),
        "log": _log_path(job_id),
    }


def worker_read_job(job_id: str):
    return _read_json(_config_path(job_id))


def worker_read_state(job_id: str):
    return _read_json(_state_path(job_id), {}) or {}


def worker_write_state(job_id: str, state: dict):
    state = dict(state or {})
    state["updated_ts"] = _now_ts()
    state["heartbeat_ts"] = _now_ts()
    _atomic_write_json(_state_path(job_id), state)


def worker_read_results(job_id: str):
    return _read_pickle(_result_path(job_id), default={"result_sets": {}, "result_meta": {}}) or {"result_sets": {}, "result_meta": {}}


def worker_write_results(job_id: str, payload: dict):
    _atomic_write_pickle(_result_path(job_id), payload)


def worker_pid_alive(pid):
    return _pid_alive(pid)
