# Fitil: M15 gün içi işlem botu

Python ile yazılmış, MetaTrader 5'e bağlanan gün içi bot. 7 major parite, altın ve petrolde
her M15 çubuğu kapanışında tarama yapar.

**Durum (Kasım 2026):**
- Altyapı 102 testle ve iki bağımsız kod incelemesiyle sınandı.
- MT5 bağlantısı gerçek terminalde çalıştırılmadı: terminal yalnızca Windows'ta çalışır ve bu geliştirme
  ortamında kurulamıyor. Bunun yerine MT5'in Python arayüzünü taklit eden bir simülatör yazıldı
  (`tests/mt5_sim.py`): canlı kod yolu (bağdaştırıcı + motor + komut satırı) bu simülatöre karşı baştan
  sona çalışıyor ve doğrudan simülasyonla işlem işlem aynı sonucu veriyor (sunucu saati, yaz saati
  geçişi, stop taşıma, gün sonu kapanışı dahil). Kullanılan tüm sabitler, fonksiyonlar, alan ve istek adları resmi
  `MetaTrader5` paketinin (5.0.6231) kaynağıyla karşılaştırıldı ve eşleşti.
- **Strateji kanıtı karışık ve yetersiz.** Geri çekilme kurulumu gerçek EURUSD verisinde 2019-20'de
  M15'te maliyet sonrası +0,14R (197 işlem), 2017'de saatlikte -0,10R (146 işlem); vadeli altında
  2020-22'de -0,06R (168 işlem) verdi. Süpürme (fitil) kurulumu hiçbir dönemde kazandırmadı ve
  varsayılan olarak kapatıldı.
  Ayrıntı aşağıda "Gerçek veri sonucu" bölümünde. Demo hesapta izlemeden gerçek para kullanmayın.

## Ne yapar

- **Tarama:** 5 saniyede bir yeni M15 çubuğu kapanmış mı diye bakar; kapandıysa sinyal arar.
- **Kurulum 1, likidite süpürmesi (varsayılan olarak kapalı):** fiyat önceki günün tepe/dibini ya da Asya aralığını fitille
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
| İşlem başına risk | %0,5 (geri çekilme kurulumu). Kademeler %0,25 / %0,5 / %1 yalnızca süpürme kurulumu açıkken kullanılır |
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
- **Para çekme ya da yatırma** kâr/zarar sayılmaz: düşüş freni ve günlük kayıp sınırı yalnızca işlem
  sonuçlarını izler. Akış görüldüğü an bir tarama boyunca (5 sn) yeni giriş yapılmaz.
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

Kurulumları ayrı ayrı sınamak için config'te `"strategy": {"setups": ["pullback"]}` yazılabilir.
Zaman dilimi `"strategy": {"bar_minutes": 15}` ile seçilir (5, 15, 30 ya da 60).

Parametreyi geçmişte seçip görülmemiş dönemde sınamak (walk-forward) için:

```
python -m fitil.backtest --config config.json --data ..\data --wf --train-days 120 --test-days 30
```

Yalnızca örnek dışı (test) sonuçlar kanıt sayılır. Ortalama R, standart hatasının iki katından büyük
değilse sonuç şanstan ayırt edilemez.

## Gerçek veri sonucu

Veri: açık kaynak paketlerin içinde dağıtılan gerçek EURUSD, altın ve ham petrol verisi.
`python tools/fetch_sample_data.py` ile aynı dosyaları indirip sonuçları yeniden üretebilirsiniz.

| Dönem ve zaman dilimi | Kurulum | İşlem | Kazanma | Ortalama R (std. hata) | Getiri |
|---|---|---|---|---|---|
| Mart 2019 - Nisan 2020, **M15** | geri çekilme | 197 | %44,2 | **+0,140** (0,107) | +%13,4 |
| aynı | süpürme | 16 | %31,2 | -0,346 (0,263) | -%3,3 |
| Mart 2019 - Nisan 2020, saatlik | geri çekilme | 150 | %48,0 | +0,161 (0,108) | +%11,1 |
| aynı | süpürme | 59 | %40,7 | -0,017 (0,166) | +%6,7 |
| Ocak 2017 - Şubat 2018, saatlik | geri çekilme | 146 | %32,2 | -0,096 (0,096) | -%9,4 |
| aynı | süpürme | 70 | %25,7 | -0,277 (0,157) | -%9,2 |

