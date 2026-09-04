# V5.0 — TradingView benzeri mobil grafik motoru

- Plotly grafik motoru sonuç grafiklerinden kaldırıldı.
- Grafikler self-contained Canvas ile çiziliyor; dış kütüphane/CDN gerekmiyor.
- Tek parmak: yatay pan.
- İki parmak: gerçek pinch zoom (zaman ekseni).
- Sağ fiyat ekseni: parmak/mouse ile sürükleyerek dikey fiyat ölçeği.
- Sağ fiyat eksenine çift dokun/çift tık: otomatik fiyat ölçeğine dön.
- Ayrı hacim paneli yoktur.
- Hacim yalnız fiyat mumunun gövde genişliğine yansır; yüksek hacim daha şişkin, düşük hacim daha ince mumdur.
- Grafik içinde tekrar eden hisse adı/başlık ve mobil kullanım açıklaması kaldırıldı.
- Tarama algoritmaları değişmedi.
