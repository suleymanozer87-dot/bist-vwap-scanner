# -*- coding: utf-8 -*-
"""
chart_helpers.py — "Hacim Mumu" (Equivolume) + Zincirlenmiş VWAP çizim fonksiyonu.
app.py ve (varsa) diğer sayfalar arasında paylaşılır, kod tekrarını önler.

HACİM MUMU (Equivolume) mantığı:
  - Her mumun YÜKSEKLİĞİ  -> o barın High-Low aralığı (klasik mum gibi)
  - Her mumun GENİŞLİĞİ   -> o barın hacmi (çok hacim = şişkin/kalın mum,
                              az hacim = ince mum)
  - Rengi                 -> Close >= Open ise yeşil, değilse kırmızı
  Plotly'nin standart Candlestick trace'i değişken genişlik desteklemediği
  için mumlar, x eksenini eşit aralıklı tam sayı index'e çevirip
  go.Bar (base=Low, y=High-Low, width=hacme göre ölçeklenmiş) ile elle
  çiziliyor.

TradingView mantığı:
  - Fare tekerleği  -> yakınlaştır / uzaklaştır (scrollZoom)
  - Sürükleme       -> grafiği kaydır (pan)

HACİM İNDİKATÖRÜ (alt panel):
  - Her grafiğin ALT KISMINDA, fiyat panelinin x eksenini PAYLAŞAN ayrı
    bir hacim çubuğu paneli bulunur (bkz. _new_price_volume_figure /
    _add_volume_bars). Çubuklar üstteki equivolume mumlarıyla AYNI
    genişlik/renkte çizilir, böylece hangi hacim çubuğunun hangi muma
    ait olduğu tek bakışta anlaşılır.
"""

import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

VWAP_COLORS = {1: "#3ddc84", 2: "#5eb0ef", 3: "#e8b545"}

# Sonuç sözlüğündeki "currency" alanına göre eksen/başlık etiketi.
CURRENCY_AXIS_LABELS = {"TRY": "TL", "USD": "$", "EUR": "€"}

# Hacme göre renk skalası: DÜŞÜK hacimli mumlar açık/soluk, YÜKSEK hacimli
# mumlar koyu/doygun renkte olur — göz atınca hangi mumun hacimli olduğu
# hem GENİŞLİKTEN hem RENK TONUNDAN hemen anlaşılsın diye.
# DÜZELTME: düşük-hacim uçları eskiden çok soluktu (#8fe6b3 / #f2a8a1) ve
# koyu (plotly_dark) arka planda neredeyse kayboluyordu. Biraz daha
# doygun/koyu başlangıç tonlarına çekildi ki en ince mum bile seçilebilsin.
UP_COLOR_LOW_VOL = "#6fd89e"    # açık yeşil — düşük hacim (eskiden #8fe6b3)
UP_COLOR_HIGH_VOL = "#0f7a3e"   # koyu yeşil — yüksek hacim
DOWN_COLOR_LOW_VOL = "#ef8b83"  # açık kırmızı — düşük hacim (eskiden #f2a8a1)
DOWN_COLOR_HIGH_VOL = "#a8281d"  # koyu kırmızı — yüksek hacim

# Geriye dönük uyumluluk / VWAP kırılma noktası yıldızı gibi tek-renk
# kullanılan yerler için orta ton.
UP_COLOR = "#3ddc84"
DOWN_COLOR = "#e2574c"

# Mum genişliği, x ekseni birimi cinsinden bu aralıkta ölçekleniyor.
# (x eksenindeki bar aralığı = 1 birim.) DÜZELTME: MAX_CANDLE_WIDTH önceden
# 1.55'ti — 1.0'ı geçtiği için komşu mumlar birbirinin ÜZERİNE biniyordu
# (üst üste binme, "şişkinlik" değil, gerçek bir görsel hataydı). 0.92'de
# tavanlanmıştı; sonra hacim farkı daha belirgin görünsün diye 0.97'ye
# çıkarıldı.
# DÜZELTME 2: MIN_CANDLE_WIDTH 0.10 -> 0.28 çıkarıldı — düşük hacimli
# mumlar o kadar inceydi ki ekranda neredeyse hiç görünmüyordu ("hacim
# mumları hiç belli olmuyor" şikayeti).
# DÜZELTME 3: Kullanıcı geri bildirimi — 0.97 MAX ile komşu iki yüksek
# hacimli mum arasında sadece 0.03 birimlik pay kalıyordu; kenar
# çizgileriyle (marker_line_width) birleşince mumlar GÖRSEL OLARAK "iç
# içe giriyor" hissi veriyordu. MAX_CANDLE_WIDTH 0.97 -> 0.90'a düşürülerek
# mumlar arasına HER ZAMAN görünür bir boşluk (0.10 birim) bırakılıyor.
# Bunun karşılığında MIN_CANDLE_WIDTH 0.28 -> 0.36'ya yükseltildi ki düşük
# hacimli mumlar da (MAX biraz kısıldığı için görece daralan aralıkta)
# hâlâ belirgin/şişkin görünsün — "hacime göre biraz daha şişir" isteği
# esas olarak ince uçtaki mumların şişirilmesiyle karşılanıyor, kalın uçta
# ise küçük bir fedakarlıkla gerçek bir ayrım boşluğu açılıyor.
MIN_CANDLE_WIDTH = 0.36
MAX_CANDLE_WIDTH = 0.90

