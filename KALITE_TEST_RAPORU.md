# Yukarı Yön Kalite Motoru — Regresyon Test Özeti

Test tarihi: 2026-09-03

Bu test, paket içindeki eski gerçek BIST cache verilerinden daha önce kaydedilmiş geçmiş sinyal olaylarını kullanır. Puan her olay gününde yalnız o güne kadar mevcut veriyle yeniden hesaplanmıştır; gelecek barlar sadece başarı/başarısızlık sonucunu ölçmek için kullanılmıştır.

## Hızlı regresyon örneklemi

| Motor | Test edilen sinyal | Ham başarı | 60+ puan | 70+ puan | 80+ puan |
|---|---:|---:|---:|---:|---:|
| Düşen trend 1h | 179 | %53,5 | %64,5 (72) | %64,0 (30) | %77,8 (10; 9 net) |
| Üçgen 4h | 178 | %47,8 yukarı kırılım | %61,9 (42) | %88,9 (9) | örnek yok |
| VWAP haftalık USD | 87 | %60,3 | %65,4 (60) | %65,9 (49) | %81,0 (23) |
| Alternasyon aylık | 180 | %52,8 sonraki mum pozitif | %53,8 (26) | %75,0 (4) | örnek yok |

Düşen trend başarı ölçümü: sonraki 20 adet 1 saatlik barda +%3 hedefin -%3 zarardan önce gelmesi (aynı barda ikisi birden oluşan belirsiz olaylar net başarı hesabından çıkarılır).

Üçgen başarı ölçümü: sonraki 10 adet 4 saatlik bar içinde ilk üçgen dışı kapanışın yukarı yönde olması.

VWAP başarı ölçümü: sonraki 8 haftada +%5 hedefin -%5 zarardan önce gelmesi; aynı haftada iki seviye de görülürse sıra bilinmediği için belirsiz kabul edilir.

Alternasyon başarı ölçümü: sonraki aylık mum kapanışının sinyal kapanışından yüksek olması.

## Yorum

- Kalite puanı özellikle Üçgen, Düşen Trend ve VWAP'ta ham sinyalleri sıralamada faydalı ayrım üretti.
- 80+ ve bazı 70+ gruplarda örnek sayısı küçüktür; yüzdeler istatistiksel garanti değildir.
- BIST 100 göreceli güç verisi eski cache paketinde bulunmadığı için bu tarihsel doğrulamada ilgili bileşen nötr puanla çalıştı. Canlı kullanımda `XU100.IS` verisi erişilebildiğinde göreceli güç de puana eklenir.
- Alternasyon tek başına hâlâ zayıf bir yön tahmin aracıdır; yüksek puan alması için dış teyitlere ihtiyaç duyar.