Hepsi varsayılan maliyetlerle (spread 1,2 pip, kayma, lot başına 7 USD komisyon). Giriş ve çıkış
kuralları veri görülmeden önce yazıldığı haliyle; hiçbir parametre bu verilere göre ayarlanmadı.
Tek değişiklik pozisyon büyüklüğünde: geniş giriş çubuğuna verilen iki kat risk kaldırıldı, çünkü dört
veri setinde (554 işlem) bu girişler daha iyi çıkmadı (-0,03R'ye karşı +0,04R). İşlemler ve R değerleri
aynı; getiri ve düşüş rakamları bu sabit %0,5 riskle hesaplandı.

Geri çekilme kurulumunun 2019-20 M15 sonucunun sağlamlığı:

- 14 ayın 9'unda pozitif. En iyi ay (Mart 2020) çıkarılınca +0,09R; en iyi 3 işlem çıkarılınca +0,05R.
- Dönemin ilk yarısı +0,04R, ikinci yarısı +0,24R. Short işlemler +0,23R, long işlemler +0,06R.
- Yeniden örnekleme (bootstrap): ortalama R'nin sıfırdan büyük olma olasılığı %91; %90 aralığı
  [-0,03R, +0,32R]. Yani olumlu ama istatistiksel olarak kanıtlanmış değil.

Walk-forward (2019-20 M15; stop parametreleri 120 günlük geçmişte seçilip sonraki görülmemiş 30 günde
sınandı): 8 dilimin 7'si pozitif, toplam 103 örnek dışı işlemde ortalama +0,21R (std. hata 0,15).
Yön tutarlı, ama işlem sayısı kesin hüküm için hâlâ az.

Aynı kurallar, parametre değiştirilmeden, 2019-20 verisinde başka koşullarda:

| Koşul | İşlem | Ortalama R (std. hata) | Getiri |
|---|---|---|---|
| M5 | 140 | +0,076 (0,112) | +%5,0 |
| M15 (temel, spread 1,2 pip) | 197 | +0,140 (0,107) | +%13,4 |
| M30 | 190 | +0,098 (0,098) | +%9,1 |
| Saatlik | 150 | +0,161 (0,108) | +%11,1 |
| M15, spread 2 pip | 58 | +0,057 (0,161) | +%0,8 |
| M15, spread 3 pip | 23 | -0,002 (0,213) | -%0,1 |

Dört zaman diliminin hepsinde işaret pozitif. Sonuç maliyete duyarlı: spread 2 pip olunca "stop en az
10 spread" kuralı işlemlerin çoğunu eler ve kalan avantaj küçülür; 3 pipte kaybolur. Bu sistem ancak
düşük spread'li (EURUSD'de yaklaşık 1 pip ve altı) bir hesapta anlamlıdır.

Altın ve petrol (aynı kurallar, parametre değişmeden, varsayılan maliyetlerle):

| Veri | İşlem | Kazanma | Ortalama R (std. hata) | Getiri | Maliyetsiz ortalama R |
|---|---|---|---|---|---|
| Altın XAUUSD M15, tam seans, Ekim - Aralık 2021 | 43 | %32,6 | +0,121 (0,268) | +%2,2 | +0,418 |
| Vadeli altın GC M15, yalnız New York seansı, 2020 - Şubat 2022 | 168 | %44,0 | -0,063 (0,059) | -%4,9 | +0,029 |
| Ham petrol CL M15, yalnız New York seansı, Ekim - Aralık 2021 | 8 | %25,0 | -0,230 (0,274) | -%1,0 | -0,176 |

Vadeli altın en büyük bağımsız örnek ve sonucu net: maliyet öncesi sıfır, maliyet sonrası hafif eksi.
Kısıtı, verinin yalnızca New York seansını içermesi (bot Londra ve New York'u birlikte kullanmak üzere
tasarlandı) ve vadeli fiyat olması. Spot altın ve petrol örnekleri hüküm vermek için çok küçük.

Okuma:

- **Süpürme kurulumu** hiçbir dönemde kazandırmadı. 2017 verisinde sinyalden sonraki 4-12 saatte fiyat
  ortalamada sinyalin tersine gitti. Bu yüzden varsayılan olarak kapalı; açmak için config'e
  `"strategy": {"setups": ["sweep", "pullback"]}` yazılır.
- **Geri çekilme kurulumu** döneme ve enstrümana bağlı: EURUSD'de 2019-20'de kazandırdı, 2017'de
  kaybettirdi; vadeli altında 2020-22'de kazandırmadı. Güvenilir bir avantaj olduğu gösterilmiş değil;
  eldeki tek olumlu sonuç EURUSD'nin bir dönemi.
- Eksik olan: diğer majör pariteler, tam seans altın ve petrolün daha uzun dönemleri.

Bu istatistikler gerçek olsaydı ne anlama gelirdi (`fitil.plan` ile, 2019-20 M15 değerleri: kazanma
%44, ortalama kazanç 1,33R, ortalama kayıp 0,80R, enstrüman başına günde 0,7 işlem):

