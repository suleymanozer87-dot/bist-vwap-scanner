# -*- coding: utf-8 -*-
"""
vwap_core.py — Zincirleme Anchored VWAP algoritmasının çekirdeği.
Hem CLI scriptinde (bist_vwap_scanner.py) hem arayüzde (app.py) kullanılır.
"""

import os
import time
import math
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

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
# "daily", "weekly", "monthly" GÜNLÜK barlardan (5 yıllık geçmiş)
# RESAMPLE edilerek üretilir (bkz. resample_ohlcv). "4h" ise Yahoo
# Finance'in GÜN-İÇİ (intraday) barlarından (60 dakikalık ham veri,
# ~730 günlük geçmişle sınırlı — Yahoo'nun kendi limiti) üretilir (bkz.
# resample_intraday_to_4h). Bu yüzden "4h" periyodunda VWAP zincirinin
# görebildiği geçmiş, günlük/haftalık/aylık periyotlara göre ÇOK DAHA
# KISADIR — bu normaldir, Yahoo Finance'in gün-içi veri politikasından
# kaynaklanır.
PERIOD_OPTIONS = ("daily", "weekly", "monthly", "4h")
INTRADAY_PERIODS = {"4h"}
PERIOD_LABELS = {"daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık", "4h": "4 Saatlik"}
# period -> (Yahoo'dan çekilecek ham gün-içi interval, geriye kaç günlük veri istensin)
INTRADAY_FETCH_SPEC = {"4h": ("60m", "730d"), "1h": ("60m", "730d")}


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
    anchor_price = float(df["High"].iloc[anchor_idx]) if anchor_reason == "ATH" \
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
    """Son mumdan geriye doğru, renk (yeşil/kırmızı) alternasyonu yapan
    kesintisiz en uzun zinciri bulur ve gövde boylarının birbirine
    yakınlığına göre 0-100 arası bir puan üretir.

    Renk: Close >= Open ise 'yeşil', değilse 'kırmızı' (chart_helpers.py'
    deki mum renklendirmesiyle AYNI kural).

    Zincir uzunluğu min_chain'den KISAYSA (yani son mumdan itibaren en az
    `min_chain` mum boyunca kesintisiz renk değişimi yoksa) None döner.

    Puan hesabı: (en büyük gövde - en küçük gövde) / en büyük gövde
    oranı 0'a ne kadar yakınsa (yani gövdeler birbirine ne kadar
    yakınsa) puan 100'e o kadar yakın olur:
        score = 100 * (1 - (max_body - min_body) / max_body)
    Tüm gövdeler eşitse score = 100. Gövdelerin tamamı 0 boyundaysa
    (Close == Open her mumda) score = 100 kabul edilir (hepsi de eşit
    şekilde "iğne" gövdeli demektir).

    Dönüş: None ya da
        {"chain_length": int, "score": float, "start_idx": int, "end_idx": int,
         "colors": [...], "body_sizes": [...]}
    start_idx/end_idx, verilen df'in (0-tabanlı, pozisyonel) index'lerine
    göredir — df["Open"]/df["Close"] pozisyonel olarak indekslenebilir
    olmalı (örn. reset_index yapılmış ya da .iloc ile erişilecek).
    """
    if df is None or len(df) < min_chain:
        return None

    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)
    n = len(df)

    is_green = closes >= opens  # True = yeşil, False = kırmızı

    # Son mumdan geriye doğru, renk her mumda ÖNCEKİNDEN FARKLI olduğu
    # sürece zincir uzar; ilk aynı-renk komşu çiftte durur.
    chain_len = 1
    for i in range(n - 1, 0, -1):
        if is_green[i] != is_green[i - 1]:
            chain_len += 1
        else:
            break

    if chain_len < min_chain:
        return None

    start_idx = n - chain_len
    end_idx = n - 1

    body_sizes = [abs(float(closes[i]) - float(opens[i])) for i in range(start_idx, end_idx + 1)]
    max_body = max(body_sizes)
    min_body = min(body_sizes)

    if max_body <= 0:
        score = 100.0  # tüm gövdeler 0 boyunda -> zaten birbirine tam eşit
    else:
        score = 100.0 * (1.0 - (max_body - min_body) / max_body)

    return {
        "chain_length": chain_len,
        "score": round(score, 1),
        "start_idx": start_idx,
        "end_idx": end_idx,
        "colors": ["yeşil" if is_green[i] else "kırmızı" for i in range(start_idx, end_idx + 1)],
        "body_sizes": [round(b, 4) for b in body_sizes],
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


def _count_trendline_touches(df, line, tolerance_pct=TRENDLINE_TOUCH_TOLERANCE_PCT):
    """Çizginin x1..x2 aralığında, kaç barın High'ı çizgiye `tolerance_pct`
    içinde 'değdiğini' sayar — sadece bilgi/kalite amaçlı (kaç pivot
    barı bu çizgiyi gerçekten test etmiş)."""
    highs = df["High"].to_numpy(dtype=float)
    x1, x2 = line["x1"], line["x2"]
    slope, intercept = line["slope"], line["intercept"]
    touches = 0
    for i in range(x1, x2 + 1):
        line_val = slope * i + intercept
        if line_val <= 0:
            continue
        if abs(highs[i] - line_val) / line_val * 100.0 <= tolerance_pct:
            touches += 1
    return touches


def find_trendline_crossover(df, line, lookback=3):
    """line'ın (slope, intercept) tanımladığı düşen çizgiyi, x2'den SONRA
    kapanışın YUKARI kırdığı en son anı arar (VWAP zincirindeki
    find_recent_crossover ile aynı mantık — referans VWAP yerine trend
    çizgisi kullanılır)."""
    n = len(df)
    closes = df["Close"].to_numpy(dtype=float)
    slope, intercept = line["slope"], line["intercept"]
    start = max(line["x2"] + 1, n - lookback)
    for i in range(start, n):
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

    touches = _count_trendline_touches(df, line, tolerance_pct=touch_tolerance_pct)
    if touches < min_touches:
        return None  # istenen minimum temas sayısını karşılamıyor

    n = len(df)
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

def resample_ohlcv(df, period):
    if period == "daily":
        return df
    rule = {"weekly": "W-FRI", "monthly": "ME"}[period]
    out = df.resample(rule).agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna()
    return out


def resample_intraday_to_4h(intraday_df):
    """Ham gün-içi barları (60 dakikalık) 4 saatlik mumlara indirger.

    Sabit saat dilimlerinde (00:00-04:00, 04:00-08:00, ...) gruplanır.
    BIST seans saatleri (yaklaşık 10:00-18:00) bu dilimlere HER ZAMAN tam
    oturmayabilir (örn. son dilim seans kapanışıyla erken kesilebilir) —
    bu, ham gün-içi barları birleştirmenin standart ve basit yoludur;
    az bar içeren bir dilim yine de o dilimdeki gerçek fiyat/hacim
    hareketini doğru yansıtır, sadece komşu barlardan biraz daha az
    veriye dayanır."""
    if intraday_df is None or len(intraday_df) == 0:
        return intraday_df
    out = intraday_df.resample("4h").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    }).dropna(subset=["Open", "High", "Low", "Close"])
    return out


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


