# -*- coding: utf-8 -*-
"""BIST VWAP Tarayıcı — sade, mobil uyumlu Streamlit arayüzü."""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

from vwap_core import (
    ALTERNATION_MIN_CHAIN,
    ALTERNATION_SCAN_PERIOD_LABELS,
    ALTERNATION_SCAN_PERIOD_OPTIONS,
    CURRENCY_OPTIONS,
    DEFAULT_SYMBOLS,
    PERIOD_LABELS,
    PERIOD_OPTIONS,
    TRENDLINE_LOOKBACK_BARS,
    TRENDLINE_MIN_SPAN_BARS,
    TRENDLINE_MIN_TOUCHES,
    TRENDLINE_PIVOT_WINDOW,
    TRENDLINE_SCAN_PERIOD_LABELS,
    TRENDLINE_SCAN_PERIOD_OPTIONS,
    TRENDLINE_TOUCH_TOLERANCE_PCT,
    TRENDLINE_VOLUME_FACTOR,
    TRIANGLE_LOOKBACK_BARS,
    TRIANGLE_MAX_APEX_BARS_AHEAD,
    TRIANGLE_MAX_SQUEEZE_PCT,
    TRIANGLE_MIN_APEX_BARS_AHEAD,
    TRIANGLE_MIN_SPAN_BARS,
    TRIANGLE_PIVOT_WINDOW,
    TRIANGLE_SCAN_PERIOD_LABELS,
    TRIANGLE_SCAN_PERIOD_OPTIONS,
    fetch_and_scan_alternation_only,
    fetch_and_scan_trendline_only,
    fetch_and_scan_triangle_only,
    normalize_symbol_list,
    scan_alternation_symbols_parallel,
    scan_symbols_parallel,
    scan_trendline_symbols_parallel,
    scan_triangle_symbols_parallel,
)
from chart_helpers import (
    render_alternation_chart,
    render_triangle_chart,
    render_trendline_chart,
    render_vwap_chart,
)
from scan_jobs import get_scan_job_manager

# Yukarıdaki koşullu import ifadesi yalnız eski sabit adıyla uyumluluk için yazılmıştır;
# Python import listesinde koşul kullanılamaz. Bu satır dosya oluşturulurken aşağıda temizlenir.

