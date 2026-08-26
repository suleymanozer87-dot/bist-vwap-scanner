#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bist_vwap_scanner.py — Komut satırı sürümü (arayüz istemiyorsanız).
Arayüz için: streamlit run app.py

ÇALIŞTIRMA:
    python bist_vwap_scanner.py
    python bist_vwap_scanner.py --period daily
    python bist_vwap_scanner.py --symbols THYAO.IS,ASELS.IS

YATAY (SIDEWAYS) FİLTRESİ — VWAP taramasından bağımsız, EK bir filtre.
Son N ayda dar bir bantta hareket eden ("yatay girmiş") hisseleri de
tespit eder. Tespit yöntemi ve sonuçların nasıl gösterileceği SEÇMELİDİR:
    --sideways                     Yatay filtresini etkinleştirir
    --sideways-method {range,atr,both}   Tespit yöntemi (varsayılan: range)
    --sideways-months N             Kaç aylık pencereye bakılsın (varsayılan: 6)
    --sideways-range-pct X          "range"/"both" için bant genişliği eşiği %
    --sideways-atr-pct X            "atr"/"both" için ATR eşiği %
    --no-sideways-tag               VWAP sonuç tablosuna "Yatay" sütunu EKLEME
    --no-sideways-list              Ayrı "yatay hisseler" listesini/CSV'sini GÖSTERME
