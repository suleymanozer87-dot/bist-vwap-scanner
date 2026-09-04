# -*- coding: utf-8 -*-
"""
vwap_core.py — Zincirleme Anchored VWAP algoritmasının çekirdeği.
Hem CLI scriptinde (bist_vwap_scanner.py) hem arayüzde (app.py) kullanılır.
"""

import os
import time
import math
import random
import threading
from datetime import date, datetime, time as dt_time, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "veri_onbellek")

DEFAULT_SYMBOLS = [
    "AEFES.IS","AGHOL.IS","AKBNK.IS","AKSA.IS","AKSEN.IS","ALARK.IS","ALFAS.IS","ANHYT.IS",
    "ARCLK.IS","ASELS.IS","ASTOR.IS","BERA.IS","BIMAS.IS","BRSAN.IS","BRYAT.IS","BUCIM.IS",
    "CANTE.IS","CCOLA.IS","CIMSA.IS","DOAS.IS","DOHOL.IS","ECILC.IS","ECZYT.IS","EGEEN.IS",
    "EKGYO.IS","ENJSA.IS","ENKAI.IS","EREGL.IS","EUPWR.IS","FROTO.IS","GARAN.IS","GESAN.IS",
    "GUBRF.IS","HALKB.IS","HEKTS.IS","ISCTR.IS","ISMEN.IS","IZMDC.IS","KARSN.IS","KAYSE.IS",
    "KCHOL.IS","KLSER.IS","KONTR.IS","KONYA.IS","KORDS.IS","KOZAA.IS","KOZAL.IS","KRDMD.IS",
    "MAVI.IS","MGROS.IS","MIATK.IS","ODAS.IS","OTKAR.IS","OYAKC.IS","PENTA.IS","PETKM.IS",
    "PGSUS.IS","QUAGR.IS","REEDR.IS","SAHOL.IS","SASA.IS","SISE.IS","SKBNK.IS","SMRTG.IS",
    "SOKM.IS","TABGD.IS","TAVHL.IS","TCELL.IS","THYAO.IS","TKFEN.IS","TOASO.IS","TSKB.IS",
    "TTKOM.IS","TTRAK.IS","TUKAS.IS","TUPRS.IS","TURSG.IS","ULKER.IS","VAKBN.IS","VESBE.IS",
    "VESTL.IS","YKBNK.IS","YEOTK.IS","ZOREN.IS",
]

# ====================================================================
# PARA BİRİMİ BAZI (TRY / USD / EUR)
# ====================================================================
# BIST hisseleri Yahoo Finance'te TL (TRY) bazında gelir. Kullanıcı USD ya
# da EUR bazında taramak isterse, günlük TL fiyat serisi taramadan ÖNCE
# ilgili kura (USDTRY=X / EURTRY=X) bölünerek çevrilir — VWAP zincir
# algoritmasının kendisi (anchored_vwap_series, run_vwap_chain_scan vb.)
# HİÇ DEĞİŞMEDEN, sadece girdi serisi farklı bir para biriminde olacak
# şekilde çalışır.
CURRENCY_FX_SYMBOLS = {"USD": "USDTRY=X", "EUR": "EURTRY=X"}
CURRENCY_OPTIONS = ("TRY", "USD", "EUR")


# ====================================================================
# TARAMA PERİYODU (daily / weekly / monthly / 4h)
# ====================================================================
# "daily", "weekly", "monthly" GÜNLÜK barlardan (tam Yahoo geçmişi)
# RESAMPLE edilerek üretilir (bkz. resample_ohlcv). "4h" ise Yahoo
# Finance'in GÜN-İÇİ (intraday) barlarından (60 dakikalık ham veri,
# tarama ihtiyacına göre 6 ay/1 yıl geçmişle) üretilir (bkz.
# resample_intraday_to_4h). Bu yüzden "4h" periyodunda VWAP zincirinin
# görebildiği geçmiş, günlük/haftalık/aylık periyotlara göre ÇOK DAHA
# KISADIR — bu normaldir, Yahoo Finance'in gün-içi veri politikasından
# kaynaklanır.
PERIOD_OPTIONS = ("daily", "weekly", "monthly", "4h")
INTRADAY_PERIODS = {"4h"}
PERIOD_LABELS = {"daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık", "4h": "4 Saatlik"}
# period -> (Yahoo'dan çekilecek ham gün-içi interval, geriye kaç günlük veri istensin)
INTRADAY_FETCH_SPEC = {"4h": ("60m", "1y"), "1h": ("60m", "6mo")}
# VWAP stratejisinin ilk paketteki veri pencereleri. Strateji semantiğini korumak için
# VWAP taraması bunları kullanır; diğer taramaların Yahoo dayanıklılık pencereleri değişmez.
VWAP_DAILY_HISTORY_PERIOD = "5y"
VWAP_INTRADAY_FETCH_SPEC = {"4h": ("60m", "730d")}

# STRATEJİ KİLİDİ: Aşağıdaki VWAP zincir semantiği kullanıcının ilk paketindeki
# mantıkla birebir korunur. Teknik veri/arayüz hataları düzeltilebilir; açık kullanıcı
# talebi olmadan crossover/anchor/zincir kuralları değiştirilmemelidir.
VWAP_STRATEGY_MODE = "ORIGINAL_2026_08"
INTRADAY_MAX_WORKERS = 6
INTRADAY_CACHE_TTL_MINUTES = 45
INTRADAY_REQUEST_MIN_GAP_SECONDS = 0.18
_INTRADAY_REQUEST_LOCK = threading.Lock()
_INTRADAY_LAST_REQUEST_TS = 0.0


def _effective_scan_workers(period, requested):
    """Gün-içi taramalarda Yahoo'yu aşırı paralel istekten korur."""
    try:
        requested = max(1, int(requested))
    except Exception:
        requested = INTRADAY_MAX_WORKERS
    if period in {"1h", "4h"}:
        return min(requested, INTRADAY_MAX_WORKERS)
    return requested


# ====================================================================
# BAĞIMSIZ TREND ÇİZGİSİ TARAMASI — KENDİ PERİYODU
# ====================================================================
# Aşağıdaki periyot listesi, YUKARIDAKİ ana VWAP taramasının periyodundan
# (PERIOD_OPTIONS) TAMAMEN AYRIDIR. Kullanıcı, VWAP ve diğer (yatay/
# zirveden düşüş/alternasyon) filtrelerden bağımsız olarak, SADECE düşen
# trend çizgisi kırılımını kendi seçtiği bir periyotta (1 Saat/4 Saat/
# Günlük/Haftalık) tarayabilsin diye eklendi (bkz. fetch_period_ohlcv,
# fetch_and_scan_trendline_only, scan_trendline_symbols_parallel).
# "1h" periyodu, Yahoo'nun zaten 60 dakikalık ham barlarını OLDUĞU GİBİ
# kullanır (ek resample gerekmez) — "4h" ise bu ham barları 4 saatlik
# mumlara indirger (bkz. resample_intraday_to_4h).
# DÜZELTME: "monthly" eklendi. fetch_period_ohlcv, "monthly"yi zaten
# resample_ohlcv üzerinden genel olarak destekliyordu (bkz. resample_ohlcv
# içindeki {"weekly": "W-FRI", "monthly": "ME"} eşlemesi) — sadece bu
# seçenek listesinde/etiketlerinde eksikti, arayüzde seçilemiyordu.
TRENDLINE_SCAN_PERIOD_OPTIONS = ("1h", "4h", "daily", "weekly", "monthly")
TRENDLINE_SCAN_INTRADAY_PERIODS = {"1h", "4h"}
TRENDLINE_SCAN_PERIOD_LABELS = {
    "1h": "1 Saatlik", "4h": "4 Saatlik", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
}

# Bağımsız ÜÇGEN taraması da (trend çizgisi taraması gibi) kendi periyodunu,
# VWAP taramasından TAMAMEN AYRI olarak seçebilsin diye — aynı yapı.
TRIANGLE_SCAN_PERIOD_OPTIONS = ("1h", "4h", "daily", "weekly", "monthly")
TRIANGLE_SCAN_INTRADAY_PERIODS = {"1h", "4h"}
TRIANGLE_SCAN_PERIOD_LABELS = {
    "1h": "1 Saatlik", "4h": "4 Saatlik", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
}

# Bağımsız ALTERNASYON (zigzag) taraması — aynı yapı, kendi periyodu.
ALTERNATION_SCAN_PERIOD_OPTIONS = ("1h", "4h", "daily", "weekly", "monthly")
ALTERNATION_SCAN_INTRADAY_PERIODS = {"1h", "4h"}
ALTERNATION_SCAN_PERIOD_LABELS = {
    "1h": "1 Saatlik", "4h": "4 Saatlik", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
}


# ====================================================================
# SEMBOL YARDIMCI FONKSİYONU — otomatik ".IS" ekleme
# ====================================================================

def normalize_symbol(raw):
    """'thyao', 'THYAO', 'thyao.is', 'THYAO.IS' -> her zaman 'THYAO.IS'."""
    s = raw.strip().upper()
    if not s:
        return None
    if s.endswith(".IS"):
        return s
    return s + ".IS"


def normalize_symbol_list(raw_list):
    """Ham metin listesinden (satır satır, virgüllü ya da karışık) temiz, tekilleşmiş sembol listesi."""
    out = []
    seen = set()
    for raw in raw_list:
        normalized_text = str(raw).replace(";", ",").replace("\n", ",")
        for piece in normalized_text.split(","):
            sym = normalize_symbol(piece)
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


# ====================================================================
# ANCHORED VWAP ZİNCİRİ
# ====================================================================

def anchored_vwap_series(df, anchor_idx):
    """anchor_idx'ten itibaren kümülatif VWAP serisi döner (öncesi NaN)."""
    vwap = pd.Series(index=df.index, dtype=float)
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].fillna(0)

    sub_tp = tp.iloc[anchor_idx:]
    sub_vol = vol.iloc[anchor_idx:]
    cum_pv = (sub_tp * sub_vol).cumsum()
    cum_v = sub_vol.cumsum()
    result = cum_pv / cum_v.replace(0, pd.NA)
    result = result.fillna(sub_tp)
    vwap.iloc[anchor_idx:] = result.values
    return vwap


def find_recent_crossover(df, vwap, anchor_idx, lookback=3):
    n = len(df)
    start = max(anchor_idx + 1, n - lookback)
    for i in range(start, n):
        prev_close = df["Close"].iloc[i - 1]
        prev_vwap = vwap.iloc[i - 1]
        close = df["Close"].iloc[i]
        v = vwap.iloc[i]
        if pd.isna(prev_vwap) or pd.isna(v):
            continue
        if close > v and prev_close <= prev_vwap:
            return i
    return None

def find_last_touch(df, vwap, anchor_idx):
    """anchor_idx sonrasında fiyatın VWAP çizgisine EN SON gerçekten temas
    ettiği mumun index'ini döndürür.

    DÜZELTME (kök neden): eski sürüm (ve onun bir önceki düzeltmesi) sadece
    KAPANIŞ (Close) fiyatının VWAP çizgisini KESTİĞİ anları "temas"
    sayıyordu. Ama gerçekte bir mum, gövdesi/fitiliyle VWAP çizgisine değip
    geri dönebilir; Close hiçbir zaman karşı tarafa geçmeden — bu da gözle
    bakıldığında (ve kullanıcının beklediği gibi) tam bir "temas"tır, sadece
    Close bazlı kesişim değildir. Sadece Close'a bakmak bu tür (çok daha
    SIK rastlanan) temasları atlıyor ve zincir, olması gerekenden çok daha
    ERKEN bir kesişim noktasına anchor'lanıyordu (bkz. örnek: VWAP-2,
    fiyatın aslında aylar sonra tekrar değdiği bir noktaya değil, ilk kesişim
    anına atanıyordu).

    Yeni tanım: bir mum "temas eder" ⇔ mumun [Low, High] aralığı VWAP
    değerini kapsıyorsa (Low <= VWAP <= High). Ayrıca, mum aralığı VWAP'a
    hiç değmeden onu tamamen ATLADIĞI (gap) nadir durum için, Close'un
    işaret değiştirdiği an da yedek olarak sayılır. En SON (kronolojik
    olarak en yakın) böyle mum döndürülür.
    """
    n = len(df)
    last_touch = anchor_idx
    for i in range(anchor_idx + 1, n):
        v = vwap.iloc[i]
        if pd.isna(v):
            continue
        low = df["Low"].iloc[i]
        high = df["High"].iloc[i]
        if low <= v <= high:
            last_touch = i
            continue
        prev_v = vwap.iloc[i - 1]
        if pd.isna(prev_v):
            continue
        prev_close = df["Close"].iloc[i - 1]
        close = df["Close"].iloc[i]
        if (close - v > 0) != (prev_close - prev_v > 0):
            last_touch = i
    return last_touch


def _fmt_ts(ts, intraday=False):
    """Bir pandas Timestamp'i tarih (günlük/haftalık/aylık için) ya da
    tarih+saat (4h gibi gün-içi periyotlar için — aksi halde aynı güne
    ait birden fazla bar hep aynı 'tarih' etiketiyle görünür, saat bilgisi
    kaybolur) string'ine çevirir."""
    if intraday:
        return str(ts.strftime("%Y-%m-%d %H:%M"))
    return str(ts.date())


def determine_first_anchor(df):
    ipo_price = df["Close"].iloc[0]
    current_price = df["Close"].iloc[-1]
    if current_price > ipo_price:
        ath_idx = int(df["High"].values.argmax())
        return ath_idx, "ATH"
    return 0, "IPO"

# ====================================================================
# YUKARI YÖN KALİTE MOTORU (0-100)
# ====================================================================
# Bu puan bir "kesin yükseliş olasılığı" değildir. Dört farklı taramanın
# bulduğu teknik yapıları aynı ölçekte sıralamak için kullanılan, tamamen
# deterministik bir KALİTE PUANIDIR. Ana sinyal motorlarını değiştirmez;
# yalnızca sonuçların hangisinin daha güçlü teyitlere sahip olduğunu gösterir.
QUALITY_BENCHMARK_SYMBOL = "XU100.IS"
QUALITY_BENCHMARK_LABEL = "BIST 100"


def _q_series(df, col):
    if df is None or len(df) == 0 or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").astype(float)


def _q_rsi(close, period=14):
    close = pd.Series(close, dtype=float)
    if len(close) < period + 2:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    v = rsi.iloc[-1]
    return None if pd.isna(v) else float(v)


def _q_return(close, bars):
    close = pd.Series(close, dtype=float).dropna()
    if len(close) <= bars or close.iloc[-bars-1] <= 0:
        return None
    return (float(close.iloc[-1]) / float(close.iloc[-bars-1]) - 1.0) * 100.0


def _q_align_returns(stock_df, benchmark_df, bars):
    """Aynı bar periyodundaki hisse ve BIST 100 getirilerini karşılaştırır."""
    if benchmark_df is None or len(benchmark_df) == 0:
        return None
    sc = _q_series(stock_df, "Close").dropna()
    bc = _q_series(benchmark_df, "Close").dropna()
    if len(sc) <= bars or len(bc) <= bars:
        return None
    # Bar sayısı bazlı karşılaştırma, iki seri aynı periyoda resample edildiği
    # için tatil/eksik barlarda tarih inner-join zorlamasından daha dayanıklıdır.
    sr = _q_return(sc, bars)
    br = _q_return(bc, bars)
    if sr is None or br is None:
        return None
    return {"stock": sr, "benchmark": br, "excess": sr - br}


def _q_volume_ratio(df, idx=None, lookback=20):
    vol = _q_series(df, "Volume").fillna(0)
    if len(vol) < 5:
        return None
    if idx is None:
        idx = len(vol) - 1
    idx = max(0, min(int(idx), len(vol) - 1))
    start = max(0, idx - lookback)
    base = float(vol.iloc[start:idx].mean()) if idx > start else 0.0
    cur = float(vol.iloc[idx])
    if base <= 0:
        return None
    return cur / base


def _q_higher_trend(higher_df):
    if higher_df is None or len(higher_df) < 25:
        return None
    close = _q_series(higher_df, "Close").dropna()
    if len(close) < 25:
        return None
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean() if len(close) >= 50 else None
    last = float(close.iloc[-1])
    e20 = float(ema20.iloc[-1])
    e20_prev = float(ema20.iloc[-5]) if len(ema20) >= 5 else float(ema20.iloc[0])
    slope_up = e20 > e20_prev
    above20 = last > e20
    above50 = bool(ema50 is not None and last > float(ema50.iloc[-1]))
    return {"above20": above20, "above50": above50, "ema20_up": slope_up,
            "last": last, "ema20": e20,
            "ema50": (float(ema50.iloc[-1]) if ema50 is not None else None)}


def _q_resistance_room(df, current=None, lookback=160, pivot_window=3):
    """En yakın anlamlı pivot dirence yüzde mesafeyi tahmin eder."""
    if df is None or len(df) < 2 * pivot_window + 5:
        return None
    highs = _q_series(df, "High").to_numpy(dtype=float)
    closes = _q_series(df, "Close").to_numpy(dtype=float)
    current = float(closes[-1] if current is None else current)
    if current <= 0:
        return None
    start = max(pivot_window, len(df) - lookback)
    pivots = []
    for i in range(start, len(df) - pivot_window):
        h = highs[i]
        if h >= max(highs[i-pivot_window:i]) and h >= max(highs[i+1:i+pivot_window+1]):
            # Çok yakın gürültü seviyesini direnç olarak sayma.
            if h > current * 1.012:
                pivots.append(float(h))
    if not pivots:
        return {"room_pct": 15.0, "resistance": None}
    resistance = min(pivots)
    return {"room_pct": max(0.0, (resistance/current - 1.0) * 100.0),
            "resistance": resistance}


