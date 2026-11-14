//+------------------------------------------------------------------+
//| ArdhalenPortfolioTrend.mq5                                        |
//| Çok sembollü trend takip portföyü: forex majors + altın + petrol  |
//|                                                                    |
//| Kurallar research/fxlab/backtest.py ile aynıdır:                  |
//|  - Sinyal: Donchian kırılım topluluğu, kapanmış çubukta hesaplanır |
//|  - Giriş: sonraki çubukta, piyasa emri, her zaman stop-loss ile    |
//|  - Stop: ATR katı; çubuk kapanışlarında iz sürer                   |
//|  - Lot: özkaynak * risk% / stop mesafesi (aşağı yuvarlanır)        |
//|  - Portföy: toplam açık risk, net USD riski, günlük kayıp sınırı   |
//|                                                                    |
//| DURUM: bulut ortamında yazıldı, DERLENMEDİ. Önce Strategy Tester, |
//| sonra demo hesap. Tek bir grafiğe takılır, tüm sembolleri yönetir. |
//+------------------------------------------------------------------+
#property copyright "ardhalen"
#property version   "1.00"

#include <Trade/Trade.mqh>
#include <Ardhalen/Signals.mqh>
#include <Ardhalen/Risk.mqh>

input group "Semboller (Market Watch'taki TAM adlar, virgülle)"
input string          InpSymbols         = "EURUSD,GBPUSD,USDJPY,USDCHF,AUDUSD,USDCAD,NZDUSD,XAUUSD,XTIUSD";
input string          InpUsdNeutral      = "XTIUSD,XBRUSD";   // USD yönü hesabına katılmayanlar (petrol)
input ENUM_TIMEFRAMES InpTimeframe       = PERIOD_D1;

input group "Sinyal"
input int             InpLookback1       = 20;
input int             InpLookback2       = 60;
input int             InpLookback3       = 120;
input double          InpEntryThreshold  = 0.66;   // |skor| >= bu değer ise giriş
input int             InpAtrPeriod       = 20;
input double          InpStopAtr         = 3.0;    // stop mesafesi (ATR katı)
input int             InpMaxScanBars     = 500;    // son kırılımı ararken geriye bakılacak çubuk

input group "Risk"
input double          InpRiskPerTradePct = 0.5;    // işlem başına risk (özkaynak %)
input double          InpMaxPortfolioPct = 4.0;    // tüm açık pozisyonların toplam riski (%)
input double          InpMaxUsdNetPct    = 2.0;    // tek USD yönünde net risk (%)
input double          InpDailyLossPct    = 2.0;    // günlük kayıp sınırı (%); aşılınca yeni giriş yok

input group "Emir"
input ulong           InpMagic           = 20261114;
input int             InpSlippagePoints  = 30;
input double          InpMaxSpreadAtr    = 0.05;   // spread, ATR'nin bu oranını aşarsa bekle
input int             InpEntryDelayMin   = 60;     // yeni çubuktan sonra bekleme (rollover spread'i)
input int             InpEntryWindowMin  = 360;    // bu süre içinde girilemezse sinyal düşer
input int             InpMaxSendRetries  = 3;

struct SymState
  {
   string            name;
   int               usdBeta;
   datetime          lastBar;
   int               pendingDir;     // 0: yok, +1/-1: bekleyen giriş
   double            pendingAtr;
   datetime          pendingBar;
   int               sendFails;
   bool              pendingExit;
  };

