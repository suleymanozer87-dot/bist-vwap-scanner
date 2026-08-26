# -*- coding: utf-8 -*-
"""
app.py — BIST Zincirleme VWAP Tarayıcı — Grafikli Arayüz (Streamlit)

ÇALIŞTIRMA:
    streamlit run app.py

Tarayıcınızda otomatik olarak bir sekme açılır (genelde localhost:8501).
Hiçbir veri dışarıya gönderilmez, her şey kendi bilgisayarınızda çalışır.

NOT: Bu sürüm tabloda ÇİFT TIKLAMA ile grafik açar (tek tıkla seçim yapılmaz).
Bunun için 'streamlit-aggrid' paketi gerekir:
    pip install streamlit-aggrid
Grafik açılınca fare tekerleği ile TradingView mantığında yakınlaştırma/
uzaklaştırma yapılabilir, sürükleyerek kaydırılabilir; alt panelde hacim
çubukları gösterilir (bkz. chart_helpers.py).

AYARLARIN HATIRLANMASI: Tüm ayarlar (periyot, filtreler, son kullanılan
hisse listesi vb.) her değişiklikte otomatik olarak yanınızdaki
".vwap_ayarlar.json" dosyasına kaydedilir ve bir sonraki açılışta oradan
geri yüklenir — her seferinde baştan ayarlamanıza gerek kalmaz.
"""

import os
import sys
import json
import time
import pandas as pd
import streamlit as st

from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode

from vwap_core import (
    ALTERNATION_MIN_CHAIN,
    CURRENCY_OPTIONS,
    PERIOD_LABELS,
    PERIOD_OPTIONS,
    SIDEWAYS_METHODS,
    ALTERNATION_SCAN_PERIOD_OPTIONS,
    ALTERNATION_SCAN_PERIOD_LABELS,
    TRENDLINE_PIVOT_WINDOW,
    TRENDLINE_MIN_SPAN_BARS,
    TRENDLINE_LOOKBACK_BARS,
    TRENDLINE_TOUCH_TOLERANCE_PCT,
    TRENDLINE_VOLUME_FACTOR,
    TRENDLINE_MIN_TOUCHES,
    TRENDLINE_SCAN_PERIOD_OPTIONS,
    TRENDLINE_SCAN_PERIOD_LABELS,
    TRIANGLE_PIVOT_WINDOW,
    TRIANGLE_MIN_SPAN_BARS,
    TRIANGLE_LOOKBACK_BARS,
    TRIANGLE_MIN_APEX_BARS_AHEAD,
    TRIANGLE_MAX_APEX_BARS_AHEAD,
    TRIANGLE_MAX_SQUEEZE_PCT,
    TRIANGLE_SCAN_PERIOD_OPTIONS,
    TRIANGLE_SCAN_PERIOD_LABELS,
    normalize_symbol_list,
    scan_symbols_parallel,
    scan_alternation_symbols_parallel,
    scan_trendline_symbols_parallel,
    scan_triangle_symbols_parallel,
)

CURRENCY_LABELS = {
    "TRY": "TL — orijinal (BIST'in işlem gördüğü para birimi)",
    "USD": "USD ($) — TL fiyatı USDTRY kuruna bölünerek çevrilir",
    "EUR": "EUR (€) — TL fiyatı EURTRY kuruna bölünerek çevrilir",
}
CURRENCY_AXIS_LABELS = {"TRY": "TL", "USD": "$", "EUR": "€"}
from chart_helpers import (
    render_vwap_chart, render_trendline_chart, render_triangle_chart,
    render_alternation_chart,
)

st.set_page_config(page_title="BIST Zincirleme VWAP Tarayıcı", layout="wide")


# ====================================================================
# AYARLARIN DİSKE KAYDEDİLMESİ / GERİ YÜKLENMESİ
# ====================================================================
SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".vwap_ayarlar.json")

DEFAULT_SETTINGS = {
    "period": "weekly",
    "currency": "TRY",
    "lookback": 3,
    "max_workers": 20,
    "use_cache": True,
    "sideways_enabled": False,
    "sideways_method": "range",
    "sideways_months_list": [3, 6, 12, 18, 24],
    "sideways_combine_mode": "Hepsinde (en katı)",
    "sideways_min_windows": None,
    "sideways_range_pct": 15.0,
    "sideways_atr_pct": 5.0,
    "sideways_show_tag": True,
    "sideways_show_list": True,
    "drawdown_enabled": False,
    "drawdown_min_pct": 60.0,
    "drawdown_show_tag": True,
    "drawdown_show_list": True,
    "son_semboller_text": "",
    # --- Bağımsız Alternasyon (Zigzag) Taraması (VWAP taramasından ayrı periyot) ---
    "alt_scan_period": "4h",
    "alt_scan_min_chain": ALTERNATION_MIN_CHAIN,
    "alt_scan_min_score": None,
    # --- Bağımsız Trend Çizgisi Taraması (VWAP taramasından ayrı periyot) ---
    "tl_scan_period": "4h",
    "tl_scan_pivot_window": TRENDLINE_PIVOT_WINDOW,
    "tl_scan_min_span_bars": TRENDLINE_MIN_SPAN_BARS,
    "tl_scan_lookback_bars": TRENDLINE_LOOKBACK_BARS,
    "tl_scan_breakout_lookback": 3,
    "tl_scan_touch_tolerance_pct": TRENDLINE_TOUCH_TOLERANCE_PCT,
    "tl_scan_min_touches": TRENDLINE_MIN_TOUCHES,
    "tl_scan_require_volume": False,
    "tl_scan_volume_factor": TRENDLINE_VOLUME_FACTOR,
    # --- Bağımsız Üçgen Kırılım Taraması (VWAP taramasından ayrı periyot) ---
    "tri_scan_period": "4h",
    "tri_scan_pivot_window": TRIANGLE_PIVOT_WINDOW,
    "tri_scan_min_span_bars": TRIANGLE_MIN_SPAN_BARS,
    "tri_scan_lookback_bars": TRIANGLE_LOOKBACK_BARS,
    "tri_scan_min_apex_bars_ahead": TRIANGLE_MIN_APEX_BARS_AHEAD,
    "tri_scan_max_apex_bars_ahead": TRIANGLE_MAX_APEX_BARS_AHEAD,
    "tri_scan_max_squeeze_pct": TRIANGLE_MAX_SQUEEZE_PCT,
}


def load_settings():
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(loaded)
            return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(d):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # kaydedilemese de arayüz çalışmaya devam etsin


def save_partial_settings(updates):
    """save_settings() dosyanın TAMAMINI değiştirdiği için (ana VWAP
    ayarları dahil), diskteki mevcut ayarları önce okuyup SADECE verilen
    anahtarları güncelleyerek geri yazar — Bağımsız Trend Çizgisi
    Taraması bölümü gibi, sayfanın ana ayar bloğundan SONRA tanımlanan
    widget'ların ayarlarını, ana bloğun kaydettiği ayarları SİLMEDEN
    kaydedebilmek için kullanılır."""
    current = load_settings()
    current.update(updates)
    save_settings(current)


saved = load_settings()


def show_chart_popup(sym, r):
    """Çift tıklanan hissenin grafiğini AÇILIR PENCEREDE (popup/modal) gösterir.
    Sayfa değişmez — mevcut sayfanın üstünde açılır, kapatınca aynı yerde kalırsın.
    Grafik TradingView mantığında: tekerlek zoom, sürükleyerek kaydırma, hacim paneli.

    Not: Streamlit, @st.dialog elemanının ID'sini verilen parametrelere (başlık,
    genişlik) göre otomatik üretiyor. Başlık her zaman sabit "Grafik" olsaydı,
    farklı hisseler için art arda açılan diyaloglar AYNI ID'yi üretip
    StreamlitDuplicateElementId hatasına yol açıyordu. Başlığa sembolü
    ekleyerek her hisse için benzersiz bir ID garantiliyoruz."""

    @st.dialog(f"Grafik — {sym}", width="large")
    def _dialog():
        try:
            render_vwap_chart(sym, r, key=f"popup_chart_{sym}")
        except Exception:
            _show_chart_error(sym)

    _dialog()


def _show_chart_error(sym):
    """Bir grafik çizerken beklenmeyen bir hata olursa, diyalog SESSİZCE
    boş/açılmamış görünmek yerine hatayı EKRANDA gösterir — 'grafik
    açılmıyor' şikayetinin asıl sebebinin görülüp düzeltilebilmesi için."""
    st.error(f"**{sym}** için grafik çizilirken bir hata oluştu.")
    st.exception(sys.exc_info()[1])
    st.caption(
        "Bu hatayı görüyorsanız lütfen ekran görüntüsünü paylaşın — "
        "veriyle ilgili beklenmeyen bir durum (ör. eksik/bozuk bar) olabilir."
    )


def show_trendline_chart_popup(sym, r):
    """Bağımsız Trend Çizgisi Taraması sonucunda çift tıklanan hissenin
    grafiğini açılır pencerede gösterir (bkz. show_chart_popup — aynı
    mantık, sadece VWAP zinciri yerine trend çizgisi çizilir)."""

    @st.dialog(f"Trend Çizgisi Grafiği — {sym}", width="large")
    def _dialog():
        try:
            render_trendline_chart(sym, r, key=f"popup_tl_chart_{sym}")
        except Exception:
            _show_chart_error(sym)

    _dialog()


def show_alternation_chart_popup(sym, r):
    """Bağımsız Alternasyon Taraması sonucunda çift tıklanan hissenin
    grafiğini açılır pencerede gösterir (bkz. show_chart_popup — aynı
    mantık, sadece VWAP zinciri yerine zigzag zinciri vurgulanır)."""

    @st.dialog(f"Alternasyon Grafiği — {sym}", width="large")
    def _dialog():
        try:
            render_alternation_chart(sym, r, key=f"popup_alt_chart_{sym}")
        except Exception:
            _show_chart_error(sym)

    _dialog()


def show_triangle_chart_popup(sym, r):
    """Üçgen Kırılım Taraması sonucunda çift tıklanan hissenin grafiğini
    açılır pencerede gösterir (bkz. show_chart_popup — aynı mantık,
    sadece VWAP zinciri yerine üst/alt üçgen çizgileri çizilir)."""

    @st.dialog(f"Üçgen Kırılım Grafiği — {sym}", width="large")
    def _dialog():
        try:
            render_triangle_chart(sym, r, key=f"popup_tri_chart_{sym}")
        except Exception:
            _show_chart_error(sym)

    _dialog()


# ------------------------------------------------------------------
# Çift tıkla satır seçimi için AgGrid tablosu — yardımcı fonksiyon
# ------------------------------------------------------------------
_DOUBLE_CLICK_SELECT_JS = JsCode("""
    function(event) {
        event.node.setSelected(true, true);
    }
""")