(--sideways-tag ve --sideways-list varsayılan olarak İKİSİ DE açıktır;
istenirse ikisi birden ya da tek tek kapatılabilir.)
"""

import argparse
import time
from datetime import datetime

import pandas as pd

from vwap_core import (
    ALTERNATION_MIN_CHAIN,
    CURRENCY_OPTIONS,
    DEFAULT_SYMBOLS,
    PERIOD_LABELS,
    PERIOD_OPTIONS,
    SIDEWAYS_METHODS,
    TRENDLINE_PIVOT_WINDOW,
    TRENDLINE_MIN_SPAN_BARS,
    TRENDLINE_LOOKBACK_BARS,
    TRENDLINE_TOUCH_TOLERANCE_PCT,
    TRENDLINE_VOLUME_FACTOR,
    TRENDLINE_MIN_TOUCHES,
    normalize_symbol_list,
    scan_symbols_parallel,
)


def main():
    parser = argparse.ArgumentParser(description="BIST Zincirleme VWAP Tarayıcı (CLI)")
    parser.add_argument("--period", choices=list(PERIOD_OPTIONS), default="weekly",
                         help="Tarama periyodu: daily/weekly/monthly (günlük veriden "
                              "resample) ya da 4h (Yahoo'nun gün-içi 60dk barlarından "
                              "resample — geçmişi ~730 gün ile sınırlıdır, Yahoo'nun "
                              "kendi gün-içi veri limitidir)")
    parser.add_argument("--symbols", type=str, default=None,
                         help="Virgülle ayrılmış sembol listesi (örn: THYAO,ASELS). .IS otomatik eklenir.")
    parser.add_argument("--symbols-file", type=str, default=None,
                         help="Sembolleri bir TXT dosyasından oku (her satıra bir sembol).")
    parser.add_argument("--lookback", type=int, default=3)
    parser.add_argument("--workers", type=int, default=20, help="Paralel çekme sayısı")
    parser.add_argument("--no-cache", action="store_true", help="Günlük önbelleği kullanma")
    parser.add_argument("--out", type=str, default="bist_vwap_sonuclar.csv")

    # --- Para birimi bazı — VWAP taramasından bağımsız, opsiyonel bir çevirim ---
    parser.add_argument("--currency", choices=CURRENCY_OPTIONS, default="TRY",
                         help="Tarama hangi para birimi bazında yapılsın: TRY (varsayılan, "
                              "TL — hisselerin Yahoo Finance'teki orijinal fiyatı), USD ya da "
                              "EUR (günlük TL fiyat, USDTRY=X/EURTRY=X kuruna bölünerek "
                              "çevrilir, VWAP zinciri bu çevrilmiş seri üzerinde hesaplanır)")

    # --- Yatay (sideways) filtresi — tamamen opsiyonel, seçmeli ---
    parser.add_argument("--sideways", action="store_true",
                         help="Yatay/konsolidasyon filtresini etkinleştir")
    parser.add_argument("--sideways-method", choices=SIDEWAYS_METHODS, default="range",
                         help="Tespit yöntemi: range (bant genişliği), atr (oynaklık), "
                              "both (ikisi birden — daha katı)")
    parser.add_argument("--sideways-months", type=str, default="3,6,12,18,24",
                         help="Virgülle ayrılmış vade (ay) listesi — hisse HER BİR vadeyi "
                              "kendi penceresinde ayrı ayrı kontrol eder (varsayılan: 3,6,12,18,24)")
    parser.add_argument("--sideways-min-windows", type=int, default=None,
                         help="Hissenin 'yatay' sayılması için en az kaç vadede yataylık "
                              "şartını sağlaması gerektiği (varsayılan: verilen vadelerin HEPSİ)")
    parser.add_argument("--sideways-range-pct", type=float, default=15.0,
                         help="'range'/'both' yöntemi için bant genişliği eşiği yüzde (varsayılan: 15)")
    parser.add_argument("--sideways-atr-pct", type=float, default=5.0,
                         help="'atr'/'both' yöntemi için ATR eşiği yüzde (varsayılan: 5)")
    parser.add_argument("--no-sideways-tag", action="store_true",
                         help="VWAP sonuç tablosuna 'Yatay' sütunu eklemeyi kapat")
    parser.add_argument("--no-sideways-list", action="store_true",
                         help="Ayrı yatay-hisseler listesini/CSV'sini kapat")
    parser.add_argument("--sideways-out", type=str, default="bist_yatay_sonuclar.csv")

    # --- Zirveden düşüş filtresi — VWAP taramasından bağımsız, ek bir filtre ---
    parser.add_argument("--drawdown", action="store_true",
                         help="Zirveden (VWAP anchor'ından) düşüş filtresini etkinleştir")
    parser.add_argument("--drawdown-min-pct", type=float, default=60.0,
                         help="Hissenin 'çökmüş' sayılması için VWAP anchor fiyatından "
                              "en az yüzde kaç düşmüş olması gerektiği (varsayılan: 60)")
    parser.add_argument("--drawdown-out", type=str, default="bist_zirveden_dusus_sonuclar.csv")

    # --- Mum alternasyon (zigzag) filtresi — VWAP taramasından bağımsız, ek bir filtre ---
    parser.add_argument("--alternation", action="store_true",
                         help="Mum renk alternasyonu (zigzag) filtresini etkinleştir: "
                              "son mumdan geriye doğru yeşil-kırmızı-yeşil-kırmızı "
                              "(ya da tersi) kesintisiz sıralama arar")
    parser.add_argument("--alternation-min-chain", type=int, default=ALTERNATION_MIN_CHAIN,
                         help=f"Zincirin sayılması için gereken en az mum sayısı "
                              f"(varsayılan: {ALTERNATION_MIN_CHAIN})")
    parser.add_argument("--alternation-min-score", type=float, default=None,
                         help="Gövde-boyu düzenlilik puanı için en az eşik (0-100). "
                              "Belirtilmezse sadece zincir uzunluğu şartı aranır, "
                              "puan eşiği aranmaz.")
    parser.add_argument("--alternation-out", type=str, default="bist_alternasyon_sonuclar.csv")

    # --- Düşen trend çizgisi kırılımı filtresi — VWAP taramasından bağımsız, ek bir filtre ---
    parser.add_argument("--trendline", action="store_true",
                         help="Düşen trend çizgisi kırılımı filtresini etkinleştir: pivot "
                              "tepe noktalarından kurulan düşen bir direnç çizgisini "
                              "kapanışla yukarı kıran hisseleri arar")
    parser.add_argument("--trendline-pivot-window", type=int, default=TRENDLINE_PIVOT_WINDOW,
                         help=f"Pivot tepe tespiti için sol/sağ bar penceresi (varsayılan: {TRENDLINE_PIVOT_WINDOW})")
    parser.add_argument("--trendline-min-span", type=int, default=TRENDLINE_MIN_SPAN_BARS,
                         help=f"Çizginin en az kaç bar uzunluğunda olması gerektiği (varsayılan: {TRENDLINE_MIN_SPAN_BARS})")
    parser.add_argument("--trendline-lookback-bars", type=int, default=TRENDLINE_LOOKBACK_BARS,
                         help=f"Pivot aranacak en fazla geçmiş bar sayısı (varsayılan: {TRENDLINE_LOOKBACK_BARS})")
    parser.add_argument("--trendline-breakout-lookback", type=int, default=3,
                         help="Kırılımın 'taze' sayılması için kaç bar geriye bakılsın (varsayılan: 3)")
    parser.add_argument("--trendline-touch-tolerance-pct", type=float, default=TRENDLINE_TOUCH_TOLERANCE_PCT,
                         help=f"Temas sayımı için tolerans yüzdesi (varsayılan: {TRENDLINE_TOUCH_TOLERANCE_PCT})")
    parser.add_argument("--trendline-min-touches", type=int, default=TRENDLINE_MIN_TOUCHES,
                         help="Çizgiye en az kaç kez temas edilmiş olması gerektiği (iki "
                              "pivot -tepe + son pivot- zaten temas sayılır; daha fazla ara "
                              "temas istenirse bu değer yükseltilir) "
                              f"(varsayılan: {TRENDLINE_MIN_TOUCHES})")
    parser.add_argument("--trendline-require-volume", action="store_true",
                         help="Kırılım barında hacim teyidi şartı ekle")
    parser.add_argument("--trendline-volume-factor", type=float, default=TRENDLINE_VOLUME_FACTOR,
                         help=f"Hacim teyidi şartıysa, kırılım hacminin son 20 barın "
                              f"ortalamasının en az kaç katı olması gerektiği (varsayılan: {TRENDLINE_VOLUME_FACTOR})")
    parser.add_argument("--trendline-out", type=str, default="bist_trend_cizgisi_sonuclar.csv")
    args = parser.parse_args()

    if args.symbols_file:
        with open(args.symbols_file, encoding="utf-8") as f:
            symbols = normalize_symbol_list(f.read().splitlines())
    elif args.symbols:
        symbols = normalize_symbol_list(args.symbols.split(","))
    else:
        symbols = DEFAULT_SYMBOLS

    sideways_tag = args.sideways and not args.no_sideways_tag
    sideways_list = args.sideways and not args.no_sideways_list
    sideways_months_list = [int(m.strip()) for m in args.sideways_months.split(",") if m.strip()]

    print(f"BIST Zincirleme VWAP Tarayıcı — periyot: {PERIOD_LABELS.get(args.period, args.period)} — para birimi: "
          f"{args.currency} — {len(symbols)} hisse — paralellik: {args.workers}")
    if args.period == "4h":
        print("Not: 4 saatlik periyotta VWAP zinciri Yahoo Finance'in gün-içi veri "
              "limiti nedeniyle günlük/haftalık/aylığa göre ÇOK DAHA KISA bir geçmişi "
              "(~730 gün) görür — bu normaldir.")
    if args.sideways:
        min_w = args.sideways_min_windows or len(sideways_months_list)
        print(f"Yatay filtresi: AÇIK — yöntem: {args.sideways_method} — vadeler: "
              f"{sideways_months_list} ay — en az {min_w}/{len(sideways_months_list)} "
              f"vadede yatay şart"
              f" — gösterim: {'etiket' if sideways_tag else ''}"
              f"{' + ' if sideways_tag and sideways_list else ''}"
              f"{'ayrı liste' if sideways_list else ''}")
    if args.drawdown:
        print(f"Zirveden düşüş filtresi: AÇIK — eşik: VWAP anchor fiyatından en az "
              f"%{args.drawdown_min_pct:.0f} düşüş")
    if args.alternation:
        score_txt = (f" — en az puan: {args.alternation_min_score:.0f}"
                     if args.alternation_min_score is not None else "")
        print(f"Alternasyon (zigzag) filtresi: AÇIK — en az {args.alternation_min_chain} "
              f"mum kesintisiz yeşil/kırmızı sıralaması{score_txt}")
    if args.trendline:
        vol_txt = (f" — hacim teyidi: en az {args.trendline_volume_factor:.1f}x"
                   if args.trendline_require_volume else "")
        print(f"Trend çizgisi kırılımı filtresi: AÇIK — pivot penceresi: "
              f"{args.trendline_pivot_window}, en az uzunluk: {args.trendline_min_span} bar{vol_txt}")
    print(f"Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    start = time.time()
    done_count = [0]
    fetch_errors = []

    def on_progress(done, total, sym):
        done_count[0] = done
        print(f"\r[{done}/{total}] işlendi...", end="", flush=True)

    matches_raw, sideways_raw, drawdown_raw, alternation_raw, trendline_raw = scan_symbols_parallel(
        symbols, args.period, lookback=args.lookback,
        max_workers=args.workers, use_cache=not args.no_cache,
        progress_callback=on_progress, errors_out=fetch_errors,
        sideways_enabled=args.sideways, sideways_months_list=sideways_months_list,
        sideways_range_pct=args.sideways_range_pct,
        sideways_atr_pct=args.sideways_atr_pct,
        sideways_method=args.sideways_method,
        sideways_min_windows=args.sideways_min_windows,
        drawdown_enabled=args.drawdown,
        drawdown_min_pct=args.drawdown_min_pct,
        alternation_enabled=args.alternation,
        alternation_min_chain=args.alternation_min_chain,
        alternation_min_score=args.alternation_min_score,
        trendline_enabled=args.trendline,
        trendline_pivot_window=args.trendline_pivot_window,
        trendline_min_span_bars=args.trendline_min_span,
        trendline_lookback_bars=args.trendline_lookback_bars,
        trendline_breakout_lookback=args.trendline_breakout_lookback,
        trendline_touch_tolerance_pct=args.trendline_touch_tolerance_pct,
        trendline_min_touches=args.trendline_min_touches,
        trendline_require_volume=args.trendline_require_volume,
        trendline_volume_factor=args.trendline_volume_factor,
        currency=args.currency,
    )
    print()  # satır sonu

    if fetch_errors:
        print(f"UYARI: {len(fetch_errors)}/{len(symbols)} hissenin verisi çekilemedi:")
        for sym, err in fetch_errors[:10]:
            print(f"  - {sym}: {err}")
        if len(fetch_errors) > 10:
            print(f"  ... ve {len(fetch_errors) - 10} tane daha")
        if len(fetch_errors) == len(symbols):
            print("Hiçbir sembol için veri çekilemedi — sonuçlar 'eşleşme yok' değil, "
                  "tarama aslında hiç çalışmadı. İnternet bağlantısını, Yahoo Finance "
                  "erişimini ya da yfinance sürümünü kontrol edin.\n")

    sideways_by_symbol = {s["symbol"]: s for s in sideways_raw}
    drawdown_by_symbol = {d["symbol"]: d for d in drawdown_raw}
    alternation_by_symbol = {a["symbol"]: a for a in alternation_raw}
    trendline_by_symbol = {t["symbol"]: t for t in trendline_raw}

    matches = [{
        "symbol": r["symbol"],
        "level": r["level"],
        "anchor_reason": r["anchor_reason"],
        "anchor_date": r["anchor_date"],
        "cross_date": r["cross_date"],
        "bars_ago": r["bars_ago"],
        "last_close": r["last_close"],
        "last_vwap": r["last_vwap"],
        "period": args.period,
        "currency": args.currency,
        **({"yatay": (f"EVET ({sideways_by_symbol[r['symbol']]['sideways_count']}/"
                      f"{sideways_by_symbol[r['symbol']]['total_windows']})"
                      if r["symbol"] in sideways_by_symbol else "")} if sideways_tag else {}),
        **({"zirveden_dusus_pct": (drawdown_by_symbol[r["symbol"]]["drawdown_pct"]
                                    if r["symbol"] in drawdown_by_symbol else "")} if args.drawdown else {}),
        **({"alternasyon": (f"EVET ({alternation_by_symbol[r['symbol']]['chain_length']} mum, "
                             f"puan {alternation_by_symbol[r['symbol']]['score']:.0f})"
                             if r["symbol"] in alternation_by_symbol else "")} if args.alternation else {}),
        **({"trend_cizgisi": (f"EVET ({trendline_by_symbol[r['symbol']]['touches']} temas, "
                               f"{trendline_by_symbol[r['symbol']]['bars_ago']} bar önce)"
                               if r["symbol"] in trendline_by_symbol else "")} if args.trendline else {}),
    } for r in matches_raw]

    elapsed = time.time() - start
    print(f"\nBitiş: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ({elapsed:.0f} saniye sürdü)")
    print(f"Toplam eşleşme: {len(matches)}\n")

    if matches:
        result_df = pd.DataFrame(matches).sort_values(["level", "symbol"])
        print(result_df.to_string(index=False))
        result_df.to_csv(args.out, index=False, encoding="utf-8-sig")
        print(f"\nSonuçlar kaydedildi: {args.out}")
    else:
        print("Kriterlere uyan hisse bulunamadı.")

    if sideways_list:
        print(f"\nYatay hisseler (vadeler: {sideways_months_list} ay, yöntem: "
              f"{args.sideways_method}): {len(sideways_raw)} adet\n")
        if sideways_raw:
            flat_rows = [{
                "symbol": s["symbol"],
                "yatay_vade": f"{s['sideways_count']}/{s['total_windows']}",
                "yatay_olan_aylar": ",".join(str(m) for m in s["sideways_months"]),
                **{
                    f"bant_pct_{m}ay": (s["details"][m]["range_pct"] if s["details"].get(m) else "")
                    for m in sideways_months_list
                },
            } for s in sideways_raw]
            sideways_df = pd.DataFrame(flat_rows).sort_values("symbol")
            print(sideways_df.to_string(index=False))
            sideways_df.to_csv(args.sideways_out, index=False, encoding="utf-8-sig")
            print(f"\nYatay hisse listesi kaydedildi: {args.sideways_out}")
        else:
            print("Kriterlere uyan yatay hisse bulunamadı.")

    if args.drawdown:
        print(f"\nZirveden en az %{args.drawdown_min_pct:.0f} düşmüş hisseler: "
              f"{len(drawdown_raw)} adet\n")
        if drawdown_raw:
            drawdown_df = pd.DataFrame([{
                "symbol": d["symbol"],
                "zirveden_dusus_pct": d["drawdown_pct"],
                "anchor_tarihi": d.get("anchor_date"),
                "anchor_nedeni": d.get("anchor_reason"),
            } for d in drawdown_raw]).sort_values("zirveden_dusus_pct", ascending=False)
            print(drawdown_df.to_string(index=False))
            drawdown_df.to_csv(args.drawdown_out, index=False, encoding="utf-8-sig")
            print(f"\nZirveden düşüş listesi kaydedildi: {args.drawdown_out}")
        else:
            print("Kriterlere uyan zirveden-düşmüş hisse bulunamadı.")

    if args.trendline:
        print(f"\nDüşen trend çizgisini kıran hisseler: {len(trendline_raw)} adet\n")
        if trendline_raw:
            trendline_df = pd.DataFrame([{
                "symbol": t["symbol"],
                "temas_sayisi": t["touches"],
                "cizgi_baslangic": t.get("start_date"),
                "cizgi_bitis": t.get("end_date"),
                "kirilma_tarihi": t["cross_date"],
                "kac_bar_once": t["bars_ago"],
                "son_kapanis": t["last_close"],
                "cizgi_su_anki_deger": t["line_value_now"],
                **({"hacim_teyidi": t.get("volume_confirmed")} if args.trendline_require_volume else {}),
            } for t in trendline_raw]).sort_values("kac_bar_once")
            print(trendline_df.to_string(index=False))
            trendline_df.to_csv(args.trendline_out, index=False, encoding="utf-8-sig")
            print(f"\nTrend çizgisi kırılım listesi kaydedildi: {args.trendline_out}")
        else:
            print("Kriterlere uyan trend çizgisi kırılımı bulunamadı.")


if __name__ == "__main__":
    main()
