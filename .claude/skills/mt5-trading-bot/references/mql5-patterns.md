# MQL5 kod kalıpları

Bu kalıplar bulut ortamında derlenmemiştir; kullanıcı MetaEditor'da derleyince çıkan
hata/uyarılara göre düzelt.

## Riske göre lot

```mql5
// slDistance: giriş ile SL arasındaki fiyat farkı (ör. 0.00250), riskPct: özkaynak yüzdesi
double LotByRisk(const string sym, const double riskPct, const double slDistance)
{
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS);
   double step      = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vMin      = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vMax      = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(tickSize <= 0 || tickValue <= 0 || step <= 0 || slDistance <= 0)
      return 0.0;

   double riskMoney  = AccountInfoDouble(ACCOUNT_EQUITY) * riskPct / 100.0;
   double lossPerLot = slDistance / tickSize * tickValue;
   double lots       = MathFloor(riskMoney / lossPerLot / step) * step;

   if(lots < vMin)
      return 0.0;                 // riski büyütmek yerine işlemi atla
   return MathMin(lots, vMax);
}
```

## Yeni mum tespiti

```mql5
bool IsNewBar(const string sym, const ENUM_TIMEFRAMES tf)
{
   static datetime last = 0;
   datetime t = iTime(sym, tf, 0);
   if(t == 0 || t == last)
      return false;
   last = t;
   return true;
}
```

Birden fazla sembol/zaman dilimi izleniyorsa her biri için ayrı `last` tut (tek `static`
değişken yetmez).

## Gösterge okuma

```mql5
int atrHandle = INVALID_HANDLE;

int OnInit()
{
   atrHandle = iATR(_Symbol, PERIOD_CURRENT, 14);
   if(atrHandle == INVALID_HANDLE)
      return INIT_FAILED;
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(atrHandle != INVALID_HANDLE)
      IndicatorRelease(atrHandle);
}

bool GetAtr(double &value)          // kapanmış son mumun ATR'si
{
   double buf[];
   if(CopyBuffer(atrHandle, 0, 1, 1, buf) != 1)
      return false;
   value = buf[0];
   return true;
}
```

## Spread ve stop mesafesi kontrolü

```mql5
bool SpreadOk(const string sym, const int maxSpreadPoints)
{
   return (int)SymbolInfoInteger(sym, SYMBOL_SPREAD) <= maxSpreadPoints;
}

bool StopDistanceOk(const string sym, const double entry, const double sl)
{
   double point   = SymbolInfoDouble(sym, SYMBOL_POINT);
   long   minPts  = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   return MathAbs(entry - sl) >= minPts * point;
}
```

## Dolum tipi seçimi

```mql5
ENUM_ORDER_TYPE_FILLING PickFilling(const string sym)
{
   long modes = SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if((modes & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
   if((modes & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
   return ORDER_FILLING_RETURN;
}
```

## Emir gönderme iskeleti

```mql5
#include <Trade/Trade.mqh>
input ulong InpMagic = 20261114;
CTrade trade;

// OnInit içinde:
//   trade.SetExpertMagicNumber(InpMagic);
//   trade.SetTypeFilling(PickFilling(_Symbol));
//   trade.SetDeviationInPoints(InpMaxSlippagePoints);

bool OpenBuy(const double lots, const double sl, const double tp)
{
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(!trade.Buy(lots, _Symbol, ask,
                 NormalizeDouble(sl, _Digits), NormalizeDouble(tp, _Digits)))
   {
      Print("Buy başarısız: ", trade.ResultRetcode(), " ", trade.ResultRetcodeDescription());
      return false;
   }
   return trade.ResultRetcode() == TRADE_RETCODE_DONE ||
          trade.ResultRetcode() == TRADE_RETCODE_PLACED;
}
```

## Günlük kayıp sınırı

Gün başı özkaynağı sunucu günü değişince kaydet; `(günBaşı - ACCOUNT_EQUITY) / günBaşı`
sınırı aşınca o gün yeni işlem açma. Değeri EA yeniden başlatıldığında kaybolmasın diye
`GlobalVariableSet` ile sakla.

## Yalnızca kendi pozisyonlarını say

```mql5
int CountMyPositions(const string sym, const ulong magic)
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == sym &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == magic)
         n++;
   }
   return n;
}
```