SymState     g_sym[];
int          g_lookbacks[];
CTrade       g_trade;
CDailyGuard  g_guard;
int          g_need = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   if(InpRiskPerTradePct <= 0.0 || InpStopAtr <= 0.0 || InpAtrPeriod <= 0)
     {
      Print("Geçersiz girdi: risk, stop ATR katı ve ATR periyodu pozitif olmalı");
      return INIT_PARAMETERS_INCORRECT;
     }
   ArrayResize(g_lookbacks, 0);
   int lbs[3];
   lbs[0] = InpLookback1; lbs[1] = InpLookback2; lbs[2] = InpLookback3;
   int maxLb = 0;
   for(int i = 0; i < 3; i++)
      if(lbs[i] > 0)
        {
         int n = ArraySize(g_lookbacks);
         ArrayResize(g_lookbacks, n + 1);
         g_lookbacks[n] = lbs[i];
         maxLb = MathMax(maxLb, lbs[i]);
        }
   if(ArraySize(g_lookbacks) == 0)
     {
      Print("En az bir lookback pozitif olmalı");
      return INIT_PARAMETERS_INCORRECT;
     }
   g_need = MathMax(maxLb, InpAtrPeriod) + InpMaxScanBars + 2;

   string parts[];
   int cnt = StringSplit(InpSymbols, StringGetCharacter(",", 0), parts);
   ArrayResize(g_sym, 0);
   for(int i = 0; i < cnt; i++)
     {
      string s = parts[i];
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "") continue;
      if(!SymbolSelect(s, true))
        {
         Print("Sembol bulunamadı, atlanıyor: ", s, " (Market Watch'taki tam adı girin)");
         continue;
        }
      int n = ArraySize(g_sym);
      ArrayResize(g_sym, n + 1);
      g_sym[n].name        = s;
      g_sym[n].usdBeta     = UsdBeta(s, InpUsdNeutral);
      g_sym[n].lastBar     = 0;
      g_sym[n].pendingDir  = 0;
      g_sym[n].pendingAtr  = 0.0;
      g_sym[n].pendingBar  = 0;
      g_sym[n].sendFails   = 0;
      g_sym[n].pendingExit = false;
      PrintFormat("%s: USD yönü=%d, kontrat=%.2f, min lot=%.2f, adım=%.2f", s, g_sym[n].usdBeta,
                  SymbolInfoDouble(s, SYMBOL_TRADE_CONTRACT_SIZE),
                  SymbolInfoDouble(s, SYMBOL_VOLUME_MIN), SymbolInfoDouble(s, SYMBOL_VOLUME_STEP));
     }
   if(ArraySize(g_sym) == 0)
     {
      Print("İşlem yapılacak geçerli sembol yok");
      return INIT_FAILED;
     }

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpSlippagePoints);
   g_guard.Init(InpMagic, InpDailyLossPct);

   if(AccountInfoInteger(ACCOUNT_MARGIN_MODE) != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
      Print("UYARI: netting hesap. Aynı sembolde başka EA/elle işlem varsa pozisyonlar birleşir; bu EA'yı tek başına çalıştırın.");
   if(AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL)
      Print("UYARI: GERÇEK hesap. Bu EA önce Strategy Tester ve demo hesapta doğrulanmalıdır.");

   EventSetTimer(30);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason) { EventKillTimer(); }
void OnTick()  { Process(); }
void OnTimer() { Process(); }

//+------------------------------------------------------------------+
//| Bu EA'ya ait, verilen semboldeki pozisyonu seçer                  |
//+------------------------------------------------------------------+
bool SelectMyPosition(const string sym, ulong &ticket)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == sym && (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
        {
         ticket = t;
         return true;
        }
     }
   return false;
  }

// Bu EA'nın açık pozisyonlarının toplam riski ve net USD yönü riski
void OpenRisk(double &total, double &usdNet)
  {
   total = 0.0;
   usdNet = 0.0;
   double fallback = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPerTradePct / 100.0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if((ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic) continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      int dir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      double risk = SelectedPositionRisk();
      if(risk < 0.0)
        {
         Print("UYARI: ", sym, " pozisyonunun riski hesaplanamadı (SL yok?); tam risk sayılıyor");
         risk = fallback;
        }
      total  += risk;
      usdNet += risk * dir * UsdBeta(sym, InpUsdNeutral);
     }
  }

//+------------------------------------------------------------------+
void Process()
  {
   g_guard.Allows();                          // gün başı özkaynağını her gün ilk çağrıda kaydeder
   for(int i = 0; i < ArraySize(g_sym); i++)
     {
      OnNewBar(i);
      DoExit(i);
      DoEntry(i);
     }
  }