| Varsayım | 1000 dolarla haftalık çekim | Bir yıl sonra toplam (medyan) | Haftada 100 dolar için sermaye |
|---|---|---|---|
| Tek enstrüman | yaklaşık 1,4 dolar | yaklaşık 1.130 dolar | yaklaşık 71.000 dolar |
| Beş bağımsız enstrüman | yaklaşık 7,4 dolar | yaklaşık 1.750 dolar | yaklaşık 13.600 dolar |

İkinci satır, diğer enstrümanların da aynı sonucu vereceğini ve birbirinden bağımsız olacağını varsayar;
ikisi de sınanmadı.

## Beklenti hesabı

Backtest ya da demo sonuçlarınızdaki kazanma oranı ve ortalama kazançla, risk kuralları altında
bir yılın nasıl geçebileceğini gösterir:

```
python -m fitil.plan --equity 1000 --win-rate 0.42 --avg-win 1.5 --trades-per-day 6 --weekly-target 100
```

Girdiler sizin varsayımınızdır; araç bir tahmin değil, "bu istatistikler gerçekse" hesabıdır.

## İlk çalıştırma kontrol listesi

1. `python -m fitil --config config.json` (emir göndermez). Açılışta "Hesap: DEMO" yazmalı.
2. Hata çıkarsa mesajı aynen saklayın; MT5 bağlantısı gerçek terminalde ilk kez çalışıyor olacak.
3. Birkaç saat çalıştırın. `fitil_journal.csv` içinde `SİNYAL` satırlarının saatini MT5 grafiğindeki
   M15 çubuklarıyla karşılaştırın: sinyal, grafikte gördüğünüz çubuğun kapanışına denk gelmeli.
   Bir saat kayma varsa `server_time` ayarı yanlıştır.
4. Sinyaller mantıklıysa `--trade` ile demo hesapta emir göndermeye geçin. İlk işlemlerde lotu,
   stop fiyatını ve `risk_yuzde` değerini elle doğrulayın.
5. En az birkaç hafta demo. Sonuçları `fitil.plan` ile değerlendirin.

## Simülatörün doğrulanması

Yönü olmayan rastgele veride hiçbir strateji maliyet öncesi kazanamaz. Simülatör bu testten geçti:
136 farklı rastgele seride, sıfır maliyetle 8.816 işlemin ortalaması -0,02R (standart hata 0,015).
Yani simülatörde ileriye bakma yok. Bu, stratejinin gerçek piyasada kazandığını göstermez; yalnızca
backtest aracının kendiliğinden kâr üretmediğini gösterir.

## Düşüş freni devreye girerse

Zirveden %20 düşüşte bot yeni giriş yapmayı bırakır ve günlüğe `ENGEL ... düşüş freni: sistem durdu`
yazar. Bu durum kendiliğinden kalkmaz; amaç durup nedenini incelemektir. İnceledikten sonra
`python -m fitil --config config.json --reset-peak` ile zirveyi şimdiki özkaynağa çekip devam edebilirsiniz.

## Sunucu saati

MT5 zamanları broker sunucu saatindedir, bot UTC kullanır. En sağlam ayar, sunucunun saat dilimini
tanımlamaktır. Çoğu broker "New York + 7 saat" kullanır (kışın UTC+2, yazın UTC+3):

```
"server_time": {"tz": "America/New_York", "plus_hours": 7}
```

Bu ayar yoksa fark, piyasa açıkken canlı fiyat akışından kestirilir; piyasa kapalıyken bot başlamaz ve
ayarı ister. Emin olmak için MT5 Market Watch'taki saati UTC ile karşılaştırın.

## Bilinen sınırlar

- Strateji yalnızca EURUSD'de, iki dönemde sınandı; diğer pariteler, altın ve petrol sınanmadı.
  `fitil.backtest` ile kendi verinizde sınamadan ve demo hesapta izlemeden gerçek para kullanmayın.
- MT5 bağlantısı gerçek terminalde çalıştırılmadı; ilk çalıştırmada hata çıkması olasıdır.

- Bot kapalıyken yeni işlem açılmaz ve stoplar sürüklenmez; mevcut stoplar brokerda kalır.
- Sunucu saat farkı tick saatinden kestirilir. Piyasa kapalıyken başlatırsanız
  `server_utc_offset_hours` ayarını elle yazmanız gerekir.
- Haber takvimi otomatik değildir; `blackouts` listesini siz güncellersiniz.
- Netting hesaplarda aynı sembolde elle işlem açmayın; pozisyonlar birleşir.