# Fitil (wick) genişliği — Low..High çizgisi için SABİT ve İNCE bir genişlik
# (hacimden bağımsız). Gövde (Open..Close) hacme göre MIN..MAX_CANDLE_WIDTH
# arasında şişerken, fitil her zaman aynı ince genişlikte kalır — klasik mum
# görünümü ("fitilli mum") böyle elde ediliyor.
WICK_WIDTH = 0.14

# DÜZELTME 3: WIDTH_EXPONENT (üs tabanlı) yaklaşım TERK EDİLDİ.
# Neden: ratio zaten 0..1 aralığında; ratio**0.32 gibi 1'den KÜÇÜK bir üs,
# aslında orta/yüksek hacimli mumların NEREDEYSE HEPSİNİ 1'e (yani MAX
# genişliğe) doğru SIKIŞTIRIYORDU (test: 200 örnekte 50., 75. ve 95.
# yüzdelik dilimler sırasıyla 0.71 / 0.79 / 0.97 çıkıyor — hepsi üst banda
# toplanmış). Bu da tam olarak şikayet edilen "hacim mumları birbirinden
# ayırt edilemiyor" sorununun kaynağıydı: mumların çoğu 0.28-0.97
# aralığının sadece üst yarısını kullanıyor, ince/soluk uç neredeyse hiç
# mum tarafından kullanılmıyordu.
# Yerine RANK (sıra) tabanlı normalizasyon kullanılıyor (bkz.
# _equivolume_ratio): her mum, ekrandaki DİĞER mumlar arasında hacim
# SIRASINA göre 0..1'e yerleştirilir. Bu, veri dağılımı ne kadar çarpık
# (skewed) olursa olsun genişlik/renk farkının MIN..MAX aralığına HER
# ZAMAN eşit yayılmasını garanti eder (aynı testte 5./25./50./75./95.
# yüzdelikler: 0.32 / 0.46 / 0.63 / 0.80 / 0.94 — skalanın tamamı
# kullanılıyor).

# TradingView'daki gibi: tekerlek = zoom, sürükle = kaydır (pan).
PLOTLY_CONFIG = {
    "responsive": True,
    "scrollZoom": True,
    "displaylogo": False,
    # Mobilde hover olmadığı için araç çubuğu sürekli görünür. Böylece
    # Pan / Zoom / +/- / Autoscale / Reset kontrollerine telefonda da
    # doğrudan dokunulabilir.
    "displayModeBar": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
    "doubleClick": "reset+autosize",
}


def _blend_hex(color_a, color_b, t):
    """color_a (t=0) ile color_b (t=1) arasında doğrusal RGB karışımı."""
    a = tuple(int(color_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(color_b[i:i + 2], 16) for i in (1, 3, 5))
    t = max(0.0, min(1.0, t))
    mixed = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return f"#{mixed[0]:02x}{mixed[1]:02x}{mixed[2]:02x}"


def _equivolume_ratio(volume):
    """Hacim serisini 0..1 aralığına ölçekler — genişlik VE renk için
    ORTAK olarak kullanılır (ikisi de aynı hacim algısını yansıtsın diye).

    DÜZELTME 3: volume/max veya quantile-cap + üs yaklaşımlarının ikisi de
    "gerçek hacim ORANI"nı korumaya çalışıyor, ama bu yüzden veri çarpık
    (skewed) olduğunda (borsa hacimleri neredeyse hep öyledir — birkaç
    yüksek hacimli gün, çoğu günün çok üzerinde) mumların büyük kısmı
    ölçeğin bir ucunda TOPLANIP birbirinden ayırt edilemez hale geliyordu.

    Bunun yerine RANK (yüzdelik SIRA) tabanlı normalizasyon kullanılıyor:
    her mum kendi hacmiyle değil, ekrandaki DİĞER mumlara göre SIRASIYLA
    konumlandırılıyor (en düşük hacimli mum -> 0'a en yakın, en yüksek
    hacimli mum -> 1'e en yakın). Bu, MIN_CANDLE_WIDTH..MAX_CANDLE_WIDTH
    (ve açık..koyu renk) aralığının, veri dağılımı ne olursa olsun HER
    ZAMAN uçtan uca kullanılmasını garanti eder — yani ekrandaki mumlar
    her zaman birbirinden GÖRSEL OLARAK ayırt edilebilir olur. Aynı hacme
    sahip mumlar aynı oranı paylaşır (method="average").
    """
    if len(volume) <= 1:
        return volume.clip(lower=0.0) * 0.0 + 1.0
    return volume.rank(pct=True, method="average")


def _equivolume_widths(ratio):
    """0..1 hacim oranını MIN_CANDLE_WIDTH..MAX_CANDLE_WIDTH mum
    genişliğine çevirir."""
    widths = MIN_CANDLE_WIDTH + ratio * (MAX_CANDLE_WIDTH - MIN_CANDLE_WIDTH)
    return widths.tolist()


def _equivolume_colors(ratio, up_mask):
    """0..1 hacim oranını, yön (yükseliş/düşüş) rengine göre açık->koyu
    renk skalasına çevirir: düşük hacim = soluk, yüksek hacim = koyu."""
    colors = []
    for t, up in zip(ratio.tolist(), up_mask.tolist()):
        if up:
            colors.append(_blend_hex(UP_COLOR_LOW_VOL, UP_COLOR_HIGH_VOL, t))
        else:
            colors.append(_blend_hex(DOWN_COLOR_LOW_VOL, DOWN_COLOR_HIGH_VOL, t))
    return colors


def _new_price_volume_figure():
    """Fiyat (HACİM MUMU) grafiği ÜSTTE, HACİM İNDİKATÖRÜ ALTTA olacak
    şekilde x ekseni PAYLAŞILAN (shared_xaxes) 2 satırlık bir subplot
    figürü oluşturur. TradingView'daki gibi: üst panel mumlar + tüm
    overlay'ler (VWAP/trend çizgisi/üçgen/zigzag vb. — bunlar bu
    fonksiyonun DIŞINDA, çağıran yerde row=1, col=1 ile eklenir), alt
    panel ise SADECE hacim çubuklarını gösterir. İki panel aynı x eksenini
    paylaştığı için birlikte kaydırılır/yakınlaştırılır.
    """
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.72, 0.28], vertical_spacing=0.035,
    )
    # barmode="overlay": üst paneldeki fitil + gövde bar'ları (bkz.
    # _add_equivolume_candles) yan yana değil, AYNI x konumunda ÜST ÜSTE
    # çizilsin diye — aksi halde Plotly'nin varsayılan "group" modu ikisini
    # yan yana ayrı mumlar gibi ikiye böler.
    fig.update_layout(barmode="overlay")
    return fig