def render_double_click_table(df, grid_key):
    """DataFrame'i AgGrid ile gösterir; satır sadece ÇİFT TIKLANINCA seçilir
    (tek tık hiçbir şey yapmaz — TradingView tarzı yanlışlıkla açılmayı önler).
    Seçilen satırı (varsa) pandas Series olarak döndürür, yoksa None."""
    gb = GridOptionsBuilder.from_dataframe(df)
    gb.configure_default_column(resizable=True, sortable=True, filter=True)
    gb.configure_grid_options(
        suppressRowClickSelection=True,   # tek tıkla seçilmesin
        rowSelection="single",
        onRowDoubleClicked=_DOUBLE_CLICK_SELECT_JS,  # sadece çift tıkta seç
        domLayout="normal",
    )
    grid_options = gb.build()

    row_height = 35
    grid_height = min(420, 46 + row_height * max(len(df), 1))

    grid_response = AgGrid(
        df,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.SELECTION_CHANGED,
        data_return_mode=DataReturnMode.AS_INPUT,
        allow_unsafe_jscode=True,
        fit_columns_on_grid_load=True,
        theme="alpine-dark",
        height=grid_height,
        key=grid_key,
    )

    selected = grid_response.get("selected_rows")
    if selected is None:
        return None
    # st_aggrid sürümüne göre selected_rows liste-of-dict ya da DataFrame olabilir
    if hasattr(selected, "iloc"):
        if len(selected) == 0:
            return None
        return selected.iloc[0]
    if len(selected) == 0:
        return None
    return pd.Series(selected[0])


# ------------------------------------------------------------------
# Başlık
# ------------------------------------------------------------------
st.title("BIST Zincirleme VWAP Tarayıcı")
st.caption(
    "ATH/IPO'dan başlayan, en fazla 3 seviye zincirlenen Anchored VWAP kırılımlarını "
    "yakalar. Veri kaynağı: Yahoo Finance (gecikmeli, EOD). Yatırım tavsiyesi değildir."
)

if "results" not in st.session_state:
    st.session_state.results = None
    st.session_state.scanned_period = None


tab_vwap, tab_alt, tab_tl, tab_tri, tab_all = st.tabs(
    ["🔍 VWAP Taraması", "🔀 Alternasyon", "📐 Trend Çizgisi", "🔺 Üçgen", "🚀 Tümünü Tara"]
)

with tab_vwap:
    with st.expander("⚙️ Ayarlar", expanded=(st.session_state.results is None)):
        tab_genel, tab_hiz, tab_yatay, tab_dusus, tab_liste = st.tabs(
            ["📈 Tarama", "⚡ Hız", "📏 Yatay Filtre", "📉 Zirveden Düşüş", "📋 Hisse Listesi"]
        )

        # --- Tarama ayarları ---
        with tab_genel:
            period_options = list(PERIOD_OPTIONS)
            period = st.selectbox(
                "Periyot",
                options=period_options,
                index=period_options.index(saved["period"]) if saved["period"] in period_options else 0,
                format_func=lambda p: PERIOD_LABELS.get(p, p),
                help="VWAP'ın hangi mum periyodunda hesaplanacağını belirler (haftalık/günlük/aylık/4 saatlik).",
            )
            st.caption(
                "VWAP hangi mum periyodunda hesaplansın. Haftalık: dengeli. "
                "Günlük: daha sık ama daha gürültülü sinyal. Aylık: nadir ama güvenilir. "
                "4 Saatlik: gün-içi, en sık/en gürültülü sinyal — Yahoo Finance'in gün-içi "
                "veri limiti nedeniyle geçmişi ~730 gün ile SINIRLIDIR (günlük/haftalık/"
                "aylığa göre çok daha kısa bir VWAP geçmişi görür, bu normaldir)."
            )

            currency_default = saved.get("currency", "TRY")
            currency = st.selectbox(
                "Para birimi bazı",
                options=list(CURRENCY_OPTIONS),
                index=list(CURRENCY_OPTIONS).index(currency_default) if currency_default in CURRENCY_OPTIONS else 0,
                format_func=lambda c: CURRENCY_LABELS[c],
                help="TL dışı seçilirse fiyatlar taramadan önce o para birimine çevrilir.",
            )
            st.caption(
                "TL dışı seçilirse, hissenin günlük TL fiyat serisi taramadan ÖNCE "
                "Yahoo Finance'ten çekilen USDTRY=X / EURTRY=X kuruna bölünerek o para "
                "birimi bazına çevrilir; zincirleme VWAP algoritması, Yatay ve Zirveden "
                "Düşüş filtreleri AYNEN TL taramasındaki gibi, sadece bu çevrilmiş seri "
                "üzerinde çalışır (mantık değişmez, sadece girdi fiyatı değişir)."
            )

            lookback = st.slider(
                "Kaç bar öncesine kadar 'yeni kırılım' sayılsın", 1, 5, int(saved["lookback"]),
                help="Bir VWAP kırılımının 'taze' sayılması için en fazla kaç bar geriye bakılacağını belirler.",
            )
            st.caption(
                "Kırılımın 'taze' sayılması için kaç bar geriye bakılsın. "
                "Artırırsan daha çok (ama bazısı bayat) sonuç; azaltırsan az ama güncel sonuç."
            )

        # --- Hız ayarları ---
        with tab_hiz:
            max_workers = st.slider(
                "Aynı anda kaç hisse çekilsin (paralellik)", 5, 40, int(saved["max_workers"]),
                help="Sadece tarama hızını etkiler, sonuçları değiştirmez. Yüksek değer Yahoo Finance'i geçici olarak engelleyebilir.",
            )
            st.caption(
                "Sadece hızı etkiler, sonuçları değiştirmez. Yüksek değer hızlı tarar ama "
                "Yahoo Finance geçici olarak engelleyebilir — hata alırsan düşür."
            )

            use_cache = st.checkbox(
                "Günlük önbelleği kullan", value=bool(saved["use_cache"]),
                help="Açıksa aynı gün içindeki tekrar taramalar diskten hızlıca okunur ama fiyatlar günün ilk taraması ile donmuş kalır.",
            )
            st.caption(
                "Açıksa aynı gün tekrar taramalar diskten okunur (hızlı ama fiyatlar günün "
                "ilk taraması ile 'donmuş' kalır). Kapalıysa her seferinde taze veri çekilir."
            )

            if st.button("🗑️ Önbelleği temizle (verileri zorla yeniden çek)"):
                import shutil
                from vwap_core import CACHE_DIR
                shutil.rmtree(CACHE_DIR, ignore_errors=True)
                st.success("Önbellek temizlendi.")

        # --- Yatay filtre ayarları ---
        with tab_yatay:
            st.caption(
                "VWAP kırılımından bağımsız ek bir filtre: hisse, VWAP'ın anchor "
                "tarihinden (ATH/IPO) beri dar bir bantta mı kalmış, buna bakar."
            )

            sideways_enabled = st.checkbox(
                "Yatay filtresini etkinleştir", value=bool(saved["sideways_enabled"]),
                help="Hissenin VWAP'ın atıldığı tarihten (ATH/IPO) beri dar bir fiyat bandında kalıp kalmadığını kontrol eder.",
            )

            sideways_method = saved["sideways_method"]
            sideways_months_list = list(saved["sideways_months_list"])
            sideways_min_windows = saved["sideways_min_windows"]
            sideways_range_pct = float(saved["sideways_range_pct"])
            sideways_atr_pct = float(saved["sideways_atr_pct"])
            sideways_show_tag = bool(saved["sideways_show_tag"])
            sideways_show_list = bool(saved["sideways_show_list"])
            combine_mode = saved["sideways_combine_mode"]

            if sideways_enabled:
                method_options = list(SIDEWAYS_METHODS)
                sideways_method = st.selectbox(
                    "Tespit yöntemi",
                    options=method_options,
                    index=method_options.index(sideways_method) if sideways_method in method_options else 0,
                    format_func=lambda m: {
                        "range": "Aralık bazlı (fiyat bandı dar mı)",
                        "atr": "ATR bazlı (oynaklık düşük mü)",
                        "both": "İkisi birden (daha katı)",
                    }[m],
                    help="Aralık bazlı fiyat bandına mı yoksa ATR ile ölçülen oynaklığa mı bakılacağını belirler.",
                )
                st.caption(
                    "Aralık bazlı: dönemin en yüksek/en düşük farkına bakar, gerçek dar "
                    "bant yakalar (önerilen). ATR bazlı: sadece günlük oynaklığa bakar, "
                    "yavaş trend eden hisseleri yanlışlıkla 'yatay' gösterebilir."
                )

                all_month_options = [1, 2, 3, 6, 9, 12, 18, 24, 36]
                valid_default_months = [m for m in sideways_months_list if m in all_month_options] or [3, 6, 12, 18, 24]
                sideways_months_list = st.multiselect(
                    "Vadeler (ay) — her biri kendi penceresinde ayrı kontrol edilir",
                    options=all_month_options, default=valid_default_months,
                    help="Hissenin yatay sayılması için kontrol edilecek zaman pencereleri (ay cinsinden). Birden fazla seçilebilir.",
                )
                st.caption(
                    "Anchor tarihi bir vade için yeterince eski değilse o vade '—' olarak "
                    "işaretlenir. Daha çok vade = daha katı/az sonuç ama daha güvenilir."
                )

                if sideways_months_list:
                    combine_options = ["Hepsinde (en katı)", "En az N tanesinde"]
                    combine_mode = st.radio(
                        "Kaç vadede yatay şartı aransın?",
                        options=combine_options,
                        index=combine_options.index(combine_mode) if combine_mode in combine_options else 0,
                        help="'Hepsinde' seçilen tüm vadelerde yatay şartını arar (daha katı); 'En az N' ise sadece bir kısmında aranmasına izin verir (daha esnek).",
                    )
                    if combine_mode == "En az N tanesinde":
                        default_n = sideways_min_windows or len(sideways_months_list)
                        default_n = min(max(default_n, 1), len(sideways_months_list))
                        n = st.slider(
                            "En az kaç vadede yatay olsun", 1, len(sideways_months_list), default_n,
                            help="Seçilen vadelerden en az kaç tanesinde yatay şartının sağlanması gerektiğini belirler.",
                        )
                        sideways_min_windows = n
                    else:
                        sideways_min_windows = None

                if sideways_method in ("range", "both"):
                    sideways_range_pct = st.slider(
                        "Bant genişliği eşiği (%)", 5, 40, int(sideways_range_pct),
                        help="Bu yüzdenin altında kalan hisseler yatay sayılır.",
                    )
                if sideways_method in ("atr", "both"):
                    sideways_atr_pct = st.slider(
                        "ATR eşiği (%)", 1, 15, int(sideways_atr_pct),
                        help="Bu yüzdenin altında kalan hisseler yatay sayılır.",
                    )

                st.markdown("**Sonuçlarda gösterim**")
                sideways_show_tag = st.checkbox(
                    "VWAP tablolarında 'Yatay' etiketi göster", value=sideways_show_tag,
                    help="VWAP kırılımı sonuç tablosunda, yatay şartını da sağlayan hisselerin yanına bir etiket ekler.",
                )
                sideways_show_list = st.checkbox(
                    "Ayrı 'Yatay Hisseler' bölümü göster (VWAP eşleşmesi şart değil)",
                    value=sideways_show_list,
                    help="VWAP kırılımı olsun olmasın, sadece yatay şartını sağlayan TÜM hisseleri ayrı bir listede gösterir.",
                )

        # --- Zirveden düşüş filtresi ---
        with tab_dusus:
            st.caption(
                "VWAP kırılımından bağımsız ek bir filtre: hisse, VWAP'ın atıldığı "
                "fiyattan (ATH'de gerçek zirve, IPO'da ilk gün kapanışı) bugüne kadar "
                "en az X% düşmüş mü, buna bakar — 'zirveden çökmüş' hisseleri, VWAP "
                "kırılımı henüz gerçekleşmemiş olsa bile yakalamak için."
            )

            drawdown_enabled = st.checkbox(
                "Zirveden düşüş filtresini etkinleştir", value=bool(saved["drawdown_enabled"]),
                help="Hissenin VWAP'ın atıldığı fiyattan bugüne en az belirlenen yüzde kadar düşüp düşmediğini kontrol eder.",
            )

            drawdown_min_pct = float(saved["drawdown_min_pct"])
            drawdown_show_tag = bool(saved["drawdown_show_tag"])
            drawdown_show_list = bool(saved["drawdown_show_list"])

            if drawdown_enabled:
                drawdown_min_pct = st.slider(
                    "En az kaç yüzde düşmüş olsun (%)", 10, 95, int(drawdown_min_pct),
                    help="Örn. 60 seçilirse, VWAP'ın atıldığı fiyattan bugüne en az %60 "
                         "düşmüş hisseler listelenir.",
                )
                st.caption(
                    "Eşiği yükseltirsen (örn. %80) sadece çok ağır çökmüş hisseler kalır, "
                    "sonuç sayısı azalır. Düşürürsen (örn. %30) daha fazla hisse dahil olur."
                )

                st.markdown("**Sonuçlarda gösterim**")
                drawdown_show_tag = st.checkbox(
                    "VWAP tablolarında 'Zirveden Düşüş' etiketi göster", value=drawdown_show_tag,
                    help="VWAP kırılımı sonuç tablosunda, zirveden düşüş şartını da sağlayan hisselerin yanına bir etiket ekler.",
                )
                drawdown_show_list = st.checkbox(
                    "Ayrı 'Zirveden Düşmüş Hisseler' bölümü göster (VWAP eşleşmesi şart değil)",
                    value=drawdown_show_list,
                    help="VWAP kırılımı olsun olmasın, sadece zirveden düşüş şartını sağlayan TÜM hisseleri ayrı bir listede gösterir.",
                )

        # --- Hisse listesi ---
        with tab_liste:
            st.caption(
                "Varsayılan hazır liste kaldırıldı — sadece burada girdiğiniz/yüklediğiniz "
                "semboller taranır. Son kullandığınız liste otomatik hatırlanır."
            )

            TEXTAREA_KEY = "manual_symbols_text"
            if TEXTAREA_KEY not in st.session_state:
                st.session_state[TEXTAREA_KEY] = saved.get("son_semboller_text", "")

            uploaded_file = st.file_uploader(
                "TXT dosyasından hisse ekle (her satıra bir sembol, .IS otomatik eklenir)",
                type=["txt"],
                help="Her satırında bir hisse sembolü olan bir .txt dosyası yükleyin; mevcut listeye eklenir, üzerine yazmaz.",
            )
            if uploaded_file is not None:
                file_sig = f"{uploaded_file.name}_{uploaded_file.size}"
                if st.session_state.get("last_uploaded_sig") != file_sig:
                    content = uploaded_file.read().decode("utf-8", errors="ignore")
                    lines = [l for l in content.splitlines() if l.strip()]
                    new_syms = normalize_symbol_list(lines)
                    existing = normalize_symbol_list(st.session_state[TEXTAREA_KEY].splitlines())
                    merged = normalize_symbol_list(existing + new_syms)
                    st.session_state[TEXTAREA_KEY] = "\n".join(merged)
                    st.session_state.last_uploaded_sig = file_sig
                    st.success(f"{len(new_syms)} sembol yüklendi ve listeye eklendi (kalıcı olarak hatırlanacak).")

            manual_text = st.text_area(
                "Hisse listesi (virgül veya alt alta, .IS eklemenize gerek yok)",
                key=TEXTAREA_KEY, height=140,
                help="Taranacak hisse sembollerini buraya yazın — virgülle ayırarak ya da her satıra bir tane. Son girdiğiniz liste otomatik hatırlanır.",
            )

            symbols = normalize_symbol_list(manual_text.splitlines())
            st.caption(f"Toplam **{len(symbols)}** hisse taranacak.")

        scan_button = st.button("🔍 VWAP Taramasını Başlat", type="primary", width="stretch")