def _q_recent_higher_low(df, bars=20):
    lows = _q_series(df, "Low").dropna()
    if len(lows) < max(8, bars):
        return None
    arr = lows.iloc[-bars:].to_numpy(dtype=float)
    half = max(2, len(arr)//2)
    return bool(float(arr[-half:].min()) > float(arr[:half].min()))


def _q_retest_holds(df, level_series, cross_idx, tolerance=0.012):
    if level_series is None or cross_idx is None:
        return False
    n = len(df)
    if cross_idx >= n - 1:
        return False
    lows = _q_series(df, "Low").to_numpy(dtype=float)
    closes = _q_series(df, "Close").to_numpy(dtype=float)
    try:
        levels = pd.Series(level_series).to_numpy(dtype=float)
    except Exception:
        return False
    end = min(n, int(cross_idx) + 7)
    touched = False
    for i in range(int(cross_idx)+1, end):
        if i >= len(levels) or not math.isfinite(levels[i]) or levels[i] <= 0:
            continue
        lvl = float(levels[i])
        if lows[i] <= lvl * (1.0 + tolerance):
            touched = True
            if closes[i] < lvl * (1.0 - tolerance):
                return False
    return touched


def _q_grade(score):
    if score >= 80:
        return "A+", "Çok Güçlü"
    if score >= 70:
        return "A", "Güçlü"
    if score >= 60:
        return "B", "İzlenebilir"
    if score >= 50:
        return "C", "Zayıf"
    return "D", "Ele"


def compute_upside_quality(df, signal_type, signal_info=None,
                           benchmark_df=None, higher_df=None):
    """Bir teknik sinyal için 0-100 arası ortak 'Yükseliş Kalite Puanı'.

    Bileşenler: teknik yapı 25, BIST 100 göreceli güç 20, hacim 15,
    üst zaman trendi 15, momentum 10, yakın dirence alan 10, kırılım/retest
    veya üçgen konumu 5. Eksik benchmark/üst zaman verisi varsa sistem
    çökmez; nötr puan verir ve 'veri eksik' uyarısını result içine yazar.
    """
    info = signal_info or {}
    if df is None or len(df) < 10:
        grade, label = _q_grade(0)
        return {"score": 0, "grade": grade, "label": label, "components": {},
                "reasons": [], "warnings": ["Kalite puanı için yeterli veri yok"]}

    close = _q_series(df, "Close").dropna()
    high = _q_series(df, "High").dropna()
    low = _q_series(df, "Low").dropna()
    last = float(close.iloc[-1])
    reasons, warnings = [], []
    comps = {}

    # 1) Teknik formasyon kalitesi — 25
    technical = 0.0
    stype = str(signal_type).lower()
    if stype == "triangle":
        squeeze = float(info.get("squeeze_pct", 100) or 100)
        pos = 0.5
        up = float(info.get("upper_now", last) or last)
        lo = float(info.get("lower_now", last) or last)
        if up > lo:
            pos = max(0.0, min(1.0, (last-lo)/(up-lo)))
        touches = int(info.get("upper_touches", 2) or 2) + int(info.get("lower_touches", 2) or 2)
        higher_low = _q_recent_higher_low(df, 20)
        technical += 10 if squeeze <= 25 else 8 if squeeze <= 35 else 5 if squeeze <= 50 else 2
        technical += 7 if pos >= .72 else 5 if pos >= .58 else 2 if pos >= .42 else 0
        technical += 5 if touches >= 6 else 3 if touches >= 4 else 1
        technical += 3 if higher_low else 0
        if pos >= .58: reasons.append("Üçgenin üst bölgesinde")
        if higher_low: reasons.append("Son dipler yükseliyor")
    elif stype == "trendline":
        touches = int(info.get("touches", 0) or 0)
        bars_ago = int(info.get("bars_ago", 99) or 99)
        lvl = float(info.get("line_value_now", last) or last)
        margin = (last/lvl-1)*100 if lvl > 0 else 0
        technical += min(9, 3 + max(0, touches-2)*2)
        technical += 7 if 0.3 <= margin <= 5 else 4 if 0 < margin <= 8 else 1
        technical += 6 if bars_ago <= 1 else 4 if bars_ago <= 3 else 2 if bars_ago <= 5 else 0
        technical += 3 if _q_recent_higher_low(df, 20) else 0
        if touches >= 3: reasons.append(f"Düşen çizgi {touches} bağımsız temasla doğrulanmış")
        if 0.3 <= margin <= 5: reasons.append("Kırılım güçlü ama aşırı uzaklaşmamış")
    elif stype == "vwap":
        # USD/EUR gibi düşük nominal fiyatlarda run_vwap_chain_scan içindeki
        # 2 ondalıklı gösterim değerini puanlamada kullanma; gerçek seri değeri şart.
        try:
            _vv_exact = pd.Series(info.get("chain", [])[-1].get("vwap")).dropna()
            lvl = float(_vv_exact.iloc[-1]) if len(_vv_exact) else float(info.get("last_vwap", last) or last)
        except Exception:
            lvl = float(info.get("last_vwap", last) or last)
        dist = (last/lvl-1)*100 if lvl > 0 else 99
        bars_ago = int(info.get("bars_ago", 99) or 99)
        technical += 11 if 0.2 <= dist <= 4 else 7 if 0 < dist <= 7 else 3 if 0 < dist <= 12 else 0
        technical += 6 if bars_ago <= 1 else 4 if bars_ago <= 3 else 2 if bars_ago <= 5 else 0
        # VWAP eğimi
        try:
            vser = info.get("chain", [])[-1].get("vwap")
            vv = pd.Series(vser).dropna()
            if len(vv) >= 5 and float(vv.iloc[-1]) > float(vv.iloc[-5]):
                technical += 5
                reasons.append("VWAP yukarı eğimli")
        except Exception:
            pass
        technical += 3 if _q_recent_higher_low(df, 20) else 0
        if 0.2 <= dist <= 4: reasons.append("VWAP üstünde kontrollü mesafe")
    else:  # alternation
        regularity = float(info.get("score", 0) or 0)
        chain_len = int(info.get("chain_length", 0) or 0)
        technical += min(12, regularity * 0.12)
        technical += min(8, max(0, chain_len-2) * 2)
        if len(close) and len(_q_series(df, "Open")):
            technical += 5 if float(close.iloc[-1]) > float(_q_series(df, "Open").iloc[-1]) else 0
        if regularity >= 65: reasons.append("Alternasyon düzenliliği yüksek")
    comps["Teknik yapı"] = round(min(25.0, technical), 1)

    # 2) BIST 100'e göre göreceli güç — 20
    relative = 0.0
    rs_details = {}
    found_rs = False
    for bars, weight in ((20, 10), (60, 10)):
        rs = _q_align_returns(df, benchmark_df, bars)
        if rs is None:
            continue
        found_rs = True
        excess = rs["excess"]
        pts = weight if excess >= 5 else weight*.8 if excess >= 2 else weight*.6 if excess >= 0 else weight*.3 if excess >= -2 else 0
        relative += pts
        rs_details[f"{bars}bar_excess_pct"] = round(excess, 2)
    if not found_rs:
        relative = 10.0  # nötr; veri yok diye hisseyi otomatik cezalandırma
        warnings.append("BIST 100 göreceli güç verisi yok; nötr puan kullanıldı")
    elif relative >= 14:
        reasons.append("BIST 100'e göre güçlü")
    comps["Göreceli güç"] = round(min(20.0, relative), 1)

    # 3) Hacim — 15. Üçgende kuruma + alış baskısı, kırılımlarda breakout hacmi.
    volume = 0.0
    vol_ratio = None
    if stype == "trendline":
        vol_ratio = _q_volume_ratio(df, info.get("cross_idx"))
    elif stype == "vwap":
        vol_ratio = _q_volume_ratio(df, info.get("cross_idx"))
    else:
        vol_ratio = _q_volume_ratio(df)
    if stype == "triangle":
        dry = info.get("volume_dryness_pct")
        if dry is not None:
            dry = float(dry)
            volume += 8 if dry <= 65 else 6 if dry <= 80 else 3 if dry <= 100 else 0
            if dry <= 80: reasons.append("Üçgen içinde hacim kuruyor")
        # son 5 barda yeşil mum hacmi kırmızıdan baskın mı
        op = _q_series(df, "Open")
        vol = _q_series(df, "Volume").fillna(0)
        if len(vol) >= 5 and len(op) == len(close):
            last5 = pd.DataFrame({"o": op.iloc[-5:].to_numpy(), "c": close.iloc[-5:].to_numpy(), "v": vol.iloc[-5:].to_numpy()})
            upv = float(last5.loc[last5.c > last5.o, "v"].sum())
            dnv = float(last5.loc[last5.c < last5.o, "v"].sum())
            volume += 7 if upv > dnv * 1.25 else 4 if upv >= dnv else 1
    else:
        if vol_ratio is not None:
            volume = 15 if vol_ratio >= 2 else 12 if vol_ratio >= 1.5 else 8 if vol_ratio >= 1.2 else 5 if vol_ratio >= 1 else 1
            if vol_ratio >= 1.5: reasons.append(f"Hacim ortalamanın {vol_ratio:.1f} katı")
        else:
            volume = 5
            warnings.append("Hacim oranı hesaplanamadı")
    comps["Hacim"] = round(min(15.0, volume), 1)

    # 4) Üst zaman dilimi trendi — 15
    ht = _q_higher_trend(higher_df)
    if ht is None:
        higher = 7.0
        warnings.append("Trend teyidi verisi yok; nötr puan kullanıldı")
    else:
        higher = 0.0
        if ht["above20"]: higher += 5
        if ht["above50"]: higher += 5
        if ht["ema20_up"]: higher += 5
        if higher >= 10: reasons.append("Trend teyidi pozitif")
    comps["Trend teyidi"] = round(min(15.0, higher), 1)

    # 5) Momentum — 10
    rsi = _q_rsi(close, 14)
    roc10 = _q_return(close, 10)
    momentum = 0.0
    if rsi is not None:
        momentum += 6 if 52 <= rsi <= 68 else 4 if 48 <= rsi < 52 else 3 if 68 < rsi <= 75 else 1 if 45 <= rsi < 48 else 0
    if roc10 is not None:
        momentum += 4 if 1 <= roc10 <= 12 else 3 if 0 < roc10 < 1 else 2 if 12 < roc10 <= 20 else 0
    if rsi is not None and 52 <= rsi <= 68 and (roc10 or 0) > 0:
        reasons.append("Momentum pozitif ve aşırı ısınmamış")
    comps["Momentum"] = round(min(10.0, momentum), 1)

    # 6) Yakın dirence açık alan — 10
    room = _q_resistance_room(df, current=last)
    if room is None:
        resistance = 5.0
    else:
        rp = room["room_pct"]
        resistance = 10 if rp >= 10 else 8 if rp >= 7 else 6 if rp >= 5 else 3 if rp >= 3 else 0
        if rp >= 7: reasons.append(f"Yakın dirence yaklaşık %{rp:.1f} alan var")
    comps["Dirence alan"] = round(resistance, 1)

    # 7) Onay / retest — 5
    confirmation = 0.0
    retest = False
    if stype == "trendline":
        cross = info.get("cross_idx")
        line = info.get("line") or {}
        if cross is not None and line:
            vals = [line["slope"]*i + line["intercept"] for i in range(len(df))]
            retest = _q_retest_holds(df, vals, int(cross))
        confirmation = 5 if retest else 3 if int(info.get("bars_ago", 99) or 99) <= 1 else 1
    elif stype == "vwap":
        try:
            vser = info.get("chain", [])[-1].get("vwap")
            retest = _q_retest_holds(df, vser, int(info.get("cross_idx")))
        except Exception:
            retest = False
        confirmation = 5 if retest else 3 if int(info.get("bars_ago", 99) or 99) <= 1 else 1
    elif stype == "triangle":
        up = float(info.get("upper_now", last) or last); lo = float(info.get("lower_now", last) or last)
        pos = (last-lo)/(up-lo) if up > lo else .5
        confirmation = 5 if pos >= .72 and _q_recent_higher_low(df, 20) else 3 if pos >= .58 else 1
    else:
        confirmation = 5 if (roc10 or -999) > 0 and (rsi or 0) >= 50 else 2
    if retest: reasons.append("Kırılım sonrası retest tutulmuş")
    comps["Onay / retest"] = round(confirmation, 1)

    # Sinyal türüne göre kalibrasyon. Aynı 0-100 ölçek korunur; ancak
    # backtestte farklı davranan stratejilere aynı ağırlığı zorla uygulamayız.
    if stype == "trendline":
        # 1h düşen trend backtestinde en ayırt edici özellikler: kırılımın
        # çizgiye çok yakın olması, günlük trend teyidi ve RSI'ın 60-67 bandı.
        # Zaten hacim filtresi zorunluysa aşırı hacme tekrar büyük ödül vermek
        # fayda sağlamadığı için hacim burada yalnız 5 puan taşır.
        _margin = margin
        _line_pts = 24 if 0 < _margin <= .35 else 15 if _margin <= .70 else 8 if _margin <= 1.50 else 12 if _margin <= 4.0 else 5
        _ht_pts = 22 if higher >= 10 else 7 if higher >= 5 else 0
        _rsi_pts = 18 if (rsi is not None and 60 <= rsi <= 67.5) else 11 if (rsi is not None and 50 <= rsi < 60) else 7 if (rsi is not None and 67.5 < rsi <= 75) else 4
        _rp = room["room_pct"] if room else 0
        _room_pts = 13 if 1.8 <= _rp <= 3.2 else 7 if 1.5 <= _rp <= 5 else 2
        _touch_pts = 8 if 3 <= touches <= 4 else 4
        _vol_pts = 5 if (vol_ratio is not None and 1.8 <= vol_ratio <= 2.8) else 2
        if found_rs:
            _excess_vals = list(rs_details.values())
            _avg_excess = sum(_excess_vals) / len(_excess_vals) if _excess_vals else 0
            _rs_pts = 5 if _avg_excess >= 2 else 4 if _avg_excess >= 0 else 2 if _avg_excess >= -2 else 0
        else:
            _rs_pts = 3
        _conf_pts = 5 if retest else 3 if int(info.get("bars_ago", 99) or 99) <= 1 else 1
        comps = {
            "Kırılım konumu": _line_pts, "Trend teyidi": _ht_pts,
            "Momentum": _rsi_pts, "Direnç konumu": _room_pts,
            "Temas kalitesi": _touch_pts, "Hacim": _vol_pts,
            "Göreceli güç": _rs_pts, "Onay / retest": _conf_pts,
        }
        if 0 < _margin <= .35:
            reasons.append("Kırılım çizgiye yakın ve kontrollü")
    elif stype == "vwap":
        # VWAP sistemi burada çoğunlukla dipten/çöküşten dönüş karakterinde.
        # Backtest, aşırı momentum/hacim yerine VWAP'ın %0.7-2 üstündeki
        # kontrollü geçişi, RSI 47-57 ve normalleşen hacmi daha başarılı buldu.
        _dist = dist
        _dist_pts = 25 if .7 <= _dist <= 2.0 else 18 if (.3 <= _dist < .7 or 2.0 < _dist <= 4.5) else 10 if 0 < _dist <= 8 else 4
        _rsi_pts = 20 if (rsi is not None and 47 <= rsi <= 57) else 16 if (rsi is not None and 40 <= rsi < 47) else 8 if (rsi is not None and 57 < rsi <= 65) else 3
        _roc_pts = 15 if (roc10 is not None and roc10 <= 4) else 10 if (roc10 is not None and roc10 <= 15) else 5
        _vol_pts = 15 if (vol_ratio is not None and .75 <= vol_ratio <= 1.25) else 10 if (vol_ratio is not None and vol_ratio < .75) else 7 if (vol_ratio is not None and vol_ratio <= 2) else 5
        _rp = room["room_pct"] if room else 0
        _room_pts = 10 if _rp >= 3 else 5
        # Günlük/alt bağlamın rolü küçük; VWAP dönüş stratejisini sırf aylık trend
        # aşağı diye cezalandırmıyoruz.
        _trend_pts = 5 if higher >= 10 else 3
        if found_rs:
            _excess_vals = list(rs_details.values())
            _avg_excess = sum(_excess_vals) / len(_excess_vals) if _excess_vals else 0
            _rs_pts = 5 if _avg_excess >= 2 else 4 if _avg_excess >= 0 else 2 if _avg_excess >= -2 else 0
        else:
            _rs_pts = 3
        _conf_pts = 5 if retest else 3 if int(info.get("bars_ago", 99) or 99) <= 1 else 1
        comps = {
            "VWAP mesafesi": _dist_pts, "Dönüş momentumu": _rsi_pts + _roc_pts,
            "Hacim dengesi": _vol_pts, "Dirence alan": _room_pts,
            "Trend teyidi": _trend_pts, "Göreceli güç": _rs_pts,
            "Onay / retest": _conf_pts,
        }
        # Dönüş momentumu iki alt parçadan oluştuğu için toplam 35 taşıyor;
        # bütün bileşenlerin teorik toplamı yine 100'dür.
        if .7 <= _dist <= 2.0:
            reasons.append("VWAP üstünde ideal kontrollü mesafe")
        if rsi is not None and 47 <= rsi <= 57:
            reasons.append("RSI dönüş bölgesinde, aşırı ısınmamış")
    total = round(sum(comps.values()), 1)
    total = max(0.0, min(100.0, total))
    grade, label = _q_grade(total)
    return {
        "score": total, "grade": grade, "label": label,
        "components": comps, "reasons": reasons[:6], "warnings": warnings,
        "rsi14": (round(rsi, 1) if rsi is not None else None),
        "roc10_pct": (round(roc10, 2) if roc10 is not None else None),
        "volume_ratio": (round(vol_ratio, 2) if vol_ratio is not None else None),
        "relative_strength": rs_details,
        "resistance_room_pct": (round(room["room_pct"], 2) if room else None),
        "nearest_resistance": (round(room["resistance"], 4) if room and room["resistance"] else None),
        "retest_confirmed": bool(retest),
    }


def _quality_higher_period(period):
    return {"1h": "daily", "4h": "daily", "daily": "weekly",
            "weekly": "daily", "monthly": "weekly"}.get(period, "daily")


def _fetch_quality_higher_df(symbol, period, use_cache=True):
    daily, _err = fetch_history_cached(symbol, use_cache=use_cache)
    if daily is None or len(daily) == 0:
        return None
    hp = _quality_higher_period(period)
    return resample_ohlcv(daily, hp)


def _fetch_quality_benchmark(period, use_cache=True, currency="TRY", fx_df=None):
    """BIST 100'ü sinyal ile aynı periyoda getirir. Başarısızsa None döner."""
    try:
        if period in {"1h", "4h"}:
            raw, _err = fetch_intraday_history_cached(
                QUALITY_BENCHMARK_SYMBOL, *INTRADAY_FETCH_SPEC.get(period, ("60m", "6mo")), use_cache=use_cache,
            )
            if raw is None:
                return None
            if currency != "TRY" and fx_df is not None:
                raw = convert_intraday_ohlc_to_currency(raw, fx_df)
            return resample_intraday_to_4h(raw, closed_only=True) if period == "4h" else _drop_incomplete_intraday_bars(raw, bar_minutes=60)
        daily, _err = fetch_history_cached(QUALITY_BENCHMARK_SYMBOL, use_cache=use_cache)
        if daily is None:
            return None
        if currency != "TRY" and fx_df is not None:
            daily = convert_ohlc_to_currency(daily, fx_df)
        return resample_ohlcv(daily, period)
    except Exception:
        return None

# ====================================================================
# ZİRVEDEN (ANCHOR'DAN) DÜŞÜŞ FİLTRESİ
# ====================================================================
# VWAP kırılımından TAMAMEN BAĞIMSIZ, ek bir filtre: hissenin VWAP'ın
# atıldığı fiyattan (ATH anchor'da gerçek zirve, IPO anchor'da ilk gün
# kapanışı) bugüne kadar en az X% düştüğünü tespit eder — "zirveden
# çökmüş, dipten dönüş adayı" hisseleri VWAP kırılımı gerçekleşmemiş
# olsa bile yakalamak için.

def compute_anchor_drawdown(df, anchor_idx, anchor_reason):
    """anchor_idx'teki (VWAP'ın atıldığı) fiyattan son kapanışa kadar
    YÜZDE KAÇ düşüldüğünü hesaplar.

    Referans fiyat: ATH anchor'da o günün GERÇEK zirve değeri (High),
    IPO anchor'da o günün kapanışı (Close) — ikisi de "VWAP'ın atıldığı
    seviye"yi en doğru yansıtan değer.

    Dönüş: pozitif sayı = düşüş yüzdesi (örn. 62.4 -> zirveden %62.4
    düşmüş). Negatif/0 = zirveden bu yana yükselmiş ya da aynı seviyede.
    Geçersiz veri varsa None döner.
    """
    if df is None or len(df) == 0:
        return None
    high_anchor_reasons = {"ATH", "PENCERE ZİRVESİ"}
    anchor_price = float(df["High"].iloc[anchor_idx]) if anchor_reason in high_anchor_reasons \
        else float(df["Close"].iloc[anchor_idx])
    last_close = float(df["Close"].iloc[-1])
    if anchor_price <= 0:
        return None
    return (anchor_price - last_close) / anchor_price * 100.0


# ====================================================================
# MUM RENK ALTERNASYONU (ZİGZAG) TESPİTİ
# ====================================================================
# VWAP zincirinden TAMAMEN BAĞIMSIZ, ek bir filtre: SON mumdan geriye
# doğru bakarak mum renklerinin (yeşil/kırmızı) kesintisiz şekilde
# birbirini takip edip etmediğini (yeşil-kırmızı-yeşil-kırmızı ya da
# tam tersi) tespit eder. En az ALTERNATION_MIN_CHAIN (varsayılan 4)
# mum boyunca kesintisiz alternasyon yoksa desen "yok" sayılır.
#
# Desen bulunduysa, zincirdeki mumların GÖVDE boyu (|Close-Open| —
# fitiller/High-Low'a HİÇ bakılmaz) birbirine ne kadar yakınsa 0-100
# arası bir "düzenlilik puanı" hesaplanır: gövdeler tamamen eşitse
# puan 100, aralarındaki fark (en büyük gövdeye oranla) arttıkça puan
# düşer.

ALTERNATION_MIN_CHAIN = 4


def detect_candle_alternation(df, min_chain=ALTERNATION_MIN_CHAIN):
    """Son mumdan geriye doğru kesintisiz yeşil/kırmızı alternasyonunu bulur.

    Doji (Close == Open) artık zorla "yeşil" sayılmaz; zinciri keser. Düzenlilik
    puanı ham TL gövde boyu yerine fiyatın yüzdesi olarak normalize edilmiş gövde
    büyüklüklerinin varyasyon katsayısından hesaplanır. Böylece tek küçük mum bütün
    puanı sıfıra düşürmez ve farklı fiyat seviyeleri daha adil karşılaştırılır.
    """
    if df is None or len(df) < min_chain:
        return None

    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    n = len(df)
    diff = closes - opens
    colors_sign = (diff > 0).astype(int) - (diff < 0).astype(int)  # +1 yeşil, -1 kırmızı, 0 doji

    if colors_sign[-1] == 0:
        return None

    chain_len = 1
    for i in range(n - 1, 0, -1):
        if colors_sign[i - 1] == 0:
            break
        if colors_sign[i] != colors_sign[i - 1]:
            chain_len += 1
        else:
            break

    if chain_len < min_chain:
        return None

    start_idx = n - chain_len
    end_idx = n - 1
    raw_bodies = abs(diff[start_idx:end_idx + 1])
    mid_prices = (abs(opens[start_idx:end_idx + 1]) + abs(closes[start_idx:end_idx + 1])) / 2.0
    body_pct = raw_bodies / pd.Series(mid_prices).replace(0, pd.NA).astype(float).to_numpy() * 100.0
    body_pct = pd.Series(body_pct).replace([float("inf"), float("-inf")], pd.NA).dropna().to_numpy(dtype=float)
    if len(body_pct) == 0:
        return None

    mean_body = float(body_pct.mean())
    if mean_body <= 1e-12:
        score = 100.0
    else:
        std_body = float(body_pct.std(ddof=0))
        cv = std_body / mean_body
        score = 100.0 / (1.0 + 2.0 * cv)

    return {
        "chain_length": chain_len,
        "score": round(max(0.0, min(100.0, score)), 1),
        "start_idx": start_idx,
        "end_idx": end_idx,
        "colors": ["yeşil" if colors_sign[i] > 0 else "kırmızı" for i in range(start_idx, end_idx + 1)],
        "body_sizes": [round(float(b), 4) for b in raw_bodies],
    }


# ====================================================================
# DÜŞEN TREND ÇİZGİSİ KIRILIMI TESPİTİ
# ====================================================================
# VWAP zincirinden TAMAMEN BAĞIMSIZ, ek bir filtre: pivot TEPE noktalarından
# (yerel maksimumlardan) geçen, fiyatın hiçbir zaman üstüne çıkmadığı en
# dıştaki ("üst zarf" / upper convex hull) düşen bir direnç çizgisi kurar
# ve kapanışın bu çizgiyi son barlarda YUKARI kırdığı anı arar — ekteki
# TradingView örneğindeki gibi (düşen trend çizgisi + kırılım noktası).
#
# Yöntem özetle:
#   1) Pivot tepeleri bul (solundaki/sağındaki `pivot_window` bar boyunca
#      en yüksek High'a sahip barlar).
#   2) Bu pivot noktalarının ÜST ZARFINI (upper convex hull) çıkar — zarf
#      özelliği gereği HİÇBİR pivot bu çizginin üstünde kalmaz, bu yüzden
#      "gerçek bir direnç" tanımına uyar (rastgele iki noktadan geçen
#      anlamsız bir çizgi değil).
#   3) Zarfın EN SON (en güncel) segmentini al — hull'un dışbükeylik
#      özelliği gereği segment eğimleri soldan sağa monotonik azalır,
#      yani son segment her zaman "şu an aktif olan" direnci temsil eder.
#   4) Bu segment DÜŞEN (negatif eğimli) ise, çizgiyi ileriye (bugüne
#      kadar) uzatıp kapanışın onu ne zaman yukarı kırdığını ara.

TRENDLINE_PIVOT_WINDOW = 3
TRENDLINE_MIN_SPAN_BARS = 12
TRENDLINE_LOOKBACK_BARS = 200
TRENDLINE_TOUCH_TOLERANCE_PCT = 1.5
TRENDLINE_VOLUME_FACTOR = 1.5
# Çizgiyi oluşturan İKİ pivot (x1 tepe, x2 tepe) tolerans içinde çizginin
# üzerinde olduğu için "temas sayısı" HER ZAMAN en az 2'dir — bunlar
# kullanıcının "1. temas" (en tepedeki pivot) ve zarfın son pivotu (bir
# sonraki temas) olarak düşündüğü noktalardır. TRENDLINE_MIN_TOUCHES,
# bunun ÜZERİNE en az kaç EK/ara temas (veya çok pivotlu, daha "kanıtlanmış"
# bir çizgi) arandığını belirler; varsayılan 2 = filtre kapalı (davranış
# değişmez), yükseltilirse (örn. 3-4) sadece çizgiye daha ÇOK kez değen
# ("kırılmadan önce defalarca test edilmiş") çizgiler eşleşme sayılır.
TRENDLINE_MIN_TOUCHES = 2


# ====================================================================
# ÜÇGEN KIRILIM (KIRILMAK ÜZERE) TARAMASI
# ====================================================================
# Yukarıdaki düşen trend çizgisi dedektörü, çizginin ZATEN kırıldığı anı
# arar. Buradaki filtre TAMAMEN FARKLI bir soru sorar: hisse şu anda bir
# ÜÇGEN (yakınsayan iki çizgi) içinde mi ve bu üçgen KIRILMAK ÜZERE mi
# (henüz kırılmamış, ama apex'e — iki çizginin kesişeceği noktaya —
# yaklaşmış ve sıkışmış)? VWAP zincirinden ve diğer TÜM filtrelerden
# TAMAMEN BAĞIMSIZDIR.
#
# Yöntem özetle:
#   1) Pivot TEPE noktalarından üst zarfın (upper convex hull) SON
#      segmenti = direnç çizgisi. Pivot DİP noktalarından alt zarfın
#      (lower convex hull) SON segmenti = destek çizgisi.
#   2) İki çizgi YAKINSIYOR mu (direnç eğimi < destek eğimi)? Değilse
#      (paralel/ıraksıyorsa) üçgen yoktur.
#   3) Çizgilerin kesişeceği bar (apex) HENÜZ GELMEMİŞ ve makul bir
#      mesafede mi (çok geçmişte kalmışsa üçgen tükenmiş, çok uzaktaysa
#      henüz erken)?
#   4) Fiyat HÂLÂ iki çizginin ARASINDA mı (zaten kırılmışsa bu artık
#      "kırılmak üzere" değil, "kırılmış"tır — o zaman zaten yukarıdaki
#      trend çizgisi ya da normal fiyat hareketiyle görünür)?
#   5) Üçgenin GENİŞLİĞİ, başlangıcına göre yeterince DARALMIŞ mı
#      (sıkışma/squeeze) — bu, kırılımın YAKIN olduğunun asıl işaretidir.

TRIANGLE_PIVOT_WINDOW = 3
TRIANGLE_MIN_SPAN_BARS = 12
TRIANGLE_LOOKBACK_BARS = 200
TRIANGLE_MIN_APEX_BARS_AHEAD = 1
TRIANGLE_MAX_APEX_BARS_AHEAD = 40
TRIANGLE_MAX_SQUEEZE_PCT = 50.0


def find_pivot_highs(df, window=TRENDLINE_PIVOT_WINDOW):
    """Solundaki VE sağındaki `window` bar boyunca High'ı en yüksek olan
    barları 'pivot tepe' (yerel maksimum) sayar. Son `window` bar, sağ
    tarafta yeterli teyit barı olmadığı için pivot adayı OLAMAZ — bu,
    henüz oluşmamış/teyit edilmemiş tepe noktalarını yanlışlıkla çizgiye
    anchor etmemek için kasıtlıdır.

    Ardışık barlar aynı (düz tepe) High değerine sahipse, aralarında en az
    `window` bar mesafe olmadan ikinci bir pivot eklenmez — anlamsız,
    birbirine yapışık pivot çiftleri üretmemek için.

    Dönüş: pivot barların (0-tabanlı, pozisyonel) index listesi, kronolojik sırada.
    """
    highs = df["High"].to_numpy(dtype=float)
    n = len(highs)
    pivots = []
    for i in range(window, n - window):
        seg = highs[i - window:i + window + 1]
        if highs[i] >= seg.max() and (not pivots or i - pivots[-1] > window):
            pivots.append(i)
    return pivots


def find_pivot_lows(df, window=TRIANGLE_PIVOT_WINDOW):
    """find_pivot_highs()'ın aynası — solundaki VE sağındaki `window` bar
    boyunca Low'u en düşük olan barları 'pivot dip' (yerel minimum) sayar.
    Aynı kurallar geçerlidir (son `window` bar pivot adayı olamaz, yapışık
    pivot çiftleri engellenir).

    Dönüş: pivot barların (0-tabanlı, pozisyonel) index listesi, kronolojik sırada.
    """
    lows = df["Low"].to_numpy(dtype=float)
    n = len(lows)
    pivots = []
    for i in range(window, n - window):
        seg = lows[i - window:i + window + 1]
        if lows[i] <= seg.min() and (not pivots or i - pivots[-1] > window):
            pivots.append(i)
    return pivots


def _upper_hull(points):
    """(x, y) noktalarının ÜST ZARFINI (upper convex hull) döner — yani
    hiçbir noktanın üstünde kalmayan, en dıştaki tepe noktalarını
    birleştiren dışbükey çizgi (Andrew'in monotone chain algoritmasının
    üst yarısı). points, x'e göre ARTAN sırada olmalıdır."""
    hull = []
    for p in points:
        while len(hull) >= 2:
            x1, y1 = hull[-2]
            x2, y2 = hull[-1]
            x3, y3 = p
            cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
            if cross >= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    return hull


def _lower_hull(points):
    """(x, y) noktalarının ALT ZARFINI (lower convex hull) döner — yani
    hiçbir noktanın ALTINDA kalmayan, en dıştaki dip noktalarını
    birleştiren dışbükey çizgi (_upper_hull'ın dönüş yönü ters çevrilmiş
    aynısı). points, x'e göre ARTAN sırada olmalıdır."""
    hull = []
    for p in points:
        while len(hull) >= 2:
            x1, y1 = hull[-2]
            x2, y2 = hull[-1]
            x3, y3 = p
            cross = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
            if cross <= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    return hull


def detect_descending_trendline(df, pivot_window=TRENDLINE_PIVOT_WINDOW,
                                 lookback_bars=TRENDLINE_LOOKBACK_BARS,
                                 min_span_bars=TRENDLINE_MIN_SPAN_BARS):
    """Son `lookback_bars` bar içindeki pivot tepe noktalarının üst
    zarfından, hâlâ DÜŞEN (negatif eğimli) EN GÜNCEL segmenti "aktif
    direnç çizgisi" olarak döndürür.

    Dönüş: None (yeterli pivot yok / zarfın son segmenti yükseliyor / çok
    kısa) ya da {"x1","y1","x2","y2","slope","intercept"} — x1/x2 pivot
    barların pozisyonel index'i, y1/y2 o barlardaki High değeri.
    """
    n = len(df)
    if n < min_span_bars + 2 * pivot_window + 1:
        return None

    zone_start = max(0, n - lookback_bars)
    pivots = [i for i in find_pivot_highs(df, window=pivot_window) if i >= zone_start]
    if len(pivots) < 2:
        return None

    highs = df["High"].to_numpy(dtype=float)
    points = [(i, highs[i]) for i in pivots]
    hull = _upper_hull(points)
    if len(hull) < 2:
        return None

    (x1, y1), (x2, y2) = hull[-2], hull[-1]
    if x2 - x1 < min_span_bars:
        return None

    slope = (y2 - y1) / (x2 - x1)
    if slope >= 0:
        return None  # zarfın son segmenti yükseliyor -> şu an aktif düşen direnç yok

    intercept = y1 - slope * x1
    return {"x1": int(x1), "y1": float(y1), "x2": int(x2), "y2": float(y2),
            "slope": float(slope), "intercept": float(intercept)}


def _count_line_touch_clusters(df, line, price_col="High",
                                tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT,
                                min_gap_bars=3, x_start=None, x_end=None):
    """Bir çizgiye yakın ardışık mumları tek bir bağımsız 'temas bölgesi' sayar.

    Eski kod çizgi yakınında art arda duran 10 mumu 10 ayrı temas sayıyordu.
    Burada temas barları kümelenir; yeni bir temas sayılması için önceki temas
    kümesinden en az `min_gap_bars` bar uzaklaşmış olması gerekir.
    """
    values = df[price_col].to_numpy(dtype=float)
    x1 = line["x1"] if x_start is None else max(int(x_start), 0)
    x2 = line["x2"] if x_end is None else min(int(x_end), len(df) - 1)
    slope, intercept = line["slope"], line["intercept"]
    candidates = []
    for i in range(x1, x2 + 1):
        line_val = slope * i + intercept
        if line_val <= 0:
            continue
        if abs(values[i] - line_val) / line_val * 100.0 <= tolerance_pct:
            candidates.append(i)
    if not candidates:
        return 0
    clusters = 1
    last = candidates[0]
    for i in candidates[1:]:
        if i - last >= max(1, int(min_gap_bars)):
            clusters += 1
        last = i
    return clusters


def _count_trendline_touches(df, line, tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT):
    return _count_line_touch_clusters(
        df, line, price_col="High", tolerance_pct=tolerance_pct,
        min_gap_bars=3, x_start=line["x1"], x_end=line["x2"],
    )


def find_trendline_crossover(df, line, lookback=3):
    """Düşen trend çizgisinin son `lookback` bardaki EN YENİ yukarı kırılımını bulur."""
    n = len(df)
    closes = df["Close"].to_numpy(dtype=float)
    slope, intercept = line["slope"], line["intercept"]
    start = max(line["x2"] + 1, n - lookback)
    for i in range(n - 1, start - 1, -1):
        prev_line = slope * (i - 1) + intercept
        cur_line = slope * i + intercept
        if closes[i] > cur_line and closes[i - 1] <= prev_line:
            return i
    return None


def detect_trendline_break(df, pivot_window=TRENDLINE_PIVOT_WINDOW,
                            lookback_bars=TRENDLINE_LOOKBACK_BARS,
                            min_span_bars=TRENDLINE_MIN_SPAN_BARS,
                            breakout_lookback=3,
                            touch_tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT,
                            require_volume=False, volume_factor=TRENDLINE_VOLUME_FACTOR,
                            min_touches=TRENDLINE_MIN_TOUCHES,
                            intraday=False):
    """Tek bir sembol için düşen trend çizgisi kırılımını tespit eder.

    df: pozisyonel (0-tabanlı) index'e sahip, 'Date' sütunu olacak şekilde
    reset_index edilmiş bir DataFrame olmalı (run_vwap_chain_scan'in
    ürettiği df ile aynı formatta — bkz. fetch_and_scan).

    require_volume=True ise, kırılım barının hacmi son 20 barın ortalama
    hacminin en az `volume_factor` katı DEĞİLSE eşleşme sayılmaz (hacim
    teyidi olmayan kırılımlar elenir — yatırımcıların genelde aradığı
    "hacimli kırılım" şartı).

    min_touches: çizgiye (x1..x2 aralığında, touch_tolerance_pct içinde)
    değen bar sayısı bu değerin ALTINDAYSA eşleşme sayılmaz. Çizgiyi
    kuran iki pivot (x1, x2) tolerans içinde zaten çizginin üzerinde
    olduğundan touches HER ZAMAN >= 2'dir — kullanıcının "tepe = 1. temas,
    bir sonraki pivot = 2. temas" diye düşündüğü noktalar bunlardır;
    min_touches=3/4 gibi yükseltilirse ARADA da (iki pivot dışında) en az
    o kadar ek bar çizgiye değmiş olması, yani çizginin "daha çok test
    edilmiş/kanıtlanmış" bir direnç olması şart koşulur.

    Dönüş: None (çizgi kurulamadı ya da taze bir kırılım yok) ya da
      {"matched": True, "line": {...}, "touches": int, "cross_idx": int,
       "cross_date": str, "bars_ago": int, "last_close": float,
       "line_value_now": float, "start_date": str, "end_date": str,
       "volume_confirmed": bool|None}
    """
    if df is None or len(df) < min_span_bars + 2 * pivot_window + 5:
        return None

    line = detect_descending_trendline(df, pivot_window=pivot_window,
                                        lookback_bars=lookback_bars,
                                        min_span_bars=min_span_bars)
    if line is None:
        return None

    cross_idx = find_trendline_crossover(df, line, lookback=breakout_lookback)
    if cross_idx is None:
        return None

    # Kırılım taze olsa bile fiyat şu anda yeniden çizginin altına döndüyse
    # bunu aktif trend kırılımı olarak raporlama.
    n = len(df)
    line_now = line["slope"] * (n - 1) + line["intercept"]
    if float(df["Close"].iloc[-1]) <= line_now:
        return None

    touches = _count_trendline_touches(df, line, tolerance_pct=touch_tolerance_pct)
    if touches < min_touches:
        return None  # istenen minimum temas sayısını karşılamıyor

    volume_confirmed = None
    if require_volume:
        vol = df["Volume"].fillna(0).astype(float)
        lookback_avg = vol.iloc[max(0, cross_idx - 20):cross_idx].mean()
        breakout_vol = float(vol.iloc[cross_idx])
        volume_confirmed = bool(lookback_avg > 0 and breakout_vol >= lookback_avg * volume_factor)
        if not volume_confirmed:
            return None  # hacim teyidi ŞARTSA ve yoksa eşleşme sayılmaz

    return {
        "matched": True,
        "line": line,
        "touches": touches,
        "cross_idx": cross_idx,
        "cross_date": _fmt_ts(df["Date"].iloc[cross_idx], intraday),
        "bars_ago": n - 1 - cross_idx,
        "last_close": round(float(df["Close"].iloc[-1]), 2),
        "line_value_now": round(line["slope"] * (n - 1) + line["intercept"], 2),
        "start_date": _fmt_ts(df["Date"].iloc[line["x1"]], intraday),
        "end_date": _fmt_ts(df["Date"].iloc[line["x2"]], intraday),
        "volume_confirmed": volume_confirmed,
    }


def detect_triangle_lines(df, pivot_window=TRIANGLE_PIVOT_WINDOW,
                           lookback_bars=TRIANGLE_LOOKBACK_BARS,
                           min_span_bars=TRIANGLE_MIN_SPAN_BARS):
    """Son `lookback_bars` bar içindeki pivot TEPE ve pivot DİP
    noktalarından, hâlâ YAKINSAYAN (direnç eğimi < destek eğimi) EN
    GÜNCEL çizgi çiftini "aktif üçgen" olarak döndürür.

    Dönüş: None (yeterli pivot yok / çizgiler yakınsamıyor / çok kısa)
    ya da {"upper": {...}, "lower": {...}, "apex_x": float, "apex_y": float}
    — upper/lower, detect_descending_trendline()'ın döndürdüğüyle aynı
    formatta ({"x1","y1","x2","y2","slope","intercept"}); apex_x/apex_y,
    iki çizginin kesişeceği (henüz gelmiş ya da gelmemiş) noktadır.
    """
    n = len(df)
    if n < min_span_bars + 2 * pivot_window + 1:
        return None

    zone_start = max(0, n - lookback_bars)
    highs = df["High"].to_numpy(dtype=float)
    lows = df["Low"].to_numpy(dtype=float)

    piv_highs = [i for i in find_pivot_highs(df, window=pivot_window) if i >= zone_start]
    piv_lows = [i for i in find_pivot_lows(df, window=pivot_window) if i >= zone_start]
    if len(piv_highs) < 2 or len(piv_lows) < 2:
        return None

    upper_hull = _upper_hull([(i, highs[i]) for i in piv_highs])
    lower_hull = _lower_hull([(i, lows[i]) for i in piv_lows])
    if len(upper_hull) < 2 or len(lower_hull) < 2:
        return None

    (ux1, uy1), (ux2, uy2) = upper_hull[-2], upper_hull[-1]
    (lx1, ly1), (lx2, ly2) = lower_hull[-2], lower_hull[-1]
    if ux2 - ux1 < min_span_bars or lx2 - lx1 < min_span_bars:
        return None

    upper_slope = (uy2 - uy1) / (ux2 - ux1)
    lower_slope = (ly2 - ly1) / (lx2 - lx1)
    if upper_slope >= lower_slope:
        return None  # paralel ya da ıraksıyor -> yakınsayan bir üçgen yok

    upper_intercept = uy1 - upper_slope * ux1
    lower_intercept = ly1 - lower_slope * lx1
    denom = upper_slope - lower_slope
    apex_x = (lower_intercept - upper_intercept) / denom
    apex_y = upper_slope * apex_x + upper_intercept
    if not (math.isfinite(apex_x) and math.isfinite(apex_y)):
        return None  # neredeyse paralel çizgiler -> kesişim noktası güvenilmez

    return {
        "upper": {"x1": int(ux1), "y1": float(uy1), "x2": int(ux2), "y2": float(uy2),
                  "slope": float(upper_slope), "intercept": float(upper_intercept)},
        "lower": {"x1": int(lx1), "y1": float(ly1), "x2": int(lx2), "y2": float(ly2),
                  "slope": float(lower_slope), "intercept": float(lower_intercept)},
        "apex_x": float(apex_x),
        "apex_y": float(apex_y),
    }


def _triangle_pattern_type(upper_slope, lower_slope, avg_price):
    """Üçgenin görsel tipini etiketler — sadece bilgi amaçlı, filtre
    mantığını etkilemez. Fiyat ölçeğine göre nispi bir tolerans (ortalama
    fiyatın on binde biri / bar) 'yatay' sayılan eğimi belirler."""
    flat_eps = max(avg_price, 1e-6) * 0.0001
    upper_flat = abs(upper_slope) < flat_eps
    lower_flat = abs(lower_slope) < flat_eps
    if upper_flat and lower_slope > 0:
        return "Yükselen Üçgen"
    if lower_flat and upper_slope < 0:
        return "Alçalan Üçgen"
    if upper_slope < 0 and lower_slope > 0:
        return "Simetrik Üçgen"
    return "Yakınsayan Üçgen"


def detect_triangle_break(df, pivot_window=TRIANGLE_PIVOT_WINDOW,
                           lookback_bars=TRIANGLE_LOOKBACK_BARS,
                           min_span_bars=TRIANGLE_MIN_SPAN_BARS,
                           min_apex_bars_ahead=TRIANGLE_MIN_APEX_BARS_AHEAD,
                           max_apex_bars_ahead=TRIANGLE_MAX_APEX_BARS_AHEAD,
                           max_squeeze_pct=TRIANGLE_MAX_SQUEEZE_PCT,
                           intraday=False):
    """Tek bir sembol için "kırılmak ÜZERE olan" üçgeni tespit eder.

    detect_trendline_break()'ten farkı: o fonksiyon çizginin ZATEN
    kırıldığı anı arar; bu fonksiyon fiyatın HÂLÂ iki çizgi arasında
    olduğu, iki çizginin (direnç + destek) yakınsadığı, apex'in yakın bir
    barda geldiği ve üçgenin belirgin şekilde SIKIŞTIĞI durumu arar —
    yani kırılım henüz gerçekleşmemiş ama yakın olan durum.

    min/max_apex_bars_ahead: apex (çizgilerin kesişeceği bar), şu anki
    son bardan en az/en fazla kaç bar ileride olmalı. Apex geçmişte
    kalmışsa (negatifse) üçgen zaten tükenmiş demektir — elenir. Çok
    uzaktaysa (max'ın üstündeyse) henüz erken sayılır — elenir.

    max_squeeze_pct: üçgenin ŞU ANKİ genişliğinin, üçgenin
    BAŞLANGICINDAKİ genişliğine oranı (%) bu eşiğin ÜSTÜNDEYSE henüz
    yeterince sıkışmamış demektir — elenir. Düşük değer = sadece çok
    daralmış (kırılıma yakın) üçgenleri yakalar.

    Dönüş: None (üçgen yok / zaten kırılmış / henüz erken / yeterince
    sıkışmamış) ya da
      {"matched": True, "pattern_type": str, "upper": {...}, "lower": {...},
       "apex_x": float, "apex_y": float, "apex_bars_ahead": float,
       "squeeze_pct": float, "last_close": float, "upper_now": float,
       "lower_now": float, "volume_dryness_pct": float|None,
       "start_date": str, "end_date": str}
    """
    n = len(df)
    if n < min_span_bars + 2 * pivot_window + 5:
        return None

    lines = detect_triangle_lines(df, pivot_window=pivot_window,
                                   lookback_bars=lookback_bars,
                                   min_span_bars=min_span_bars)
    if lines is None:
        return None

    upper, lower = lines["upper"], lines["lower"]
    last_idx = n - 1

    apex_bars_ahead = lines["apex_x"] - last_idx
    if apex_bars_ahead < min_apex_bars_ahead or apex_bars_ahead > max_apex_bars_ahead:
        return None

    upper_now = upper["slope"] * last_idx + upper["intercept"]
    lower_now = lower["slope"] * last_idx + lower["intercept"]
    if upper_now <= lower_now:
        return None  # çizgiler zaten kesişmiş -> geçersiz

    last_close = float(df["Close"].iloc[-1])
    if not (lower_now <= last_close <= upper_now):
        return None  # fiyat zaten üçgenin dışında -> "kırılmak üzere" değil

    start_x = max(upper["x1"], lower["x1"])

    # Yapı boyunca kapanışların üçgen sınırlarını gerçekten koruduğunu doğrula.
    # Ufak veri/fitil sapmaları için %0.6 tolerans verilir; belirgin bir kapanış
    # ihlali varsa desen artık geçerli üçgen sayılmaz.
    structure_tol = 0.006
    close_violations = 0
    wick_violations = 0
    for i in range(start_x, last_idx + 1):
        up = upper["slope"] * i + upper["intercept"]
        lo = lower["slope"] * i + lower["intercept"]
        if up <= lo or lo <= 0:
            return None
        c = float(df["Close"].iloc[i])
        h = float(df["High"].iloc[i])
        l = float(df["Low"].iloc[i])
        if c > up * (1.0 + structure_tol) or c < lo * (1.0 - structure_tol):
            close_violations += 1
        if h > up * (1.0 + structure_tol) or l < lo * (1.0 - structure_tol):
            wick_violations += 1
    if close_violations > 0:
        return None
    # Fitil ihlallerinin az sayıda olması kabul edilir; desenin %15'inden fazlası
    # dışarı taşıyorsa çizgiler fiyat yapısını temsil etmiyor demektir.
    structure_len = max(1, last_idx - start_x + 1)
    if wick_violations / structure_len > 0.15:
        return None

    upper_touches = _count_line_touch_clusters(
        df, upper, price_col="High", tolerance_pct=1.0, min_gap_bars=3,
        x_start=start_x, x_end=last_idx,
    )
    lower_touches = _count_line_touch_clusters(
        df, lower, price_col="Low", tolerance_pct=1.0, min_gap_bars=3,
        x_start=start_x, x_end=last_idx,
    )
    if upper_touches < 2 or lower_touches < 2:
        return None
    start_width = (upper["slope"] * start_x + upper["intercept"]) - \
                  (lower["slope"] * start_x + lower["intercept"])
    now_width = upper_now - lower_now
    if start_width <= 0:
        return None
    squeeze_pct = now_width / start_width * 100.0
    if squeeze_pct > max_squeeze_pct:
        return None  # henüz yeterince daralmamış

    vol = df["Volume"].fillna(0).astype(float)
    volume_dryness_pct = None
    if n >= 20:
        recent_vol = float(vol.iloc[-5:].mean())
        base_vol = float(vol.iloc[-20:].mean())
        if base_vol > 0:
            volume_dryness_pct = round(recent_vol / base_vol * 100.0, 1)

    avg_price = (upper_now + lower_now) / 2.0
    pattern_type = _triangle_pattern_type(upper["slope"], lower["slope"], avg_price)

    return {
        "matched": True,
        "pattern_type": pattern_type,
        "upper": upper,
        "lower": lower,
        "apex_x": lines["apex_x"],
        "apex_y": lines["apex_y"],
        "apex_bars_ahead": round(apex_bars_ahead, 1),
        "squeeze_pct": round(squeeze_pct, 1),
        "last_close": round(last_close, 2),
        "upper_now": round(upper_now, 2),
        "lower_now": round(lower_now, 2),
        "volume_dryness_pct": volume_dryness_pct,
        "upper_touches": upper_touches,
        "lower_touches": lower_touches,
        "wick_violations": wick_violations,
        "start_date": _fmt_ts(df["Date"].iloc[start_x], intraday),
        "end_date": _fmt_ts(df["Date"].iloc[last_idx], intraday),
    }


def run_vwap_chain_scan(df, lookback=3, max_levels=3, intraday=False):
    """
    Zincirleme VWAP taramasını çalıştırır.
    Dönen sözlükte 'chain' listesi, denenen HER seviyenin (eşleşmiş olan dahil)
    tam VWAP serisini içerir — böylece grafik çiziminde tüm zincir gösterilebilir.

    intraday=True ise (örn. "4h" periyodu), anchor_date/cross_date SAAT
    bilgisini de içerir (aksi halde günlük/haftalık/aylık gibi sadece
    tarih string'i üretilir).
    """
    if df is None or len(df) < 10:
        return None

    df = df.copy()
    df.index.name = "Date"
    df = df.reset_index(drop=False)

    anchor_idx, reason = determine_first_anchor(df)
    level = 1
    chain = []

    while level <= max_levels:
        vwap = anchored_vwap_series(df, anchor_idx)
        chain.append({
            "level": level,
            "anchor_idx": anchor_idx,
            "anchor_date": _fmt_ts(df["Date"].iloc[anchor_idx], intraday),
            "anchor_reason": reason if level == 1 else "TEMAS",
            "vwap": vwap,  # pandas Series, df ile aynı index
        })

        cross_idx = find_recent_crossover(df, vwap, anchor_idx, lookback)

        if cross_idx is not None:
            return {
                "matched": True,
                "level": level,
                "df": df,
                "chain": chain,
                "cross_idx": cross_idx,
                "cross_date": _fmt_ts(df["Date"].iloc[cross_idx], intraday),
                "bars_ago": len(df) - 1 - cross_idx,
                "last_close": round(float(df["Close"].iloc[-1]), 2),
                "last_vwap": round(float(vwap.iloc[-1]), 2),
                "anchor_date": chain[-1]["anchor_date"],
                "anchor_reason": chain[-1]["anchor_reason"],
            }

        # DÜZELTME (kullanıcı spesifikasyonu, adım 2/4): bir sonraki VWAP
        # seviyesine SADECE "VWAP hâlâ fiyatın üzerindeyse" (yani fiyat bu
        # VWAP'ı hiç kırmamış, hâlâ altında kapanıyorsa) geçilir. Fiyat bu
        # VWAP'ın zaten üzerindeyse — ama kırılım son `lookback` mumda
        # gerçekleşmediyse (yani "taze" değilse) — bu artık geçerli bir
        # sinyal değildir ve zincire devam etmek anlamsız/yanıltıcı sonraki
        # seviyeler üretir. Böyle durumda hisse elenir.
        last_close = float(df["Close"].iloc[-1])
        last_vwap = float(vwap.iloc[-1])
        if last_close > last_vwap:
            break

        touch_idx = find_last_touch(df, vwap, anchor_idx)
        if touch_idx <= anchor_idx or touch_idx >= len(df) - 1:
            break

        anchor_idx = touch_idx
        level += 1

    return {"matched": False, "df": df, "chain": chain}

# ====================================================================
# RESAMPLE
# ====================================================================

def _istanbul_now():
    return datetime.now(ZoneInfo("Europe/Istanbul"))


def _as_istanbul_naive_index(index):
    """Karışık UTC offsetli CSV tarihlerini de güvenle İstanbul saatine çevirir."""
    parsed = pd.to_datetime(index, utc=True, errors="coerce")
    idx = pd.DatetimeIndex(parsed)
    return idx.tz_convert("Europe/Istanbul").tz_localize(None)


def _drop_incomplete_daily_bar(df, now=None):
    """Piyasa açıkken oluşmakta olan bugünkü günlük mumu çıkarır."""
    if df is None or len(df) == 0:
        return df
    now = now or _istanbul_now()
    out = df.copy()
    local_idx = _as_istanbul_naive_index(out.index)
    if len(local_idx) == 0:
        return out
    last_date = local_idx[-1].date()
    # BIST kapanışından sonra küçük veri gecikmesi payı bırakıyoruz.
    market_closed = now.time() >= dt_time(18, 15)
    if last_date >= now.date() and not market_closed:
        out = out.iloc[:-1]
    return out


def _period_is_closed(period, now=None):
    now = now or _istanbul_now()
    if period == "weekly":
        # Pazartesi-Perşembe kapanmış hafta değildir. Cuma 18:15 sonrası veya
        # hafta sonu o haftanın barı kapanmış kabul edilir.
        if now.weekday() < 4:
            return False
        if now.weekday() == 4:
            return now.time() >= dt_time(18, 15)
        return True
    if period == "monthly":
        tomorrow = now.date() + timedelta(days=1)
        is_calendar_month_end = tomorrow.month != now.month
        return is_calendar_month_end and now.time() >= dt_time(18, 15)
    return True


def resample_ohlcv(df, period, closed_only=True):
    """Günlük veriyi gerçek son işlem tarihini koruyarak haftalık/aylığa çevirir.

    pandas'ın W-FRI/ME etiketi geçmişte henüz gelmemiş cuma/ay-sonu tarihini
    gösterebiliyordu. Burada indeks her grubun GERÇEK son işlem günüdür.
    Varsayılan `closed_only=True` ile devam eden gün/hafta/ay sinyale katılmaz.
    """
    if df is None or len(df) == 0:
        return df
    source = _drop_incomplete_daily_bar(df) if closed_only else df.copy()
    if period == "daily":
        return source
    if period not in {"weekly", "monthly"}:
        raise ValueError(f"Desteklenmeyen periyot: {period}")
    if len(source) == 0:
        return source

    naive_dates = _as_istanbul_naive_index(source.index)
    if period == "weekly":
        keys = naive_dates.to_period("W-FRI")
    else:
        keys = naive_dates.to_period("M")

    work = source.copy()
    work["__period_key"] = keys.astype(str)
    work["__real_date"] = naive_dates
    grouped = work.groupby("__period_key", sort=True)
    out = grouped.agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum", "__real_date": "last",
    }).dropna(subset=["Open", "High", "Low", "Close"])
    out = out.set_index("__real_date")
    out.index.name = df.index.name or "Date"

    if closed_only and len(out) and not _period_is_closed(period):
        # Son grup mevcut hafta/ay ise henüz tamamlanmamıştır.
        current_key = (pd.Period(_istanbul_now().replace(tzinfo=None), freq="W-FRI")
                       if period == "weekly" else
                       pd.Period(_istanbul_now().replace(tzinfo=None), freq="M"))
        last_key = pd.Period(naive_dates[-1], freq="W-FRI" if period == "weekly" else "M")
        if last_key == current_key:
            out = out.iloc[:-1]
    return out


