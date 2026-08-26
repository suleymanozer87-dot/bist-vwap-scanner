# BIST Zincirleme VWAP Tarayıcı — Arayüzlü Sürüm

Grafiklerle, mum çubuklarıyla, VWAP çizgileriyle, TXT dosyasından hisse
ekleyebildiğiniz tam bir arayüz. Hâlâ hiçbir bulut servisi yok — sadece
kendi bilgisayarınızda, tarayıcınızda açılır.

## Kurulum (bir kere)

1. Python 3.9+ kurulu olmalı.
2. Terminal açıp bu klasöre girin:
   ```
   cd bist-vwap-arayuz
   ```
3. Kütüphaneleri kurun:
   ```
   pip install -r requirements.txt
   ```

## Çalıştırma

**En kolay yol — çift tıklayın:**
- **Windows**: `baslat.bat` dosyasına çift tıklayın
- **Mac**: `baslat.command` dosyasına çift tıklayın
  (İlk seferde Mac "bilinmeyen geliştirici" uyarısı verebilir — dosyaya
  sağ tıklayıp **Aç**'ı seçerseniz izin ister, onaylayın)

Bu dosyalar gerekli kütüphaneleri otomatik kontrol edip kurar ve arayüzü
açar — hiçbir komut yazmanıza gerek yok.

**Elle çalıştırmak isterseniz**, terminalde:
```
pip install -r requirements.txt
streamlit run app.py
```

## Arayüz nasıl kullanılır

**Sol panel:**
- **Periyot**: Günlük / Haftalık / Aylık seçin
- **Aynı anda kaç hisse çekilsin (paralellik)**: Varsayılan 20 — büyük listelerde
  (600+ hisse) taramayı çok hızlandırır. Yahoo Finance geçici hata verirse
  (çok fazla eşzamanlı istek) bu değeri 10'a düşürün.
- **Günlük önbelleği kullan**: Açık bırakın — aynı gün içinde tekrar
  taradığınızda internete gitmeden diskteki kayıtlı veriyi kullanır, saniyeler
  içinde biter. Ertesi gün otomatik olarak taze veri çeker.
- **Önbelleği temizle**: Veriyi zorla yeniden çekmek isterseniz (örn. günün
  hangi saatinde olursanız olun en güncel veriyi görmek için)
- **Varsayılan listeyi kullan**: ~80 hisselik hazır liste (açıp kapatabilirsiniz)
- **TXT dosyasından hisse ekle**: Kendi listenizi yükleyin — her satıra bir
  sembol yazın (`thyao`, `ASELS`, `sasa.is` hepsi kabul edilir, `.IS` otomatik
  eklenir). 600 hisselik bir liste de yükleyebilirsiniz.
- **Taramayı Başlat** butonuna basın — bittiğinde kaç saniye sürdüğünü görürsünüz

**Sonuçlar:**
- Üstte özet tablo (sembol, hangi VWAP seviyesinde kırıldı, tarih, fiyat) —
  CSV olarak da indirebilirsiniz
- Alttaki **"Grafiğini görmek istediğiniz hisseleri seçin"** kutusundan
  istediğiniz hisseleri seçin, her biri için:
  - Mum grafiği (candlestick)
  - Zincirdeki her VWAP seviyesi ayrı renkte çizgi olarak (yeşil=VWAP-1,
    mavi=VWAP-2, sarı=VWAP-3) — eşleşen seviye düz çizgi, denenip
    geçilenler noktalı
  - Kırılma anı sarı yıldızla işaretli

## Hız hakkında

600 hisse ilk kez taranırken (önbellek boşken) paralellik=20 ile kabaca
birkaç dakika sürer — asıl darboğaz Yahoo Finance'in yanıt hızıdır, tek
tek istek atmak yerine aynı anda 20 istek gönderildiği için önceki sürüme
göre çok daha hızlıdır. **Aynı gün içindeki tekrar taramalar** önbellek
sayesinde saniyeler içinde biter. Paralelliği çok yükseltmeyin (40'ın
üzerinde) — Yahoo Finance geçici olarak bloklayabilir, o durumda birkaç
dakika bekleyip tekrar deneyin ya da paralelliği düşürün.

## Sadece komut satırı isterseniz

Arayüz olmadan, terminalde çalışan sürümü de bıraktım:
```
python bist_vwap_scanner.py
python bist_vwap_scanner.py --period daily
python bist_vwap_scanner.py --symbols thyao,asels,sasa
python bist_vwap_scanner.py --symbols-file benim_600_hissem.txt --workers 25
```
Sonuçlar `bist_vwap_sonuclar.csv` dosyasına yazılır. Bu da `.IS`'i
otomatik ekler ve aynı paralel+önbellek sistemini kullanır.

## Notlar

- Yahoo Finance verisi gecikmelidir, resmi garanti yoktur — yatırım
  tavsiyesi değildir.
- 80 hisselik tam liste taraması 1-2 dakika sürebilir; arayüzde ilerleme
  çubuğundan takip edebilirsiniz.
- Bir sembolün verisi çekilemezse (yanlış yazım, geçici bağlantı sorunu)
  o hisse sessizce atlanır, tarama durmaz.