with tab_alt:
    st.caption(
        "VWAP taramasından ve diğer bölümlerden tamamen bağımsızdır — kendi "
        "periyodunu seçin (🔍 VWAP Taraması sekmesindeki periyottan farklı "
        "olabilir) ve sadece son mumdan geriye doğru kesintisiz yeşil-kırmızı "
        "(zigzag) alternasyonu arayın. Taranacak hisse listesi, Ayarlar > "
        "Hisse Listesi sekmesinde girdiğiniz listeyle AYNIDIR."
    )

    if "alt_results" not in st.session_state:
        st.session_state.alt_results = None

    with st.expander("⚙️ Alternasyon Taraması Ayarları", expanded=(st.session_state.alt_results is None)):
        alt_period_options = list(ALTERNATION_SCAN_PERIOD_OPTIONS)
        alt_saved_period = saved.get("alt_scan_period", "4h")
        alt_period = st.selectbox(
            "Periyot (bu tarama için ayrı — VWAP periyodundan bağımsız)",
            options=alt_period_options,
            index=alt_period_options.index(alt_saved_period) if alt_saved_period in alt_period_options else 1,
            format_func=lambda p: ALTERNATION_SCAN_PERIOD_LABELS.get(p, p),
            key="alt_period_select",
            help="Bu bağımsız taramanın hangi mum periyodunda çalışacağını belirler; VWAP taramasının periyodundan tamamen ayrıdır. Ayrıca 'Tümünü Birlikte Tara' çalıştırıldığında BU periyot değil, VWAP'ın kendi periyodu kullanılır.",
        )
        st.caption(
            "1 Saatlik ve 4 Saatlik periyotlar Yahoo Finance'in gün-içi veri limiti "
            "nedeniyle geçmişi ~730 gün ile SINIRLIDIR. Günlük/Haftalık/Aylık çok daha "
            "uzun geçmiş görür ama daha nadir/az gürültülü sinyal üretir (Aylık en nadiri)."
        )
        st.caption(
            "Fitiller (High/Low) dikkate alınmaz, sadece GÖVDE (Close-Open) boyu — "
            "zincirdeki gövdeler birbirine ne kadar yakınsa puan o kadar yüksek olur "
            "(100 = tüm gövdeler eşit boyda)."
        )

        alt_min_chain = st.slider(
            "En az kaç mum kesintisiz alternasyon yapsın", 3, 12,
            int(saved.get("alt_scan_min_chain", ALTERNATION_MIN_CHAIN)), key="alt_min_chain",
            help="Son mumdan geriye doğru en az bu kadar mum boyunca renk her mumda "
                 "değişmeli (yeşil-kırmızı-yeşil-kırmızı...).",
        )
        alt_saved_score = saved.get("alt_scan_min_score", None)
        alt_use_score_filter = st.checkbox(
            "Ayrıca en az bir düzenlilik puanı şartı ara",
            value=alt_saved_score is not None,
            help="Açılırsa, sadece zincir uzunluğu değil, gövde boylarının ne kadar düzenli/eşit olduğu da bir eşik olarak aranır.",
        )
        if alt_use_score_filter:
            alt_default_score = alt_saved_score if alt_saved_score is not None else 50.0
            alt_min_score = st.slider(
                "En az düzenlilik puanı (0-100)", 0, 100, int(alt_default_score),
                help="Gövde boyları birbirine ne kadar yakınsa puan o kadar yüksek "
                     "olur. 100 = zincirdeki tüm gövdeler eşit boyda.",
            )
        else:
            alt_min_score = None
        st.caption(
            "Puan şartı KAPALIYSA sadece zincir uzunluğu yeterlidir (puan sadece "
            "bilgi amaçlı gösterilir). Puan şartı AÇIKSA, bu eşiğin ALTINDA kalan "
            "hisseler eşleşme sayılmaz."
        )

        alt_scan_button = st.button(
            "🔍 Alternasyon Taramasını Başlat", type="primary", width="stretch", key="alt_scan_button",
        )

    # Bu bölümün ayarlarını, ana VWAP ayarlarını SİLMEDEN diske kaydet.
    save_partial_settings({
        "alt_scan_period": alt_period,
        "alt_scan_min_chain": alt_min_chain,
        "alt_scan_min_score": alt_min_score,
    })