def _drop_incomplete_intraday_bars(intraday_df, bar_minutes=60, now=None):
    """Saatlik veri içinde henüz kapanmamış son barı güvenli tarafta kalarak atar."""
    if intraday_df is None or len(intraday_df) == 0:
        return intraday_df
    now = now or _istanbul_now()
    out = intraday_df.copy()
    local_idx = _as_istanbul_naive_index(out.index)
    now_naive = now.replace(tzinfo=None)
    keep = [ts + timedelta(minutes=bar_minutes) <= now_naive for ts in local_idx]
    return out.loc[keep]


def resample_intraday_to_4h(intraday_df, closed_only=True):
    """Yahoo 60m BIST barlarını seans içindeki ardışık 4 TAM bar halinde birleştirir.

    Gece yarısına hizalı `resample("4h")` yerine her işlem günündeki bar sırası
    kullanılır. Yahoo'nun 09:30 sıfır-hacimli açılış placeholder'ı varsa çıkarılır.
    Uygulama yüzlerce sembolde çalışacağı için bütün işlem groupby/cumcount ile
    vektörize edilmiştir; gün/chunk başına Python döngüsü yoktur.
    """
    if intraday_df is None or len(intraday_df) == 0:
        return intraday_df
    source = _drop_incomplete_intraday_bars(intraday_df, 60) if closed_only else intraday_df.copy()
    if len(source) == 0:
        return source

    local_idx = _as_istanbul_naive_index(source.index)
    work = source[["Open", "High", "Low", "Close", "Volume"]].copy()
    work["__local_ts"] = local_idx
    work["__day"] = pd.Series(local_idx.date, index=work.index).values
    work["__seq0"] = work.groupby("__day", sort=False).cumcount()

    # Günün ilk barı 10:00'dan önce ve hacmi sıfırsa Yahoo placeholder'ı say.
    vol = work["Volume"].fillna(0).astype(float)
    before_ten = work["__local_ts"].dt.time < dt_time(10, 0)
    placeholder = (work["__seq0"] == 0) & before_ten & (vol == 0.0)
    work = work.loc[~placeholder].copy()
    if len(work) == 0:
        return source.iloc[0:0][["Open", "High", "Low", "Close", "Volume"]]

    work["__seq"] = work.groupby("__day", sort=False).cumcount()
    work["__block"] = (work["__seq"] // 4).astype(int)
    keys = [work["__day"], work["__block"]]

    if closed_only:
        sizes = work.groupby(keys, sort=False)["Close"].transform("size")
        work = work.loc[sizes >= 4].copy()
        if len(work) == 0:
            return source.iloc[0:0][["Open", "High", "Low", "Close", "Volume"]]
        keys = [work["__day"], work["__block"]]

    out = work.groupby(keys, sort=True).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
        __label=("__local_ts", "first"),
    )
    out = out.set_index("__label")
    out.index = pd.DatetimeIndex(out.index, name=source.index.name or "Datetime")
    return out[["Open", "High", "Low", "Close", "Volume"]]