def fetch_history(symbol, retries=2, period="5y"):
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


def _cache_path(symbol):
    fname = symbol.replace(".", "_") + ".csv"
    return os.path.join(CACHE_DIR, fname)


def _load_from_cache(symbol):
    """Önbellekten oku. Dosya BUGÜN güncellenmişse geçerli sayılır, yoksa None döner."""
    path = _cache_path(symbol)
    if not os.path.exists(path):
        return None
    mtime = date.fromtimestamp(os.path.getmtime(path))
    if mtime != date.today():
        return None  # bayat, yeniden çekilmeli
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) == 0:
            return None
        return df
    except Exception:
        return None


def _save_to_cache(symbol, df):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        df.to_csv(_cache_path(symbol))
    except Exception:
        pass  # önbelleğe yazamasa da tarama devam etsin


def fetch_history_cached(symbol, period="5y", use_cache=True):
    """
    Önbellekli veri çekme: aynı gün içinde tekrar taranırsa diskten okur,
    internete gitmez. Yeni gün / önbellek yoksa Yahoo Finance'ten çeker ve kaydeder.
    Dönüş: (df, hata_mesaji) — cache'ten geldiyse hata_mesaji her zaman None.
    """
    if use_cache:
        cached = _load_from_cache(symbol)
        if cached is not None:
            return cached, None

    df, error = fetch_history(symbol, period=period)
    if df is not None and use_cache:
        _save_to_cache(symbol, df)
    return df, error