with tab_tl:
    st.caption(
        "VWAP taramasından ve diğer sekmelerden tamamen bağımsızdır — "
        "kendi periyodunu seçin (🔍 VWAP Taraması sekmesindeki periyottan farklı "
        "olabilir) ve sadece düşen trend çizgisi kırılımını arayın. Taranacak hisse "
        "listesi, 🔍 VWAP Taraması sekmesi > Ayarlar > Hisse Listesi'nde girdiğiniz listeyle AYNIDIR."
    )

    if "tl_results" not in st.session_state:
        st.session_state.tl_results = None

    with st.expander("⚙️ Trend Çizgisi Taraması Ayarları", expanded=(st.session_state.tl_results is None)):
        tl_period_options = list(TRENDLINE_SCAN_PERIOD_OPTIONS)
        tl_saved_period = saved.get("tl_scan_period", "4h")
        tl_period = st.selectbox(
            "Periyot (bu tarama için ayrı — VWAP periyodundan bağımsız)",
            options=tl_period_options,
            index=tl_period_options.index(tl_saved_period) if tl_saved_period in tl_period_options else 1,
            format_func=lambda p: TRENDLINE_SCAN_PERIOD_LABELS.get(p, p),
            key="tl_period_select",
            help="Bu bağımsız taramanın hangi mum periyodunda çalışacağını belirler; VWAP taramasının periyodundan tamamen ayrıdır.",
        )
        st.caption(
            "1 Saatlik ve 4 Saatlik periyotlar Yahoo Finance'in gün-içi veri limiti "
            "nedeniyle geçmişi ~730 gün ile SINIRLIDIR. Günlük/Haftalık/Aylık çok daha "
            "uzun geçmiş görür ama daha nadir/az gürültülü sinyal üretir (Aylık en nadiri)."
        )

        tl_col1, tl_col2 = st.columns(2)
        with tl_col1:
            tl_pivot_window = st.slider(
                "Pivot penceresi (kaç bar sol/sağ)", 2, 8,
                int(saved.get("tl_scan_pivot_window", TRENDLINE_PIVOT_WINDOW)), key="tl_pivot_window",
                help="Bir barın pivot 'tepe' sayılması için solundaki ve sağındaki kaç "
                     "bar boyunca en yüksek High'a sahip olması gerektiği.",
            )
            tl_min_span_bars = st.slider(
                "Çizginin en az uzunluğu (bar)", 5, 60,
                int(saved.get("tl_scan_min_span_bars", TRENDLINE_MIN_SPAN_BARS)), key="tl_min_span",
                help="İki pivot arası en az bu kadar bar olmalı — çok kısa/anlamsız "
                     "çizgileri eler.",
            )
            tl_lookback_bars = st.slider(
                "Pivot aranacak geçmiş (bar)", 40, 500,
                int(saved.get("tl_scan_lookback_bars", TRENDLINE_LOOKBACK_BARS)), key="tl_lookback_bars",
                help="Trend çizgisi kurmak için en fazla kaç bar geriye bakılsın.",
            )
        with tl_col2:
            tl_breakout_lookback = st.slider(
                "Kaç bar öncesine kadar 'yeni kırılım' sayılsın", 1, 10,
                int(saved.get("tl_scan_breakout_lookback", 3)), key="tl_breakout_lookback",
                help="Bir kırılımın 'taze' sayılması için en fazla kaç bar geriye bakılacağını belirler.",
            )
            tl_touch_tolerance_pct = st.slider(
                "Temas toleransı (%)", 0.5, 5.0,
                float(saved.get("tl_scan_touch_tolerance_pct", TRENDLINE_TOUCH_TOLERANCE_PCT)),
                step=0.5, key="tl_touch_tolerance",
                help="Sadece bilgi amaçlı: bir barın High'ı çizgiye bu yüzde kadar "
                     "yakınsa 'temas' sayılır.",
            )
            tl_min_touches = st.slider(
                "En az temas sayısı", 2, 6,
                int(saved.get("tl_scan_min_touches", TRENDLINE_MIN_TOUCHES)), key="tl_min_touches",
                help="Çizgiyi kuran iki pivot ZATEN temas sayılır (tepedeki pivot = "
                     "1. temas, sondaki pivot = 2. temas) — bu ayar 2'de tutulursa "
                     "davranış değişmez. Örneğin 4'e çıkarırsanız: 1. temas (tepe) → "
                     "2. temas → 3. temas (ara pivotlar) → 4. temas (kırılımdan önceki "
                     "son değme) gibi çizgiye DAHA ÇOK kez değmiş, dolayısıyla daha "
                     "'kanıtlanmış' çizgiler aranır; az sayıda temaslı (zayıf) çizgiler elenir.",
            )
            tl_require_volume = st.checkbox(
                "Kırılım barında hacim teyidi şart olsun",
                value=bool(saved.get("tl_scan_require_volume", False)), key="tl_require_volume",
                help="Açılırsa, sadece fiyat kırılımı yeterli olmaz; kırılım barında da yüksek hacim şart koşulur.",
            )
            tl_volume_factor = float(saved.get("tl_scan_volume_factor", TRENDLINE_VOLUME_FACTOR))
            if tl_require_volume:
                tl_volume_factor = st.slider(
                    "Kırılım hacmi, son 20 barın ortalamasının en az kaç katı olsun",
                    1.0, 5.0, tl_volume_factor, step=0.1, key="tl_volume_factor",
                    help="Kırılım barının hacminin, son 20 barın ortalama hacminin en az kaç katı olması gerektiğini belirler.",
                )

        tl_scan_button = st.button(
            "🔍 Trend Çizgisi Taramasını Başlat", type="primary", width="stretch", key="tl_scan_button",
        )

    # Bu bölümün ayarlarını, ana VWAP ayarlarını SİLMEDEN diske kaydet.
    save_partial_settings({
        "tl_scan_period": tl_period,
        "tl_scan_pivot_window": tl_pivot_window,
        "tl_scan_min_span_bars": tl_min_span_bars,
        "tl_scan_lookback_bars": tl_lookback_bars,
        "tl_scan_breakout_lookback": tl_breakout_lookback,
        "tl_scan_touch_tolerance_pct": tl_touch_tolerance_pct,
        "tl_scan_min_touches": tl_min_touches,
        "tl_scan_require_volume": tl_require_volume,
        "tl_scan_volume_factor": tl_volume_factor,
    })

with tab_tri:
    st.caption(
        "VWAP taramasından ve diğer sekmelerden tamamen bağımsızdır — "
        "kendi periyodunu seçin (🔍 VWAP Taraması sekmesindeki periyottan farklı "
        "olabilir) ve sadece kırılmak ÜZERE olan (henüz kırılmamış, sıkışmış, "
        "apex'e yaklaşmış) üçgenleri arayın. Taranacak hisse listesi, Ayarlar > "
        "Hisse Listesi sekmesinde girdiğiniz listeyle AYNIDIR."
    )

    if "tri_results" not in st.session_state:
        st.session_state.tri_results = None

    with st.expander("⚙️ Üçgen Kırılım Taraması Ayarları", expanded=(st.session_state.tri_results is None)):
        tri_period_options = list(TRIANGLE_SCAN_PERIOD_OPTIONS)
        tri_saved_period = saved.get("tri_scan_period", "4h")
        tri_period = st.selectbox(
            "Periyot (bu tarama için ayrı — VWAP periyodundan bağımsız)",
            options=tri_period_options,
            index=tri_period_options.index(tri_saved_period) if tri_saved_period in tri_period_options else 1,
            format_func=lambda p: TRIANGLE_SCAN_PERIOD_LABELS.get(p, p),
            key="tri_period_select",
            help="Bu bağımsız taramanın hangi mum periyodunda çalışacağını belirler; VWAP taramasının periyodundan tamamen ayrıdır.",
        )
        st.caption(
            "1 Saatlik ve 4 Saatlik periyotlar Yahoo Finance'in gün-içi veri limiti "
            "nedeniyle geçmişi ~730 gün ile SINIRLIDIR. Günlük/Haftalık/Aylık çok daha "
            "uzun geçmiş görür ama daha nadir/az gürültülü sinyal üretir (Aylık en nadiri)."
        )

        tri_col1, tri_col2 = st.columns(2)
        with tri_col1:
            tri_pivot_window = st.slider(
                "Pivot penceresi (kaç bar sol/sağ)", 2, 8,
                int(saved.get("tri_scan_pivot_window", TRIANGLE_PIVOT_WINDOW)), key="tri_scan_pivot_window",
                help="Bir barın pivot tepe/dip sayılması için solundaki ve sağındaki kaç "
                     "bar boyunca en yüksek/en düşük olması gerektiği.",
            )
            tri_min_span_bars = st.slider(
                "Her çizginin en az uzunluğu (bar)", 5, 60,
                int(saved.get("tri_scan_min_span_bars", TRIANGLE_MIN_SPAN_BARS)), key="tri_scan_min_span",
                help="İki pivot arası en az bu kadar bar olmalı — çok kısa/anlamsız "
                     "çizgileri eler.",
            )
            tri_lookback_bars = st.slider(
                "Pivot aranacak geçmiş (bar)", 40, 500,
                int(saved.get("tri_scan_lookback_bars", TRIANGLE_LOOKBACK_BARS)), key="tri_scan_lookback_bars",
                help="Üçgen çizgilerini kurmak için en fazla kaç bar geriye bakılsın.",
            )
        with tri_col2:
            tri_min_apex_bars_ahead = st.slider(
                "Apex en az kaç bar sonra olsun", 1, 20,
                int(saved.get("tri_scan_min_apex_bars_ahead", TRIANGLE_MIN_APEX_BARS_AHEAD)),
                key="tri_scan_min_apex",
                help="Apex bundan daha YAKIN geçmişteyse üçgen zaten tükenmiş sayılır.",
            )
            tri_max_apex_bars_ahead = st.slider(
                "Apex en fazla kaç bar sonra olsun", 5, 100,
                int(saved.get("tri_scan_max_apex_bars_ahead", TRIANGLE_MAX_APEX_BARS_AHEAD)),
                key="tri_scan_max_apex",
                help="Apex bundan daha UZAKTAYSA henüz erken sayılır. Düşük değer = "
                     "sadece kırılıma çok yakın üçgenleri yakalar.",
            )
            tri_max_squeeze_pct = st.slider(
                "En fazla sıkışma oranı (%)", 10.0, 90.0,
                float(saved.get("tri_scan_max_squeeze_pct", TRIANGLE_MAX_SQUEEZE_PCT)), step=5.0,
                key="tri_scan_squeeze",
                help="Üçgenin ŞU ANKİ genişliğinin, BAŞLANGICINDAKİ genişliğine oranı bu "
                     "yüzdenin ÜSTÜNDEYSE henüz yeterince daralmamış sayılır.",
            )

        tri_scan_button = st.button(
            "🔍 Üçgen Kırılım Taramasını Başlat", type="primary", width="stretch", key="tri_scan_button",
        )

    # Bu bölümün ayarlarını, ana VWAP ayarlarını SİLMEDEN diske kaydet.
    save_partial_settings({
        "tri_scan_period": tri_period,
        "tri_scan_pivot_window": tri_pivot_window,
        "tri_scan_min_span_bars": tri_min_span_bars,
        "tri_scan_lookback_bars": tri_lookback_bars,
        "tri_scan_min_apex_bars_ahead": tri_min_apex_bars_ahead,
        "tri_scan_max_apex_bars_ahead": tri_max_apex_bars_ahead,
        "tri_scan_max_squeeze_pct": tri_max_squeeze_pct,
    })