# ====================================================================
# YATAY (SIDEWAYS / KONSOLİDASYON) TESPİTİ
# ====================================================================
# VWAP zincirinden TAMAMEN BAĞIMSIZ, ek bir filtre: hissenin son N ayda
# (varsayılan 6) dar bir bantta, güçlü bir trend olmadan yatay hareket
# edip etmediğini tespit eder. Her zaman GÜNLÜK veri üzerinden hesaplanır
# (seçilen tarama periyodundan — haftalık/aylık — bağımsız), çünkü
# "yataylık" günlük kapanışlardaki gerçek dalgalanmayı yansıtmalı.

SIDEWAYS_METHODS = ("range", "atr", "both")

# Kullanıcının birden fazla vadede ("3-6-12-18-24 ay gibi") aynı anda
# yataylık kontrolü yapabilmesi için varsayılan pencere listesi.
DEFAULT_SIDEWAYS_MONTHS = [3, 6, 12, 18, 24]


def _true_range(df):
    """Klasik True Range: bugünün High-Low'u ile dünkü kapanışa olan
    mesafenin en büyüğü (gap'leri de hesaba katar)."""
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def detect_sideways(daily_df, months=6, range_pct=15.0, atr_pct=5.0, method="range", since=None):
    """
    Son `months` aylık GÜNLÜK veriye bakarak hissenin yatay/konsolidasyon
    bölgesinde olup olmadığını tespit eder.

    since: (opsiyonel) VWAP zincirinin İLK ATANDIĞI tarih (ATH ya da IPO —
    run_vwap_chain_scan()'in chain[0]["anchor_date"] alanı). Verilirse,
    pencere SADECE bu tarihten SONRAKİ barları kapsar — çünkü anchor
    öncesi fiyat hareketi FARKLI bir rejime (VWAP'ın hiç izlemediği bir
    döneme) ait olduğundan, onu yataylık hesabına karıştırmak yanıltıcı
    olur (örn. ATH'den 2 ay önce anchor atanmışsa, "24 aylık yataylık"
    aslında büyük ölçüde ANCHOR ÖNCESİ bir dönemi ölçmüş olurdu).
    `months` penceresinin gerektirdiği başlangıç tarihi `since`'ten daha
    ESKİYE gidiyorsa (yani VWAP o kadar eski değilse), bu pencere için
    GEÇERLİ veri yoktur ve None döner — o vade bu hisse için değerlendirmeye
    alınmaz (yanlışlıkla "yatay" ya da "yatay değil" diye işaretlenmez).

    method:
      "range" -> (dönem içi en yüksek - en düşük) / dönem ortalama kapanış
                 yüzdesi `range_pct` eşiğinin ALTINDAYSA yatay sayılır.
      "atr"   -> ATR(14)'ün pencere ortalaması / dönem ortalama kapanış
                 yüzdesi `atr_pct` eşiğinin ALTINDAYSA yatay sayılır
                 (günlük oynaklık düşük). NOT: bu yöntem sadece GÜN İÇİ
                 gürültüyü ölçer, toplam trend/yol katetmeyi YOK SAYAR —
                 yavaş ama istikrarlı trend eden hisseler yanlışlıkla
                 "yatay" çıkabilir; gerçek dar-bant tespiti için "range"
                 ya da "both" tercih edilmeli.
      "both"  -> her iki koşul da sağlanmalı (daha katı, daha az yanlış
                 pozitif; kullanıcı arayüzünden seçilebilir).

    Dönüş: {"is_sideways": bool, "range_pct": float, "atr_pct": float|None,
            "months": months} — yetersiz/geçersiz veri varsa None.
    """
    if daily_df is None or len(daily_df) < 20:
        return None

    cutoff = daily_df.index[-1] - pd.DateOffset(months=months)

    if since is not None:
        since_ts = pd.Timestamp(since)
        # DÜZELTME: daily_df.index yfinance'ten tz-aware (örn. Europe/Istanbul)
        # geliyor, ama `since` (anchor tarihi) düz bir tarih string'inden
        # geldiği için tz-naive bir Timestamp'e dönüşüyordu. tz-aware ile
        # tz-naive Timestamp'leri karşılaştırmak "Cannot compare tz-naive
        # and tz-aware timestamps" hatasına yol açıyordu (sadece since-gating
        # yolu çalıştığında, bu yüzden bazı sembollerde görülüyordu). since_ts'i
        # cutoff ile aynı tz durumuna getiriyoruz.
        if cutoff.tzinfo is not None and since_ts.tzinfo is None:
            since_ts = since_ts.tz_localize(cutoff.tzinfo)
        elif cutoff.tzinfo is None and since_ts.tzinfo is not None:
            since_ts = since_ts.tz_localize(None)
        if cutoff < since_ts:
            # Bu vade (örn. 24 ay), VWAP'ın anchor tarihinden daha eskiye
            # gitmeyi gerektiriyor — anchor o kadar eski değil, dolayısıyla
            # bu pencere GEÇERSİZ (anchor öncesi veriye hiç bakmıyoruz).
            return None

    window = daily_df[daily_df.index >= cutoff]
    if len(window) < max(10, months * 8):  # ayda ortalama en az ~8 işlem günü olsun
        return None

    avg_close = float(window["Close"].mean())
    if avg_close <= 0:
        return None

    actual_range_pct = (float(window["High"].max()) - float(window["Low"].min())) / avg_close * 100.0

    # DÜZELTME (kök neden — kullanıcı raporu): eski sürüm ATR için sadece
    # rolling(14) serisinin EN SON değerini alıyordu (atr_series.iloc[-1]).
    # Bu, pencerenin uzunluğundan (3 ay mı 24 ay mı) BAĞIMSIZ olarak HER ZAMAN
    # sadece son ~14 GÜNÜ ölçüyordu — yani 12 ay, 18 ay ve 24 ay pencereleri
    # aslında AYNI son-14-gün ATR değerini üretiyordu. Sonuç: bir hisse aylarca
    # güçlü bir trend/iniş-çıkış yaşamış olsa bile, sadece SON 2 HAFTASI sakin
    # geçtiyse tüm uzun vadeler yanlışlıkla "yatay" işaretleniyordu (örn. YATAS —
    # aylarca 25->48->30 gibi sert bir hareket, ama son birkaç hafta durulmuş;
    # eski kod bunu "12/18/24 ayda da yatay" diye raporluyordu, oysa range_pct
    # zaten %40+ göstererek asıl gerçeği ortaya koyuyordu).
    #
    # Düzeltme: ATR'yi pencerenin SON gününde değil, pencerenin TAMAMI
    # boyunca ORTALAMASINI alarak hesaplıyoruz — böylece uzun bir pencere,
    # o pencerenin GERÇEKTEN tüm süresindeki günlük oynaklığı yansıtır ve
    # farklı vadeler (12/18/24 ay) artık birbirinden farklı, anlamlı sonuçlar
    # üretir.
    tr = _true_range(window)
    atr_series = tr.rolling(14, min_periods=5).mean()
    atr_valid = atr_series.dropna()
    atr = float(atr_valid.mean()) if len(atr_valid) > 0 else float("nan")
    actual_atr_pct = (atr / avg_close * 100.0) if pd.notna(atr) else None

    if method == "atr":
        is_sideways = actual_atr_pct is not None and actual_atr_pct <= atr_pct
    elif method == "both":
        is_sideways = (actual_range_pct <= range_pct) and \
                      (actual_atr_pct is not None and actual_atr_pct <= atr_pct)
    else:  # "range" (varsayılan)
        is_sideways = actual_range_pct <= range_pct

    return {
        "is_sideways": bool(is_sideways),
        "range_pct": round(actual_range_pct, 1),
        "atr_pct": round(actual_atr_pct, 1) if actual_atr_pct is not None else None,
        "months": months,
    }