//+------------------------------------------------------------------+
//| Yeni çubukta: sinyali hesapla, stopu ilerlet, emirleri kuyruğa al |
//+------------------------------------------------------------------+
void OnNewBar(const int i)
  {
   string sym = g_sym[i].name;
   datetime t = iTime(sym, InpTimeframe, 0);
   if(t == 0 || t == g_sym[i].lastBar) return;

   MqlRates r[];
   ArraySetAsSeries(r, true);
   int minBars = MathMax(g_lookbacks[ArrayMaximum(g_lookbacks)], InpAtrPeriod) + 2;
   int got = CopyRates(sym, InpTimeframe, 1, g_need, r);
   if(got < minBars) return;                 // geçmiş henüz yüklenmedi; sonraki çağrıda tekrar dene

   double atr   = AtrSimple(r, got, InpAtrPeriod);
   double score = EnsembleScore(r, got, g_lookbacks, InpMaxScanBars);
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   g_sym[i].lastBar    = t;
   g_sym[i].pendingDir = 0;
   g_sym[i].sendFails  = 0;

   ulong ticket = 0;
   bool has = SelectMyPosition(sym, ticket);
   if(has)
     {
      int dir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      if(score * dir <= 0.0)
         g_sym[i].pendingExit = true;
      else if(atr > 0.0)
         TrailStop(sym, ticket, dir, r[0].close - dir * InpStopAtr * atr, digits);
     }
   if((!has || g_sym[i].pendingExit) && atr > 0.0 && MathAbs(score) >= InpEntryThreshold - 1e-9)
     {
      g_sym[i].pendingDir = (score > 0.0) ? 1 : -1;
      g_sym[i].pendingAtr = atr;
      g_sym[i].pendingBar = t;
     }
  }

// Stopu yalnızca pozisyon lehine taşır
void TrailStop(const string sym, const ulong ticket, const int dir, double newSl, const int digits)
  {
   double curSl = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   newSl = NormalizeDouble(newSl, digits);
   if(dir > 0 && curSl > 0.0 && newSl <= curSl) return;
   if(dir < 0 && curSl > 0.0 && newSl >= curSl) return;

   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double gap   = MathMax(SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL),
                          SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL)) * point;
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   if(dir > 0 && newSl >= bid - gap) return;      // fiyata fazla yakın: bu çubukta taşıma
   if(dir < 0 && newSl <= ask + gap) return;

   if(!g_trade.PositionModify(ticket, newSl, tp))
      PrintFormat("%s stop taşınamadı: %d %s", sym, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
  }

//+------------------------------------------------------------------+
void DoExit(const int i)
  {
   if(!g_sym[i].pendingExit) return;
   string sym = g_sym[i].name;
   ulong ticket = 0;
   if(!SelectMyPosition(sym, ticket))
     {
      g_sym[i].pendingExit = false;           // stop zaten kapatmış
      return;
     }
   if(TimeCurrent() < g_sym[i].lastBar + InpEntryDelayMin * 60) return;   // rollover spread'ini bekle
   if(!TradingPossible(sym)) return;
   g_trade.SetTypeFillingBySymbol(sym);
   if(g_trade.PositionClose(ticket) && g_trade.ResultRetcode() == TRADE_RETCODE_DONE)
     {
      g_sym[i].pendingExit = false;
      PrintFormat("%s sinyal çıkışı yapıldı", sym);
     }
   else
      PrintFormat("%s kapatılamadı: %d %s", sym, g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
  }

bool TradingPossible(const string sym)
  {
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) return false;
   if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT) || !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)) return false;
   long mode = SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
   return (mode == SYMBOL_TRADE_MODE_FULL);
  }