# ------------------------------------------------------------------
# Güncel ayarları diske kaydet (her rerun'da — böylece her değişiklik
# anında kalıcı olur, ayrı bir "kaydet" butonuna gerek kalmaz).
#
# DÜZELTME: Burada YANLIŞLIKLA save_settings() (dosyanın TAMAMINI bu
# dict'in içeriğiyle DEĞİŞTİREN ham fonksiyon) çağrılıyordu. Bu blok
# script'in EN SONUNDA çalıştığı için, ondan hemen önce tab_alt/tab_tl/
# tab_tri bloklarının save_partial_settings() ile diske yazdığı
# alt_scan_*, tl_scan_*, tri_scan_* ayarlarının hepsini, HER rerun'da
# (yani her tek widget etkileşiminde) SESSİZCE SİLİYORDU — "ayarlarım
# bir sonraki oturumda kalıcı olmuyor" şikayetinin asıl sebebi buydu.
# save_partial_settings() kullanmak, önce diskteki mevcut ayarları
# okuyup SADECE bu anahtarları güncelleyerek geri yazıyor; diğer
# sekmelerin az önce kaydettiği ayarlara dokunmuyor.
# ------------------------------------------------------------------
save_partial_settings({
    "period": period,
    "currency": currency,
    "lookback": lookback,
    "max_workers": max_workers,
    "use_cache": use_cache,
    "sideways_enabled": sideways_enabled,
    "sideways_method": sideways_method,
    "sideways_months_list": sideways_months_list,
    "sideways_combine_mode": combine_mode,
    "sideways_min_windows": sideways_min_windows,
    "sideways_range_pct": sideways_range_pct,
    "sideways_atr_pct": sideways_atr_pct,
    "sideways_show_tag": sideways_show_tag,
    "sideways_show_list": sideways_show_list,
    "drawdown_enabled": drawdown_enabled,
    "drawdown_min_pct": drawdown_min_pct,
    "drawdown_show_tag": drawdown_show_tag,
    "drawdown_show_list": drawdown_show_list,
    "son_semboller_text": manual_text,
})


with tab_all:
    st.caption(
        "Diğer dört sekmede (VWAP, Alternasyon, Trend Çizgisi, Üçgen) zaten "
        "girdiğiniz TÜM ayarları kullanarak, VWAP'ın periyodunda hepsini TEK "
        "taramada birleştirir — VWAP tablosunda diğer üçü etiket olarak görünür, "
        "her biri için ayrıca ayrı bir liste de gösterilir."
    )
    scan_all_button = st.button(
        "🚀 Tümünü Birlikte Tara", type="primary", width="stretch", key="scan_all_button",
    )

# ------------------------------------------------------------------
# Taramayı çalıştır
# ------------------------------------------------------------------
if scan_button or scan_all_button:
    run_alternation = bool(scan_all_button)
    run_trendline = bool(scan_all_button)
    run_triangle = bool(scan_all_button)
    if not symbols:
        st.error("Taranacak hisse yok — 🔍 VWAP Taraması sekmesi > Ayarlar > Hisse Listesi'nden sembol ekleyin.")
    else:
        progress = st.progress(0.0, text="Başlıyor...")
        status_text = st.empty()
        start_time = time.time()
        fetch_errors = []

        def on_progress(done, total, sym):
            progress.progress(done / total, text=f"{done}/{total} tamamlandı — son: {sym}")

        results = scan_symbols_parallel(
            symbols, period, lookback=lookback,
            max_workers=max_workers, use_cache=use_cache,
            progress_callback=on_progress, errors_out=fetch_errors,
            sideways_enabled=sideways_enabled, sideways_months_list=sideways_months_list,
            sideways_range_pct=sideways_range_pct, sideways_atr_pct=sideways_atr_pct,
            sideways_method=sideways_method, sideways_min_windows=sideways_min_windows,
            drawdown_enabled=drawdown_enabled, drawdown_min_pct=drawdown_min_pct,
            alternation_enabled=run_alternation, alternation_min_chain=alt_min_chain,
            alternation_min_score=alt_min_score,
            trendline_enabled=run_trendline, trendline_pivot_window=tl_pivot_window,
            trendline_min_span_bars=tl_min_span_bars,
            trendline_lookback_bars=tl_lookback_bars,
            trendline_breakout_lookback=tl_breakout_lookback,
            trendline_touch_tolerance_pct=tl_touch_tolerance_pct,
            trendline_min_touches=tl_min_touches,
            trendline_require_volume=tl_require_volume,
            trendline_volume_factor=tl_volume_factor,
            triangle_enabled=run_triangle, triangle_pivot_window=tri_pivot_window,
            triangle_min_span_bars=tri_min_span_bars,
            triangle_lookback_bars=tri_lookback_bars,
            triangle_min_apex_bars_ahead=tri_min_apex_bars_ahead,
            triangle_max_apex_bars_ahead=tri_max_apex_bars_ahead,
            triangle_max_squeeze_pct=tri_max_squeeze_pct,
            currency=currency,
        )
        (results, sideways_matches, drawdown_matches, alternation_matches,
         trendline_matches, triangle_matches) = results

        elapsed = time.time() - start_time
        progress.empty()
        status_text.caption(f"Tarama {elapsed:.0f} saniyede tamamlandı.")
        st.session_state.results = results
        st.session_state.sideways_matches = sideways_matches
        st.session_state.sideways_show_tag = sideways_show_tag
        st.session_state.sideways_show_list = sideways_show_list
        st.session_state.sideways_months_list_used = sideways_months_list
        st.session_state.drawdown_matches = drawdown_matches
        st.session_state.drawdown_show_tag = drawdown_show_tag
        st.session_state.drawdown_show_list = drawdown_show_list
        st.session_state.drawdown_min_pct_used = drawdown_min_pct
        st.session_state.alternation_matches = alternation_matches
        st.session_state.alternation_show_tag = run_alternation
        st.session_state.alternation_show_list = run_alternation
        st.session_state.alternation_min_chain_used = alt_min_chain
        st.session_state.alternation_min_score_used = alt_min_score
        st.session_state.trendline_matches = trendline_matches
        st.session_state.trendline_show_tag = run_trendline
        st.session_state.trendline_show_list = run_trendline
        st.session_state.trendline_require_volume_used = tl_require_volume
        st.session_state.triangle_matches = triangle_matches
        st.session_state.triangle_show_tag = run_triangle
        st.session_state.triangle_show_list = run_triangle
        st.session_state.scanned_period = period
        st.session_state.scanned_currency = currency
        st.session_state.scan_time = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.fetch_errors = fetch_errors
        st.session_state.total_scanned = len(symbols)

# ------------------------------------------------------------------
# Sonuçları göster
# ------------------------------------------------------------------