def detect_sideways_multi(daily_df, months_list, range_pct=15.0, atr_pct=5.0, method="range", since=None):
    """detect_sideways()'i BİRDEN FAZLA vade (ay) penceresi için ayrı ayrı
    çalıştırır — kullanıcı "3 ay, 6 ay, 12 ay, 18 ay, 24 ay gibi vadelerde
    yatay gitmiş" hisseleri aramak istediğinde kullanılır.

    since: VWAP'ın ilk anchor tarihi (ATH/IPO) — verilirse HER vade sadece
    bu tarihten sonrasına bakar (bkz. detect_sideways).

    Dönüş: {ay: detect_sideways() sonucu (dict) ya da None (yetersiz/geçersiz veri)}
    — months_list içindeki her ay için bir kayıt, sırası korunur.
    """
    return {m: detect_sideways(daily_df, months=m, range_pct=range_pct,
                                atr_pct=atr_pct, method=method, since=since)
            for m in months_list}


def summarize_sideways_multi(multi_result, min_windows=None):
    """detect_sideways_multi() çıktısını TEK bir "yatay mı?" kararına indirger.

    min_windows: hissenin "yatay" sayılması için EN AZ kaç penceride
    (ay) yataylık şartını sağlaması gerektiği.
        - None (varsayılan) -> TÜM pencerelerde yatay olmalı (en katı —
          "3 ay da, 6 ay da, 12 ay da... hepsinde yatay gitmiş" isteğine
          karşılık gelir).
        - Bir sayı (örn. 3) -> en az o kadar pencerede yatay olması yeterli.
    Veri yetersizliği yüzünden hesaplanamayan (None) pencereler "yatay
    değil" kabul edilir — eksik veriyle hisseyi yanlışlıkla "yatay" diye
    işaretlememek için.
    """
    windows = list(multi_result.keys())
    total = len(windows)
    if min_windows is None:
        min_windows = total

    sideways_windows = [m for m, r in multi_result.items() if r is not None and r["is_sideways"]]
    missing_windows = [m for m, r in multi_result.items() if r is None]

    return {
        "is_sideways": len(sideways_windows) >= min_windows,
        "sideways_count": len(sideways_windows),
        "total_windows": total,
        "min_required": min_windows,
        "sideways_months": sideways_windows,
        "missing_months": missing_windows,
        "details": multi_result,  # {ay: {"is_sideways":..,"range_pct":..,"atr_pct":..,"months":..} | None}
    }


# ====================================================================
# VERİ ÇEKME
# ====================================================================

def _fetch_via_ticker(symbol, period):
    """
    yf.Ticker(...).history() ile tek sembol çeker. yf.download()'un aksine
    Yahoo cevaplarını içeride sembol bazlı bir sözlükte gruplamaz, bu yüzden
    "KeyError: '<SEMBOL>'" hatasına yol açan iç mekanizmayı devreye sokmaz.
    Bu artık birincil yöntem.
    """
    t = yf.Ticker(symbol)
    df = t.history(period=period, interval="1d", auto_adjust=False)
    return df


