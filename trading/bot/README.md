# Fitil: M15 gün içi işlem botu

Python ile yazılmış, MetaTrader 5'e bağlanan gün içi bot. 7 major parite, altın ve petrolde
her M15 çubuğu kapanışında tarama yapar.

**Durum:** mantık simüle brokerla test edildi (bkz. `trading/tests/test_fitil.py`).
MT5 bağlantısı gerçek bir terminalde henüz çalıştırılmadı. Strateji kuralları gerçek gün içi
veride sınanmadı; kâr ettiğine dair kanıt yoktur. Önce `--trade` olmadan, sonra yalnızca demo hesapta.

## Ne yapar

- **Tarama:** 5 saniyede bir yeni M15 çubuğu kapanmış mı diye bakar; kapandıysa sinyal arar.
- **Kurulum 1, likidite süpürmesi:** fiyat önceki günün tepe/dibini ya da Asya aralığını fitille
  aşıp geri içeride güçlü kapanırsa süpürmenin tersine girer.
- **Kurulum 2, trend geri çekilmesi:** H1 ve H4 aynı yöndeyken fiyat M15 EMA'sına çekilip
  trend yönünde kapanırsa girer.
- **Stop:** yapının (fitilin ya da salınımın) ötesinde. Stop mesafesi spread'in 10 katından
  kısaysa ya da 3 ATR'den uzunsa işlem alınmaz. Her emir stop ile gönderilir.
- **Kâr:** sabit hedef yok. 1R'de stop girişe çekilir, 1,5R'den sonra son 8 çubuğun yapısını izler.
- **Gün sonu:** 20:00 UTC'de tüm pozisyonlar kapatılır; gece ve hafta sonu pozisyon taşınmaz.

## Risk kuralları (hepsi özkaynağın oranı)

| Kural | Varsayılan |
|---|---|
| İşlem başına risk (kanaat puanı 1 / 2 / 3) | %0,25 / %0,5 / %1 |
| Toplam açık risk | en fazla %3 |
| Tek USD yönünde net risk | en fazla %2 |
| Günlük kayıp sınırı | %3 (aşılınca o gün yeni giriş yok) |
| Günlük işlem sayısı / eşzamanlı pozisyon | 20 / 5 |
| Zirveden düşüş freni | %10'da risk yarıya iner, %20'de yeni giriş durur |
| Giriş saatleri | 07:00-17:00 UTC |

Lot hiçbir zaman yukarı yuvarlanmaz: bütçe en küçük lota yetmiyorsa işlem atlanır.
Küçük hesaplar için cent hesap gerekir.

## Kurulum (Windows)

1. MT5 terminalini açın, **demo** hesaba giriş yapın, "Algo Trading" düğmesini etkinleştirin.
2. Python 3.10+ kurun, sonra bu klasörde: `pip install -r requirements.txt`
3. `config.example.json` dosyasını `config.json` olarak kopyalayın. `symbols` altındaki adları
   Market Watch'taki tam adlarla değiştirin (ör. `XAUUSD.m`, `USOIL`).
4. Yüksek etkili veri saatlerini (NFP, FOMC, CPI, EIA) `blackouts` listesine UTC olarak yazın:
   `"blackouts": ["2026-11-18T19:00"]`

```
cd trading\bot
python -m fitil --config config.json            # yalnızca sinyal yazar, emir göndermez
python -m fitil --config config.json --trade    # demo hesapta emir gönderir
```

Gerçek hesapta çalışmayı bot varsayılan olarak reddeder (`--allow-real` gerekir). Bu karar sizindir.

Çalışırken `fitil_journal.csv` (her sinyal, giriş, stop taşıma, çıkış, çekim önerisi) ve
`fitil_state.json` (gün başı özkaynak, zirve, günlük işlem sayısı) oluşur. `fitil.bat` çift
tıklamayla sinyal modunda başlatır.

## Güvenlik davranışları

- **Bağlantı koparsa** bot her turda yeniden bağlanmayı dener; stoplar bu sırada brokerda durur.
- **Para çekme ya da yatırma** kâr/zarar sayılmaz: düşüş freni ve günlük kayıp sınırı buna göre ölçeklenir.
- **Stopsuz pozisyon** (bu bota ait) görülürse hemen kapatılır.
- **Durum dosyası kaybolursa** açık pozisyonun stop takibi mevcut stoptan yeniden kurulur.
- **İkinci kopya** aynı klasörde başlatılamaz (`fitil.lock`).
- **Veri saatleri:** NFP (ayın ilk cuması 08:30 New York) ve EIA (çarşamba 10:30 New York, yalnızca
  `"group": "oil"` semboller) çevresinde 15 dakika giriş yapılmaz. Tatil haftalarında bu saatler kayar;
  kesin tarihleri ve FOMC, CPI gibi verileri `blackouts` listesine siz yazın.
- **Çekim önerisi:** cuma kapanışında, önceki zirveyi aşan kârın yarısı günlüğe "ÇEKİM" satırı olarak
  yazılır. Bot para çekemez; karar ve işlem sizindir. Oran `withdraw_frac` ile değişir.

## Geçmiş veride sınama

Canlıdaki aynı strateji ve motor kodu, simüle brokerla çalışır:

```
python -m fitil.backtest --config config.json --data ..\data --tz-shift 2 --trades-out islemler.csv
```

`--data` klasöründe config'teki sembol adıyla `<SEMBOL>.csv` dosyaları olmalı. MT5 çubuk dışa
aktarımı (M15 ya da M1) ve HistData M1 biçimi okunur. `--tz-shift`, verinin UTC'den farkıdır;
MT5 sunucu saati çoğunlukla UTC+2 ya da UTC+3'tür. Rapor kuruluma, sembole, çıkış nedenine ve
giriş saatine göre döküm verir. Simülasyon sabit spread kullanır ve çubuk içi sırayı bilemez;
sonuçlar gerçeğe göre iyimser olabilir.

## Beklenti hesabı

Backtest ya da demo sonuçlarınızdaki kazanma oranı ve ortalama kazançla, risk kuralları altında
bir yılın nasıl geçebileceğini gösterir:

```
python -m fitil.plan --equity 1000 --win-rate 0.42 --avg-win 1.5 --trades-per-day 6 --weekly-target 100
```

Girdiler sizin varsayımınızdır; araç bir tahmin değil, "bu istatistikler gerçekse" hesabıdır.

## Bilinen sınırlar

- Bot kapalıyken yeni işlem açılmaz ve stoplar sürüklenmez; mevcut stoplar brokerda kalır.
- Sunucu saat farkı tick saatinden kestirilir. Piyasa kapalıyken başlatırsanız
  `server_utc_offset_hours` ayarını elle yazmanız gerekir.
- Haber takvimi otomatik değildir; `blackouts` listesini siz güncellersiniz.
- Netting hesaplarda aynı sembolde elle işlem açmayın; pozisyonlar birleşir.