with tab_vwap:
    results = st.session_state.results
    fetch_errors = st.session_state.get("fetch_errors", [])
    total_scanned = st.session_state.get("total_scanned", 0)
    sideways_matches = st.session_state.get("sideways_matches", [])
    sideways_show_tag = st.session_state.get("sideways_show_tag", False)
    sideways_show_list = st.session_state.get("sideways_show_list", False)
    sideways_months_list_used = st.session_state.get("sideways_months_list_used", [3, 6, 12, 18, 24])
    sideways_by_symbol = {s["symbol"]: s for s in sideways_matches}
    drawdown_matches = st.session_state.get("drawdown_matches", [])
    drawdown_show_tag = st.session_state.get("drawdown_show_tag", False)
    drawdown_show_list = st.session_state.get("drawdown_show_list", False)
    drawdown_min_pct_used = st.session_state.get("drawdown_min_pct_used", 60.0)
    drawdown_by_symbol = {d["symbol"]: d for d in drawdown_matches}
    alternation_matches = st.session_state.get("alternation_matches", [])
    alternation_show_tag = st.session_state.get("alternation_show_tag", False)
    alternation_show_list = st.session_state.get("alternation_show_list", False)
    alternation_min_chain_used = st.session_state.get("alternation_min_chain_used", ALTERNATION_MIN_CHAIN)
    alternation_min_score_used = st.session_state.get("alternation_min_score_used", None)
    alternation_by_symbol = {a["symbol"]: a for a in alternation_matches}
    trendline_matches = st.session_state.get("trendline_matches", [])
    trendline_show_tag = st.session_state.get("trendline_show_tag", False)
    trendline_show_list = st.session_state.get("trendline_show_list", False)
    trendline_require_volume_used = st.session_state.get("trendline_require_volume_used", False)
    trendline_by_symbol = {t["symbol"]: t for t in trendline_matches}
    triangle_matches = st.session_state.get("triangle_matches", [])
    triangle_show_tag = st.session_state.get("triangle_show_tag", False)
    triangle_show_list = st.session_state.get("triangle_show_list", False)
    triangle_by_symbol = {t["symbol"]: t for t in triangle_matches}

    LEVEL_BADGE = {1: "🥇", 2: "🥈", 3: "🥉"}

    if results is None:
        st.info("👆 Yukarıdaki **Ayarlar** panelinden ayarları yapıp **Taramayı Başlat**'a basın.")
    elif fetch_errors and len(fetch_errors) == total_scanned:
        st.error(
            f"**{total_scanned} hissenin hiçbirinde veri çekilemedi** — tarama aslında hiç "
            "çalışmadı, sadece 'eşleşme yok' gibi göründü. Muhtemel sebep: internet "
            "bağlantısı, Yahoo Finance'in geçici engeli ya da yfinance sürümü."
        )
        with st.expander("İlk birkaç hatayı gör"):
            for sym, err in fetch_errors[:10]:
                st.code(f"{sym}: {err}")
        st.caption(
            "Denenecekler: birkaç dakika sonra tekrar deneyin, paralelliği 5-10'a düşürün, "
            "`pip install --upgrade yfinance` ile kütüphaneyi güncelleyin."
        )
    else:
        st.markdown("---")

        # --- Üst özet: durum kartları ---
        c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
        c1.metric("Taranan hisse", total_scanned)
        c2.metric("VWAP eşleşmesi", len(results))
        c3.metric("Yatay hisse", len(sideways_matches) if sideways_show_list else "—")
        c4.metric("Zirveden düşmüş", len(drawdown_matches) if drawdown_show_list else "—")
        c5.metric("Alternasyon", len(alternation_matches) if alternation_show_list else "—")
        c6.metric("Trend Kırılımı", len(trendline_matches) if trendline_show_list else "—")
        c7.metric("Üçgen", len(triangle_matches) if triangle_show_list else "—")
        c8.metric("Hata", len(fetch_errors))
        scanned_currency = st.session_state.get("scanned_currency", "TRY")
        st.caption(
            f"Periyot: **{PERIOD_LABELS.get(st.session_state.scanned_period, st.session_state.scanned_period)}** "
            f"· Para birimi: **{CURRENCY_AXIS_LABELS.get(scanned_currency, scanned_currency)}** "
            f"· Tarama zamanı: **{st.session_state.scan_time}**"
        )
        if fetch_errors:
            with st.expander(f"⚠️ {len(fetch_errors)} hissenin verisi çekilemedi (tarama dışı kaldı)"):
                for sym, err in fetch_errors[:20]:
                    st.code(f"{sym}: {err}")

        if len(results) == 0:
            st.warning("Kriterlere uyan hisse bulunamadı.")
        else:
            # Özet tablo — tam liste CSV indirmek için (Seviye sütunuyla)
            unit_label = CURRENCY_AXIS_LABELS.get(scanned_currency, scanned_currency)
            table_rows = [{
                "Sembol": r["symbol"],
                "Seviye": f"VWAP-{r['level']}",
                "Anchor": f"{r['anchor_date']} ({r['anchor_reason']})",
                "Kırılma Tarihi": r["cross_date"],
                "Kaç Bar Önce": r["bars_ago"],
                f"Son Kapanış ({unit_label})": r["last_close"],
                f"VWAP Değeri ({unit_label})": r["last_vwap"],
                **({"Yatay": (f"✅ {sideways_by_symbol[r['symbol']]['sideways_count']}/{sideways_by_symbol[r['symbol']]['total_windows']}" if r["symbol"] in sideways_by_symbol else "")} if sideways_show_tag else {}),
                **({"Zirveden Düşüş": (f"📉 %{drawdown_by_symbol[r['symbol']]['drawdown_pct']:.0f}" if r["symbol"] in drawdown_by_symbol else "")} if drawdown_show_tag else {}),
                **({"Alternasyon": (f"🔀 {alternation_by_symbol[r['symbol']]['chain_length']} mum · puan {alternation_by_symbol[r['symbol']]['score']:.0f}" if r["symbol"] in alternation_by_symbol else "")} if alternation_show_tag else {}),
                **({"Trend Çizgisi": (f"📐 {trendline_by_symbol[r['symbol']]['touches']} temas · {trendline_by_symbol[r['symbol']]['bars_ago']} bar önce" if r["symbol"] in trendline_by_symbol else "")} if trendline_show_tag else {}),
                **({"Üçgen": (f"🔺 {triangle_by_symbol[r['symbol']]['pattern_type']} · apex ~{triangle_by_symbol[r['symbol']]['apex_bars_ahead']:.0f} bar" if r["symbol"] in triangle_by_symbol else "")} if triangle_show_tag else {}),
            } for r in results]
            summary_df = pd.DataFrame(table_rows).sort_values(["Seviye", "Sembol"])
            csv = summary_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Tüm sonuçları CSV indir", csv, "bist_vwap_sonuclar.csv", "text/csv")

            st.caption("💡 Bir hisseye **çift tıklayın** — grafiği açılır pencerede hemen görünür.")

            results_by_symbol = {r["symbol"]: r for r in results}
            levels_present = sorted(set(r["level"] for r in results))

            # streamlit-aggrid'in bir "kusuru": bir tabloda seçilen satır, o tabloya
            # tekrar dokunulmadığı sürece SONRAKİ her rerun'da da "seçili" olarak
            # geri dönmeye devam ediyor (bayat/eski seçim). Başka bir panelde yeni
            # bir hisseye çift tıklandığında, eski panelin bu bayat seçimi döngüde
            # önce geldiği için yanlış hisseyi açtırıyordu. Çözüm: her tablo için
            # "bu tabloda en son hangi hisseyi açtık" bilgisini session_state'te
            # tutup, sadece GERÇEKTEN DEĞİŞEN (daha önce o tablo için açılmamış)
            # bir seçimi dikkate alıyoruz.
            if "last_shown_symbol" not in st.session_state:
                st.session_state.last_shown_symbol = {}

            pending_symbol = None
            pending_grid_key = None

            level_tabs = st.tabs([
                f"{LEVEL_BADGE.get(lvl, '')} VWAP-{lvl}  ({len([r for r in results if r['level'] == lvl])})"
                for lvl in levels_present
            ])

            for lvl, tab in zip(levels_present, level_tabs):
                with tab:
                    level_results = [r for r in results if r["level"] == lvl]

                    level_rows = [{
                        "Sembol": r["symbol"],
                        "Anchor": f"{r['anchor_date']} ({r['anchor_reason']})",
                        "Kırılma Tarihi": r["cross_date"],
                        "Kaç Bar Önce": r["bars_ago"],
                        f"Son Kapanış ({unit_label})": r["last_close"],
                        f"VWAP Değeri ({unit_label})": r["last_vwap"],
                        **({"Yatay": (f"✅ {sideways_by_symbol[r['symbol']]['sideways_count']}/{sideways_by_symbol[r['symbol']]['total_windows']}" if r["symbol"] in sideways_by_symbol else "")} if sideways_show_tag else {}),
                        **({"Zirveden Düşüş": (f"📉 %{drawdown_by_symbol[r['symbol']]['drawdown_pct']:.0f}" if r["symbol"] in drawdown_by_symbol else "")} if drawdown_show_tag else {}),
                        **({"Alternasyon": (f"🔀 {alternation_by_symbol[r['symbol']]['chain_length']} mum · puan {alternation_by_symbol[r['symbol']]['score']:.0f}" if r["symbol"] in alternation_by_symbol else "")} if alternation_show_tag else {}),
                        **({"Trend Çizgisi": (f"📐 {trendline_by_symbol[r['symbol']]['touches']} temas · {trendline_by_symbol[r['symbol']]['bars_ago']} bar önce" if r["symbol"] in trendline_by_symbol else "")} if trendline_show_tag else {}),
                        **({"Üçgen": (f"🔺 {triangle_by_symbol[r['symbol']]['pattern_type']} · apex ~{triangle_by_symbol[r['symbol']]['apex_bars_ahead']:.0f} bar" if r["symbol"] in triangle_by_symbol else "")} if triangle_show_tag else {}),
                    } for r in level_results]
                    level_df = pd.DataFrame(level_rows).sort_values("Sembol").reset_index(drop=True)

                    grid_key = f"aggrid_level_{lvl}_{st.session_state.scan_time}"
                    selected_row = render_double_click_table(level_df, grid_key=grid_key)

                    if selected_row is not None:
                        candidate_symbol = selected_row["Sembol"]
                        # Bu tablo için en son açtığımız hisseyle AYNIYSA, bu bayat bir
                        # seçimdir (kullanıcı yeni bir şey yapmadı) — yok say.
                        if candidate_symbol != st.session_state.last_shown_symbol.get(grid_key) \
                                and pending_symbol is None:
                            pending_symbol = candidate_symbol
                            pending_grid_key = grid_key

            # Bir satıra ÇİFT TIKLANINCA grafiği AÇILIR PENCEREDE göster — sayfa değişmez.
            if pending_symbol is not None:
                st.session_state.last_shown_symbol[pending_grid_key] = pending_symbol
                show_chart_popup(pending_symbol, results_by_symbol[pending_symbol])

        # ------------------------------------------------------------------
        # Yatay Hisseler — VWAP eşleşmesinden BAĞIMSIZ ayrı bölüm.
        # ------------------------------------------------------------------
        if sideways_show_list:
            months_label = "-".join(str(m) for m in sideways_months_list_used) + " ay"
            with st.expander(f"📏 Yatay Hisseler · vadeler: {months_label} · {len(sideways_matches)} hisse", expanded=False):
                st.caption(
                    "VWAP kırılımından bağımsızdır. 'Yatay Vade' sütunu kaç vadede/toplam kaç "
                    "vadede yatay çıktığını gösterir."
                )
                if sideways_matches:
                    sideways_rows = [{
                        "Sembol": s["symbol"],
                        "VWAP Anchor": f"{s['anchor_date']} ({s['anchor_reason']})" if s.get("anchor_date") else "—",
                        "Yatay Vade": f"{s['sideways_count']}/{s['total_windows']}",
                        "Yatay Olan Aylar": ", ".join(str(m) for m in s["sideways_months"]),
                        **{
                            f"{m} Ay — Bant(%)": (s["details"][m]["range_pct"] if s["details"].get(m) else "—")
                            for m in sideways_months_list_used
                        },
                    } for s in sideways_matches]
                    sideways_df = pd.DataFrame(sideways_rows).sort_values("Sembol").reset_index(drop=True)
                    st.dataframe(sideways_df, width="stretch", hide_index=True)
                    sideways_csv = sideways_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("⬇️ Yatay hisseleri CSV indir", sideways_csv,
                                        "bist_yatay_sonuclar.csv", "text/csv", key="sideways_csv_download")
                else:
                    st.caption("Kriterlere uyan yatay hisse bulunamadı.")

        # ------------------------------------------------------------------
        # Zirveden Düşmüş Hisseler — VWAP eşleşmesinden BAĞIMSIZ ayrı bölüm.
        # ------------------------------------------------------------------
        if drawdown_show_list:
            with st.expander(f"📉 Zirveden Düşmüş Hisseler · eşik: %{drawdown_min_pct_used:.0f}+ · {len(drawdown_matches)} hisse", expanded=False):
                st.caption(
                    "VWAP kırılımından bağımsızdır. VWAP'ın atıldığı fiyattan (ATH'de gerçek "
                    "zirve, IPO'da ilk gün kapanışı) bugüne kadarki düşüş yüzdesini gösterir."
                )
                if drawdown_matches:
                    drawdown_rows = [{
                        "Sembol": d["symbol"],
                        "Zirveden Düşüş": f"%{d['drawdown_pct']:.1f}",
                        "VWAP Anchor": f"{d['anchor_date']} ({d['anchor_reason']})" if d.get("anchor_date") else "—",
                    } for d in drawdown_matches]
                    drawdown_df = pd.DataFrame(drawdown_rows).sort_values(
                        "Zirveden Düşüş", key=lambda s: s.str.replace("%", "").astype(float), ascending=False,
                    ).reset_index(drop=True)
                    st.dataframe(drawdown_df, width="stretch", hide_index=True)
                    drawdown_csv = drawdown_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("⬇️ Zirveden düşmüş hisseleri CSV indir", drawdown_csv,
                                        "bist_zirveden_dusus_sonuclar.csv", "text/csv", key="drawdown_csv_download")
                else:
                    st.caption("Kriterlere uyan zirveden-düşmüş hisse bulunamadı.")

        # ------------------------------------------------------------------
        # Alternasyon (Zigzag) Yapan Hisseler — VWAP eşleşmesinden BAĞIMSIZ ayrı bölüm.
        # ------------------------------------------------------------------
        if alternation_show_list:
            score_label = (f" · en az puan: {alternation_min_score_used:.0f}"
                           if alternation_min_score_used is not None else "")
            with st.expander(
                f"🔀 Alternasyon Yapan Hisseler · en az {alternation_min_chain_used} mum{score_label} · "
                f"{len(alternation_matches)} hisse", expanded=False,
            ):
                st.caption(
                    "VWAP kırılımından bağımsızdır. Son mumdan geriye doğru kesintisiz "
                    "yeşil/kırmızı alternasyonu (zigzag) yapan hisseleri listeler. "
                    "Düzenlilik puanı, zincirdeki mumların GÖVDE boyunun (fitiller hariç) "
                    "birbirine ne kadar yakın olduğunu gösterir — 100 = tüm gövdeler eşit."
                )
                if alternation_matches:
                    alternation_rows = [{
                        "Sembol": a["symbol"],
                        "Zincir Uzunluğu": f"{a['chain_length']} mum",
                        "Düzenlilik Puanı": a["score"],
                        "Renk Sırası (son mumdan geriye)": " → ".join(a["colors"][::-1]),
                        "Başlangıç": a.get("start_date", "—"),
                        "Bitiş (son mum)": a.get("end_date", "—"),
                    } for a in alternation_matches]
                    alternation_df = pd.DataFrame(alternation_rows).sort_values(
                        "Düzenlilik Puanı", ascending=False,
                    ).reset_index(drop=True)
                    st.dataframe(alternation_df, width="stretch", hide_index=True)
                    alternation_csv = alternation_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("⬇️ Alternasyon yapan hisseleri CSV indir", alternation_csv,
                                        "bist_alternasyon_sonuclar.csv", "text/csv", key="alternation_csv_download")
                else:
                    st.caption("Kriterlere uyan alternasyon (zigzag) yapan hisse bulunamadı.")

        # ------------------------------------------------------------------
        # Düşen Trend Çizgisini Kıranlar — VWAP eşleşmesinden BAĞIMSIZ ayrı bölüm.
        # ------------------------------------------------------------------
        if trendline_show_list:
            vol_label = " · hacim teyitli" if trendline_require_volume_used else ""
            with st.expander(
                f"📐 Düşen Trend Çizgisini Kıranlar{vol_label} · {len(trendline_matches)} hisse",
                expanded=False,
            ):
                st.caption(
                    "VWAP kırılımından bağımsızdır. Pivot tepe noktalarından kurulan "
                    "düşen bir direnç çizgisini (üst zarf/upper hull yöntemiyle) "
                    "kapanışla yukarı kıran hisseleri listeler — ekteki TradingView "
                    "örneğindeki gibi. 'Temas' sütunu çizginin kaç pivotla test "
                    "edildiğini gösterir; ne kadar çoksa çizgi o kadar 'gerçek'dir."
                )
                if trendline_matches:
                    trendline_rows = [{
                        "Sembol": t["symbol"],
                        "Temas Sayısı": t["touches"],
                        "Çizgi Başlangıcı": t.get("start_date", "—"),
                        "Çizgi Bitişi": t.get("end_date", "—"),
                        "Kırılma Tarihi": t["cross_date"],
                        "Kaç Bar Önce": t["bars_ago"],
                        "Son Kapanış": t["last_close"],
                        "Çizginin Şu Anki Değeri": t["line_value_now"],
                        **({"Hacim Teyidi": ("✅" if t.get("volume_confirmed") else "❌")}
                           if trendline_require_volume_used else {}),
                    } for t in trendline_matches]
                    trendline_df = pd.DataFrame(trendline_rows).sort_values(
                        "Kaç Bar Önce",
                    ).reset_index(drop=True)
                    st.dataframe(trendline_df, width="stretch", hide_index=True)
                    trendline_csv = trendline_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("⬇️ Trend çizgisi kırılımlarını CSV indir", trendline_csv,
                                        "bist_trend_cizgisi_sonuclar.csv", "text/csv", key="trendline_csv_download")
                else:
                    st.caption("Kriterlere uyan trend çizgisi kırılımı bulunamadı.")

        # ------------------------------------------------------------------
        # Kırılmak Üzere Olan Üçgenler — VWAP eşleşmesinden BAĞIMSIZ ayrı bölüm.
        # ------------------------------------------------------------------
        if triangle_show_list:
            with st.expander(
                f"🔺 Kırılmak Üzere Olan Üçgenler · {len(triangle_matches)} hisse",
                expanded=False,
            ):
                st.caption(
                    "VWAP kırılımından bağımsızdır. Fiyat hâlâ yakınsayan iki çizgi "
                    "(direnç + destek) ARASINDA, üçgen belirgin şekilde SIKIŞMIŞ ve "
                    "apex'e (kesişim noktasına) yaklaşılmış hisseleri listeler — yani "
                    "kırılım henüz OLMAMIŞ ama YAKLAŞTIĞI düşünülen hisseler. 'Sıkışma' "
                    "ne kadar düşükse üçgen o kadar daralmış; 'Hacim Kuruması' ne kadar "
                    "düşükse (örn. %40) sıkışma sırasında hacim o kadar kurumuş — klasik "
                    "bir ön-kırılım işaretidir."
                )
                if triangle_matches:
                    triangle_rows = [{
                        "Sembol": t["symbol"],
                        "Desen": t["pattern_type"],
                        "Apex (bar sonra)": t["apex_bars_ahead"],
                        "Sıkışma (%)": t["squeeze_pct"],
                        "Son Kapanış": t["last_close"],
                        "Üst Çizgi (Direnç)": t["upper_now"],
                        "Alt Çizgi (Destek)": t["lower_now"],
                        "Hacim Kuruması (%)": t.get("volume_dryness_pct", "—"),
                        "Başlangıç": t.get("start_date", "—"),
                        "Bitiş (son mum)": t.get("end_date", "—"),
                    } for t in triangle_matches]
                    triangle_df = pd.DataFrame(triangle_rows).sort_values(
                        "Apex (bar sonra)",
                    ).reset_index(drop=True)

                    triangle_csv = triangle_df.to_csv(index=False).encode("utf-8-sig")
                    st.download_button("⬇️ Kırılmak üzere olan üçgenleri CSV indir", triangle_csv,
                                        "bist_ucgen_sonuclar.csv", "text/csv", key="triangle_csv_download")

                    st.caption("💡 Bir hisseye **çift tıklayın** — grafiği açılır pencerede görünür.")

                    triangle_results_by_symbol = {t["symbol"]: t for t in triangle_matches}
                    if "vwap_triangle_last_shown_symbol" not in st.session_state:
                        st.session_state.vwap_triangle_last_shown_symbol = {}

                    triangle_grid_key = f"aggrid_vwap_triangle_{st.session_state.scan_time}"
                    triangle_selected_row = render_double_click_table(triangle_df, grid_key=triangle_grid_key)

                    if triangle_selected_row is not None:
                        tri_candidate_symbol = triangle_selected_row["Sembol"]
                        if tri_candidate_symbol != st.session_state.vwap_triangle_last_shown_symbol.get(triangle_grid_key):
                            st.session_state.vwap_triangle_last_shown_symbol[triangle_grid_key] = tri_candidate_symbol
                            show_triangle_chart_popup(tri_candidate_symbol, triangle_results_by_symbol[tri_candidate_symbol])
                else:
                    st.caption("Kriterlere uyan (kırılmak üzere olan) üçgen bulunamadı.")