def _fetch_via_download(symbol, period):
    """Yedek yöntem: eski yf.download() çağrısı."""
    df = yf.download(symbol, period=period, interval="1d",
                      progress=False, auto_adjust=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_history(symbol, retries=2, period="max"):
    """
    Tek bir sembol için Yahoo Finance'ten veri çeker (önbelleksiz, ham çağrı).
    Önce yf.Ticker().history() dener (daha az hataya açık), o başarısız olursa
    yf.download() ile yedek dener.
    Dönüş: (df, hata_mesaji). Başarılıysa df dolu, hata_mesaji None.
    Başarısızsa df None, hata_mesaji tüm denemelerdeki son hatayı açıklar.
    """
    last_error = None
    for attempt in range(retries + 1):
        for fetch_fn in (_fetch_via_ticker, _fetch_via_download):
            try:
                df = fetch_fn(symbol, period)
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.dropna(subset=["Open", "High", "Low", "Close"])
                if len(df) == 0:
                    last_error = "Yahoo Finance boş veri döndürdü (sembol yanlış olabilir ya da veri yok)"
                    continue
                return df, None
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
        if attempt < retries:
            time.sleep(1.5)
    return None, last_error
    return None, last_error


def _cache_path(symbol, period="max"):
    safe_period = str(period).replace("/", "_").replace(" ", "_")
    suffix = "" if safe_period == "5y" else f"_{safe_period}"
    fname = symbol.replace(".", "_") + suffix + ".csv"
    return os.path.join(CACHE_DIR, fname)


def _load_from_cache(symbol, period="max"):
    """Aynı gün ve aynı geçmiş kapsamı için oluşturulmuş önbelleği okur."""
    path = _cache_path(symbol, period)
    if not os.path.exists(path):
        return None
    mtime = date.fromtimestamp(os.path.getmtime(path))
    if mtime != date.today():
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) == 0:
            return None
        return df
    except Exception:
        return None


def _save_to_cache(symbol, df, period="max"):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        df.to_csv(_cache_path(symbol, period))
    except Exception:
        pass


def fetch_history_cached(symbol, period="max", use_cache=True):
    """Tam geçmişi (varsayılan `max`) kapsam bilgisiyle ayrı cache dosyasında tutar.

    Böylece eski 5 yıllık cache yanlışlıkla 'gerçek IPO/ATH geçmişi' diye yeniden
    kullanılmaz. İlk çalıştırma daha uzun olabilir; sonraki taramalar aynı gün cache'ten gelir.
    """
    if use_cache:
        cached = _load_from_cache(symbol, period)
        if cached is not None:
            return cached, None

    df, error = fetch_history(symbol, period=period)
    if df is not None and use_cache:
        _save_to_cache(symbol, df, period)
    return df, error


# --- Gün-içi (intraday) veri çekme — "4h" gibi periyotlar için --------
# Günlük veriden AYRI bir mekanizma: farklı bir yfinance interval'i
# ("60m") ve taramaya göre daha kısa Yahoo geçmiş penceresi kullanır, bu yüzden
# ayrı bir önbellek dosyasına (sembol + interval'e göre adlandırılmış)
# yazılır — günlük önbellekle KARIŞMAZ/ÇAKIŞMAZ.

def _throttle_intraday_request():
    """Yahoo'ya gün-içi istekleri kısa aralıklarla göndererek ani istek patlamasını önler."""
    global _INTRADAY_LAST_REQUEST_TS
    with _INTRADAY_REQUEST_LOCK:
        now = time.monotonic()
        wait_for = INTRADAY_REQUEST_MIN_GAP_SECONDS - (now - _INTRADAY_LAST_REQUEST_TS)
        if wait_for > 0:
            time.sleep(wait_for)
        _INTRADAY_LAST_REQUEST_TS = time.monotonic()


def _fetch_via_ticker_intraday(symbol, yf_period, yf_interval):
    _throttle_intraday_request()
    t = yf.Ticker(symbol)
    return t.history(period=yf_period, interval=yf_interval, auto_adjust=False)


def _fetch_via_download_intraday(symbol, yf_period, yf_interval):
    """Ticker.history boş dönerse aynı veriyi yfinance download yolu ile dener."""
    _throttle_intraday_request()
    df = yf.download(
        symbol, period=yf_period, interval=yf_interval,
        progress=False, auto_adjust=False, threads=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _intraday_period_candidates(yf_period):
    """İstenen pencere başarısızsa daha kısa ama tarama için yeterli pencereye düşer."""
    mapping = {
        "730d": ["730d", "1y", "6mo"],
        "2y": ["1y", "6mo"],
        "1y": ["1y", "6mo"],
        "6mo": ["6mo", "3mo"],
        "3mo": ["3mo", "1mo"],
    }
    values = mapping.get(str(yf_period), [str(yf_period)])
    out = []
    for value in values:
        if value not in out:
            out.append(value)
    return out


def _clean_intraday_df(df):
    if df is None:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    required = ["Open", "High", "Low", "Close"]
    if not all(col in df.columns for col in required):
        return None
    df = df.dropna(subset=required)
    return df if len(df) else None


def _is_rate_limit_error(message):
    text = str(message or "").lower()
    tokens = ("rate limit", "too many requests", "429", "timed out", "timeout", "connection", "crumb")
    return any(token in text for token in tokens)


def fetch_intraday_history(symbol, yf_interval, yf_period, retries=2):
    """Yahoo Finance'ten HAM gün-içi barları dayanıklı biçimde çeker.

    - Önce istenen pencereyi dener.
    - Ticker.history boş/başarısızsa yf.download yolunu dener.
    - Gerekirse daha kısa pencereye düşer.
    - Başarısız pencere turları arasında kademeli bekler.

    Böylece geçici Yahoo rate-limit/boş-DataFrame durumu doğrudan
    "sembol yanlış" diye raporlanmaz.
    """
    last_error = None
    saw_empty = False
    saw_rate_limit = False
    candidates = _intraday_period_candidates(yf_period)
    backoffs = [2.0, 6.0, 12.0]

    for idx, candidate in enumerate(candidates):
        for fetch_fn in (_fetch_via_ticker_intraday, _fetch_via_download_intraday):
            try:
                df = _clean_intraday_df(fetch_fn(symbol, candidate, yf_interval))
                if df is not None:
                    # Hangi pencerenin başarılı olduğunu tanı amaçlı sakla.
                    df.attrs["yf_period_used"] = candidate
                    return df, None
                saw_empty = True
                last_error = f"Yahoo {candidate}/{yf_interval} için boş gün-içi veri döndürdü"
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                last_error = msg
                saw_rate_limit = saw_rate_limit or _is_rate_limit_error(msg)

        if idx < len(candidates) - 1:
            delay = backoffs[min(idx, len(backoffs) - 1)] + random.uniform(0.15, 0.75)
            time.sleep(delay)

    attempted = ", ".join(candidates)
    if saw_rate_limit:
        return None, (
            f"Yahoo geçici istek sınırı/bağlantı sorunu nedeniyle gün-içi veri alınamadı "
            f"({yf_interval}; denenen dönemler: {attempted}). Bir sonraki taramada tekrar denenecek."
        )
    if saw_empty:
        return None, (
            f"Yahoo gün-içi veriyi geçici olarak boş döndürdü "
            f"({yf_interval}; denenen dönemler: {attempted}). Bu mesaj sembolün yanlış olduğu anlamına gelmez."
        )
    return None, last_error or f"Yahoo gün-içi veri alınamadı ({yf_interval}; {attempted})"


def _intraday_cache_path(symbol, yf_interval, yf_period=None):
    period_part = f"_{str(yf_period).replace('/', '_')}" if yf_period else ""
    fname = f"{symbol.replace('.', '_')}_{yf_interval}{period_part}.csv"
    return os.path.join(CACHE_DIR, fname)


def _load_intraday_from_cache(symbol, yf_interval, yf_period=None, max_age_minutes=INTRADAY_CACHE_TTL_MINUTES):
    path = _intraday_cache_path(symbol, yf_interval, yf_period)
    if not os.path.exists(path):
        return None
    try:
        age_seconds = max(0.0, time.time() - os.path.getmtime(path))
        if age_seconds > float(max_age_minutes) * 60.0:
            return None
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) == 0:
            return None
        return df
    except Exception:
        return None


def _save_intraday_to_cache(symbol, yf_interval, yf_period, df):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        df.to_csv(_intraday_cache_path(symbol, yf_interval, yf_period))
    except Exception:
        pass


def fetch_intraday_history_cached(symbol, yf_interval, yf_period, use_cache=True):
    """Gün-içi veriyi 45 dakikalık akıllı cache ile kullanır.

    Aynı taramayı kısa süre içinde tekrar çalıştırmak Yahoo'ya yüzlerce yeni
    istek göndermediği için hem daha hızlıdır hem de geçici boş veri riskini azaltır.
    """
    if use_cache:
        cached = _load_intraday_from_cache(symbol, yf_interval, yf_period)
        if cached is not None:
            return cached, None

    df, error = fetch_intraday_history(symbol, yf_interval, yf_period)
    if df is not None and use_cache:
        _save_intraday_to_cache(symbol, yf_interval, yf_period, df)
    return df, error

def _to_naive_normalized_index(index):
    """Karışık yaz/kış UTC offsetlerini güvenle normalize edip tarihe indirger."""
    return _as_istanbul_naive_index(index).normalize()


def fetch_fx_rate_cached(currency, use_cache=True):
    """TRY -> {currency} çevirimi için günlük kur verisini (önbellekli)
    çeker. currency 'TRY' ise çevirime gerek olmadığından (None, None)
    döner. Var olan fetch_history_cached() aynen kullanılır — kur
    sembolleri (örn. 'USDTRY=X') de normal bir yfinance sembolü gibi
    çekilir, ayrı bir veri kaynağı/mantık eklenmez.
    Dönüş: (fx_df, hata_mesaji)."""
    if currency == "TRY":
        return None, None
    fx_symbol = CURRENCY_FX_SYMBOLS.get(currency)
    if fx_symbol is None:
        return None, f"Bilinmeyen para birimi: {currency}"
    return fetch_history_cached(fx_symbol, use_cache=use_cache)


def convert_ohlc_to_currency(daily_df, fx_df):
    """daily_df (TL bazlı günlük OHLCV) içindeki Open/High/Low/Close
    sütunlarını fx_df'nin (örn. USDTRY=X) günlük kapanış kuruna bölerek
    başka bir para birimi bazına çevirir.

    - Volume DEĞİŞMEZ: hacim işlem gören hisse ADEDİdir, para biriminden
      bağımsızdır.
    - Hizalama: BIST ile FX piyasası farklı günlerde/tatillerde işlem
      görebildiği (ve index'leri farklı saat dilimi bilgisine sahip
      olabildiği) için, her iki index de önce tarihe indirgenir, sonra
      BIST'in her işlem gününe EN SON GEÇERLİ kur (ileri doldurma/ffill)
      eşlenir. Kurun henüz başlamadığı (çok eski) tarihler, geçersiz
      çevirim üretmemek için sonuçtan atılır.
    """
    if daily_df is None or len(daily_df) == 0 or fx_df is None or len(fx_df) == 0:
        return None

    df = daily_df.copy()
    df_dates = _to_naive_normalized_index(df.index)

    fx_dates = _to_naive_normalized_index(fx_df.index)
    fx_close = pd.Series(fx_df["Close"].astype(float).values, index=fx_dates)
    fx_close = fx_close[~fx_close.index.duplicated(keep="last")].sort_index()

    fx_aligned = fx_close.reindex(df_dates, method="ffill")
    fx_aligned.index = df.index  # pozisyonel hizalama, orijinal (tz'li) index'e geri koy

    valid = fx_aligned.notna() & (fx_aligned > 0)
    if not valid.any():
        return None

    for col in ("Open", "High", "Low", "Close"):
        df[col] = df[col] / fx_aligned

    return df[valid]


def convert_intraday_ohlc_to_currency(intraday_df, fx_intraday_df):
    """Gün-içi hisse barlarını aynı/önceki gerçek saatlik FX barına göre çevirir.

    Günün kapanış kurunu sabah barlarına uygulayan look-ahead etkisini önler.
    İki piyasanın farklı timezone/offsetleri UTC üzerinde hizalanır ve yalnız
    geçmişteki en son kur ileri doldurulur.
    """
    if intraday_df is None or len(intraday_df) == 0 or fx_intraday_df is None or len(fx_intraday_df) == 0:
        return None
    stock = intraday_df.copy()
    stock_utc = pd.DatetimeIndex(pd.to_datetime(stock.index, utc=True, errors="coerce"))
    fx_utc = pd.DatetimeIndex(pd.to_datetime(fx_intraday_df.index, utc=True, errors="coerce"))
    fx_close = pd.Series(fx_intraday_df["Close"].astype(float).values, index=fx_utc)
    fx_close = fx_close[~fx_close.index.duplicated(keep="last")].sort_index()
    aligned = fx_close.reindex(stock_utc, method="ffill")
    aligned.index = stock.index
    valid = aligned.notna() & (aligned > 0)
    if not valid.any():
        return None
    for col in ("Open", "High", "Low", "Close"):
        stock[col] = stock[col].astype(float) / aligned
    return stock[valid]


def _prep_positional_df(df):
    """resample edilmiş bir df'i, run_vwap_chain_scan'in ürettiğiyle AYNI
    formata (pozisyonel index + 'Date' sütunu) getirir — trend çizgisi
    dedektörünün VWAP eşleşmesinden BAĞIMSIZ çalışabilmesi için (VWAP
    eşleşmediğinde de bu df'e ihtiyaç var).

    Not: 'Date' sütunu pd.to_datetime ile ZORLA datetime tipine çevrilir.
    Normalde index zaten DatetimeIndex'tir, ama önbellekten (CSV) geri
    okunan gün-içi (1h/4h) veride, saat dilimi bilgili tarihlerin bazı
    pandas sürümlerinde METİN (string) olarak kalması ve bunun da
    chart_helpers.py'deki .strftime()/.date() çağrılarında sessizce
    (ve grafiğin hiç açılmamasıyla sonuçlanan) bir hataya yol açması
    ihtimaline karşı bir güvenlik önlemidir."""
    out = df.copy()
    out.index.name = "Date"
    out = out.reset_index(drop=False)
    out["Date"] = _as_istanbul_naive_index(out["Date"])
    return out


