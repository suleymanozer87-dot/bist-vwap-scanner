# V5.3.1 — Sonuçlar sayfası hata kaydı düzeltmesi

- `errors` kayıtlarının tuple/list/string/dict biçimleri güvenli okunur.
- Arka plan worker hata kayıtlarını stringe çevirmez; JSON uyumlu `[symbol, message]` olarak saklar.
- Eski checkpointlerdeki `str(tuple)` kayıtları da normalize edilir.
- Tarama ve VWAP strateji mantığı değiştirilmemiştir.