with tab_alt:
    st.markdown("---")
    st.subheader("🔀 Alternasyon Taraması Sonuçları")

    if alt_scan_button:
        if not symbols:
            st.error("Taranacak hisse yok — 🔍 VWAP Taraması sekmesi > Ayarlar > Hisse Listesi'nden sembol ekleyin.")
        else:
            alt_progress = st.progress(0.0, text="Başlıyor...")
            alt_status = st.empty()
            alt_start_time = time.time()
            alt_errors = []

            def alt_on_progress(done, total, sym):
                alt_progress.progress(done / total, text=f"{done}/{total} tamamlandı — son: {sym}")

            alt_matches = scan_alternation_symbols_parallel(
                symbols, alt_period, max_workers=max_workers, use_cache=use_cache,
                progress_callback=alt_on_progress, errors_out=alt_errors,
                min_chain=alt_min_chain, min_score=alt_min_score,
            )

            alt_elapsed = time.time() - alt_start_time
            alt_progress.empty()
            alt_status.caption(f"Tarama {alt_elapsed:.0f} saniyede tamamlandı.")

            st.session_state.alt_results = alt_matches
            st.session_state.alt_errors = alt_errors
            st.session_state.alt_total_scanned = len(symbols)
            st.session_state.alt_scanned_period = alt_period
            st.session_state.alt_scan_time = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    alt_results = st.session_state.get("alt_results")
    alt_errors = st.session_state.get("alt_errors", [])
    alt_total_scanned = st.session_state.get("alt_total_scanned", 0)

    if alt_results is None:
        st.info("👆 Ayarları yapıp **Alternasyon Taramasını Başlat**'a basın.")
    elif alt_errors and len(alt_errors) == alt_total_scanned:
        st.error(
            f"**{alt_total_scanned} hissenin hiçbirinde veri çekilemedi** — tarama aslında "
            "hiç çalışmadı, sadece 'eşleşme yok' gibi göründü."
        )
        with st.expander("İlk birkaç hatayı gör"):
            for sym, err in alt_errors[:10]:
                st.code(f"{sym}: {err}")
    else:
        alt_scanned_period = st.session_state.get("alt_scanned_period", alt_period)
        st.caption(
            f"Periyot: **{ALTERNATION_SCAN_PERIOD_LABELS.get(alt_scanned_period, alt_scanned_period)}** "
            f"· Taranan: **{alt_total_scanned}** · Eşleşme: **{len(alt_results)}** "
            f"· Tarama zamanı: **{st.session_state.alt_scan_time}**"
        )
        if alt_errors:
            with st.expander(f"⚠️ {len(alt_errors)} hissenin verisi çekilemedi (tarama dışı kaldı)"):
                for sym, err in alt_errors[:20]:
                    st.code(f"{sym}: {err}")

        if len(alt_results) == 0:
            st.warning("Kriterlere uyan hisse bulunamadı.")
        else:
            alt_rows = [{
                "Sembol": a["symbol"],
                "Zincir Uzunluğu": f"{a['chain_length']} mum",
                "Düzenlilik Puanı": a["score"],
                "Renk Sırası (son mumdan geriye)": " → ".join(a["colors"][::-1]),
                "Başlangıç": a.get("start_date", "—"),
                "Bitiş (son mum)": a.get("end_date", "—"),
            } for a in alt_results]
            alt_df = pd.DataFrame(alt_rows).sort_values(
                "Düzenlilik Puanı", ascending=False,
            ).reset_index(drop=True)

            alt_csv = alt_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Sonuçları CSV indir", alt_csv,
                                "bist_bagimsiz_alternasyon_sonuclar.csv", "text/csv",
                                key="alt_csv_download")

            st.caption("💡 Bir hisseye **çift tıklayın** — grafiği açılır pencerede görünür.")

            alt_results_by_symbol = {a["symbol"]: a for a in alt_results}
            if "alt_last_shown_symbol" not in st.session_state:
                st.session_state.alt_last_shown_symbol = {}

            alt_grid_key = f"aggrid_alt_{st.session_state.alt_scan_time}"
            alt_selected_row = render_double_click_table(alt_df, grid_key=alt_grid_key)

            if alt_selected_row is not None:
                alt_candidate_symbol = alt_selected_row["Sembol"]
                if alt_candidate_symbol != st.session_state.alt_last_shown_symbol.get(alt_grid_key):
                    st.session_state.alt_last_shown_symbol[alt_grid_key] = alt_candidate_symbol
                    show_alternation_chart_popup(alt_candidate_symbol, alt_results_by_symbol[alt_candidate_symbol])