def fetch_and_scan(symbol, period, lookback, use_cache=True,
                    sideways_enabled=False, sideways_months_list=None,
                    sideways_range_pct=15.0, sideways_atr_pct=5.0,
                    sideways_method="range", sideways_min_windows=None,
                    drawdown_enabled=False, drawdown_min_pct=60.0,
                    alternation_enabled=False, alternation_min_chain=ALTERNATION_MIN_CHAIN,
                    alternation_min_score=None,
                    trendline_enabled=False, trendline_pivot_window=TRENDLINE_PIVOT_WINDOW,
                    trendline_min_span_bars=TRENDLINE_MIN_SPAN_BARS,
                    trendline_lookback_bars=TRENDLINE_LOOKBACK_BARS,
                    trendline_breakout_lookback=3,
                    trendline_touch_tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT,
                    trendline_require_volume=False, trendline_volume_factor=TRENDLINE_VOLUME_FACTOR,
                    trendline_min_touches=TRENDLINE_MIN_TOUCHES,
                    triangle_enabled=False, triangle_pivot_window=TRIANGLE_PIVOT_WINDOW,
                    triangle_min_span_bars=TRIANGLE_MIN_SPAN_BARS,
                    triangle_lookback_bars=TRIANGLE_LOOKBACK_BARS,
                    triangle_min_apex_bars_ahead=TRIANGLE_MIN_APEX_BARS_AHEAD,
                    triangle_max_apex_bars_ahead=TRIANGLE_MAX_APEX_BARS_AHEAD,
                    triangle_max_squeeze_pct=TRIANGLE_MAX_SQUEEZE_PCT,
                    currency="TRY", fx_df=None, benchmark_df=None):
    """Tek bir sembolü çek + (istenirse USD/EUR bazına çevir) + resample
    et + tara. Paralel ThreadPoolExecutor için tasarlandı.

    currency: 'TRY' (varsayılan, çevirim yok), 'USD' ya da 'EUR'. 'TRY'
    dışındaysa, fx_df (scan_symbols_parallel tarafından TÜM semboller için
    TEK SEFERDE, paylaşılan olarak önceden çekilmiş kur verisi) kullanılarak
    günlük TL serisi taramadan ÖNCE çevrilir. Zincirleme VWAP algoritması,
    yataylık ve zirveden-düşüş filtreleri bu ÇEVRİLMİŞ seri üzerinde,
    aynen TL taramasındaki gibi çalışır — mantık değişmez.

    Yataylık tespiti (etkinse) artık VWAP zincirinin İLK ANCHOR NOKTASINA
    (ATH ya da IPO) BAĞLI olarak hesaplanır: sadece o tarihten SONRAKİ
    fiyat hareketine bakılır. Böylece "hisse yatay gitmiş" derken, aslında
    VWAP'ın hiç görmediği (anchor öncesi) bir dönemi değil, tam olarak
    VWAP'ın İZLEDİĞİ dönemi ölçmüş oluruz — VWAP ATH'den mi IPO'dan mı
    atıldıysa, yataylık da o noktadan itibaren sayılır.

    sideways_months_list: örn. [3, 6, 12, 18, 24] — hisse bu vadelerin
    HER BİRİNDE (ya da sideways_min_windows ile belirlenen en az sayıda)
    ayrı ayrı yataylık şartını sağlıyorsa "yatay" sayılır. Anchor, bir
    vadenin gerektirdiği kadar eski değilse (örn. anchor 2 ay önceyse ve
    vade 24 aysa) o vade o hisse için DEĞERLENDİRİLMEZ (yanlış pozitif/
    negatif üretmemek için)."""
    intraday = period in INTRADAY_PERIODS

    if intraday:
        yf_interval, yf_period = VWAP_INTRADAY_FETCH_SPEC[period]
        raw, error = fetch_intraday_history_cached(symbol, yf_interval, yf_period, use_cache=use_cache)
        if raw is None:
            return {"symbol": symbol, "ok": False, "error": error or "bilinmeyen hata"}

        base = raw
        if currency != "TRY":
            if fx_df is None or len(fx_df) == 0:
                return {"symbol": symbol, "ok": False,
                        "error": f"{currency} kur verisi ({CURRENCY_FX_SYMBOLS.get(currency)}) çekilemediği için sembol atlandı"}
            converted = convert_intraday_ohlc_to_currency(base, fx_df)
            if converted is None or len(converted) == 0:
                return {"symbol": symbol, "ok": False,
                        "error": f"{currency} bazına çevrilecek örtüşen/yeterli veri yok"}
            base = converted

        # Eski VWAP stratejisi oluşmakta olan son bloğu da değerlendirebiliyordu.
        # Seans hizalama hatası düzeltilmiş kalır; strateji filtresi eklenmez.
        df_period = resample_intraday_to_4h(base, closed_only=False)

        daily_for_sideways = None
        if sideways_enabled:
            daily_for_sideways, _sw_err = fetch_history_cached(symbol, period=VWAP_DAILY_HISTORY_PERIOD, use_cache=use_cache)
            if daily_for_sideways is not None and currency != "TRY" and fx_df is not None:
                converted_daily = convert_ohlc_to_currency(daily_for_sideways, fx_df)
                daily_for_sideways = converted_daily if converted_daily is not None else None
    else:
        daily, error = fetch_history_cached(symbol, period=VWAP_DAILY_HISTORY_PERIOD, use_cache=use_cache)
        if daily is None:
            return {"symbol": symbol, "ok": False, "error": error or "bilinmeyen hata"}

        if currency != "TRY":
            if fx_df is None or len(fx_df) == 0:
                return {"symbol": symbol, "ok": False,
                        "error": f"{currency} kur verisi ({CURRENCY_FX_SYMBOLS.get(currency)}) çekilemediği için sembol atlandı"}
            converted = convert_ohlc_to_currency(daily, fx_df)
            if converted is None or len(converted) == 0:
                return {"symbol": symbol, "ok": False,
                        "error": f"{currency} bazına çevrilecek örtüşen/yeterli veri yok"}
            daily = converted

        # İlk paketteki VWAP stratejisi mevcut hafta/ay mumunu da kullanıyordu.
        # Gelecek tarih etiketi hatası düzeltilmiş resampler korunur.
        df_period = resample_ohlcv(daily, period, closed_only=False)
        daily_for_sideways = daily

    # DÜZELTME: lookback artık gerçekten iletiliyor (önceden hep varsayılan 3 kullanılıyordu)
    result = run_vwap_chain_scan(df_period, lookback=lookback, intraday=intraday)

    # Kalite puanı yalnız sonuç/desen bulunduğunda kullanılacak; üst zaman verisi
    # burada tek kez hazırlanır ve dört motor arasında paylaşılır.
    quality_higher_df = None

    sideways_info = None
    if (sideways_enabled and result is not None and result.get("chain")
            and daily_for_sideways is not None and len(daily_for_sideways) > 0):
        # chain[0] HER ZAMAN ilk anchor'dır (ATH ya da IPO) — eşleşme
        # sonradan (level 2/3'te "TEMAS" ile) gerçekleşmiş olsa bile,
        # yataylığı hep bu İLK noktadan itibaren ölçüyoruz.
        first_anchor = result["chain"][0]
        anchor_date = first_anchor["anchor_date"]
        anchor_reason = first_anchor["anchor_reason"]

        months_list = sideways_months_list or DEFAULT_SIDEWAYS_MONTHS
        multi = detect_sideways_multi(
            daily_for_sideways, months_list, range_pct=sideways_range_pct,
            atr_pct=sideways_atr_pct, method=sideways_method,
            since=anchor_date,
        )
        sideways_info = summarize_sideways_multi(multi, min_windows=sideways_min_windows)
        sideways_info["anchor_date"] = anchor_date
        sideways_info["anchor_reason"] = anchor_reason

    drawdown_info = None
    if drawdown_enabled and result is not None and result.get("chain"):
        first_anchor = result["chain"][0]
        drawdown_pct = compute_anchor_drawdown(
            df_period, first_anchor["anchor_idx"], first_anchor["anchor_reason"],
        )
        if drawdown_pct is not None:
            drawdown_info = {
                "is_drawdown": drawdown_pct >= drawdown_min_pct,
                "drawdown_pct": round(drawdown_pct, 1),
                "anchor_date": first_anchor["anchor_date"],
                "anchor_reason": first_anchor["anchor_reason"],
            }

    alternation_info = None
    if alternation_enabled and df_period is not None and len(df_period) > 0:
        alt = detect_candle_alternation(df_period, min_chain=alternation_min_chain)
        if alt is not None:
            meets_score = alternation_min_score is None or alt["score"] >= alternation_min_score
            # Grafik sayfasının ihtiyacı olan start_idx/end_idx + pozisyonel df'yi
            # Tümünü Tara yolunda da koru. V3'te bu alanlar özetlenirken düşürüldüğü
            # için Alternasyon grafiği result["df"] -> KeyError veriyordu.
            alt_pos_df = result["df"] if (result is not None and result.get("df") is not None)                 else _prep_positional_df(df_period)
            alternation_info = dict(alt)
            alternation_info.update({
                "is_alternating": meets_score,
                "start_date": _fmt_ts(df_period.index[alt["start_idx"]], intraday),
                "end_date": _fmt_ts(df_period.index[alt["end_idx"]], intraday),
                "df": alt_pos_df,
                "period": period,
            })
            if meets_score:
                if quality_higher_df is None:
                    quality_higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
                alternation_info["quality"] = compute_upside_quality(
                    df_period, "alternation", alternation_info,
                    benchmark_df=benchmark_df, higher_df=quality_higher_df,
                )

    trendline_info = None
    if trendline_enabled and df_period is not None and len(df_period) > 0:
        # VWAP eşleşmişse zaten pozisyonel/reset_index yapılmış df hazır
        # (result["df"]) — onu tekrar kullan, yoksa kendimiz hazırlayalım.
        pos_df = result["df"] if (result is not None and result.get("df") is not None) \
            else _prep_positional_df(df_period)
        trendline_info = detect_trendline_break(
            pos_df, pivot_window=trendline_pivot_window,
            lookback_bars=trendline_lookback_bars,
            min_span_bars=trendline_min_span_bars,
            breakout_lookback=trendline_breakout_lookback,
            touch_tolerance_pct=trendline_touch_tolerance_pct,
            require_volume=trendline_require_volume,
            volume_factor=trendline_volume_factor,
            min_touches=trendline_min_touches,
            intraday=intraday,
        )
        if trendline_info and trendline_info.get("matched"):
            # Grafik için çizgi bilgisine ek olarak mum verisini de koru.
            # Tümünü Tara sonuçları V3'te df/line/cross_idx alanlarını kırptığı
            # için sonuç satırı açılırken KeyError oluşuyordu.
            trendline_info["df"] = pos_df
            trendline_info["period"] = period
            if quality_higher_df is None:
                quality_higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
            trendline_info["quality"] = compute_upside_quality(
                pos_df, "trendline", trendline_info, benchmark_df=benchmark_df,
                higher_df=quality_higher_df,
            )

    triangle_info = None
    if triangle_enabled and df_period is not None and len(df_period) > 0:
        pos_df = result["df"] if (result is not None and result.get("df") is not None) \
            else _prep_positional_df(df_period)
        triangle_info = detect_triangle_break(
            pos_df, pivot_window=triangle_pivot_window,
            lookback_bars=triangle_lookback_bars,
            min_span_bars=triangle_min_span_bars,
            min_apex_bars_ahead=triangle_min_apex_bars_ahead,
            max_apex_bars_ahead=triangle_max_apex_bars_ahead,
            max_squeeze_pct=triangle_max_squeeze_pct,
            intraday=intraday,
        )
        if triangle_info:
            triangle_info["df"] = pos_df
            if quality_higher_df is None:
                quality_higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
            triangle_info["quality"] = compute_upside_quality(
                pos_df, "triangle", triangle_info, benchmark_df=benchmark_df,
                higher_df=quality_higher_df,
            )

    if result and result.get("matched"):
        if quality_higher_df is None:
            quality_higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
        result["quality"] = compute_upside_quality(
            result.get("df", df_period), "vwap", result, benchmark_df=benchmark_df,
            higher_df=quality_higher_df,
        )
        result["symbol"] = symbol.replace(".IS", "")
        result["period"] = period
        result["currency"] = currency
        price_digits = 2 if currency == "TRY" else 4
        result["last_close"] = round(float(result["df"]["Close"].iloc[-1]), price_digits)
        result["last_vwap"] = round(float(result["chain"][-1]["vwap"].iloc[-1]), price_digits)
        result["sideways"] = sideways_info
        result["drawdown"] = drawdown_info
        result["alternation"] = alternation_info
        result["trendline"] = trendline_info
        result["triangle"] = triangle_info
        return {"symbol": symbol, "ok": True, "matched": True, "result": result,
                "sideways": sideways_info, "drawdown": drawdown_info,
                "alternation": alternation_info, "trendline": trendline_info,
                "triangle": triangle_info}
    return {"symbol": symbol, "ok": True, "matched": False,
            "sideways": sideways_info, "drawdown": drawdown_info,
            "alternation": alternation_info, "trendline": trendline_info,
            "triangle": triangle_info}


