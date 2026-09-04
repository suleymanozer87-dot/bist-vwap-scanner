# -*- coding: utf-8 -*-
"""V4.6 bağımsız tarama worker prosesi.

Bu dosya Streamlit UI prosesinden subprocess olarak başlatılır. Tarama küçük
bloklara ayrılır; her bloktan sonra sonuç ve cursor diske checkpoint edilir.
Worker kesilirse sonraki açılışta aynı job_id ile kaldığı bloktan devam eder.
"""

from __future__ import annotations

import ast
import os
import sys
import time
import traceback
from datetime import datetime

from scan_jobs import (
    worker_read_job,
    worker_read_results,
    worker_read_state,
    worker_write_results,
    worker_write_state,
)
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


def now_text():
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def _phases(kind):
    if kind == "Tümünü Tara":
        return ["VWAP", "Üçgen", "Düşen Trend", "Alternasyon"]
    return [kind]


def _period_for(phase, cfg):
    if phase == "VWAP":
        return cfg.get("period", "weekly")
    if phase == "Üçgen":
        return cfg.get("tri_scan_period", "4h")
    if phase == "Düşen Trend":
        return cfg.get("tl_scan_period", "1h")
    if phase == "Alternasyon":
        return cfg.get("alt_scan_period", "monthly")
    return ""


def _period_label(phase, cfg):
    p = _period_for(phase, cfg)
    if phase == "VWAP":
        return PERIOD_LABELS.get(p, p)
    if phase == "Üçgen":
        return TRIANGLE_SCAN_PERIOD_LABELS.get(p, p)
    if phase == "Düşen Trend":
        return TRENDLINE_SCAN_PERIOD_LABELS.get(p, p)
    if phase == "Alternasyon":
        return ALTERNATION_SCAN_PERIOD_LABELS.get(p, p)
    return p


def _chunk_size(phase, cfg):
    p = str(_period_for(phase, cfg)).lower()
    # Yahoo intraday çağrılarını küçük bloklarda tut. Günlük/haftalık/aylık daha büyük olabilir.
    if p in {"1h", "4h", "hourly", "intraday"}:
        return 12
    return 28


def _merge_rows(old_rows, new_rows):
    # Her tarama bir sembol için en fazla bir sonuç üretir. Yeniden işlenen blokta
    # aynı sembol varsa yenisi eskisinin yerine geçer; böylece crash sonrası duplicate olmaz.
    out = []
    pos = {}
    for row in list(old_rows or []) + list(new_rows or []):
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper()
        if not sym:
            continue
        if sym in pos:
            out[pos[sym]] = row
        else:
            pos[sym] = len(out)
            out.append(row)
    return out


def _normalize_error_item(item):
    """Worker sonucunda hata kayıtlarını JSON-uyumlu [symbol, message] biçiminde tut."""
    if isinstance(item, str):
        text = item.strip()
        if text.startswith(("(", "[", "{")):
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                parsed = None
            if parsed is not None and parsed is not item:
                return _normalize_error_item(parsed)
        if ": " in text:
            sym, msg = text.split(": ", 1)
            return [sym.strip() or "—", msg.strip() or "Bilinmeyen hata"]
        return ["—", text or "Bilinmeyen hata"]
    if isinstance(item, dict):
        sym = item.get("symbol") or item.get("sym") or item.get("ticker") or item.get("code") or "—"
        msg = item.get("error") or item.get("message") or item.get("detail") or item.get("reason") or str(item)
        return [str(sym), str(msg)]
    if isinstance(item, (list, tuple)):
        if len(item) >= 2:
            return [str(item[0] or "—"), " | ".join(str(x) for x in item[1:] if x not in (None, "")) or "Bilinmeyen hata"]
        if len(item) == 1:
            return ["—", str(item[0])]
        return ["—", "Bilinmeyen hata"]
    # Eski checkpoint'lerde str(tuple) bulunabilir; biçimi burada bozmadan UI güvenli okuyacak.
    return ["—", str(item) if item is not None else "Bilinmeyen hata"]