with tab_tl:
    st.markdown("---")
    st.subheader("📐 Trend Çizgisi Taraması Sonuçları")

    if tl_scan_button:
        if not symbols:
            st.error("Taranacak hisse yok — 🔍 VWAP Taraması sekmesi > Ayarlar > Hisse Listesi'nden sembol ekleyin.")
        else:
            tl_progress = st.progress(0.0, text="Başlıyor...")
            tl_status = st.empty()
            tl_start_time = time.time()
            tl_errors = []

            def tl_on_progress(done, total, sym):
                tl_progress.progress(done / total, text=f"{done}/{total} tamamlandı — son: {sym}")

            tl_matches = scan_trendline_symbols_parallel(
                symbols, tl_period, max_workers=max_workers, use_cache=use_cache,
                progress_callback=tl_on_progress, errors_out=tl_errors,
                pivot_window=tl_pivot_window, min_span_bars=tl_min_span_bars,
                lookback_bars=tl_lookback_bars, breakout_lookback=tl_breakout_lookback,
                touch_tolerance_pct=tl_touch_tolerance_pct, min_touches=tl_min_touches,
                require_volume=tl_require_volume, volume_factor=tl_volume_factor,
            )

            tl_elapsed = time.time() - tl_start_time
            tl_progress.empty()
            tl_status.caption(f"Tarama {tl_elapsed:.0f} saniyede tamamlandı.")

            st.session_state.tl_results = tl_matches
            st.session_state.tl_errors = tl_errors
            st.session_state.tl_total_scanned = len(symbols)
            st.session_state.tl_scanned_period = tl_period
            st.session_state.tl_require_volume_used = tl_require_volume
            st.session_state.tl_scan_time = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    tl_results = st.session_state.get("tl_results")
    tl_errors = st.session_state.get("tl_errors", [])
    tl_total_scanned = st.session_state.get("tl_total_scanned", 0)

    if tl_results is None:
        st.info("👆 Ayarları yapıp **Trend Çizgisi Taramasını Başlat**'a basın.")
    elif tl_errors and len(tl_errors) == tl_total_scanned:
        st.error(
            f"**{tl_total_scanned} hissenin hiçbirinde veri çekilemedi** — tarama aslında "
            "hiç çalışmadı, sadece 'eşleşme yok' gibi göründü."
        )
        with st.expander("İlk birkaç hatayı gör"):
            for sym, err in tl_errors[:10]:
                st.code(f"{sym}: {err}")
    else:
        tl_scanned_period = st.session_state.get("tl_scanned_period", tl_period)
        st.caption(
            f"Periyot: **{TRENDLINE_SCAN_PERIOD_LABELS.get(tl_scanned_period, tl_scanned_period)}** "
            f"· Taranan: **{tl_total_scanned}** · Eşleşme: **{len(tl_results)}** "
            f"· Tarama zamanı: **{st.session_state.tl_scan_time}**"
        )
        if tl_errors:
            with st.expander(f"⚠️ {len(tl_errors)} hissenin verisi çekilemedi (tarama dışı kaldı)"):
                for sym, err in tl_errors[:20]:
                    st.code(f"{sym}: {err}")

        if len(tl_results) == 0:
            st.warning("Kriterlere uyan hisse bulunamadı.")
        else:
            tl_require_volume_used = st.session_state.get("tl_require_volume_used", False)
            tl_rows = [{
                "Sembol": t["symbol"],
                "Temas Sayısı": t["touches"],
                "Çizgi Başlangıcı": t.get("start_date", "—"),
                "Çizgi Bitişi": t.get("end_date", "—"),
                "Kırılma Tarihi": t["cross_date"],
                "Kaç Bar Önce": t["bars_ago"],
                "Son Kapanış": t["last_close"],
                "Çizginin Şu Anki Değeri": t["line_value_now"],
                **({"Hacim Teyidi": ("✅" if t.get("volume_confirmed") else "❌")}
                   if tl_require_volume_used else {}),
            } for t in tl_results]
            tl_df = pd.DataFrame(tl_rows).sort_values("Kaç Bar Önce").reset_index(drop=True)

            tl_csv = tl_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Sonuçları CSV indir", tl_csv,
                                "bist_bagimsiz_trend_cizgisi_sonuclar.csv", "text/csv",
                                key="tl_csv_download")

            st.caption("💡 Bir hisseye **çift tıklayın** — grafiği açılır pencerede görünür.")

            tl_results_by_symbol = {t["symbol"]: t for t in tl_results}
            if "tl_last_shown_symbol" not in st.session_state:
                st.session_state.tl_last_shown_symbol = {}

            tl_grid_key = f"aggrid_tl_{st.session_state.tl_scan_time}"
            tl_selected_row = render_double_click_table(tl_df, grid_key=tl_grid_key)

            if tl_selected_row is not None:
                tl_candidate_symbol = tl_selected_row["Sembol"]
                if tl_candidate_symbol != st.session_state.tl_last_shown_symbol.get(tl_grid_key):
                    st.session_state.tl_last_shown_symbol[tl_grid_key] = tl_candidate_symbol
                    show_trendline_chart_popup(tl_candidate_symbol, tl_results_by_symbol[tl_candidate_symbol])



with tab_tri:
    st.markdown("---")
    st.subheader("🔺 Üçgen Kırılım Taraması Sonuçları")

    if tri_scan_button:
        if not symbols:
            st.error("Taranacak hisse yok — 🔍 VWAP Taraması sekmesi > Ayarlar > Hisse Listesi'nden sembol ekleyin.")
        else:
            tri_progress = st.progress(0.0, text="Başlıyor...")
            tri_status = st.empty()
            tri_start_time = time.time()
            tri_errors = []

            def tri_on_progress(done, total, sym):
                tri_progress.progress(done / total, text=f"{done}/{total} tamamlandı — son: {sym}")

            tri_matches = scan_triangle_symbols_parallel(
                symbols, tri_period, max_workers=max_workers, use_cache=use_cache,
                progress_callback=tri_on_progress, errors_out=tri_errors,
                pivot_window=tri_pivot_window, min_span_bars=tri_min_span_bars,
                lookback_bars=tri_lookback_bars,
                min_apex_bars_ahead=tri_min_apex_bars_ahead,
                max_apex_bars_ahead=tri_max_apex_bars_ahead,
                max_squeeze_pct=tri_max_squeeze_pct,
            )

            tri_elapsed = time.time() - tri_start_time
            tri_progress.empty()
            tri_status.caption(f"Tarama {tri_elapsed:.0f} saniyede tamamlandı.")

            st.session_state.tri_results = tri_matches
            st.session_state.tri_errors = tri_errors
            st.session_state.tri_total_scanned = len(symbols)
            st.session_state.tri_scanned_period = tri_period
            st.session_state.tri_scan_time = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

    tri_results = st.session_state.get("tri_results")
    tri_errors = st.session_state.get("tri_errors", [])
    tri_total_scanned = st.session_state.get("tri_total_scanned", 0)

    if tri_results is None:
        st.info("👆 Ayarları yapıp **Üçgen Kırılım Taramasını Başlat**'a basın.")
    elif tri_errors and len(tri_errors) == tri_total_scanned:
        st.error(
            f"**{tri_total_scanned} hissenin hiçbirinde veri çekilemedi** — tarama aslında "
            "hiç çalışmadı, sadece 'eşleşme yok' gibi göründü."
        )
        with st.expander("İlk birkaç hatayı gör"):
            for sym, err in tri_errors[:10]:
                st.code(f"{sym}: {err}")
    else:
        tri_scanned_period = st.session_state.get("tri_scanned_period", tri_period)
        st.caption(
            f"Periyot: **{TRIANGLE_SCAN_PERIOD_LABELS.get(tri_scanned_period, tri_scanned_period)}** "
            f"· Taranan: **{tri_total_scanned}** · Eşleşme: **{len(tri_results)}** "
            f"· Tarama zamanı: **{st.session_state.tri_scan_time}**"
        )
        if tri_errors:
            with st.expander(f"⚠️ {len(tri_errors)} hissenin verisi çekilemedi (tarama dışı kaldı)"):
                for sym, err in tri_errors[:20]:
                    st.code(f"{sym}: {err}")

        if len(tri_results) == 0:
            st.warning("Kriterlere uyan (kırılmak üzere olan) üçgen bulunamadı.")
        else:
            tri_rows = [{
                "Sembol": t["symbol"],
                "Desen": t["pattern_type"],
                "Apex (bar sonra)": t["apex_bars_ahead"],
                "Sıkışma (%)": t["squeeze_pct"],
                "Son Kapanış": t["last_close"],
                "Üst Çizgi (Direnç)": t["upper_now"],
                "Alt Çizgi (Destek)": t["lower_now"],
                "Hacim Kuruması (%)": t.get("volume_dryness_pct", "—"),
                "Başlangıç": t.get("start_date", "—"),
                "Bitiş (son mum)": t.get("end_date", "—"),
            } for t in tri_results]
            tri_df = pd.DataFrame(tri_rows).sort_values("Apex (bar sonra)").reset_index(drop=True)

            tri_csv = tri_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("⬇️ Sonuçları CSV indir", tri_csv,
                                "bist_bagimsiz_ucgen_sonuclar.csv", "text/csv",
                                key="tri_csv_download")

            st.caption("💡 Bir hisseye **çift tıklayın** — grafiği açılır pencerede görünür.")

            tri_results_by_symbol = {t["symbol"]: t for t in tri_results}
            if "tri_last_shown_symbol" not in st.session_state:
                st.session_state.tri_last_shown_symbol = {}

            tri_grid_key = f"aggrid_tri_{st.session_state.tri_scan_time}"
            tri_selected_row = render_double_click_table(tri_df, grid_key=tri_grid_key)

            if tri_selected_row is not None:
                tri_candidate_symbol = tri_selected_row["Sembol"]
                if tri_candidate_symbol != st.session_state.tri_last_shown_symbol.get(tri_grid_key):
                    st.session_state.tri_last_shown_symbol[tri_grid_key] = tri_candidate_symbol
                    show_triangle_chart_popup(tri_candidate_symbol, tri_results_by_symbol[tri_candidate_symbol])

with tab_all:
    if scan_all_button:
        st.success("Tümünü birlikte tarama tamamlandı — sonuçlar aşağıda ve ayrıca 🔍 VWAP Taraması sekmesinde (etiketli/ayrı listeli tam görünüm) mevcut.")
    _all_results = st.session_state.get("results")
    if _all_results is not None:
        _all_c1, _all_c2, _all_c3, _all_c4, _all_c5 = st.columns(5)
        _all_c1.metric("VWAP eşleşmesi", len(_all_results))
        _all_c2.metric("Alternasyon", len(st.session_state.get("alternation_matches", [])))
        _all_c3.metric("Trend Çizgisi", len(st.session_state.get("trendline_matches", [])))
        _all_c4.metric("Üçgen", len(st.session_state.get("triangle_matches", [])))
        _all_c5.metric("Taranan", st.session_state.get("total_scanned", 0))
        st.caption("Tam tablo, etiketler ve ayrı listeler için 🔍 **VWAP Taraması** sekmesine bakın.")
    else:
        st.info("👆 Yukarıdaki **Tümünü Birlikte Tara** butonuna basın.")