def scan_symbols_parallel(symbols, period, lookback=3, max_workers=20,
                           use_cache=True, progress_callback=None, errors_out=None,
                           sideways_enabled=False, sideways_months_list=None,
                           sideways_range_pct=15.0, sideways_atr_pct=5.0,
                           sideways_method="range", sideways_min_windows=None,
                           drawdown_enabled=False, drawdown_min_pct=60.0,
                           alternation_enabled=False, alternation_min_chain=ALTERNATION_MIN_CHAIN,
                           alternation_min_score=None,
                           trendline_enabled=False, trendline_pivot_window=TRENDLINE_PIVOT_WINDOW,
                           trendline_min_span_bars=TRENDLINE_MIN_SPAN_BARS,
                           trendline_lookback_bars=TRENDLINE_LOOKBACK_BARS,
                           trendline_breakout_lookback=3,
                           trendline_touch_tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT,
                           trendline_require_volume=False, trendline_volume_factor=TRENDLINE_VOLUME_FACTOR,
                           trendline_min_touches=TRENDLINE_MIN_TOUCHES,
                           triangle_enabled=False, triangle_pivot_window=TRIANGLE_PIVOT_WINDOW,
                           triangle_min_span_bars=TRIANGLE_MIN_SPAN_BARS,
                           triangle_lookback_bars=TRIANGLE_LOOKBACK_BARS,
                           triangle_min_apex_bars_ahead=TRIANGLE_MIN_APEX_BARS_AHEAD,
                           triangle_max_apex_bars_ahead=TRIANGLE_MAX_APEX_BARS_AHEAD,
                           triangle_max_squeeze_pct=TRIANGLE_MAX_SQUEEZE_PCT,
                           currency="TRY"):
    """
    Sembol listesini PARALEL tarar (600 hisse için önerilen yöntem).

    currency='USD' ya da 'EUR' verilirse, TÜM semboller taranmadan ÖNCE
    ilgili kur (USDTRY=X/EURTRY=X) BİR KEZ çekilir ve her sembolün günlük
    TL fiyatı, kendi thread'inde bu paylaşılan kura bölünerek çevrilir —
    kur verisi sembol başına tekrar tekrar çekilmez.
    progress_callback(tamamlanan, toplam, sembol) her sembol bitince çağrılır.
    errors_out verilirse (boş bir liste), veri çekilemeyen semboller ve hata
    mesajları buraya (sembol, hata_mesaji) çiftleri olarak eklenir — böylece
    çağıran taraf "kırılım yok" ile "veri hiç çekilemedi" durumunu ayırt edebilir.

    sideways_enabled=True ise, VWAP eşleşmesinden BAĞIMSIZ olarak, sideways_months_list
    içindeki HER vadede (örn. 3-6-12-18-24 ay) ayrı ayrı yataylık kontrolü yapılır.
    sideways_min_windows ile "en az kaç vadede yatay olsun" ayarlanabilir (None = hepsinde).

    drawdown_enabled=True ise, YİNE VWAP eşleşmesinden BAĞIMSIZ olarak, hissenin
    VWAP'ın atıldığı fiyattan (ATH/IPO) bugüne en az drawdown_min_pct kadar
    düşüp düşmediği kontrol edilir ("zirveden çökmüş" hisseleri yakalamak için).

    alternation_enabled=True ise, YİNE VWAP eşleşmesinden BAĞIMSIZ olarak, son
    mumdan geriye doğru en az alternation_min_chain (varsayılan 4) mum boyunca
    kesintisiz yeşil/kırmızı alternasyonu (zigzag) olup olmadığı kontrol edilir.
    Varsa, zincirdeki mumların gövde boyu (fitiller hariç, |Close-Open|)
    birbirine ne kadar yakınsa 0-100 arası bir "düzenlilik puanı" hesaplanır.
    alternation_min_score verilirse, sadece bu puanın ALTINDA KALMAYAN hisseler
    eşleşme sayılır (None ise puan eşiği aranmaz, sadece zincir uzunluğu yeter).

    trendline_enabled=True ise, YİNE VWAP eşleşmesinden BAĞIMSIZ olarak, pivot
    tepe noktalarından kurulan düşen bir direnç çizgisinin son
    trendline_breakout_lookback bar içinde YUKARI kırılıp kırılmadığı
    kontrol edilir (bkz. detect_trendline_break). trendline_require_volume
    ile, kırılım barında en az trendline_volume_factor katı hacim şartı
    eklenebilir.

    Her filtre de eşleşen VWAP sonuçlarının içine "sideways"/"drawdown"/
    "alternation"/"trendline" alanı olarak eklenir — böylece "hem yatay/
    çökmüş/zigzag/trend kırılımı HEM DE VWAP sistemine girmiş" hisseler tek
    taramada birlikte görülebilir.

    Dönüş: (vwap_matches, sideways_matches, drawdown_matches,
    alternation_matches, trendline_matches, triangle_matches) — altı ayrı liste. İlgili filtre
    kapalıysa o listenin dönüşü her zaman boş olur.
    """
    matches = []
    sideways_matches = []
    drawdown_matches = []
    alternation_matches = []
    trendline_matches = []
    triangle_matches = []
    total = len(symbols)
    done = 0

    fx_df = None
    if currency != "TRY":
        if period in INTRADAY_PERIODS:
            fx_symbol = CURRENCY_FX_SYMBOLS.get(currency)
            yf_interval, yf_period = VWAP_INTRADAY_FETCH_SPEC[period]
            fx_df, fx_error = fetch_intraday_history_cached(
                fx_symbol, yf_interval, yf_period, use_cache=use_cache,
            )
        else:
            fx_df, fx_error = fetch_fx_rate_cached(currency, use_cache=use_cache)
        if fx_df is None and errors_out is not None:
            errors_out.append((CURRENCY_FX_SYMBOLS.get(currency, currency),
                                fx_error or f"{currency} kur verisi çekilemedi"))

    benchmark_df = _fetch_quality_benchmark(
        period, use_cache=use_cache, currency=currency, fx_df=fx_df,
    )

    with ThreadPoolExecutor(max_workers=_effective_scan_workers(period, max_workers)) as executor:
        futures = {
            executor.submit(
                fetch_and_scan, sym, period, lookback, use_cache,
                sideways_enabled, sideways_months_list, sideways_range_pct,
                sideways_atr_pct, sideways_method, sideways_min_windows,
                drawdown_enabled, drawdown_min_pct,
                alternation_enabled, alternation_min_chain, alternation_min_score,
                trendline_enabled, trendline_pivot_window, trendline_min_span_bars,
                trendline_lookback_bars, trendline_breakout_lookback,
                trendline_touch_tolerance_pct, trendline_require_volume,
                trendline_volume_factor, trendline_min_touches,
                triangle_enabled, triangle_pivot_window, triangle_min_span_bars,
                triangle_lookback_bars, triangle_min_apex_bars_ahead,
                triangle_max_apex_bars_ahead, triangle_max_squeeze_pct,
                currency, fx_df, benchmark_df,
            ): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                out = future.result()
                if out.get("matched"):
                    matches.append(out["result"])
                elif not out.get("ok", True) and errors_out is not None:
                    errors_out.append((sym, out.get("error", "bilinmeyen hata")))

                sw = out.get("sideways")
                if sw and sw.get("is_sideways"):
                    sideways_matches.append({
                        "symbol": sym.replace(".IS", ""),
                        "sideways_count": sw["sideways_count"],
                        "total_windows": sw["total_windows"],
                        "min_required": sw["min_required"],
                        "sideways_months": sw["sideways_months"],
                        "details": sw["details"],
                        "anchor_date": sw.get("anchor_date"),
                        "anchor_reason": sw.get("anchor_reason"),
                    })

                dd = out.get("drawdown")
                if dd and dd.get("is_drawdown"):
                    drawdown_matches.append({
                        "symbol": sym.replace(".IS", ""),
                        "drawdown_pct": dd["drawdown_pct"],
                        "anchor_date": dd.get("anchor_date"),
                        "anchor_reason": dd.get("anchor_reason"),
                    })

                alt = out.get("alternation")
                if alt and alt.get("is_alternating"):
                    # Özet kopya yerine grafik çizmek için gerekli tüm alanları koru.
                    alt_item = dict(alt)
                    alt_item["symbol"] = sym.replace(".IS", "")
                    alt_item.setdefault("period", period)
                    alternation_matches.append(alt_item)

                tl = out.get("trendline")
                if tl and tl.get("matched"):
                    # line / cross_idx / df grafik için zorunlu. V3'te bunların
                    # yalnız tablo alanlarını saklamak KeyError 'df' üretiyordu.
                    tl_item = dict(tl)
                    tl_item["symbol"] = sym.replace(".IS", "")
                    tl_item.setdefault("period", period)
                    trendline_matches.append(tl_item)
                tri = out.get("triangle")
                if tri and tri.get("matched"):
                    triangle_matches.append({
                        "symbol": sym.replace(".IS", ""),
                        "pattern_type": tri["pattern_type"],
                        "apex_bars_ahead": tri["apex_bars_ahead"],
                        "squeeze_pct": tri["squeeze_pct"],
                        "last_close": tri["last_close"],
                        "upper_now": tri["upper_now"],
                        "lower_now": tri["lower_now"],
                        "volume_dryness_pct": tri.get("volume_dryness_pct"),
                        "start_date": tri.get("start_date"),
                        "end_date": tri.get("end_date"),
                        "df": tri.get("df"),
                        "upper": tri["upper"],
                        "lower": tri["lower"],
                        "apex_x": tri["apex_x"],
                        "apex_y": tri["apex_y"],
                        "period": period,
                        "quality": tri.get("quality"),
                        "upper_touches": tri.get("upper_touches"),
                        "lower_touches": tri.get("lower_touches"),
                    })
            except Exception as exc:
                if errors_out is not None:
                    errors_out.append((sym, f"beklenmeyen hata: {exc}"))
            if progress_callback:
                progress_callback(done, total, sym)

    return (matches, sideways_matches, drawdown_matches, alternation_matches,
            trendline_matches, triangle_matches)


# ====================================================================
# BAĞIMSIZ TREND ÇİZGİSİ TARAMASI — çekirdek fonksiyonlar
# ====================================================================
# Yukarıdaki scan_symbols_parallel/fetch_and_scan, trend çizgisini HER
# ZAMAN ana VWAP taramasının periyoduna (ve VWAP/currency/sideways/
# drawdown/alternation makinesine) bağlı olarak hesaplar. Buradaki üç
# fonksiyon ise VWAP'tan, para biriminden ve diğer TÜM filtrelerden
# TAMAMEN BAĞIMSIZ, sadece trend çizgisi kırılımı arayan, KENDİ periyodu
# (TRENDLINE_SCAN_PERIOD_OPTIONS) seçilebilen ayrı bir tarama yolu sağlar
# — app.py'de ayrı bir bölüm/buton olarak kullanılır.

def fetch_period_ohlcv(symbol, period, use_cache=True):
    """Tek bir sembol için, VERİLEN periyotta (1h/4h/daily/weekly) OHLCV
    verisini çeker ve gerekiyorsa o periyoda resample eder. Ana VWAP
    taramasından bağımsız, sadece ham periyot verisine ihtiyaç duyan
    yerler (örn. bağımsız trend çizgisi taraması) için kullanılır.

    Dönüş: (df, intraday_bool, hata_mesaji). Veri çekilemezse df None,
    hata_mesaji doludur.
    """
    if period in TRENDLINE_SCAN_INTRADAY_PERIODS:
        yf_interval, yf_period = INTRADAY_FETCH_SPEC[period]
        raw, error = fetch_intraday_history_cached(symbol, yf_interval, yf_period, use_cache=use_cache)
        if raw is None:
            return None, True, error or "bilinmeyen hata"
        if period == "4h":
            df = resample_intraday_to_4h(raw, closed_only=True)
        else:
            # "1h": yalnız KAPANMIŞ 60 dakikalık barları kullan.
            df = _drop_incomplete_intraday_bars(raw, bar_minutes=60)
        return df, True, None

    daily, error = fetch_history_cached(symbol, use_cache=use_cache)
    if daily is None:
        return None, False, error or "bilinmeyen hata"
    df = resample_ohlcv(daily, period)
    return df, False, None


def fetch_and_scan_trendline_only(symbol, period, use_cache=True,
                                   pivot_window=TRENDLINE_PIVOT_WINDOW,
                                   min_span_bars=TRENDLINE_MIN_SPAN_BARS,
                                   lookback_bars=TRENDLINE_LOOKBACK_BARS,
                                   breakout_lookback=3,
                                   touch_tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT,
                                   require_volume=False,
                                   volume_factor=TRENDLINE_VOLUME_FACTOR,
                                   min_touches=TRENDLINE_MIN_TOUCHES, benchmark_df=None):
    """Tek bir sembol için, VWAP zincirinden ve diğer TÜM filtrelerden
    (yatay/zirveden düşüş/alternasyon) TAMAMEN BAĞIMSIZ olarak SADECE
    düşen trend çizgisi kırılımını arar. `period` burada AYRI seçilir —
    ana VWAP taramasının periyodundan tamamen kopuk çalışır.

    Dönüş: {"symbol", "ok", "matched", ["result"|"error"]}. Eşleşme
    varsa "result", detect_trendline_break() çıktısına ek olarak
    "symbol"/"period"/"df" alanlarını içerir (grafik çizimi için)."""
    df_period, intraday, error = fetch_period_ohlcv(symbol, period, use_cache=use_cache)
    if df_period is None:
        return {"symbol": symbol, "ok": False, "error": error}
    if len(df_period) == 0:
        return {"symbol": symbol, "ok": False, "error": "yeterli veri yok"}

    pos_df = _prep_positional_df(df_period)
    trendline_info = detect_trendline_break(
        pos_df, pivot_window=pivot_window, lookback_bars=lookback_bars,
        min_span_bars=min_span_bars, breakout_lookback=breakout_lookback,
        touch_tolerance_pct=touch_tolerance_pct, require_volume=require_volume,
        volume_factor=volume_factor, min_touches=min_touches, intraday=intraday,
    )

    if trendline_info and trendline_info.get("matched"):
        trendline_info["symbol"] = symbol.replace(".IS", "")
        trendline_info["period"] = period
        trendline_info["df"] = pos_df
        higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
        trendline_info["quality"] = compute_upside_quality(
            pos_df, "trendline", trendline_info, benchmark_df=benchmark_df, higher_df=higher_df,
        )
        return {"symbol": symbol, "ok": True, "matched": True, "result": trendline_info}
    return {"symbol": symbol, "ok": True, "matched": False}


def scan_trendline_symbols_parallel(symbols, period, max_workers=20, use_cache=True,
                                     progress_callback=None, errors_out=None,
                                     pivot_window=TRENDLINE_PIVOT_WINDOW,
                                     min_span_bars=TRENDLINE_MIN_SPAN_BARS,
                                     lookback_bars=TRENDLINE_LOOKBACK_BARS,
                                     breakout_lookback=3,
                                     touch_tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT,
                                     require_volume=False,
                                     volume_factor=TRENDLINE_VOLUME_FACTOR,
                                     min_touches=TRENDLINE_MIN_TOUCHES):
    """Sembol listesini SADECE düşen trend çizgisi kırılımı için PARALEL
    tarar. VWAP zincirinin, para biriminin, yatay/zirveden düşüş/
    alternasyon filtrelerinin HİÇBİRİYLE ilgisi yoktur — VWAP taraması
    hiç çalıştırılmamış olsa bile tek başına kullanılabilir. `period`
    (1h/4h/daily/weekly) sadece bu tarama için geçerlidir.

    progress_callback(tamamlanan, toplam, sembol) her sembol bitince çağrılır.
    errors_out verilirse (boş bir liste), veri çekilemeyen semboller buraya
    (sembol, hata_mesaji) çiftleri olarak eklenir.

    Dönüş: eşleşen sonuçların listesi (her biri detect_trendline_break()
    çıktısı + symbol/period/df alanları)."""
    matches = []
    total = len(symbols)
    done = 0
    benchmark_df = _fetch_quality_benchmark(period, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=_effective_scan_workers(period, max_workers)) as executor:
        futures = {
            executor.submit(
                fetch_and_scan_trendline_only, sym, period, use_cache,
                pivot_window, min_span_bars, lookback_bars, breakout_lookback,
                touch_tolerance_pct, require_volume, volume_factor, min_touches, benchmark_df,
            ): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                out = future.result()
                if out.get("matched"):
                    matches.append(out["result"])
                elif not out.get("ok", True) and errors_out is not None:
                    errors_out.append((sym, out.get("error", "bilinmeyen hata")))
            except Exception as exc:
                if errors_out is not None:
                    errors_out.append((sym, f"beklenmeyen hata: {exc}"))
            if progress_callback:
                progress_callback(done, total, sym)

    return matches


# ====================================================================
# BAĞIMSIZ ÜÇGEN KIRILIM TARAMASI — çekirdek fonksiyonlar
# ====================================================================
# fetch_and_scan_trendline_only / scan_trendline_symbols_parallel'in
# aynası: VWAP'tan, para biriminden ve diğer TÜM filtrelerden TAMAMEN
# BAĞIMSIZ, sadece "kırılmak üzere olan üçgen" arayan, KENDİ periyodu
# (TRIANGLE_SCAN_PERIOD_OPTIONS) seçilebilen ayrı bir tarama yolu sağlar.

def fetch_and_scan_triangle_only(symbol, period, use_cache=True,
                                  pivot_window=TRIANGLE_PIVOT_WINDOW,
                                  min_span_bars=TRIANGLE_MIN_SPAN_BARS,
                                  lookback_bars=TRIANGLE_LOOKBACK_BARS,
                                  min_apex_bars_ahead=TRIANGLE_MIN_APEX_BARS_AHEAD,
                                  max_apex_bars_ahead=TRIANGLE_MAX_APEX_BARS_AHEAD,
                                  max_squeeze_pct=TRIANGLE_MAX_SQUEEZE_PCT, benchmark_df=None):
    """Tek bir sembol için, VWAP zincirinden ve diğer TÜM filtrelerden
    TAMAMEN BAĞIMSIZ olarak SADECE kırılmak üzere olan üçgeni arar.
    `period` burada AYRI seçilir — ana VWAP taramasının periyodundan
    tamamen kopuk çalışır.

    Dönüş: {"symbol", "ok", "matched", ["result"|"error"]}. Eşleşme
    varsa "result", detect_triangle_break() çıktısına ek olarak
    "symbol"/"period"/"df" alanlarını içerir (grafik çizimi için)."""
    df_period, intraday, error = fetch_period_ohlcv(symbol, period, use_cache=use_cache)
    if df_period is None:
        return {"symbol": symbol, "ok": False, "error": error}
    if len(df_period) == 0:
        return {"symbol": symbol, "ok": False, "error": "yeterli veri yok"}

    pos_df = _prep_positional_df(df_period)
    triangle_info = detect_triangle_break(
        pos_df, pivot_window=pivot_window, lookback_bars=lookback_bars,
        min_span_bars=min_span_bars, min_apex_bars_ahead=min_apex_bars_ahead,
        max_apex_bars_ahead=max_apex_bars_ahead, max_squeeze_pct=max_squeeze_pct,
        intraday=intraday,
    )

    if triangle_info and triangle_info.get("matched"):
        triangle_info["symbol"] = symbol.replace(".IS", "")
        triangle_info["period"] = period
        triangle_info["df"] = pos_df
        higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
        triangle_info["quality"] = compute_upside_quality(
            pos_df, "triangle", triangle_info, benchmark_df=benchmark_df, higher_df=higher_df,
        )
        return {"symbol": symbol, "ok": True, "matched": True, "result": triangle_info}
    return {"symbol": symbol, "ok": True, "matched": False}


def scan_triangle_symbols_parallel(symbols, period, max_workers=20, use_cache=True,
                                    progress_callback=None, errors_out=None,
                                    pivot_window=TRIANGLE_PIVOT_WINDOW,
                                    min_span_bars=TRIANGLE_MIN_SPAN_BARS,
                                    lookback_bars=TRIANGLE_LOOKBACK_BARS,
                                    min_apex_bars_ahead=TRIANGLE_MIN_APEX_BARS_AHEAD,
                                    max_apex_bars_ahead=TRIANGLE_MAX_APEX_BARS_AHEAD,
                                    max_squeeze_pct=TRIANGLE_MAX_SQUEEZE_PCT):
    """Sembol listesini SADECE kırılmak üzere olan üçgen için PARALEL
    tarar. VWAP zincirinin, para biriminin, diğer TÜM filtrelerin
    HİÇBİRİYLE ilgisi yoktur. `period` (1h/4h/daily/weekly) sadece bu
    tarama için geçerlidir.

    progress_callback(tamamlanan, toplam, sembol) her sembol bitince çağrılır.
    errors_out verilirse, veri çekilemeyen semboller buraya (sembol,
    hata_mesajı) çiftleri olarak eklenir.

    Dönüş: eşleşen sonuçların listesi (her biri detect_triangle_break()
    çıktısı + symbol/period/df alanları)."""
    matches = []
    total = len(symbols)
    done = 0
    benchmark_df = _fetch_quality_benchmark(period, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=_effective_scan_workers(period, max_workers)) as executor:
        futures = {
            executor.submit(
                fetch_and_scan_triangle_only, sym, period, use_cache,
                pivot_window, min_span_bars, lookback_bars,
                min_apex_bars_ahead, max_apex_bars_ahead, max_squeeze_pct, benchmark_df,
            ): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                out = future.result()
                if out.get("matched"):
                    matches.append(out["result"])
                elif not out.get("ok", True) and errors_out is not None:
                    errors_out.append((sym, out.get("error", "bilinmeyen hata")))
            except Exception as exc:
                if errors_out is not None:
                    errors_out.append((sym, f"beklenmeyen hata: {exc}"))
            if progress_callback:
                progress_callback(done, total, sym)

    return matches


# ====================================================================
# BAĞIMSIZ ALTERNASYON (ZİGZAG) TARAMASI — çekirdek fonksiyonlar
# ====================================================================
# fetch_and_scan_trendline_only / scan_trendline_symbols_parallel'in
# aynası: VWAP'tan, para biriminden ve diğer TÜM filtrelerden TAMAMEN
# BAĞIMSIZ, sadece son mumdan geriye doğru kesintisiz renk alternasyonu
# (zigzag) arayan, KENDİ periyodu (ALTERNATION_SCAN_PERIOD_OPTIONS)
# seçilebilen ayrı bir tarama yolu sağlar.

def fetch_and_scan_alternation_only(symbol, period, use_cache=True,
                                     min_chain=ALTERNATION_MIN_CHAIN,
                                     min_score=None, benchmark_df=None):
    """Tek bir sembol için, VWAP zincirinden ve diğer TÜM filtrelerden
    TAMAMEN BAĞIMSIZ olarak SADECE mum alternasyonunu (zigzag) arar.
    `period` burada AYRI seçilir — ana VWAP taramasının periyodundan
    tamamen kopuk çalışır.

    Dönüş: {"symbol", "ok", "matched", ["result"|"error"]}. Eşleşme
    varsa "result", detect_candle_alternation() çıktısına ek olarak
    "symbol"/"period"/"df"/"start_date"/"end_date" alanlarını içerir
    (tablo ve grafik çizimi için)."""
    df_period, intraday, error = fetch_period_ohlcv(symbol, period, use_cache=use_cache)
    if df_period is None:
        return {"symbol": symbol, "ok": False, "error": error}
    if len(df_period) == 0:
        return {"symbol": symbol, "ok": False, "error": "yeterli veri yok"}

    pos_df = _prep_positional_df(df_period)
    alt = detect_candle_alternation(pos_df, min_chain=min_chain)

    if alt is not None and (min_score is None or alt["score"] >= min_score):
        alt["symbol"] = symbol.replace(".IS", "")
        alt["period"] = period
        alt["df"] = pos_df
        alt["start_date"] = _fmt_ts(df_period.index[alt["start_idx"]], intraday)
        alt["end_date"] = _fmt_ts(df_period.index[alt["end_idx"]], intraday)
        higher_df = _fetch_quality_higher_df(symbol, period, use_cache=use_cache)
        alt["quality"] = compute_upside_quality(
            pos_df, "alternation", alt, benchmark_df=benchmark_df, higher_df=higher_df,
        )
        return {"symbol": symbol, "ok": True, "matched": True, "result": alt}
    return {"symbol": symbol, "ok": True, "matched": False}


def scan_alternation_symbols_parallel(symbols, period, max_workers=20, use_cache=True,
                                       progress_callback=None, errors_out=None,
                                       min_chain=ALTERNATION_MIN_CHAIN,
                                       min_score=None):
    """Sembol listesini SADECE mum alternasyonu (zigzag) için PARALEL
    tarar. VWAP zincirinin, para biriminin, diğer TÜM filtrelerin
    HİÇBİRİYLE ilgisi yoktur — VWAP taraması hiç çalıştırılmamış olsa
    bile tek başına kullanılabilir. `period` (1h/4h/daily/weekly) sadece
    bu tarama için geçerlidir.

    progress_callback(tamamlanan, toplam, sembol) her sembol bitince çağrılır.
    errors_out verilirse (boş bir liste), veri çekilemeyen semboller buraya
    (sembol, hata_mesajı) çiftleri olarak eklenir.

    Dönüş: eşleşen sonuçların listesi (her biri detect_candle_alternation()
    çıktısı + symbol/period/df alanları)."""
    matches = []
    total = len(symbols)
    done = 0
    benchmark_df = _fetch_quality_benchmark(period, use_cache=use_cache)

    with ThreadPoolExecutor(max_workers=_effective_scan_workers(period, max_workers)) as executor:
        futures = {
            executor.submit(
                fetch_and_scan_alternation_only, sym, period, use_cache,
                min_chain, min_score, benchmark_df,
            ): sym
            for sym in symbols
        }
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                out = future.result()
                if out.get("matched"):
                    matches.append(out["result"])
                elif not out.get("ok", True) and errors_out is not None:
                    errors_out.append((sym, out.get("error", "bilinmeyen hata")))
            except Exception as exc:
                if errors_out is not None:
                    errors_out.append((sym, f"beklenmeyen hata: {exc}"))
            if progress_callback:
                progress_callback(done, total, sym)

    return matches