def _add_equivolume_candles(fig, x, df, widths, colors, hover_text, sym):
    """Üst panele (row=1) FİTİLLİ hacim mumlarını ekler — iki bar trace'i
    aynı x konumunda ÜST ÜSTE (barmode="overlay", bkz. _new_price_volume_figure)
    çizilerek tek bir mum gibi görünür:

      1) FİTİL   -> Low..High aralığı, SABİT ince genişlik (WICK_WIDTH).
                    Klasik mumdaki ince "gölge/fitil" çizgisi.
      2) GÖVDE   -> Open..Close aralığı, hacme göre MIN..MAX_CANDLE_WIDTH
                    arasında şişen/incelen (equivolume) genişlik. Hacim
                    "şişkinliği" burada, sadece gövdede kalıyor.
    """
    high = df["High"].tolist()
    low = df["Low"].tolist()
    open_ = df["Open"].tolist()
    close = df["Close"].tolist()

    # --- 1) Fitil: Low -> High, sabit ince genişlik, hover'sız (gövde
    #     zaten aynı x'te tam hover bilgisini veriyor; iki kez göstermemek
    #     için fitil trace'i hover'dan muaf tutuluyor). ---
    wick_heights = [h - l for h, l in zip(high, low)]
    fig.add_trace(go.Bar(
        x=x, y=wick_heights, base=low, width=WICK_WIDTH,
        marker_color=colors, marker_line_width=0,
        name=f"{sym} (fitil)", showlegend=False,
        hoverinfo="skip",
    ), row=1, col=1)

    # --- 2) Gövde: Open -> Close, hacme göre şişkin (equivolume) genişlik.
    #     Open == Close (doji) durumunda gövde tamamen sıfır yükseklikte
    #     kaybolmasın diye minik bir görünür yükseklik veriliyor. ---
    body_bases = [min(o, c) for o, c in zip(open_, close)]
    body_heights = [
        (max(o, c) - min(o, c)) if o != c else max((h - l) * 0.01, 1e-6)
        for o, c, h, l in zip(open_, close, high, low)
    ]
    fig.add_trace(go.Bar(
        x=x, y=body_heights, base=body_bases, width=widths,
        marker_color=colors, marker_line_color="#0c0f14", marker_line_width=0.9,
        name=sym, opacity=0.95,
        hovertext=hover_text, hoverinfo="text",
    ), row=1, col=1)


def _add_volume_bars(fig, x, volume, widths, colors):
    """Alt panele (row=2) HACİM İNDİKATÖRÜNÜ ekler — her çubuk, üstteki
    equivolume mumuyla AYNI genişlikte ve AYNI (hacme göre açık/koyu
    tonlanmış) renkte çizilir ki üst-alt hizası bozulmasın ve renk dili
    tutarlı kalsın."""
    vol_hover = [f"Hacim: {v:,.0f}" for v in volume.tolist()]
    fig.add_trace(go.Bar(
        x=x, y=volume.tolist(), width=widths,
        marker_color=colors, marker_line_width=0,
        name="Hacim", showlegend=False,
        hovertext=vol_hover, hoverinfo="text",
    ), row=2, col=1)


