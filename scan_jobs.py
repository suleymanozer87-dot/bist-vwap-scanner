# -*- coding: utf-8 -*-
"""Streamlit oturumundan bağımsız arka plan tarama işleri.

Amaç: Mobil tarayıcı askıya alınsa / WebSocket kısa süreli kopsa bile tarama
sunucu prosesinde devam etsin. UI geri bağlandığında iş durumu ve sonuçlar
aynı job_id üzerinden tekrar alınır.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import streamlit as st

from vwap_core import (
    ALTERNATION_SCAN_PERIOD_LABELS,
    PERIOD_LABELS,
    TRENDLINE_SCAN_PERIOD_LABELS,
    TRIANGLE_SCAN_PERIOD_LABELS,
    scan_alternation_symbols_parallel,
    scan_symbols_parallel,
    scan_trendline_symbols_parallel,
    scan_triangle_symbols_parallel,
)


def _now_text():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def _now_ts():
    return time.time()


class ScanJobManager:
    """Tek proses içinde kalıcı tarama işlerini yönetir.

    Streamlit Session State'ten bağımsızdır. Community Cloud prosesinin kendisi
    yeniden başlatılırsa işler kaybolabilir; fakat normal mobil ekran kapatma,
    uygulama değiştirme veya geçici WebSocket kopmalarında devam eder.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bist-scan-job")
        self._jobs = {}
        self._latest_job_id = None

    def _prune(self):
        now = _now_ts()
        removable = []
        for job_id, job in self._jobs.items():
            if job.get("status") in {"completed", "failed"} and now - float(job.get("updated_ts") or now) > 6 * 3600:
                removable.append(job_id)
        for job_id in removable:
            self._jobs.pop(job_id, None)
        if len(self._jobs) > 8:
            ordered = sorted(
                self._jobs.items(),
                key=lambda kv: float(kv[1].get("created_ts") or 0),
            )
            for job_id, job in ordered:
                if len(self._jobs) <= 8:
                    break
                if job.get("status") in {"completed", "failed"}:
                    self._jobs.pop(job_id, None)

    def start(self, kind, symbols, cfg):
        kind = str(kind)
        symbols = list(symbols or [])
        cfg = dict(cfg or {})
        with self._lock:
            # Aynı proses içinde aynı anda iki büyük Yahoo taraması başlatmayalım.
            for existing in self._jobs.values():
                if existing.get("status") in {"queued", "running"}:
                    return existing["id"], False

            job_id = uuid.uuid4().hex[:12]
            created = _now_ts()
            job = {
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
                "started_at": None,
                "finished_at": None,
                "error": None,
                "result_sets": {},
                "result_meta": {},
                "revision": 0,
            }
            self._jobs[job_id] = job
            self._latest_job_id = job_id
            self._prune()
            self._executor.submit(_background_scan_worker, self, job_id, kind, symbols, cfg)
            return job_id, True

    def update(self, job_id, **updates):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            job["updated_ts"] = _now_ts()

    def set_result(self, job_id, name, rows, meta):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["result_sets"][name] = list(rows or [])
            job["result_meta"][name] = dict(meta or {})
            job["revision"] = int(job.get("revision") or 0) + 1
            job["updated_ts"] = _now_ts()

    def complete(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update({
                "status": "completed",
                "progress": 1.0,
                "detail": "Tarama tamamlandı.",
                "finished_at": _now_text(),
                "updated_ts": _now_ts(),
                "revision": int(job.get("revision") or 0) + 1,
            })

    def fail(self, job_id, exc):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update({
                "status": "failed",
                "detail": "Tarama hata ile durdu.",
                "error": str(exc),
                "finished_at": _now_text(),
                "updated_ts": _now_ts(),
                "revision": int(job.get("revision") or 0) + 1,
            })

    def snapshot(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            # DataFrame içeren sonuçları deep-copy etmiyoruz; sonuç setleri yalnız
            # tamamlandıktan sonra yayınlandığı için referansları güvenle okunabilir.
            out = dict(job)
            out["result_sets"] = dict(job.get("result_sets") or {})
            out["result_meta"] = dict(job.get("result_meta") or {})
            return out

    def latest_snapshot(self):
        with self._lock:
            job_id = self._latest_job_id
        return self.snapshot(job_id) if job_id else None

    def active_snapshot(self):
        with self._lock:
            for job in reversed(list(self._jobs.values())):
                if job.get("status") in {"queued", "running"}:
                    return self.snapshot(job.get("id"))
        return None


@st.cache_resource(show_spinner=False)
def get_scan_job_manager():
    return ScanJobManager()


def _meta(total, period, errors, source, currency=None):
    return {
        "total": int(total or 0),
        "period": period,
        "errors": list(errors or []),
        "source": source,
        "currency": currency,
        "scan_time": _now_text(),
    }


def _job_progress(manager, job_id, label, start=0.0, span=1.0):
    def cb(done, total, sym):
        ratio = (done / total) if total else 1.0
        overall = max(0.0, min(1.0, float(start) + float(span) * ratio))
        manager.update(
            job_id,
            status="running",
            progress=overall,
            detail=f"{label}: {done}/{total} · {sym}",
            current_symbol=str(sym or ""),
            done=int(done or 0),
            total=int(total or 0),
        )
    return cb


def _scan_vwap(manager, job_id, symbols, cfg, start=0.0, span=1.0, source="VWAP taraması"):
    errors = []
    callback = _job_progress(manager, job_id, "VWAP taranıyor", start, span)
    results, _, _, _, _, _ = scan_symbols_parallel(
        symbols,
        cfg.get("period", "weekly"),
        lookback=int(cfg.get("lookback", 3)),
        max_workers=int(cfg.get("max_workers", 20)),
        use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback,
        errors_out=errors,
        sideways_enabled=bool(cfg.get("sideways_enabled", False)),
        sideways_months_list=list(cfg.get("sideways_months_list") or [3, 6, 12]),
        sideways_range_pct=float(cfg.get("sideways_range_pct", 15.0)),
        sideways_atr_pct=float(cfg.get("sideways_atr_pct", 5.0)),
        sideways_method=str(cfg.get("sideways_method", "range")),
        sideways_min_windows=cfg.get("sideways_min_windows"),
        drawdown_enabled=bool(cfg.get("drawdown_enabled", False)),
        drawdown_min_pct=float(cfg.get("drawdown_min_pct", 60.0)),
        alternation_enabled=False,
        trendline_enabled=False,
        triangle_enabled=False,
        currency=str(cfg.get("currency", "TRY")),
    )
    manager.set_result(
        job_id,
        "VWAP",
        results,
        _meta(
            len(symbols),
            PERIOD_LABELS.get(cfg.get("period"), cfg.get("period")),
            errors,
            source,
            cfg.get("currency"),
        ),
    )
    return results, errors


def _scan_triangle(manager, job_id, symbols, cfg, start=0.0, span=1.0, source="Üçgen taraması"):
    errors = []
    callback = _job_progress(manager, job_id, "Üçgen taranıyor", start, span)
    results = scan_triangle_symbols_parallel(
        symbols,
        cfg.get("tri_scan_period", "4h"),
        max_workers=int(cfg.get("max_workers", 20)),
        use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback,
        errors_out=errors,
        pivot_window=int(cfg.get("tri_scan_pivot_window", 3)),
        min_span_bars=int(cfg.get("tri_scan_min_span_bars", 28)),
        lookback_bars=int(cfg.get("tri_scan_lookback_bars", 200)),
        min_apex_bars_ahead=int(cfg.get("tri_scan_min_apex_bars_ahead", 1)),
        max_apex_bars_ahead=int(cfg.get("tri_scan_max_apex_bars_ahead", 40)),
        max_squeeze_pct=float(cfg.get("tri_scan_max_squeeze_pct", 50.0)),
    )
    manager.set_result(
        job_id,
        "Üçgen",
        results,
        _meta(
            len(symbols),
            TRIANGLE_SCAN_PERIOD_LABELS.get(cfg.get("tri_scan_period"), cfg.get("tri_scan_period")),
            errors,
            source,
        ),
    )
    return results, errors


def _scan_trend(manager, job_id, symbols, cfg, start=0.0, span=1.0, source="Düşen trend taraması"):
    errors = []
    callback = _job_progress(manager, job_id, "Düşen trend taranıyor", start, span)
    results = scan_trendline_symbols_parallel(
        symbols,
        cfg.get("tl_scan_period", "1h"),
        max_workers=int(cfg.get("max_workers", 20)),
        use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback,
        errors_out=errors,
        pivot_window=int(cfg.get("tl_scan_pivot_window", 3)),
        min_span_bars=int(cfg.get("tl_scan_min_span_bars", 30)),
        lookback_bars=int(cfg.get("tl_scan_lookback_bars", 200)),
        breakout_lookback=int(cfg.get("tl_scan_breakout_lookback", 3)),
        touch_tolerance_pct=float(cfg.get("tl_scan_touch_tolerance_pct", 1.5)),
        require_volume=bool(cfg.get("tl_scan_require_volume", True)),
        volume_factor=float(cfg.get("tl_scan_volume_factor", 1.5)),
        min_touches=int(cfg.get("tl_scan_min_touches", 3)),
    )
    manager.set_result(
        job_id,
        "Düşen Trend",
        results,
        _meta(
            len(symbols),
            TRENDLINE_SCAN_PERIOD_LABELS.get(cfg.get("tl_scan_period"), cfg.get("tl_scan_period")),
            errors,
            source,
        ),
    )
    return results, errors


def _scan_alternation(manager, job_id, symbols, cfg, start=0.0, span=1.0, source="Alternasyon taraması"):
    errors = []
    callback = _job_progress(manager, job_id, "Alternasyon taranıyor", start, span)
    min_score = cfg.get("alt_scan_min_score")
    if min_score in ("", None):
        min_score = None
    else:
        min_score = float(min_score)
    results = scan_alternation_symbols_parallel(
        symbols,
        cfg.get("alt_scan_period", "monthly"),
        max_workers=int(cfg.get("max_workers", 20)),
        use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback,
        errors_out=errors,
        min_chain=int(cfg.get("alt_scan_min_chain", 3)),
        min_score=min_score,
    )
    manager.set_result(
        job_id,
        "Alternasyon",
        results,
        _meta(
            len(symbols),
            ALTERNATION_SCAN_PERIOD_LABELS.get(cfg.get("alt_scan_period"), cfg.get("alt_scan_period")),
            errors,
            source,
        ),
    )
    return results, errors


def _background_scan_worker(manager, job_id, kind, symbols, cfg):
    try:
        manager.update(
            job_id,
            status="running",
            started_at=_now_text(),
            detail=f"{kind} taraması başlıyor...",
            progress=0.0,
        )
        if kind == "VWAP":
            _scan_vwap(manager, job_id, symbols, cfg)
        elif kind == "Üçgen":
            _scan_triangle(manager, job_id, symbols, cfg)
        elif kind == "Düşen Trend":
            _scan_trend(manager, job_id, symbols, cfg)
        elif kind == "Alternasyon":
            _scan_alternation(manager, job_id, symbols, cfg)
        elif kind == "Tümünü Tara":
            _scan_vwap(manager, job_id, symbols, cfg, 0.00, 0.25, source="Tümünü Tara")
            _scan_triangle(manager, job_id, symbols, cfg, 0.25, 0.25, source="Tümünü Tara")
            _scan_trend(manager, job_id, symbols, cfg, 0.50, 0.25, source="Tümünü Tara")
            _scan_alternation(manager, job_id, symbols, cfg, 0.75, 0.25, source="Tümünü Tara")
        else:
            raise ValueError(f"Bilinmeyen tarama türü: {kind}")
        manager.complete(job_id)
    except Exception as exc:
        manager.fail(job_id, exc)
