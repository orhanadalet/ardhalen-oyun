//+------------------------------------------------------------------+
//| Risk.mqh - lot hesabı, açık risk, USD yönü, günlük kayıp sınırı   |
//+------------------------------------------------------------------+

// 1 lotun, entry'den sl'ye giderse hesap para biriminde kaybı (pozitif sayı). Hata: 0
double LossPerLot(const string sym, const int dir, const double entry, const double sl)
  {
   double profit = 0.0;
   ENUM_ORDER_TYPE type = (dir > 0) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   if(OrderCalcProfit(type, sym, 1.0, entry, sl, profit) && profit < 0.0)
      return -profit;
   // Yedek yol: tick değeri
   double tickSize  = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE_LOSS);
   if(tickSize <= 0.0 || tickValue <= 0.0) return 0.0;
   return MathAbs(entry - sl) / tickSize * tickValue;
  }

// Adıma AŞAĞI yuvarlar; minimumun altındaysa 0 (risk büyütülmez)
double RoundLotsDown(const string sym, const double lots)
  {
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vMin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vMax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(step <= 0.0 || lots <= 0.0 || !MathIsValidNumber(lots)) return 0.0;
   double v = MathFloor(lots / step + 1e-9) * step;
   if(v < vMin - 1e-12) return 0.0;
   return NormalizeDouble(MathMin(v, vMax), 8);
  }

// Seçili pozisyonun stopa giderse kaybı (kâr kilitlendiyse 0). SL yoksa -1 döner.
double SelectedPositionRisk()
  {
   double sl = PositionGetDouble(POSITION_SL);
   if(sl <= 0.0) return -1.0;
   string sym   = PositionGetString(POSITION_SYMBOL);
   double open  = PositionGetDouble(POSITION_PRICE_OPEN);
   double vol   = PositionGetDouble(POSITION_VOLUME);
   bool   isBuy = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
   double profit = 0.0;
   if(!OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, vol, open, sl, profit))
      return -1.0;
   return (profit < 0.0) ? -profit : 0.0;
  }

// Sembolde LONG olmak USD'de: -1 short, +1 long, 0 nötr
int UsdBeta(const string sym, const string neutralList)
  {
   if(StringFind("," + neutralList + ",", "," + sym + ",") >= 0) return 0;
   if(SymbolInfoString(sym, SYMBOL_CURRENCY_BASE)   == "USD") return  1;
   if(SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT) == "USD") return -1;
   return 0;
  }

//+------------------------------------------------------------------+
//| Günlük kayıp sınırı: gün başı özkaynağı terminal global           |
//| değişkeninde tutulur (EA yeniden başlasa da kaybolmaz).           |
//+------------------------------------------------------------------+
class CDailyGuard
  {
private:
   string            m_keyEq, m_keyDay;
   double            m_limitPct;
public:
   void              Init(const ulong magic, const double limitPct)
     {
      string p = "ARDH_" + (string)magic + "_" + (string)AccountInfoInteger(ACCOUNT_LOGIN);
      m_keyEq = p + "_dayEq";
      m_keyDay = p + "_day";
      m_limitPct = limitPct;
     }
   // Yeni işlem açılabilir mi?
   bool              Allows()
     {
      MqlDateTime dt;
      TimeToStruct(TimeCurrent(), dt);
      double today  = dt.year * 10000.0 + dt.mon * 100.0 + dt.day;
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(!GlobalVariableCheck(m_keyDay) || GlobalVariableGet(m_keyDay) != today)
        {
         GlobalVariableSet(m_keyDay, today);
         GlobalVariableSet(m_keyEq, equity);
        }
      double startEq = GlobalVariableGet(m_keyEq);
      if(startEq <= 0.0 || m_limitPct <= 0.0) return true;
      return (startEq - equity) / startEq * 100.0 < m_limitPct;
     }
  };