//+------------------------------------------------------------------+
void DoEntry(const int i)
  {
   if(g_sym[i].pendingDir == 0 || g_sym[i].pendingExit) return;
   string sym = g_sym[i].name;
   int    dir = g_sym[i].pendingDir;
   double atr = g_sym[i].pendingAtr;

   ulong ticket = 0;
   if(SelectMyPosition(sym, ticket)) { g_sym[i].pendingDir = 0; return; }

   datetime now = TimeCurrent();
   if(now < g_sym[i].pendingBar + InpEntryDelayMin * 60) return;
   if(now > g_sym[i].pendingBar + InpEntryWindowMin * 60)
     {
      PrintFormat("%s: giriş penceresi doldu, sinyal düşürüldü", sym);
      g_sym[i].pendingDir = 0;
      return;
     }
   if(!TradingPossible(sym)) return;
   if(!g_guard.Allows()) return;              // günlük kayıp sınırı: bugün yeni giriş yok

   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   if(bid <= 0.0 || ask <= 0.0) return;
   if(ask - bid > InpMaxSpreadAtr * atr) return;   // spread geniş: bekle

   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
   double entry  = (dir > 0) ? ask : bid;
   double dist   = InpStopAtr * atr;
   double sl     = NormalizeDouble(entry - dir * dist, digits);
   if(dist < SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point || sl <= 0.0)
     {
      PrintFormat("%s: stop mesafesi broker alt sınırının altında, sinyal düşürüldü", sym);
      g_sym[i].pendingDir = 0;
      return;
     }

   // --- Boyutlandırma ve portföy sınırları ---
   double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
   double perLot  = LossPerLot(sym, dir, entry, sl);
   if(perLot <= 0.0) return;
   double total = 0.0, usdNet = 0.0;
   OpenRisk(total, usdNet);
   double budget = equity * InpRiskPerTradePct / 100.0;
   double room   = equity * InpMaxPortfolioPct / 100.0 - total;
   if(room < budget) budget = MathMax(0.0, room);
   double lots = RoundLotsDown(sym, budget / perLot);
   if(lots <= 0.0)
     {
      PrintFormat("%s: risk bütçesi minimum lota yetmiyor, sinyal düşürüldü", sym);
      g_sym[i].pendingDir = 0;
      return;
     }
   double risk   = lots * perLot;
   int    usdDir = dir * g_sym[i].usdBeta;
   if(usdDir != 0)
     {
      double after = usdNet + usdDir * risk;
      if(MathAbs(after) > equity * InpMaxUsdNetPct / 100.0 && MathAbs(after) > MathAbs(usdNet))
        {
         PrintFormat("%s: net USD risk sınırı dolu, sinyal düşürüldü", sym);
         g_sym[i].pendingDir = 0;
         return;
        }
     }
   double margin = 0.0;
   ENUM_ORDER_TYPE type = (dir > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(!OrderCalcMargin(type, sym, lots, entry, margin) || margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     {
      PrintFormat("%s: serbest teminat yetersiz, sinyal düşürüldü", sym);
      g_sym[i].pendingDir = 0;
      return;
     }

   // --- Emir ---
   g_trade.SetTypeFillingBySymbol(sym);
   bool sent = (dir > 0) ? g_trade.Buy(lots, sym, 0.0, sl, 0.0, "ardh-trend")
                         : g_trade.Sell(lots, sym, 0.0, sl, 0.0, "ardh-trend");
   uint rc = g_trade.ResultRetcode();
   if(sent && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED))
     {
      PrintFormat("%s %s %.2f lot, SL=%s, risk=%.2f (%s)", sym, dir > 0 ? "AL" : "SAT", lots,
                  DoubleToString(sl, digits), risk, AccountInfoString(ACCOUNT_CURRENCY));
      g_sym[i].pendingDir = 0;
      return;
     }
   g_sym[i].sendFails++;
   PrintFormat("%s emir reddedildi (%d/%d): %d %s", sym, g_sym[i].sendFails, InpMaxSendRetries,
               rc, g_trade.ResultRetcodeDescription());
   if(g_sym[i].sendFails >= InpMaxSendRetries)
      g_sym[i].pendingDir = 0;
  }
//+------------------------------------------------------------------+