st.set_page_config(
    page_title="BIST Teknik Tarayıcı",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# -----------------------------------------------------------------------------
# Görünüm — tek açık tema, mobilde tüm sütunlar otomatik alt alta.
# -----------------------------------------------------------------------------
st.markdown(
    """
<style>
:root { color-scheme: light; }
[data-testid="stAppViewContainer"] { background: #f4f7fb; color: #172033; }
[data-testid="stHeader"] { display:none !important; }
[data-testid="stToolbar"] { display:none !important; }
[data-testid="stDecoration"] { display:none !important; }
.block-container { max-width: 1450px; padding-top: 1.25rem; padding-bottom: 2.5rem; }

h1, h2, h3 { color: #172033; letter-spacing: -.02em; }
.small-muted { color:#64748b; font-size:.90rem; }
.hero {
    background: linear-gradient(135deg,#ffffff 0%,#f0fdfa 100%);
    border:1px solid #dbe6ee; border-radius:18px; padding:18px 20px;
    margin-bottom:12px; box-shadow:0 2px 8px rgba(15,23,42,.04);
}
.scan-card {
    background:#fff; border:1px solid #dbe6ee; border-radius:16px;
    padding:14px 16px; min-height:122px; box-shadow:0 1px 4px rgba(15,23,42,.035);
}
.result-card {
    background:#fff; border:1px solid #dbe6ee; border-radius:14px;
    padding:10px 12px; margin-bottom:8px;
}
[data-testid="stMetric"] {
    background:#fff; border:1px solid #dbe6ee; border-radius:14px;
    padding:.65rem .78rem; box-shadow:0 1px 4px rgba(15,23,42,.035);
}
[data-testid="stExpander"] {
    background:#fff; border:1px solid #dbe6ee !important; border-radius:14px !important;
}
.stButton > button, .stDownloadButton > button {
    border-radius:11px; min-height:2.7rem; font-weight:650;
}
button[kind="primary"] { box-shadow:0 2px 7px rgba(15,118,110,.18); }
[data-testid="stDataFrame"] { background:#fff; border-radius:12px; overflow:hidden; }
hr { border-color:#dbe6ee !important; }

/* Üst navigasyon: Streamlit üst çubuğundan bağımsız, görünür bir menü alanı. */
.nav-shell {
    background:#ffffff; border:1px solid #dbe6ee; border-radius:16px;
    padding:10px 12px 4px 12px; margin:0 0 14px 0;
    box-shadow:0 2px 10px rgba(15,23,42,.06);
}
.nav-label {
    color:#64748b; font-size:.76rem; font-weight:800; letter-spacing:.08em;
    text-transform:uppercase; margin:0 0 6px 2px;
}
.nav-spacer { height:4px; }
[data-testid="stPlotlyChart"] { width:100% !important; max-width:100% !important; overflow:hidden !important; }
[data-testid="stPlotlyChart"] > div { width:100% !important; max-width:100% !important; }

@media (max-width: 760px) {
    .block-container { padding-left:.45rem; padding-right:.45rem; padding-top:.75rem; }
    h1 { font-size:1.45rem !important; }
    h2 { font-size:1.25rem !important; }
    h3 { font-size:1.08rem !important; }
    [data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:.35rem !important; }
    [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
        min-width:100% !important; width:100% !important; flex:1 1 100% !important;
    }
    [data-testid="stMetricValue"] { font-size:1.15rem !important; }
    .hero { padding:13px 13px; border-radius:14px; }
    .scan-card { min-height:auto; }
    .nav-shell { padding:9px 9px 3px 9px; margin-bottom:10px; }
    .nav-label { font-size:.70rem; margin-bottom:4px; }
    .stButton > button, .stDownloadButton > button { width:100% !important; min-height:2.9rem !important; }
    [data-testid="stPlotlyChart"] { min-height:640px !important; touch-action:none !important; overscroll-behavior:contain !important; -webkit-user-select:none !important; user-select:none !important; }
    [data-testid="stPlotlyChart"] .js-plotly-plot,
    [data-testid="stPlotlyChart"] .plot-container,
    [data-testid="stPlotlyChart"] .svg-container { touch-action:none !important; overscroll-behavior:contain !important; }
    [data-testid="stPlotlyChart"] .modebar { opacity:1 !important; }
    [data-testid="stPlotlyChart"] .modebar-btn { min-width:38px !important; min-height:38px !important; padding:8px !important; }
    div[role="radiogroup"] { gap:.25rem !important; flex-wrap:wrap !important; }
}
@media (max-width: 430px) {
    .block-container { padding-left:.30rem; padding-right:.30rem; }
    [data-testid="stPlotlyChart"] { min-height:610px !important; }
}
</style>
""",
    unsafe_allow_html=True,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(BASE_DIR, ".vwap_ayarlar.json")
BIST_LIST_PATH = os.path.join(BASE_DIR, "bist_list.txt")

CURRENCY_LABELS = {
    "TRY": "TL",
    "USD": "USD",
    "EUR": "EUR",
}

DEFAULT_SETTINGS = {
    "period": "weekly",
    "currency": "TRY",
    "lookback": 3,
    "max_workers": 20,
    "use_cache": True,
    "sideways_enabled": False,
    "sideways_method": "range",
    "sideways_months_list": [3, 6, 12],
    "sideways_min_windows": None,
    "sideways_range_pct": 15.0,
    "sideways_atr_pct": 5.0,
    "drawdown_enabled": False,
    "drawdown_min_pct": 60.0,
    "son_semboller_text": "",
    "alt_scan_period": "monthly",
    "alt_scan_min_chain": 3,
    "alt_scan_min_score": 50,
    "tl_scan_period": "1h",
    "tl_scan_pivot_window": 3,
    "tl_scan_min_span_bars": 30,
    "tl_scan_lookback_bars": 200,
    "tl_scan_breakout_lookback": 3,
    "tl_scan_touch_tolerance_pct": 1.5,
    "tl_scan_min_touches": 3,
    "tl_scan_require_volume": True,
    "tl_scan_volume_factor": 1.5,
    "tri_scan_period": "4h",
    "tri_scan_pivot_window": 3,
    "tri_scan_min_span_bars": 28,
    "tri_scan_lookback_bars": 200,
    "tri_scan_min_apex_bars_ahead": 1,
    "tri_scan_max_apex_bars_ahead": 40,
    "tri_scan_max_squeeze_pct": 50.0,
}


def load_settings():
    cfg = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(SETTINGS_PATH):
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                cfg.update(data)
    except Exception:
        pass
    return cfg


def save_partial_settings(updates):
    cfg = load_settings()
    cfg.update(updates)
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def read_bist_list_text():
    try:
        with open(BIST_LIST_PATH, "r", encoding="utf-8-sig") as f:
            text = f.read().strip()
        if text:
            return text
    except Exception:
        pass
    return "\n".join(DEFAULT_SYMBOLS)


def symbol_text_from_settings(cfg):
    text = str(cfg.get("son_semboller_text") or "").strip()
    return text or read_bist_list_text()


def q_score(r):
    try:
        return float(((r or {}).get("quality") or {}).get("score") or 0.0)
    except Exception:
        return 0.0


def q_grade(r):
    q = (r or {}).get("quality") or {}
    grade = q.get("grade", "—")
    label = q.get("label", "—")
    return f"{grade} · {label}"


def q_reasons(r, limit=3):
    return " · ".join((((r or {}).get("quality") or {}).get("reasons") or [])[:limit]) or "—"


def ensure_result_store():
    st.session_state.setdefault("_result_sets", {})
    st.session_state.setdefault("_result_meta", {})


def store_result_set(name, rows, *, total, period, errors=None, source="Tarama", currency=None):
    ensure_result_store()
    st.session_state._result_sets[name] = list(rows or [])
    st.session_state._result_meta[name] = {
        "total": int(total or 0),
        "period": period,
        "errors": list(errors or []),
        "source": source,
        "currency": currency,
        "scan_time": datetime.now().strftime("%d.%m.%Y %H:%M"),
    }


def set_page(page, scan_type=None, result_focus=None):
    st.session_state["_app_page"] = page
    if scan_type:
        st.session_state["_scan_type"] = scan_type
    if result_focus:
        st.session_state["_results_focus"] = result_focus
    st.rerun()



# -----------------------------------------------------------------------------
# Mobil / bağlantı kopmasına dayanıklı arka plan taraması
# -----------------------------------------------------------------------------
def _job_query_id():
    try:
        value = st.query_params.get("job")
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else None
        return str(value).strip() if value else None
    except Exception:
        return None


def _attach_job(job_id):
    if not job_id:
        return
    st.session_state["_active_job_id"] = str(job_id)
    try:
        st.query_params["job"] = str(job_id)
    except Exception:
        pass


def _sync_job_results(snapshot):
    if not snapshot:
        return False
    ensure_result_store()
    changed = False
    for name, rows in (snapshot.get("result_sets") or {}).items():
        if st.session_state._result_sets.get(name) is not rows:
            st.session_state._result_sets[name] = rows
            changed = True
    for name, meta in (snapshot.get("result_meta") or {}).items():
        st.session_state._result_meta[name] = meta
        changed = True
    st.session_state["_synced_job_revision"] = int(snapshot.get("revision") or 0)
    return changed


def _resolve_job_snapshot(auto_attach_running=True):
    manager = get_scan_job_manager()
    job_id = _job_query_id() or st.session_state.get("_active_job_id")
    snap = manager.snapshot(job_id) if job_id else None
    if not snap and auto_attach_running:
        # Mobil tarayıcı URL query parametresini kaybetmişse sunucuda hâlâ devam
        # eden tek taramaya yeniden bağlan. Bu uygulama tek tarama işini aynı
        # anda çalıştırdığı için güvenli ve kullanıcı dostu bir geri kazanımdır.
        snap = manager.active_snapshot()
        if snap:
            _attach_job(snap.get("id"))
    if snap:
        _attach_job(snap.get("id"))
        revision = int(snap.get("revision") or 0)
        if revision != int(st.session_state.get("_synced_job_revision") or -1):
            _sync_job_results(snap)
    return snap


def _clear_results_for_job(kind):
    ensure_result_store()
    targets = ["VWAP", "Üçgen", "Düşen Trend", "Alternasyon"] if kind == "Tümünü Tara" else [kind]
    for name in targets:
        st.session_state._result_sets.pop(name, None)
        st.session_state._result_meta.pop(name, None)


def launch_background_scan(kind, symbols, cfg, result_focus=None):
    manager = get_scan_job_manager()
    _clear_results_for_job(kind)
    job_id, started = manager.start(kind, list(symbols), dict(cfg))
    _attach_job(job_id)
    st.session_state["_synced_job_revision"] = -1
    st.session_state["_app_page"] = "Sonuçlar"
    st.session_state["_results_focus"] = result_focus or ("Özet" if kind == "Tümünü Tara" else kind)
    if not started:
        st.session_state["_job_start_notice"] = "Sunucuda zaten devam eden bir tarama vardı; ona yeniden bağlandım."
    st.rerun()


@st.fragment(run_every=2.0)
def render_live_scan_status():
    snap = _resolve_job_snapshot(auto_attach_running=True)
    if not snap:
        return
    status = snap.get("status")
    progress = float(snap.get("progress") or 0.0)
    kind = snap.get("kind") or "Tarama"
    detail = snap.get("detail") or ""

    if status in {"queued", "running"}:
        st.info(f"🛰️ **{kind} ayrı worker prosesinde devam ediyor.** Telefon ekranı kapansa bile tarama Streamlit oturumuna bağlı değildir; geri geldiğinde kayıtlı işe yeniden bağlanır.")
        st.progress(max(0.0, min(1.0, progress)), text=detail or f"{kind} sürüyor...")
        st.caption(f"İş no: {snap.get('id')} · Başlangıç: {snap.get('started_at') or 'hazırlanıyor'}")
        return

    revision = int(snap.get("revision") or 0)
    seen_key = f"{snap.get('id')}:{revision}:{status}"
    if st.session_state.get("_job_terminal_seen") != seen_key:
        _sync_job_results(snap)
        st.session_state["_job_terminal_seen"] = seen_key
        # Tam sayfayı bir kez yenileyerek sonuç tablolarının da yeni veriyi
        # hemen görmesini sağla. Sonraki fragment turlarında tekrar etmez.
        st.rerun()

    if status == "completed":
        total_found = sum(len(v or []) for v in (snap.get("result_sets") or {}).values())
        st.success(f"✅ **{kind} tamamlandı.** Toplam {total_found} eşleşme bulundu. Sonuçlar aşağıda.")
    elif status == "failed":
        st.error(f"❌ **{kind} taraması durdu:** {snap.get('error') or 'Bilinmeyen hata'}")


# -----------------------------------------------------------------------------
# Grafik sayfası
# -----------------------------------------------------------------------------
def chart_payload_complete(kind, result):
    required = {
        "vwap": ("df", "chain"),
        "alternation": ("df", "start_idx", "end_idx"),
        "trendline": ("df", "line", "cross_idx"),
        "triangle": ("df", "upper", "lower", "apex_x", "apex_y"),
    }.get(kind, ())
    return isinstance(result, dict) and bool(required) and all(result.get(k) is not None for k in required)


def repair_chart_payload(kind, sym, result):
    if chart_payload_complete(kind, result):
        return result
    if kind == "vwap":
        return result

    cfg = load_settings()
    period = (result or {}).get("period") or {
        "alternation": cfg.get("alt_scan_period", "monthly"),
        "trendline": cfg.get("tl_scan_period", "1h"),
        "triangle": cfg.get("tri_scan_period", "4h"),
    }.get(kind)
    yf_symbol = str(sym).upper().strip()
    if not yf_symbol.endswith(".IS"):
        yf_symbol += ".IS"

    try:
        if kind == "alternation":
            out = fetch_and_scan_alternation_only(
                yf_symbol, period, use_cache=bool(cfg.get("use_cache", True)),
                min_chain=int(cfg.get("alt_scan_min_chain", 3)),
                min_score=cfg.get("alt_scan_min_score"),
            )
        elif kind == "trendline":
            out = fetch_and_scan_trendline_only(
                yf_symbol, period, use_cache=bool(cfg.get("use_cache", True)),
                pivot_window=int(cfg.get("tl_scan_pivot_window", 3)),
                min_span_bars=int(cfg.get("tl_scan_min_span_bars", 30)),
                lookback_bars=int(cfg.get("tl_scan_lookback_bars", 200)),
                breakout_lookback=int(cfg.get("tl_scan_breakout_lookback", 3)),
                touch_tolerance_pct=float(cfg.get("tl_scan_touch_tolerance_pct", 1.5)),
                require_volume=bool(cfg.get("tl_scan_require_volume", True)),
                volume_factor=float(cfg.get("tl_scan_volume_factor", 1.5)),
                min_touches=int(cfg.get("tl_scan_min_touches", 3)),
            )
        elif kind == "triangle":
            out = fetch_and_scan_triangle_only(
                yf_symbol, period, use_cache=bool(cfg.get("use_cache", True)),
                pivot_window=int(cfg.get("tri_scan_pivot_window", 3)),
                min_span_bars=int(cfg.get("tri_scan_min_span_bars", 28)),
                lookback_bars=int(cfg.get("tri_scan_lookback_bars", 200)),
                min_apex_bars_ahead=int(cfg.get("tri_scan_min_apex_bars_ahead", 1)),
                max_apex_bars_ahead=int(cfg.get("tri_scan_max_apex_bars_ahead", 40)),
                max_squeeze_pct=float(cfg.get("tri_scan_max_squeeze_pct", 50.0)),
            )
        else:
            return result
    except Exception as exc:
        repaired = dict(result or {})
        repaired["_chart_repair_error"] = f"Grafik verisi hazırlanamadı: {exc}"
        return repaired

    if out and out.get("matched") and isinstance(out.get("result"), dict):
        rebuilt = out["result"]
        if isinstance(result, dict) and result.get("quality") is not None:
            rebuilt["quality"] = result.get("quality")
        return rebuilt

    repaired = dict(result or {})
    repaired["_chart_repair_error"] = "Bu sonuç güncel veride aynı formasyon şartını artık karşılamıyor. Taramayı yeniden çalıştırın."
    return repaired


def render_quality_panel(result, *, compact=False):
    q = (result or {}).get("quality") or {}
    if not q:
        return
    score = float(q.get("score") or 0)
    rsi = "—" if q.get("rsi14") is None else f"{float(q['rsi14']):.1f}"
    room = "—" if q.get("resistance_room_pct") is None else f"%{float(q['resistance_room_pct']):.1f}"
    if compact:
        st.info(f"Yükseliş Puanı **{score:.1f}/100** · **{q_grade(result)}** · RSI **{rsi}** · Dirence alan **{room}**")
        return
    st.markdown("### Yukarı Yön Kalitesi")
    c1, c2 = st.columns(2)
    c1.metric("Puan", f"{score:.1f}/100")
    c2.metric("Sınıf", q_grade(result))
    c3, c4 = st.columns(2)
    c3.metric("RSI 14", rsi)
    c4.metric("Dirence Alan", room)
    if q.get("reasons"):
        st.success("Güçlü teyitler: " + " · ".join(q["reasons"][:5]))
    if q.get("warnings"):
        st.caption("⚠️ " + " · ".join(q["warnings"][:5]))
    comps = q.get("components") or {}
    if comps:
        st.dataframe(pd.DataFrame([{"Bileşen": k, "Puan": v} for k, v in comps.items()]), width="stretch", hide_index=True)
    st.caption("Bu puan garanti getiri değildir; teknik sinyalleri aynı ölçekte sıralamak için kullanılır.")


def open_chart(kind, sym, result):
    st.session_state["_chart_page"] = {"kind": kind, "symbol": sym, "result": result}
    st.rerun()


def render_chart_page_if_requested():
    view = st.session_state.get("_chart_page")
    if not view:
        return False

    if st.button("← Sonuçlar", type="secondary", width="content"):
        st.session_state.pop("_chart_page", None)
        st.session_state["_app_page"] = "Sonuçlar"
        st.rerun()

    kind = view.get("kind")
    sym = view.get("symbol", "")
    result = repair_chart_payload(kind, sym, view.get("result"))
    st.session_state["_chart_page"]["result"] = result
    title_map = {
        "vwap": "VWAP",
        "triangle": "Üçgen",
        "trendline": "Düşen Trend Kırılımı",
        "alternation": "Alternasyon",
    }
    # TradingView benzeri sade grafik ekranı: üstte ekstra puan kartı/başlık yok.

    if not chart_payload_complete(kind, result):
        st.error((result or {}).get("_chart_repair_error") or "Grafik için gerekli veri bulunamadı. İlgili taramayı yeniden çalıştırın.")
        return True
    try:
        if kind == "vwap":
            render_vwap_chart(sym, result, key=f"chart_vwap_{sym}")
        elif kind == "triangle":
            render_triangle_chart(sym, result, key=f"chart_tri_{sym}")
        elif kind == "trendline":
            render_trendline_chart(sym, result, key=f"chart_tl_{sym}")
        elif kind == "alternation":
            render_alternation_chart(sym, result, key=f"chart_alt_{sym}")
    except Exception as exc:
        st.error(f"{sym} grafiği çizilemedi: {exc}")
        st.exception(exc)
        return True

    with st.expander("Yükseliş puanı ve teyit ayrıntıları", expanded=False):
        render_quality_panel(result, compact=False)
    return True


# -----------------------------------------------------------------------------
# Sonuç tablo yardımcıları
# -----------------------------------------------------------------------------
def result_rows(view, items):
    rows = []
    for r in items:
        base = {
            "Sembol": r.get("symbol", "—"),
            "Yükseliş Puanı": round(q_score(r), 1),
            "Kalite": q_grade(r),
            "Teyitler": q_reasons(r),
        }
        if view == "VWAP":
            base.update({
                "Seviye": f"{r.get('level', '—')}. VWAP",
                "Kırılma": r.get("cross_date", "—"),
                "Bar Önce": r.get("bars_ago", "—"),
                "Son Kapanış": r.get("last_close", "—"),
                "VWAP": r.get("last_vwap", "—"),
            })
        elif view == "Üçgen":
            base.update({
                "Desen": r.get("pattern_type", "—"),
                "Apex Bar": r.get("apex_bars_ahead", "—"),
                "Sıkışma %": r.get("squeeze_pct", "—"),
                "Son Kapanış": r.get("last_close", "—"),
            })
        elif view == "Düşen Trend":
            base.update({
                "Temas": r.get("touches", "—"),
                "Kırılma": r.get("cross_date", "—"),
                "Bar Önce": r.get("bars_ago", "—"),
                "Son Kapanış": r.get("last_close", "—"),
            })
        elif view == "Alternasyon":
            base.update({
                "Zincir": r.get("chain_length", "—"),
                "Düzenlilik": r.get("score", "—"),
                "Başlangıç": r.get("start_date", "—"),
                "Bitiş": r.get("end_date", "—"),
            })
        rows.append(base)
    return rows


def chart_kind_for(view):
    return {
        "VWAP": "vwap",
        "Üçgen": "triangle",
        "Düşen Trend": "trendline",
        "Alternasyon": "alternation",
    }[view]


def render_results_page():
    ensure_result_store()
    sets = st.session_state._result_sets
    meta = st.session_state._result_meta

    st.title("Sonuçlar")
    st.caption("Bütün taramaların sonuçları tek yerde. Tablo satırına tıklayınca ilgili hisse grafiği açılır.")

    names = ["VWAP", "Üçgen", "Düşen Trend", "Alternasyon"]
    active_job = _resolve_job_snapshot(auto_attach_running=True)
    if not any(sets.get(n) is not None for n in names):
        if active_job and active_job.get("status") in {"queued", "running"}:
            st.info("Tarama ayrı worker prosesinde devam ediyor. Ekran kapansa bile iş Streamlit oturumundan bağımsızdır; geri geldiğinde checkpoint durumundan yeniden bağlanır.")
        else:
            st.info("Henüz tarama sonucu yok. Tarama sayfasından bir tarama başlatın.")
            if st.button("🔎 Tarama sayfasına git", type="primary", width="stretch"):
                set_page("Tarama")
        return

    cols = st.columns(4)
    for col, name in zip(cols, names):
        rows = list(sets.get(name) or [])
        col.metric(name, len(rows), delta=f"{sum(q_score(r) >= 70 for r in rows)} adet 70+")

    focus = st.session_state.get("_results_focus", "Özet")
    choices = ["Özet"] + names
    index = choices.index(focus) if focus in choices else 0
    view = st.selectbox("Hangi sonucu görmek istiyorsun?", choices, index=index, key="results_view_select")
    st.session_state["_results_focus"] = view

    if view == "Özet":
        combined = []
        for name in names:
            for r in list(sets.get(name) or []):
                combined.append((q_score(r), name, r))
        combined.sort(key=lambda x: x[0], reverse=True)

        st.markdown("### Bugünün en güçlü teknik adayları")
        if not combined:
            st.warning("Kayıtlı sonuçların içinde eşleşme yok.")
            return

        overview_items = combined[:50]
        overview_df = pd.DataFrame([
            {
                "Sembol": str(r.get("symbol", "—")),
                "Tarama": (f"VWAP · {r.get('level', '—')}. VWAP" if name == "VWAP" else name),
                "Yükseliş Puanı": round(q_score(r), 1),
                "Kalite": q_grade(r),
                "Teyitler": q_reasons(r),
            }
            for _, name, r in overview_items
        ])
        st.caption("📈 Grafiği açmak için tablodaki hisse satırına bir kez tıkla veya telefonda dokun.")
        table_nonce = int(st.session_state.get("_result_table_nonce", 0))
        overview_event = st.dataframe(
            overview_df,
            width="stretch",
            hide_index=True,
            height=min(620, 80 + 35 * len(overview_df)),
            on_select="rerun",
            selection_mode="single-row",
            key=f"result_table_overview_{table_nonce}",
        )
        selected_rows = []
        try:
            selected_rows = list(overview_event.selection.rows)
        except Exception:
            try:
                selected_rows = list((overview_event or {}).get("selection", {}).get("rows", []))
            except Exception:
                selected_rows = []
        if selected_rows:
            row_idx = int(selected_rows[0])
            if 0 <= row_idx < len(overview_items):
                _, name, selected = overview_items[row_idx]
                sym = str(selected.get("symbol", "—"))
                st.session_state["_result_table_nonce"] = table_nonce + 1
                st.session_state["_results_focus"] = "Özet"
                open_chart(chart_kind_for(name), sym, selected)
        return

    items = list(sets.get(view) or [])
    m = meta.get(view) or {}
    errors = list(m.get("errors") or [])

    # VWAP zinciri sonucu kullanıcı için doğrudan 1./2./3. VWAP olarak
    # ayrılır. Bu yalnız sonuç görünüm filtresidir; tarama mantığını ve
    # zincir hesabını değiştirmez.
    if view == "VWAP":
        level_counts = {
            level: sum(1 for r in items if int(r.get("level") or 0) == level)
            for level in (1, 2, 3)
        }
        level_options = [
            f"Tümü ({len(items)})",
            f"1. VWAP ({level_counts[1]})",
            f"2. VWAP ({level_counts[2]})",
            f"3. VWAP ({level_counts[3]})",
        ]
        level_choice = st.radio(
            "VWAP seviyesi", level_options, horizontal=True,
            key="vwap_level_filter",
        )
        if not level_choice.startswith("Tümü"):
            selected_level = int(level_choice.split(".", 1)[0])
            items = [r for r in items if int(r.get("level") or 0) == selected_level]

    st.caption(
        f"Periyot: **{m.get('period') or '—'}** · Taranan: **{m.get('total') or '—'}** · "
        f"Tarama: **{m.get('scan_time') or '—'}** · Veri hatası: **{len(errors)}**"
    )

    f1, f2 = st.columns(2)
    with f1:
        min_quality_label = st.selectbox("En düşük kalite", ["Tümü", "60+", "70+", "80+"], index=0, key=f"quality_{view}")
    with f2:
        search = st.text_input("Hisse ara", placeholder="Örn: THYAO", key=f"search_{view}")
    min_score = {"Tümü": 0, "60+": 60, "70+": 70, "80+": 80}[min_quality_label]
    filtered = [r for r in items if q_score(r) >= min_score]
    if search.strip():
        needle = search.strip().upper()
        filtered = [r for r in filtered if needle in str(r.get("symbol", "")).upper()]
    filtered.sort(key=q_score, reverse=True)

    if not filtered:
        st.warning("Bu filtreye uyan sonuç yok.")
        return

    st.markdown("### Tablo görünümü")
    st.caption("📈 Grafiği açmak için tablodaki hisse satırına bir kez tıkla veya telefonda dokun.")
    df = pd.DataFrame(result_rows(view, filtered))

    # Streamlit'in yerleşik satır seçimini kullanıyoruz. Böylece ayrı bir
    # "hisse seç / grafiği aç" alanına ihtiyaç kalmaz: tablonun kendisi
    # grafik navigasyonudur. Nonce, grafikten geri dönüldüğünde eski seçimin
    # yeniden tetiklenmesini önler.
    table_nonce = int(st.session_state.get("_result_table_nonce", 0))
    table_event = st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        height=min(620, 80 + 35 * len(df)),
        on_select="rerun",
        selection_mode="single-row",
        key=f"result_table_{view}_{table_nonce}",
    )

    selected_rows = []
    try:
        selected_rows = list(table_event.selection.rows)
    except Exception:
        try:
            selected_rows = list((table_event or {}).get("selection", {}).get("rows", []))
        except Exception:
            selected_rows = []

    if selected_rows:
        row_idx = int(selected_rows[0])
        if 0 <= row_idx < len(filtered):
            selected = filtered[row_idx]
            sym = str(selected.get("symbol", "—"))
            # Bir sonraki sonuç ekranında tablo yeni anahtarla oluşturulsun;
            # böylece geri dönünce aynı satır otomatik tekrar açılmaz.
            st.session_state["_result_table_nonce"] = table_nonce + 1
            st.session_state["_results_focus"] = view
            open_chart(chart_kind_for(view), sym, selected)

    st.download_button(
        "⬇️ Sonuçları CSV indir",
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{view.lower().replace(' ', '_')}_sonuclar.csv",
        mime="text/csv",
        width="stretch",
    )
    if errors:
        with st.expander(f"Veri hataları ({len(errors)})"):
            for sym, err in errors[:50]:
                st.code(f"{sym}: {err}")


# -----------------------------------------------------------------------------
# Tarama çalıştırıcıları
# -----------------------------------------------------------------------------
def make_progress_callback(progress, label, start=0.0, span=1.0):
    def cb(done, total, sym):
        ratio = (done / total) if total else 1.0
        progress.progress(min(1.0, start + span * ratio), text=f"{label}: {done}/{total} · {sym}")
    return cb


def run_vwap_scan(symbols, cfg, progress=None, start=0.0, span=1.0, source="VWAP taraması"):
    errors = []
    callback = make_progress_callback(progress, "VWAP taranıyor", start, span) if progress is not None else None
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
    store_result_set(
        "VWAP", results, total=len(symbols), period=PERIOD_LABELS.get(cfg.get("period"), cfg.get("period")),
        errors=errors, source=source, currency=cfg.get("currency"),
    )
    return results, errors


def run_triangle_scan(symbols, cfg, progress=None, start=0.0, span=1.0, source="Üçgen taraması"):
    errors = []
    callback = make_progress_callback(progress, "Üçgen taranıyor", start, span) if progress is not None else None
    results = scan_triangle_symbols_parallel(
        symbols, cfg.get("tri_scan_period", "4h"),
        max_workers=int(cfg.get("max_workers", 20)), use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback, errors_out=errors,
        pivot_window=int(cfg.get("tri_scan_pivot_window", 3)),
        min_span_bars=int(cfg.get("tri_scan_min_span_bars", 28)),
        lookback_bars=int(cfg.get("tri_scan_lookback_bars", 200)),
        min_apex_bars_ahead=int(cfg.get("tri_scan_min_apex_bars_ahead", 1)),
        max_apex_bars_ahead=int(cfg.get("tri_scan_max_apex_bars_ahead", 40)),
        max_squeeze_pct=float(cfg.get("tri_scan_max_squeeze_pct", 50.0)),
    )
    store_result_set(
        "Üçgen", results, total=len(symbols),
        period=TRIANGLE_SCAN_PERIOD_LABELS.get(cfg.get("tri_scan_period"), cfg.get("tri_scan_period")),
        errors=errors, source=source,
    )
    return results, errors


def run_trend_scan(symbols, cfg, progress=None, start=0.0, span=1.0, source="Düşen trend taraması"):
    errors = []
    callback = make_progress_callback(progress, "Düşen trend taranıyor", start, span) if progress is not None else None
    results = scan_trendline_symbols_parallel(
        symbols, cfg.get("tl_scan_period", "1h"),
        max_workers=int(cfg.get("max_workers", 20)), use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback, errors_out=errors,
        pivot_window=int(cfg.get("tl_scan_pivot_window", 3)),
        min_span_bars=int(cfg.get("tl_scan_min_span_bars", 30)),
        lookback_bars=int(cfg.get("tl_scan_lookback_bars", 200)),
        breakout_lookback=int(cfg.get("tl_scan_breakout_lookback", 3)),
        touch_tolerance_pct=float(cfg.get("tl_scan_touch_tolerance_pct", 1.5)),
        require_volume=bool(cfg.get("tl_scan_require_volume", True)),
        volume_factor=float(cfg.get("tl_scan_volume_factor", 1.5)),
        min_touches=int(cfg.get("tl_scan_min_touches", 3)),
    )
    store_result_set(
        "Düşen Trend", results, total=len(symbols),
        period=TRENDLINE_SCAN_PERIOD_LABELS.get(cfg.get("tl_scan_period"), cfg.get("tl_scan_period")),
        errors=errors, source=source,
    )
    return results, errors


def run_alternation_scan(symbols, cfg, progress=None, start=0.0, span=1.0, source="Alternasyon taraması"):
    errors = []
    callback = make_progress_callback(progress, "Alternasyon taranıyor", start, span) if progress is not None else None
    min_score = cfg.get("alt_scan_min_score")
    if min_score in ("", None):
        min_score = None
    else:
        min_score = float(min_score)
    results = scan_alternation_symbols_parallel(
        symbols, cfg.get("alt_scan_period", "monthly"),
        max_workers=int(cfg.get("max_workers", 20)), use_cache=bool(cfg.get("use_cache", True)),
        progress_callback=callback, errors_out=errors,
        min_chain=int(cfg.get("alt_scan_min_chain", 3)), min_score=min_score,
    )
    store_result_set(
        "Alternasyon", results, total=len(symbols),
        period=ALTERNATION_SCAN_PERIOD_LABELS.get(cfg.get("alt_scan_period"), cfg.get("alt_scan_period")),
        errors=errors, source=source,
    )
    return results, errors


# -----------------------------------------------------------------------------
# Ana sayfa
# -----------------------------------------------------------------------------
def render_home():
    ensure_result_store()
    sets = st.session_state._result_sets

    st.markdown(
        """
<div class="hero">
  <h2 style="margin:0 0 6px 0;">BIST Teknik Tarayıcı</h2>
  <div class="small-muted">Dört tarama, tek akış: taramayı seç → başlat → sonuçlara git → grafiği aç.</div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown("### Ne taramak istiyorsun?")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="scan-card"><b>📍 VWAP</b><br><span class="small-muted">VWAP zincirinde seçilen seviyeyi son ayarlanan bar aralığında yukarı kıran hisseler.</span></div>', unsafe_allow_html=True)
        if st.button("VWAP taramasına git", width="stretch", key="home_vwap"):
            set_page("Tarama", "VWAP")
        st.markdown('<div class="scan-card"><b>📉 Düşen Trend Kırılımı</b><br><span class="small-muted">Düşen direnç çizgisini yukarı kıran hisseler.</span></div>', unsafe_allow_html=True)
        if st.button("Düşen trend taramasına git", width="stretch", key="home_trend"):
            set_page("Tarama", "Düşen Trend")
    with c2:
        st.markdown('<div class="scan-card"><b>🔺 Üçgen</b><br><span class="small-muted">Sıkışmış ve kırılıma yaklaşmış üçgen formasyonları.</span></div>', unsafe_allow_html=True)
        if st.button("Üçgen taramasına git", width="stretch", key="home_triangle"):
            set_page("Tarama", "Üçgen")
        st.markdown('<div class="scan-card"><b>🔀 Alternasyon</b><br><span class="small-muted">Mum alternasyonu ve yukarı yön kalite teyitleri.</span></div>', unsafe_allow_html=True)
        if st.button("Alternasyon taramasına git", width="stretch", key="home_alt"):
            set_page("Tarama", "Alternasyon")

    st.divider()
    st.markdown("### Son tarama sonuçları")
    cols = st.columns(4)
    for col, name in zip(cols, ["VWAP", "Üçgen", "Düşen Trend", "Alternasyon"]):
        rows = list(sets.get(name) or [])
        col.metric(name, len(rows), delta=f"{sum(q_score(r) >= 70 for r in rows)} güçlü")

    b1, b2 = st.columns(2)
    with b1:
        if st.button("🚀 Dördünü birlikte tara", type="primary", width="stretch"):
            set_page("Tarama", "Tümünü Tara")
    with b2:
        if st.button("📊 Sonuçları aç", width="stretch"):
            set_page("Sonuçlar", result_focus="Özet")


# -----------------------------------------------------------------------------
# Tarama sayfası
# -----------------------------------------------------------------------------
def render_scan_page():
    cfg = load_settings()
    st.title("Tarama")
    st.caption("Önce tarama türünü seçin. Sadece o taramanın ayarları görünür; diğer ayarlar ekranda kalabalık yapmaz.")

    # Hisse listesi ve çalışma ayarları tek yerde, varsayılan kapalı.
    current_text = symbol_text_from_settings(cfg)
    symbols_now = normalize_symbol_list([current_text])
    with st.expander(f"📋 Hisse Listesi ve Genel Ayarlar · {len(symbols_now)} hisse", expanded=False):
        manual_text = st.text_area(
            "Taranacak hisseler",
            value=current_text,
            height=180,
            help="Satır satır veya virgülle yazabilirsiniz. .IS yazmasanız da sistem ekler.",
            key="symbol_list_text",
        )
        symbols = normalize_symbol_list([manual_text])
        st.caption(f"Aktif liste: **{len(symbols)} hisse**")
        g1, g2 = st.columns(2)
        with g1:
            max_workers = st.slider("Tarama hızı / eşzamanlı iş (1s/4s Yahoo taramalarında otomatik en fazla 6)", 4, 40, int(cfg.get("max_workers", 20)), 2, key="general_workers")
        with g2:
            use_cache = st.checkbox("Önbelleği kullan (önerilir)", value=bool(cfg.get("use_cache", True)), key="general_cache")
        def _reload_bist_list():
            st.session_state["symbol_list_text"] = read_bist_list_text()

        st.button(
            "BIST listesini dosyadan yeniden yükle",
            width="stretch",
            on_click=_reload_bist_list,
        )
    # Expander içindeki widgetlar ilk renderda da değer üretir.
    manual_text = st.session_state.get("symbol_list_text", current_text)
    symbols = normalize_symbol_list([manual_text])
    max_workers = int(st.session_state.get("general_workers", cfg.get("max_workers", 20)))
    use_cache = bool(st.session_state.get("general_cache", cfg.get("use_cache", True)))
    save_partial_settings({"son_semboller_text": manual_text, "max_workers": max_workers, "use_cache": use_cache})

    scan_choices = ["VWAP", "Üçgen", "Düşen Trend", "Alternasyon", "Tümünü Tara"]
    saved_choice = st.session_state.get("_scan_type", "VWAP")
    idx = scan_choices.index(saved_choice) if saved_choice in scan_choices else 0
    selected = st.selectbox("Ne taramak istiyorsun?", scan_choices, index=idx, key="scan_type_select")
    st.session_state["_scan_type"] = selected

    if not symbols:
        st.error("Taranacak hisse listesi boş. 'Hisse Listesi ve Genel Ayarlar' bölümünden hisse ekleyin.")
        return

    # Her taramada sadece gerekli ayarlar görünür.
    if selected == "VWAP":
        st.markdown("### 📍 VWAP Taraması")
        st.caption("İlk paketteki VWAP zincir mantığıyla son ayarlanan bar aralığındaki yukarı kırılımları bulur.")
        c1, c2, c3 = st.columns(3)
        with c1:
            period = st.selectbox(
                "Periyot", list(PERIOD_OPTIONS),
                index=list(PERIOD_OPTIONS).index(cfg.get("period", "weekly")) if cfg.get("period") in PERIOD_OPTIONS else 1,
                format_func=lambda p: PERIOD_LABELS.get(p, p), key="vwap_period",
            )
        with c2:
            currency = st.selectbox(
                "Para birimi", list(CURRENCY_OPTIONS),
                index=list(CURRENCY_OPTIONS).index(cfg.get("currency", "TRY")) if cfg.get("currency") in CURRENCY_OPTIONS else 0,
                format_func=lambda x: CURRENCY_LABELS.get(x, x), key="vwap_currency",
            )
        with c3:
            lookback = st.slider("Kırılım son kaç barda olsun?", 1, 8, int(cfg.get("lookback", 3)), key="vwap_lookback")

        with st.expander("⚙️ Gelişmiş VWAP ayarları", expanded=False):
            st.caption("Bu bölümü değiştirmek zorunda değilsiniz. Varsayılan ayarlar günlük kullanım için yeterlidir.")
            sideways_enabled = st.checkbox("Yataylık bilgisini de hesapla", value=bool(cfg.get("sideways_enabled", False)), key="vwap_sideways")
            sideways_method = cfg.get("sideways_method", "range")
            sideways_months = list(cfg.get("sideways_months_list") or [3, 6, 12])
            sideways_range = float(cfg.get("sideways_range_pct", 15.0))
            sideways_atr = float(cfg.get("sideways_atr_pct", 5.0))
            if sideways_enabled:
                a1, a2 = st.columns(2)
                with a1:
                    sideways_method = st.selectbox("Yataylık yöntemi", ["range", "atr"], index=0 if sideways_method == "range" else 1, format_func=lambda x: "Fiyat Aralığı" if x == "range" else "ATR", key="vwap_sideways_method")
                    sideways_months = st.multiselect("Vadeler (ay)", [3, 6, 12, 18, 24], default=sideways_months, key="vwap_sideways_months")
                with a2:
                    sideways_range = st.slider("Maks. fiyat aralığı %", 5.0, 50.0, sideways_range, 1.0, key="vwap_sideways_range")
                    sideways_atr = st.slider("Maks. ATR %", 1.0, 15.0, sideways_atr, .5, key="vwap_sideways_atr")
            drawdown_enabled = st.checkbox("Zirveden düşüş bilgisini de hesapla", value=bool(cfg.get("drawdown_enabled", False)), key="vwap_drawdown")
            drawdown_min = float(cfg.get("drawdown_min_pct", 60.0))
            if drawdown_enabled:
                drawdown_min = st.slider("En az zirveden düşüş %", 10.0, 90.0, drawdown_min, 5.0, key="vwap_drawdown_min")

        save_partial_settings({
            "period": period, "currency": currency, "lookback": lookback,
            "sideways_enabled": sideways_enabled, "sideways_method": sideways_method,
            "sideways_months_list": sideways_months, "sideways_range_pct": sideways_range,
            "sideways_atr_pct": sideways_atr, "drawdown_enabled": drawdown_enabled,
            "drawdown_min_pct": drawdown_min,
        })
        cfg = load_settings()
        if st.button(f"🔍 VWAP Tara · {len(symbols)} hisse", type="primary", width="stretch"):
            launch_background_scan("VWAP", symbols, cfg, result_focus="VWAP")

    elif selected == "Üçgen":
        st.markdown("### 🔺 Üçgen Taraması")
        st.caption("Sıkışmış, apex'e yaklaşmış ve kırılıma hazır üçgenleri arar.")
        tri_period = st.selectbox(
            "Periyot", list(TRIANGLE_SCAN_PERIOD_OPTIONS),
            index=list(TRIANGLE_SCAN_PERIOD_OPTIONS).index(cfg.get("tri_scan_period", "4h")) if cfg.get("tri_scan_period") in TRIANGLE_SCAN_PERIOD_OPTIONS else 1,
            format_func=lambda p: TRIANGLE_SCAN_PERIOD_LABELS.get(p, p), key="tri_period",
        )
        tri_vals = {
            "tri_scan_pivot_window": int(cfg.get("tri_scan_pivot_window", 3)),
            "tri_scan_min_span_bars": int(cfg.get("tri_scan_min_span_bars", 28)),
            "tri_scan_lookback_bars": int(cfg.get("tri_scan_lookback_bars", 200)),
            "tri_scan_min_apex_bars_ahead": int(cfg.get("tri_scan_min_apex_bars_ahead", 1)),
            "tri_scan_max_apex_bars_ahead": int(cfg.get("tri_scan_max_apex_bars_ahead", 40)),
            "tri_scan_max_squeeze_pct": float(cfg.get("tri_scan_max_squeeze_pct", 50.0)),
        }
        with st.expander("⚙️ Gelişmiş üçgen ayarları", expanded=False):
            a1, a2 = st.columns(2)
            with a1:
                tri_vals["tri_scan_pivot_window"] = st.slider("Pivot penceresi", 2, 8, tri_vals["tri_scan_pivot_window"], key="tri_pivot")
                tri_vals["tri_scan_min_span_bars"] = st.slider("Min. çizgi uzunluğu (bar)", 5, 60, tri_vals["tri_scan_min_span_bars"], key="tri_span")
                tri_vals["tri_scan_lookback_bars"] = st.slider("Geçmiş arama (bar)", 40, 500, tri_vals["tri_scan_lookback_bars"], key="tri_lookback")
            with a2:
                tri_vals["tri_scan_min_apex_bars_ahead"] = st.slider("Apex en az kaç bar sonra?", 1, 20, tri_vals["tri_scan_min_apex_bars_ahead"], key="tri_apex_min")
                tri_vals["tri_scan_max_apex_bars_ahead"] = st.slider("Apex en fazla kaç bar sonra?", 5, 100, tri_vals["tri_scan_max_apex_bars_ahead"], key="tri_apex_max")
                tri_vals["tri_scan_max_squeeze_pct"] = st.slider("Maks. sıkışma %", 10.0, 90.0, tri_vals["tri_scan_max_squeeze_pct"], 5.0, key="tri_squeeze")
        save_partial_settings({"tri_scan_period": tri_period, **tri_vals})
        cfg = load_settings()
        if st.button(f"🔍 Üçgen Tara · {len(symbols)} hisse", type="primary", width="stretch"):
            launch_background_scan("Üçgen", symbols, cfg, result_focus="Üçgen")

    elif selected == "Düşen Trend":
        st.markdown("### 📉 Düşen Trend Kırılımı")
        st.caption("Düşen direnç çizgisini yukarı kıran ve kırılımı koruyan hisseleri arar.")
        c1, c2 = st.columns(2)
        with c1:
            tl_period = st.selectbox(
                "Periyot", list(TRENDLINE_SCAN_PERIOD_OPTIONS),
                index=list(TRENDLINE_SCAN_PERIOD_OPTIONS).index(cfg.get("tl_scan_period", "1h")) if cfg.get("tl_scan_period") in TRENDLINE_SCAN_PERIOD_OPTIONS else 0,
                format_func=lambda p: TRENDLINE_SCAN_PERIOD_LABELS.get(p, p), key="tl_period",
            )
        with c2:
            tl_volume = st.checkbox("Hacim teyidi şart olsun", value=bool(cfg.get("tl_scan_require_volume", True)), key="tl_volume")
        tl_vals = {
            "tl_scan_pivot_window": int(cfg.get("tl_scan_pivot_window", 3)),
            "tl_scan_min_span_bars": int(cfg.get("tl_scan_min_span_bars", 30)),
            "tl_scan_lookback_bars": int(cfg.get("tl_scan_lookback_bars", 200)),
            "tl_scan_breakout_lookback": int(cfg.get("tl_scan_breakout_lookback", 3)),
            "tl_scan_touch_tolerance_pct": float(cfg.get("tl_scan_touch_tolerance_pct", 1.5)),
            "tl_scan_min_touches": int(cfg.get("tl_scan_min_touches", 3)),
            "tl_scan_volume_factor": float(cfg.get("tl_scan_volume_factor", 1.5)),
        }
        with st.expander("⚙️ Gelişmiş düşen trend ayarları", expanded=False):
            a1, a2 = st.columns(2)
            with a1:
                tl_vals["tl_scan_pivot_window"] = st.slider("Pivot penceresi", 2, 8, tl_vals["tl_scan_pivot_window"], key="tl_pivot")
                tl_vals["tl_scan_min_span_bars"] = st.slider("Min. çizgi uzunluğu (bar)", 5, 80, tl_vals["tl_scan_min_span_bars"], key="tl_span")
                tl_vals["tl_scan_lookback_bars"] = st.slider("Geçmiş arama (bar)", 40, 500, tl_vals["tl_scan_lookback_bars"], key="tl_lookback")
                tl_vals["tl_scan_breakout_lookback"] = st.slider("Kırılım son kaç barda?", 1, 10, tl_vals["tl_scan_breakout_lookback"], key="tl_breaklook")
            with a2:
                tl_vals["tl_scan_touch_tolerance_pct"] = st.slider("Temas toleransı %", .5, 5.0, tl_vals["tl_scan_touch_tolerance_pct"], .5, key="tl_tol")
                tl_vals["tl_scan_min_touches"] = st.slider("En az bağımsız temas", 2, 6, tl_vals["tl_scan_min_touches"], key="tl_touches")
                if tl_volume:
                    tl_vals["tl_scan_volume_factor"] = st.slider("Kırılım hacmi / 20 bar ort.", 1.0, 5.0, tl_vals["tl_scan_volume_factor"], .1, key="tl_vol_factor")
        save_partial_settings({"tl_scan_period": tl_period, "tl_scan_require_volume": tl_volume, **tl_vals})
        cfg = load_settings()
        if st.button(f"🔍 Düşen Trend Tara · {len(symbols)} hisse", type="primary", width="stretch"):
            launch_background_scan("Düşen Trend", symbols, cfg, result_focus="Düşen Trend")

    elif selected == "Alternasyon":
        st.markdown("### 🔀 Alternasyon Taraması")
        st.caption("Kesintisiz mum renk alternasyonu arar; yukarı yön kalite puanı ile güçlü adayları üstte sıralar.")
        c1, c2, c3 = st.columns(3)
        with c1:
            alt_period = st.selectbox(
                "Periyot", list(ALTERNATION_SCAN_PERIOD_OPTIONS),
                index=list(ALTERNATION_SCAN_PERIOD_OPTIONS).index(cfg.get("alt_scan_period", "monthly")) if cfg.get("alt_scan_period") in ALTERNATION_SCAN_PERIOD_OPTIONS else 4,
                format_func=lambda p: ALTERNATION_SCAN_PERIOD_LABELS.get(p, p), key="alt_period",
            )
        with c2:
            alt_chain = st.slider("En az zincir", 3, 12, int(cfg.get("alt_scan_min_chain", 3)), key="alt_chain")
        with c3:
            alt_score = st.slider("Min. düzenlilik puanı", 0, 100, int(float(cfg.get("alt_scan_min_score") or 0)), 5, key="alt_score")
        save_partial_settings({"alt_scan_period": alt_period, "alt_scan_min_chain": alt_chain, "alt_scan_min_score": None if alt_score == 0 else alt_score})
        cfg = load_settings()
        if st.button(f"🔍 Alternasyon Tara · {len(symbols)} hisse", type="primary", width="stretch"):
            launch_background_scan("Alternasyon", symbols, cfg, result_focus="Alternasyon")

    else:  # Tümünü Tara
        st.markdown("### 🚀 Tümünü Birlikte Tara")
        st.caption("Dört taramayı kendi kayıtlı periyot ve ayarlarıyla sırayla çalıştırır. Bittiğinde tek Sonuçlar ekranına geçer.")
        summary_df = pd.DataFrame([
            {"Tarama": "VWAP", "Periyot": PERIOD_LABELS.get(cfg.get("period"), cfg.get("period")), "Ana Ayar": f"{cfg.get('currency','TRY')} · son {cfg.get('lookback',3)} bar"},
            {"Tarama": "Üçgen", "Periyot": TRIANGLE_SCAN_PERIOD_LABELS.get(cfg.get("tri_scan_period"), cfg.get("tri_scan_period")), "Ana Ayar": f"Sıkışma ≤ %{cfg.get('tri_scan_max_squeeze_pct',50)}"},
            {"Tarama": "Düşen Trend", "Periyot": TRENDLINE_SCAN_PERIOD_LABELS.get(cfg.get("tl_scan_period"), cfg.get("tl_scan_period")), "Ana Ayar": "Hacim teyitli" if cfg.get("tl_scan_require_volume") else "Hacim şart değil"},
            {"Tarama": "Alternasyon", "Periyot": ALTERNATION_SCAN_PERIOD_LABELS.get(cfg.get("alt_scan_period"), cfg.get("alt_scan_period")), "Ana Ayar": f"Min. zincir {cfg.get('alt_scan_min_chain',3)}"},
        ])
        st.dataframe(summary_df, width="stretch", hide_index=True)
        if st.button(f"🚀 Dördünü Tara · {len(symbols)} hisse", type="primary", width="stretch"):
            launch_background_scan("Tümünü Tara", symbols, cfg, result_focus="Özet")


# -----------------------------------------------------------------------------
# Üst navigasyon ve uygulama yönlendirmesi
# -----------------------------------------------------------------------------
st.session_state.setdefault("_app_page", "Ana Sayfa")
st.session_state.setdefault("_scan_type", "VWAP")
st.session_state.setdefault("_results_focus", "Özet")
st.session_state.setdefault("_active_job_id", None)
st.session_state.setdefault("_synced_job_revision", -1)
ensure_result_store()
_resolve_job_snapshot(auto_attach_running=True)

if render_chart_page_if_requested():
    st.stop()

with st.container(border=True):
    st.markdown('<div class="nav-label">Menü</div>', unsafe_allow_html=True)
    n1, n2, n3 = st.columns(3)
    with n1:
        if st.button("🏠 Ana Sayfa", type="primary" if st.session_state._app_page == "Ana Sayfa" else "secondary", width="stretch", key="nav_home"):
            if st.session_state._app_page != "Ana Sayfa":
                set_page("Ana Sayfa")
    with n2:
        if st.button("🔎 Tarama", type="primary" if st.session_state._app_page == "Tarama" else "secondary", width="stretch", key="nav_scan"):
            if st.session_state._app_page != "Tarama":
                set_page("Tarama")
    with n3:
        total_results = sum(len(st.session_state._result_sets.get(n) or []) for n in ["VWAP", "Üçgen", "Düşen Trend", "Alternasyon"])
        if st.button(f"📊 Sonuçlar · {total_results}", type="primary" if st.session_state._app_page == "Sonuçlar" else "secondary", width="stretch", key="nav_results"):
            if st.session_state._app_page != "Sonuçlar":
                set_page("Sonuçlar")
st.markdown('<div class="nav-spacer"></div>', unsafe_allow_html=True)

notice = st.session_state.pop("_job_start_notice", None)
if notice:
    st.info(notice)
render_live_scan_status()

page = st.session_state._app_page
if page == "Ana Sayfa":
    render_home()
elif page == "Tarama":
    render_scan_page()
else:
    render_results_page()

st.divider()
st.caption("Yahoo Finance verisi gecikmeli olabilir. Yükseliş puanı bir yatırım garantisi değil, teknik adayları sıralama aracıdır.")