# --- Gün-içi (intraday) veri çekme — "4h" gibi periyotlar için --------
# Günlük veriden AYRI bir mekanizma: farklı bir yfinance interval'i
# ("60m") ve farklı bir Yahoo geçmiş limiti ("730d") kullanır, bu yüzden
# ayrı bir önbellek dosyasına (sembol + interval'e göre adlandırılmış)
# yazılır — günlük önbellekle KARIŞMAZ/ÇAKIŞMAZ.

def _fetch_via_ticker_intraday(symbol, yf_period, yf_interval):
    t = yf.Ticker(symbol)
    df = t.history(period=yf_period, interval=yf_interval, auto_adjust=False)
    return df


def fetch_intraday_history(symbol, yf_interval, yf_period, retries=2):
    """Yahoo Finance'ten HAM gün-içi barları çeker (örn. 60 dakikalık).
    Dönüş: (df, hata_mesaji)."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            df = _fetch_via_ticker_intraday(symbol, yf_period, yf_interval)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=["Open", "High", "Low", "Close"])
            if len(df) == 0:
                last_error = ("Yahoo Finance boş gün-içi veri döndürdü (sembol yanlış "
                              "olabilir ya da bu interval için veri yok)")
            else:
                return df, None
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5)
    return None, last_error


def _intraday_cache_path(symbol, yf_interval):
    fname = f"{symbol.replace('.', '_')}_{yf_interval}.csv"
    return os.path.join(CACHE_DIR, fname)


def _load_intraday_from_cache(symbol, yf_interval):
    path = _intraday_cache_path(symbol, yf_interval)
    if not os.path.exists(path):
        return None
    mtime = date.fromtimestamp(os.path.getmtime(path))
    if mtime != date.today():
        return None  # bayat, yeniden çekilmeli
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) == 0:
            return None
        return df
    except Exception:
        return None


def _save_intraday_to_cache(symbol, yf_interval, df):
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        df.to_csv(_intraday_cache_path(symbol, yf_interval))
    except Exception:
        pass  # önbelleğe yazamasa da tarama devam etsin


def fetch_intraday_history_cached(symbol, yf_interval, yf_period, use_cache=True):
    """Gün-içi (intraday) barların önbellekli hali — bkz. fetch_history_cached.
    NOT: gün-içi veri, günlük veriden çok daha sık güncellenir; yine de
    burada "bugün çekildiyse geçerli" kuralı (günlük önbellekle aynı
    mantık) korunuyor — daha taze veri için --no-cache kullanılabilir."""
    if use_cache:
        cached = _load_intraday_from_cache(symbol, yf_interval)
        if cached is not None:
            return cached, None

    df, error = fetch_intraday_history(symbol, yf_interval, yf_period)
    if df is not None and use_cache:
        _save_intraday_to_cache(symbol, yf_interval, df)
    return df, error


def _to_naive_normalized_index(index):
    """Bir DatetimeIndex'i (tz-aware olsun olmasın) saatini/zaman dilimini
    atıp SADECE tarihe indirger — farklı borsalardan gelen (BIST vs. FX)
    tz-aware/tz-naive index'leri karşılaştırabilmek/hizalayabilmek için."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


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
    out["Date"] = pd.to_datetime(out["Date"], utc=False, errors="coerce")
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
                    currency="TRY", fx_df=None):
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
        yf_interval, yf_period = INTRADAY_FETCH_SPEC[period]
        raw, error = fetch_intraday_history_cached(symbol, yf_interval, yf_period, use_cache=use_cache)
        if raw is None:
            return {"symbol": symbol, "ok": False, "error": error or "bilinmeyen hata"}

        base = raw
        if currency != "TRY":
            if fx_df is None or len(fx_df) == 0:
                return {"symbol": symbol, "ok": False,
                        "error": f"{currency} kur verisi ({CURRENCY_FX_SYMBOLS.get(currency)}) çekilemediği için sembol atlandı"}
            converted = convert_ohlc_to_currency(base, fx_df)
            if converted is None or len(converted) == 0:
                return {"symbol": symbol, "ok": False,
                        "error": f"{currency} bazına çevrilecek örtüşen/yeterli veri yok"}
            base = converted

        df_period = resample_intraday_to_4h(base)

        daily_for_sideways = None
        if sideways_enabled:
            daily_for_sideways, _sw_err = fetch_history_cached(symbol, use_cache=use_cache)
            if daily_for_sideways is not None and currency != "TRY" and fx_df is not None:
                converted_daily = convert_ohlc_to_currency(daily_for_sideways, fx_df)
                daily_for_sideways = converted_daily if converted_daily is not None else None
    else:
        daily, error = fetch_history_cached(symbol, use_cache=use_cache)
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

        df_period = resample_ohlcv(daily, period)
        daily_for_sideways = daily

    # DÜZELTME: lookback artık gerçekten iletiliyor (önceden hep varsayılan 3 kullanılıyordu)
    result = run_vwap_chain_scan(df_period, lookback=lookback, intraday=intraday)

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
            alternation_info = {
                "is_alternating": meets_score,
                "chain_length": alt["chain_length"],
                "score": alt["score"],
                "colors": alt["colors"],
                "body_sizes": alt["body_sizes"],
                "start_date": _fmt_ts(df_period.index[alt["start_idx"]], intraday),
                "end_date": _fmt_ts(df_period.index[alt["end_idx"]], intraday),
            }

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

    if result and result.get("matched"):
        result["symbol"] = symbol.replace(".IS", "")
        result["period"] = period
        result["currency"] = currency
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
    alternation_matches, trendline_matches) — beş ayrı liste. İlgili filtre
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
        fx_df, fx_error = fetch_fx_rate_cached(currency, use_cache=use_cache)
        if fx_df is None and errors_out is not None:
            errors_out.append((CURRENCY_FX_SYMBOLS.get(currency, currency),
                                fx_error or f"{currency} kur verisi çekilemedi"))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                currency, fx_df,
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
                    alternation_matches.append({
                        "symbol": sym.replace(".IS", ""),
                        "chain_length": alt["chain_length"],
                        "score": alt["score"],
                        "colors": alt["colors"],
                        "body_sizes": alt["body_sizes"],
                        "start_date": alt.get("start_date"),
                        "end_date": alt.get("end_date"),
                    })

                tl = out.get("trendline")
                if tl and tl.get("matched"):
                    trendline_matches.append({
                        "symbol": sym.replace(".IS", ""),
                        "touches": tl["touches"],
                        "cross_date": tl["cross_date"],
                        "bars_ago": tl["bars_ago"],
                        "last_close": tl["last_close"],
                        "line_value_now": tl["line_value_now"],
                        "start_date": tl.get("start_date"),
                        "end_date": tl.get("end_date"),
                        "volume_confirmed": tl.get("volume_confirmed"),
                    })
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
            df = resample_intraday_to_4h(raw)
        else:
            # "1h": Yahoo'nun ham 60 dakikalık barı zaten 1 saatlik mumdur,
            # ekstra bir resample'a gerek yok.
            df = raw
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
                                   min_touches=TRENDLINE_MIN_TOUCHES):
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

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_and_scan_trendline_only, sym, period, use_cache,
                pivot_window, min_span_bars, lookback_bars, breakout_lookback,
                touch_tolerance_pct, require_volume, volume_factor, min_touches,
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
                                  max_squeeze_pct=TRIANGLE_MAX_SQUEEZE_PCT):
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

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_and_scan_triangle_only, sym, period, use_cache,
                pivot_window, min_span_bars, lookback_bars,
                min_apex_bars_ahead, max_apex_bars_ahead, max_squeeze_pct,
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
                                     min_score=None):
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

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_and_scan_alternation_only, sym, period, use_cache,
                min_chain, min_score,
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