def _dedupe_errors(items):
    out = []
    seen = set()
    for x in items or []:
        row = _normalize_error_item(x)
        key = (row[0], row[1])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _scan_chunk(phase, symbols, cfg, progress_cb, errors):
    max_workers = int(cfg.get("max_workers", 20))
    use_cache = bool(cfg.get("use_cache", True))
    if phase == "VWAP":
        results, _, _, _, _, _ = scan_symbols_parallel(
            symbols,
            cfg.get("period", "weekly"),
            lookback=int(cfg.get("lookback", 3)),
            max_workers=max_workers,
            use_cache=use_cache,
            progress_callback=progress_cb,
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
        return results

    if phase == "Üçgen":
        return scan_triangle_symbols_parallel(
            symbols,
            cfg.get("tri_scan_period", "4h"),
            max_workers=max_workers,
            use_cache=use_cache,
            progress_callback=progress_cb,
            errors_out=errors,
            pivot_window=int(cfg.get("tri_scan_pivot_window", 3)),
            min_span_bars=int(cfg.get("tri_scan_min_span_bars", 28)),
            lookback_bars=int(cfg.get("tri_scan_lookback_bars", 200)),
            min_apex_bars_ahead=int(cfg.get("tri_scan_min_apex_bars_ahead", 1)),
            max_apex_bars_ahead=int(cfg.get("tri_scan_max_apex_bars_ahead", 40)),
            max_squeeze_pct=float(cfg.get("tri_scan_max_squeeze_pct", 50.0)),
        )

    if phase == "Düşen Trend":
        return scan_trendline_symbols_parallel(
            symbols,
            cfg.get("tl_scan_period", "1h"),
            max_workers=max_workers,
            use_cache=use_cache,
            progress_callback=progress_cb,
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

    if phase == "Alternasyon":
        min_score = cfg.get("alt_scan_min_score")
        if min_score in ("", None):
            min_score = None
        else:
            min_score = float(min_score)
        return scan_alternation_symbols_parallel(
            symbols,
            cfg.get("alt_scan_period", "monthly"),
            max_workers=max_workers,
            use_cache=use_cache,
            progress_callback=progress_cb,
            errors_out=errors,
            min_chain=int(cfg.get("alt_scan_min_chain", 3)),
            min_score=min_score,
        )
    raise ValueError(f"Bilinmeyen faz: {phase}")


def run(job_id):
    spec = worker_read_job(job_id)
    if not spec:
        raise RuntimeError(f"İş tanımı bulunamadı: {job_id}")
    kind = str(spec.get("kind"))
    symbols = list(spec.get("symbols") or [])
    cfg = dict(spec.get("cfg") or {})
    phases = _phases(kind)
    state = worker_read_state(job_id)
    payload = worker_read_results(job_id)
    payload.setdefault("result_sets", {})
    payload.setdefault("result_meta", {})

    state.update({
        "id": job_id,
        "kind": kind,
        "pid": os.getpid(),
        "status": "running",
        "started_at": state.get("started_at") or now_text(),
        "error": None,
    })
    worker_write_state(job_id, state)

    start_phase = int(state.get("phase_index") or 0)
    cursor = int(state.get("cursor") or 0)

    for phase_idx in range(start_phase, len(phases)):
        phase = phases[phase_idx]
        phase_cursor = cursor if phase_idx == start_phase else 0
        chunk_size = _chunk_size(phase, cfg)
        existing_rows = list(payload["result_sets"].get(phase) or [])
        existing_meta = dict(payload["result_meta"].get(phase) or {})
        all_errors = list(existing_meta.get("errors") or [])

        while phase_cursor < len(symbols):
            end = min(len(symbols), phase_cursor + chunk_size)
            chunk = symbols[phase_cursor:end]
            chunk_errors = []

            def progress_cb(done, total, sym):
                local_done = min(len(chunk), int(done or 0))
                absolute = phase_cursor + local_done
                phase_ratio = absolute / len(symbols) if symbols else 1.0
                overall = (phase_idx + phase_ratio) / len(phases)
                s = worker_read_state(job_id)
                s.update({
                    "pid": os.getpid(),
                    "status": "running",
                    "phase_index": phase_idx,
                    "phase_name": phase,
                    "cursor": phase_cursor,
                    "progress": max(0.0, min(1.0, overall)),
                    "detail": f"{phase} taranıyor: {absolute}/{len(symbols)} · {sym}",
                    "current_symbol": str(sym or ""),
                    "done": absolute,
                    "total": len(symbols),
                })
                worker_write_state(job_id, s)

            new_rows = _scan_chunk(phase, chunk, cfg, progress_cb, chunk_errors)
            existing_rows = _merge_rows(existing_rows, new_rows)
            all_errors = _dedupe_errors(all_errors + chunk_errors)
            payload["result_sets"][phase] = existing_rows
            payload["result_meta"][phase] = {
                "total": len(symbols),
                "period": _period_label(phase, cfg),
                "errors": all_errors,
                "source": "Tümünü Tara" if kind == "Tümünü Tara" else f"{phase} taraması",
                "currency": cfg.get("currency") if phase == "VWAP" else None,
                "scan_time": now_text(),
                "checkpoint": end,
            }
            # Önce sonucu kaydet, sonra cursor'u ilerlet. Çökme anında aynı blok
            # tekrar işlenirse _merge_rows duplicate'i temizler.
            worker_write_results(job_id, payload)

            phase_cursor = end
            overall = (phase_idx + (phase_cursor / len(symbols) if symbols else 1.0)) / len(phases)
            s = worker_read_state(job_id)
            s.update({
                "pid": os.getpid(),
                "status": "running",
                "phase_index": phase_idx,
                "phase_name": phase,
                "cursor": phase_cursor,
                "progress": max(0.0, min(1.0, overall)),
                "detail": f"{phase}: {phase_cursor}/{len(symbols)} tamamlandı. Checkpoint kaydedildi.",
                "current_symbol": "",
                "done": phase_cursor,
                "total": len(symbols),
                "revision": int(s.get("revision") or 0) + 1,
            })
            worker_write_state(job_id, s)

        # Faz tamamlandı; bir sonraki faz için cursor sıfırla.
        cursor = 0
        s = worker_read_state(job_id)
        s.update({
            "phase_index": phase_idx + 1,
            "phase_name": phases[phase_idx + 1] if phase_idx + 1 < len(phases) else phase,
            "cursor": 0,
            "revision": int(s.get("revision") or 0) + 1,
        })
        worker_write_state(job_id, s)

    s = worker_read_state(job_id)
    s.update({
        "pid": os.getpid(),
        "status": "completed",
        "progress": 1.0,
        "detail": "Tarama tamamlandı.",
        "current_symbol": "",
        "done": len(symbols),
        "total": len(symbols),
        "finished_at": now_text(),
        "revision": int(s.get("revision") or 0) + 1,
    })
    worker_write_state(job_id, s)


if __name__ == "__main__":
    job_id = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        run(job_id)
    except Exception as exc:
        try:
            s = worker_read_state(job_id)
            s.update({
                "pid": os.getpid(),
                "status": "failed",
                "detail": "Tarama worker hata ile durdu.",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": now_text(),
                "revision": int(s.get("revision") or 0) + 1,
            })
            worker_write_state(job_id, s)
        except Exception:
            pass
        traceback.print_exc()
        raise