def _finalize_price_volume_axes(fig, tickvals, ticktext, price_range, y_title):
    """Üst panel (fiyat) ve alt panel (hacim) eksenlerini TradingView
    mantığında (fiyat sağda, crosshair, döndürülmüş tarih etiketleri SADECE
    alt panelde) ortak şekilde ayarlar. price_range verilirse üst panelin
    y ekseni buna sabitlenir (verilmezse otomatik ölçeklenir)."""
    # Üst panel x ekseni: tarih etiketleri GÖSTERİLMEZ (alt panelde zaten
    # var, iki panel aynı x eksenini paylaşıyor) — sadece crosshair kalır.
    # DÜZELTME: spikemode'a "toaxis" eklendi. Sadece "across" ile crosshair
    # çizgisi çiziliyor ama eksende DEĞERİ gösteren bir kutu belirmiyordu
    # ("imlecin olduğu yerin fiyatı görünmüyor" şikayeti). "toaxis" eklenince
    # Plotly, imlecin hizasındaki fiyatı/tarihi sağ/alt eksende küçük bir
    # etiket kutusu içinde gösteriyor (TradingView'daki imleç etiketi gibi).
    fig.update_xaxes(
        row=1, col=1,
        rangeslider_visible=False, showgrid=False,
        showticklabels=False, fixedrange=False,
        showspikes=True, spikemode="across+toaxis", spikesnap="cursor",
        spikethickness=1, spikedash="solid", spikecolor="#9aa5b1",
    )
    # Alt panel x ekseni: asıl tarih etiketleri BURADA.
    fig.update_xaxes(
        row=2, col=1,
        tickvals=tickvals, ticktext=ticktext, tickangle=-35,
        # Mobilde grafiğin kendisini sürüklemek zor olursa alttaki zaman
        # sürgüsüyle görünür pencere kolayca sağa/sola taşınabilir.
        rangeslider=dict(visible=True, thickness=0.07, bgcolor="#eef2f7", bordercolor="#cbd5e1", borderwidth=1),
        showgrid=False,
        showticklabels=True, tickfont=dict(size=10), fixedrange=False,
        showspikes=True, spikemode="across+toaxis", spikesnap="cursor",
        spikethickness=1, spikedash="solid", spikecolor="#9aa5b1",
    )
    yaxis_kwargs = dict(
        title_text=y_title,
        side="right",  # TradingView gibi fiyat ekseni sağda
        showticklabels=True, tickfont=dict(size=10), fixedrange=False,
        showspikes=True, spikemode="across+toaxis", spikesnap="cursor",
        spikethickness=1, spikedash="solid", spikecolor="#9aa5b1",
    )
    if price_range is not None:
        yaxis_kwargs["range"] = price_range
    fig.update_yaxes(row=1, col=1, **yaxis_kwargs)
    # Alt panel y ekseni: hacim — büyük sayılar SI-önekli (12M, 350K vb.)
    # kısaltılıyor ki eksen kalabalıklaşmasın.
    fig.update_yaxes(
        row=2, col=1,
        title_text="Hacim", side="right",
        showgrid=False, tickfont=dict(size=10), tickformat=".2s",
        nticks=3, fixedrange=False,
        showspikes=True, spikemode="across+toaxis", spikesnap="cursor",
        spikethickness=1, spikedash="solid", spikecolor="#9aa5b1",
    )


def _focus_recent_pattern(fig, n, start_hint=None, end_extra=2, min_bars=55, max_bars=105):
    """Mobil/dar ekranlarda tüm geçmişi ezmek yerine sinyale yakın bölgeyi gösterir.
    Kullanıcı pan/zoom ile tüm geçmişe yine ulaşabilir."""
    if n <= 0:
        return
    last = n - 1
    if start_hint is None:
        start = max(0, n - max_bars)
    else:
        try:
            start = max(0, int(start_hint) - 8)
        except Exception:
            start = max(0, n - max_bars)
        if last - start + 1 < min_bars:
            start = max(0, last - min_bars + 1)
        if last - start + 1 > max_bars:
            start = max(0, last - max_bars + 1)
    end = last + max(1, int(end_extra or 0))
    fig.update_xaxes(range=[start - 0.5, end + 0.5], row=1, col=1)
    fig.update_xaxes(range=[start - 0.5, end + 0.5], row=2, col=1)
    return start, min(last, end)


def _focused_price_range(df, start_idx, end_idx=None):
    """Görünen mumlara göre fiyat eksenini sıkılaştırır; telefonda mumları büyütür."""
    if end_idx is None:
        end_idx = len(df) - 1
    start_idx = max(0, int(start_idx or 0))
    end_idx = min(len(df) - 1, int(end_idx))
    sub = df.iloc[start_idx:end_idx + 1]
    if sub.empty:
        sub = df
    low = float(sub["Low"].min())
    high = float(sub["High"].max())
    pad = max((high - low) * 0.10, high * 0.012, 0.01)
    return [low - pad, high + pad]


def _mobile_friendly_layout(fig, title):
    """Masaüstünde de temiz, telefonda dokunarak kullanılabilen ortak görünüm."""
    fig.update_layout(
        title=dict(text=title, font=dict(size=16), x=0.01, xanchor="left"),
        height=560,
        template="plotly_white",
        # Varsayılan hareket tek parmakla kaydırma. Kullanıcı araç çubuğundan
        # büyüteci seçerse kutu/zoom moduna geçebilir.
        dragmode="pan",
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0,
            font=dict(size=9), itemwidth=30,
        ),
        margin=dict(l=6, r=42, t=78, b=52),
        bargap=0.0,
        hovermode="closest",
        hoverlabel=dict(font_size=11),
        autosize=True,
    )


def _chart_view_controls(fig, df, default_start, default_end, key):
    """Mobil/PC için hızlı görünüm seçimi ve kullanım ipucu.

    Plotly'nin dokunmatik pan/zoom'u cihaz/tarayıcıya göre değişebildiği için
    kullanıcıya grafik üstünde ayrıca güvenilir bir görünüm kısayolu verir.
    Bu sadece çizim aralığını değiştirir; tarama/sinyal verisine dokunmaz.
    """
    n = len(df)
    if n <= 0:
        return 0, 0
    safe_key = str(key or "chart").replace(" ", "_")
    choice = st.radio(
        "Grafik görünümü",
        ["Sinyale odaklan", "40 mum", "80 mum", "Tümü"],
        index=0, horizontal=True,
        key=f"{safe_key}_view",
        label_visibility="collapsed",
    )
    st.caption("📱 Tek parmak: kaydır · İki parmak: yakınlaştır · Üst araç çubuğu: Pan / Zoom / +/- / Sıfırla")

    if choice == "40 mum":
        start, end = max(0, n - 40), n - 1
    elif choice == "80 mum":
        start, end = max(0, n - 80), n - 1
    elif choice == "Tümü":
        start, end = 0, n - 1
    else:
        start = max(0, int(default_start or 0))
        end = min(n - 1, int(default_end if default_end is not None else n - 1))

    # Sağ tarafta iki mum kadar boşluk, son fiyat etiketinin sıkışmasını önler.
    fig.update_xaxes(range=[start - 0.5, end + 2.5], row=1, col=1)
    fig.update_xaxes(range=[start - 0.5, end + 2.5], row=2, col=1)
    return start, end


def _add_last_price_line(fig, last_price, is_up):
    """Üst panelin sağ kenarına, TradingView'daki gibi SON FİYATI gösteren
    yatay kesikli bir çizgi + üzerinde değeri yazan renkli bir etiket ekler.
    Son mum yükselişte ise yeşil, düşüşte ise kırmızı etiket kullanılır."""
    color = UP_COLOR if is_up else DOWN_COLOR
    fig.add_hline(
        y=last_price, row=1, col=1,
        line=dict(color=color, width=1, dash="dot"),
        annotation_text=f" {last_price:.2f} ",
        annotation_position="right",
        annotation=dict(
            font=dict(color="#0c0f14", size=12),
            bgcolor=color,
            bordercolor=color,
        ),
    )


def render_vwap_chart(sym, r, key=None):
    """Tek bir hisse için HACİM MUMU (equivolume) grafiği + zincirlenmiş VWAP
    seviyelerini, TradingView mantığında (tekerlek zoom, sürükleyerek kaydırma)
    çizer."""
    df = r["df"]
    n = len(df)
    x = list(range(n))  # eşit aralıklı tam sayı index — mum genişliğini kontrol edebilmek için

    # "4h" gibi gün-içi periyotlarda tarih etiketleri SAAT bilgisini de
    # içermeli (aksi halde aynı güne ait birden fazla mum aynı etikette
    # görünür, hangi mumun hangi saat dilimine ait olduğu anlaşılmaz).
    intraday = r.get("period") in ("4h",)
    date_fmt = (lambda ts: ts.strftime("%d.%m %H:%M")) if intraday else (lambda ts: str(ts.date()))

    volume = df["Volume"].fillna(0).astype(float)
    ratio = _equivolume_ratio(volume)
    widths = _equivolume_widths(ratio)

    up_mask = df["Close"] >= df["Open"]
    colors = _equivolume_colors(ratio, up_mask)


    hover_text = [
        f"{date_fmt(df['Date'].iloc[i])}<br>"
        f"Aç: {df['Open'].iloc[i]:.2f}  Yük: {df['High'].iloc[i]:.2f}  "
        f"Düş: {df['Low'].iloc[i]:.2f}  Kapn: {df['Close'].iloc[i]:.2f}<br>"
        f"Hacim: {volume.iloc[i]:,.0f}"
        for i in range(n)
    ]

    currency = r.get("currency", "TRY")
    currency_label = CURRENCY_AXIS_LABELS.get(currency, currency)

    fig = _new_price_volume_figure()

    # --- Hacim Mumu (Equivolume, üst panel) + Hacim İndikatörü (alt panel) ---
    _add_equivolume_candles(fig, x, df, widths, colors, hover_text, sym)
    _add_volume_bars(fig, x, volume, widths, colors)
    _add_last_price_line(fig, float(df["Close"].iloc[-1]), bool(up_mask.iloc[-1]))

    # --- Zincirlenmiş VWAP seviyeleri ---
    for level_info in r["chain"]:
        lvl = level_info["level"]
        color = VWAP_COLORS.get(lvl, "#aaaaaa")
        fig.add_trace(go.Scatter(
            x=x, y=level_info["vwap"].values,
            mode="lines", name=f"VWAP-{lvl}",
            line=dict(color=color, width=2, dash="solid" if lvl == r["level"] else "dot"),
        ), row=1, col=1)

    # --- Kırılma noktası ---
    cross_idx = r["cross_idx"]
    fig.add_trace(go.Scatter(
        x=[cross_idx], y=[df["Close"].iloc[cross_idx]],
        mode="markers", name="Kırılım",
        marker=dict(color="#e8b545", size=14, symbol="star"),
    ), row=1, col=1)

    # --- Düşen trend çizgisi (varsa) — ekteki TradingView örneğindeki gibi ---
    trendline_info = r.get("trendline")
    if trendline_info and trendline_info.get("matched"):
        line = trendline_info["line"]
        slope, intercept = line["slope"], line["intercept"]
        x1, x2 = line["x1"], line["x2"]
        tl_cross_idx = trendline_info["cross_idx"]

        # Pivotlar arası (kanıtlanmış) kısım düz çizgi, kırılıma kadarki
        # uzantısı kesikli çizgi — çizginin nereden "gerçek", nereden
        # "projeksiyon" olduğu görsel olarak ayırt edilsin diye.
        fig.add_trace(go.Scatter(
            x=[x1, x2], y=[slope * x1 + intercept, slope * x2 + intercept],
            mode="lines", name="Düşen Trend",
            line=dict(color="#ff5f5f", width=2.5, dash="solid"),
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[x2, tl_cross_idx], y=[slope * x2 + intercept, slope * tl_cross_idx + intercept],
            mode="lines", name="Trend Çizgisi (uzantı)",
            line=dict(color="#ff5f5f", width=2, dash="dash"),
            showlegend=False,
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[tl_cross_idx], y=[df["Close"].iloc[tl_cross_idx]],
            mode="markers", name="Trend Kırılımı",
            marker=dict(color="#ff5f5f", size=13, symbol="triangle-up"),
        ), row=1, col=1)

    # --- Tarih etiketleri (x ekseni tam sayı index olduğu için elle basıyoruz) ---
    tick_step = max(1, n // 7)
    tickvals = x[::tick_step]
    ticktext = [date_fmt(df["Date"].iloc[i]) for i in tickvals]

    # --- Mum alternasyon (zigzag) rozeti — varsa başlığa ek olarak eklenir ---
    alt_info = r.get("alternation")
    alt_badge = ""
    if alt_info and alt_info.get("is_alternating"):
        alt_badge = f" · 🔀 Alternasyon: {alt_info['chain_length']} mum, puan {alt_info['score']:.0f}"

    trend_badge = ""
    if trendline_info and trendline_info.get("matched"):
        trend_badge = f" · 📐 Trend Çizgisi Kırılımı ({trendline_info['cross_date']})"

    period_label = {
        "4h": "4 Saat", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
    }.get(r.get("period"), str(r.get("period") or ""))
    _mobile_friendly_layout(fig, f"{sym} · VWAP-{r['level']} · {period_label}")
    focus_start = max(0, int(r.get("cross_idx", n - 1)) - 45)
    view_start, view_end = _focus_recent_pattern(fig, n, start_hint=focus_start, end_extra=2, min_bars=55, max_bars=100)
    view_start, view_end = _chart_view_controls(fig, df, view_start, view_end, key)
    _finalize_price_volume_axes(
        fig, tickvals, ticktext,
        price_range=_focused_price_range(df, view_start, view_end),
        y_title=f"Fiyat ({currency_label})",
    )

    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key=key)


def render_triangle_chart(sym, r, key=None):
    """Bağımsız (ya da VWAP taramasına entegre) Üçgen Kırılım Taraması
    (vwap_core.detect_triangle_break) sonucu için HACİM MUMU (equivolume)
    + üst (direnç) + alt (destek) üçgen çizgileri + apex noktasını çizer.
    VWAP taramasından TAMAMEN BAĞIMSIZDIR — VWAP seviyesi/zinciri/para
    birimi çevrimi burada YOKTUR, r sadece detect_triangle_break() çıktısı
    + symbol/period/df alanlarını içerir."""
    df = r["df"]
    n = len(df)
    x = list(range(n))

    intraday = r.get("period") in ("1h", "4h")
    date_fmt = (lambda ts: ts.strftime("%d.%m %H:%M")) if intraday else (lambda ts: str(ts.date()))

    volume = df["Volume"].fillna(0).astype(float)
    ratio = _equivolume_ratio(volume)
    widths = _equivolume_widths(ratio)

    up_mask = df["Close"] >= df["Open"]
    colors = _equivolume_colors(ratio, up_mask)


    hover_text = [
        f"{date_fmt(df['Date'].iloc[i])}<br>"
        f"Aç: {df['Open'].iloc[i]:.2f}  Yük: {df['High'].iloc[i]:.2f}  "
        f"Düş: {df['Low'].iloc[i]:.2f}  Kapn: {df['Close'].iloc[i]:.2f}<br>"
        f"Hacim: {volume.iloc[i]:,.0f}"
        for i in range(n)
    ]

    fig = _new_price_volume_figure()

    # --- Hacim Mumu (Equivolume, üst panel) + Hacim İndikatörü (alt panel) ---
    _add_equivolume_candles(fig, x, df, widths, colors, hover_text, sym)
    _add_volume_bars(fig, x, volume, widths, colors)
    _add_last_price_line(fig, float(df["Close"].iloc[-1]), bool(up_mask.iloc[-1]))

    # --- Üst çizgi (direnç) + alt çizgi (destek) — pivot aralığında düz,
    #     bugüne ve apex'e kadar kesikli uzantı ile ---
    upper, lower = r["upper"], r["lower"]
    apex_x = r["apex_x"]
    last_idx = n - 1
    # Apex çok ileride olabilir (grafikte anlamlı görünmesi için, bugünün
    # biraz ötesine, ama makul bir mesafeye kadar uzatıyoruz).
    draw_to_x = min(apex_x, last_idx + max(5, (apex_x - last_idx) * 1.0))

    fig.add_trace(go.Scatter(
        x=[upper["x1"], upper["x2"]],
        y=[upper["slope"] * upper["x1"] + upper["intercept"], upper["slope"] * upper["x2"] + upper["intercept"]],
        mode="lines", name="Direnç",
        line=dict(color="#ff5f5f", width=2.5, dash="solid"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[upper["x2"], draw_to_x],
        y=[upper["slope"] * upper["x2"] + upper["intercept"], upper["slope"] * draw_to_x + upper["intercept"]],
        mode="lines", name="Üst Çizgi (uzantı)",
        line=dict(color="#ff5f5f", width=2, dash="dash"), showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[lower["x1"], lower["x2"]],
        y=[lower["slope"] * lower["x1"] + lower["intercept"], lower["slope"] * lower["x2"] + lower["intercept"]],
        mode="lines", name="Destek",
        line=dict(color="#3ddc84", width=2.5, dash="solid"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[lower["x2"], draw_to_x],
        y=[lower["slope"] * lower["x2"] + lower["intercept"], lower["slope"] * draw_to_x + lower["intercept"]],
        mode="lines", name="Alt Çizgi (uzantı)",
        line=dict(color="#3ddc84", width=2, dash="dash"), showlegend=False,
    ), row=1, col=1)

    # --- Apex noktası (henüz gelmemiş kırılım/kesişim noktası) ---
    fig.add_trace(go.Scatter(
        x=[apex_x], y=[r["apex_y"]],
        mode="markers", name="Apex",
        marker=dict(color="#e8b545", size=13, symbol="x"),
    ), row=1, col=1)

    tick_step = max(1, n // 7)
    tickvals = x[::tick_step]
    ticktext = [date_fmt(df["Date"].iloc[i]) for i in tickvals]

    period_label = {
        "1h": "1 Saatlik", "4h": "4 Saatlik", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
    }.get(r.get("period"), r.get("period", ""))
    vol_dryness = r.get("volume_dryness_pct")
    vol_badge = f" · 💧 Hacim %{vol_dryness:.0f}'e düştü" if vol_dryness is not None else ""

    _mobile_friendly_layout(fig, f"{sym} · {r['pattern_type']} · {period_label}")
    pattern_start = min(int(upper.get("x1", 0)), int(lower.get("x1", 0)))
    extra = min(8, max(2, int(r.get("apex_bars_ahead") or 2)))
    view_start, view_end = _focus_recent_pattern(fig, n, start_hint=pattern_start, end_extra=extra, min_bars=55, max_bars=105)
    view_start, view_end = _chart_view_controls(fig, df, view_start, view_end, key)
    # --- Y ekseni aralığını MUMLARIN kendi fiyat aralığına göre SABİTLE ---
    # Apex noktası/uzantısı ileri bir bara projekte edildiği için, eğim dik
    # olduğunda (örn. sıkışmış küçük bir pivot aralığından hesaplanan eğim
    # onlarca bar ileri taşındığında) apex_y çok uç bir değere savrulabilir.
    # Plotly bunu autorange'e dahil edince Y ekseni o tek uç nokta yüzünden
    # aşırı gerilir ve mumlar ekranın altına/üstüne SIKIŞIP görünmez hale
    # gelir — üçgen "çizilmiyor" gibi görünmesinin asıl sebebi budur.
    # Çözüm: ekseni mumların gerçek High/Low aralığına göre (biraz payla)
    # sabitliyoruz; apex çok uzaktaysa çizginin ucu ekranın dışına taşabilir
    # ama üçgenin kendisi (pivot noktalarından gelen gerçek segment) her
    # zaman görünür kalır.
    _finalize_price_volume_axes(
        fig, tickvals, ticktext,
        price_range=_focused_price_range(df, view_start, view_end),
        y_title="Fiyat (TL)",
    )

    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key=key)


def render_trendline_chart(sym, r, key=None):
    """Bağımsız Trend Çizgisi Taraması (vwap_core.scan_trendline_symbols_parallel)
    sonucu için HACİM MUMU (equivolume) + düşen trend çizgisi + kırılım
    noktasını çizer. VWAP taramasından TAMAMEN BAĞIMSIZDIR — VWAP seviyesi/
    zinciri/para birimi çevrimi burada YOKTUR, r sadece
    detect_trendline_break() çıktısı + symbol/period/df alanlarını içerir."""
    df = r["df"]
    n = len(df)
    x = list(range(n))

    intraday = r.get("period") in ("1h", "4h")
    date_fmt = (lambda ts: ts.strftime("%d.%m %H:%M")) if intraday else (lambda ts: str(ts.date()))

    volume = df["Volume"].fillna(0).astype(float)
    ratio = _equivolume_ratio(volume)
    widths = _equivolume_widths(ratio)

    up_mask = df["Close"] >= df["Open"]
    colors = _equivolume_colors(ratio, up_mask)


    hover_text = [
        f"{date_fmt(df['Date'].iloc[i])}<br>"
        f"Aç: {df['Open'].iloc[i]:.2f}  Yük: {df['High'].iloc[i]:.2f}  "
        f"Düş: {df['Low'].iloc[i]:.2f}  Kapn: {df['Close'].iloc[i]:.2f}<br>"
        f"Hacim: {volume.iloc[i]:,.0f}"
        for i in range(n)
    ]

    fig = _new_price_volume_figure()

    # --- Hacim Mumu (Equivolume, üst panel) + Hacim İndikatörü (alt panel) ---
    _add_equivolume_candles(fig, x, df, widths, colors, hover_text, sym)
    _add_volume_bars(fig, x, volume, widths, colors)
    _add_last_price_line(fig, float(df["Close"].iloc[-1]), bool(up_mask.iloc[-1]))

    # --- Düşen Trend Çizgisi + uzantısı + kırılım noktası ---
    line = r["line"]
    slope, intercept = line["slope"], line["intercept"]
    x1, x2 = line["x1"], line["x2"]
    cross_idx = r["cross_idx"]

    fig.add_trace(go.Scatter(
        x=[x1, x2], y=[slope * x1 + intercept, slope * x2 + intercept],
        mode="lines", name="Düşen Trend",
        line=dict(color="#ff5f5f", width=2.5, dash="solid"),
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[x2, cross_idx], y=[slope * x2 + intercept, slope * cross_idx + intercept],
        mode="lines", name="Trend Çizgisi (uzantı)",
        line=dict(color="#ff5f5f", width=2, dash="dash"),
        showlegend=False,
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=[cross_idx], y=[df["Close"].iloc[cross_idx]],
        mode="markers", name="Trend Kırılımı",
        marker=dict(color="#ff5f5f", size=14, symbol="triangle-up"),
    ), row=1, col=1)

    # --- Tarih etiketleri ---
    tick_step = max(1, n // 7)
    tickvals = x[::tick_step]
    ticktext = [date_fmt(df["Date"].iloc[i]) for i in tickvals]

    period_label = {
        "1h": "1 Saatlik", "4h": "4 Saatlik", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
    }.get(r.get("period"), r.get("period", ""))
    vol_badge = " · ✅ Hacim Teyitli" if r.get("volume_confirmed") else ""

    _mobile_friendly_layout(fig, f"{sym} · Düşen Trend Kırılımı · {period_label}")
    view_start, view_end = _focus_recent_pattern(fig, n, start_hint=int(line.get("x1", 0)), end_extra=2, min_bars=55, max_bars=105)
    view_start, view_end = _chart_view_controls(fig, df, view_start, view_end, key)
    # --- Y ekseni aralığını MUMLARIN kendi fiyat aralığına göre SABİTLE ---
    # (bkz. render_triangle_chart'taki aynı isimli blok) — kırılım
    # uzantısı çok dik bir eğimde bugüne yansıtılırsa autorange bunu tek
    # başına referans alıp mumları ekranın kenarına sıkıştırabilir; bu
    # yüzden ekseni mumların gerçek High/Low'una göre sabitliyoruz.
    _finalize_price_volume_axes(
        fig, tickvals, ticktext,
        price_range=_focused_price_range(df, view_start, view_end),
        y_title="Fiyat (TL)",
    )

    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key=key)


def render_alternation_chart(sym, r, key=None):
    """Bağımsız Alternasyon (Zigzag) Taraması (vwap_core.scan_alternation_symbols_parallel)
    sonucu için HACİM MUMU (equivolume) + zigzag zincirini vurgulayan bir
    gölgeli bölge çizer. VWAP taramasından TAMAMEN BAĞIMSIZDIR — r sadece
    detect_candle_alternation() çıktısı + symbol/period/df alanlarını içerir."""
    df = r["df"]
    n = len(df)
    x = list(range(n))

    intraday = r.get("period") in ("1h", "4h")
    date_fmt = (lambda ts: ts.strftime("%d.%m %H:%M")) if intraday else (lambda ts: str(ts.date()))

    volume = df["Volume"].fillna(0).astype(float)
    ratio = _equivolume_ratio(volume)
    widths = _equivolume_widths(ratio)

    up_mask = df["Close"] >= df["Open"]
    colors = _equivolume_colors(ratio, up_mask)


    hover_text = [
        f"{date_fmt(df['Date'].iloc[i])}<br>"
        f"Aç: {df['Open'].iloc[i]:.2f}  Yük: {df['High'].iloc[i]:.2f}  "
        f"Düş: {df['Low'].iloc[i]:.2f}  Kapn: {df['Close'].iloc[i]:.2f}<br>"
        f"Hacim: {volume.iloc[i]:,.0f}"
        for i in range(n)
    ]

    fig = _new_price_volume_figure()

    # --- Hacim Mumu (Equivolume, üst panel) + Hacim İndikatörü (alt panel) ---
    _add_equivolume_candles(fig, x, df, widths, colors, hover_text, sym)
    _add_volume_bars(fig, x, volume, widths, colors)
    _add_last_price_line(fig, float(df["Close"].iloc[-1]), bool(up_mask.iloc[-1]))

    # --- Zigzag zincirini vurgulayan gölgeli bölge + gövde-tepe çizgisi ---
    start_idx, end_idx = r["start_idx"], r["end_idx"]
    chain_low = float(df["Low"].iloc[start_idx:end_idx + 1].min())
    chain_high = float(df["High"].iloc[start_idx:end_idx + 1].max())
    pad = max((chain_high - chain_low) * 0.06, chain_high * 0.005, 0.01)

    fig.add_shape(
        type="rect",
        x0=start_idx - 0.5, x1=end_idx + 0.5,
        y0=chain_low - pad, y1=chain_high + pad,
        line=dict(color="#f5c542", width=1.5, dash="dot"),
        fillcolor="rgba(245, 197, 66, 0.08)",
        layer="above",
        row=1, col=1,
    )
    # Zincirdeki her mumun gövde-tepe noktasını (Close) birleştiren ince bir
    # zigzag çizgisi — rengin nerede döndüğünü görsel olarak vurgular.
    chain_x = list(range(start_idx, end_idx + 1))
    chain_y = [float(df["Close"].iloc[i]) for i in chain_x]
    fig.add_trace(go.Scatter(
        x=chain_x, y=chain_y, mode="lines+markers", name="Alternasyon",
        line=dict(color="#f5c542", width=1.5, dash="dot"),
        marker=dict(color="#f5c542", size=5),
    ), row=1, col=1)

    # --- Tarih etiketleri ---
    tick_step = max(1, n // 7)
    tickvals = x[::tick_step]
    ticktext = [date_fmt(df["Date"].iloc[i]) for i in tickvals]

    period_label = {
        "1h": "1 Saatlik", "4h": "4 Saatlik", "daily": "Günlük", "weekly": "Haftalık", "monthly": "Aylık",
    }.get(r.get("period"), r.get("period", ""))

    _mobile_friendly_layout(fig, f"{sym} · Alternasyon · {period_label}")
    view_start, view_end = _focus_recent_pattern(fig, n, start_hint=max(0, int(start_idx) - 20), end_extra=2, min_bars=55, max_bars=95)
    view_start, view_end = _chart_view_controls(fig, df, view_start, view_end, key)
    _finalize_price_volume_axes(
        fig, tickvals, ticktext,
        price_range=_focused_price_range(df, view_start, view_end),
        y_title="Fiyat (TL)",
    )

    st.plotly_chart(fig, width="stretch", config=PLOTLY_CONFIG, key=key)
