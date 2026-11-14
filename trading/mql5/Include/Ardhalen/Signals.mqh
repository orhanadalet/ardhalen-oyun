//+------------------------------------------------------------------+
//| Signals.mqh - Donchian kırılım topluluğu ve ATR                   |
//| research/fxlab/strategy.py ile aynı tanımlar.                     |
//| Diziler seri sıralıdır: r[0] = son KAPANMIŞ çubuk.                |
//+------------------------------------------------------------------+

// +1: en son kırılım yukarı, -1: aşağı, 0: maxScan içinde kırılım yok
int DonchianState(const MqlRates &r[], const int total, const int n, const int maxScan)
  {
   for(int j = 0; j < maxScan && j + n < total; j++)
     {
      double hi = r[j + 1].high;
      double lo = r[j + 1].low;
      for(int k = j + 2; k <= j + n; k++)
        {
         if(r[k].high > hi) hi = r[k].high;
         if(r[k].low  < lo) lo = r[k].low;
        }
      if(r[j].close > hi) return  1;
      if(r[j].close < lo) return -1;
     }
   return 0;
  }

// [-1, +1] aralığında skor
double EnsembleScore(const MqlRates &r[], const int total, const int &lookbacks[], const int maxScan)
  {
   int cnt = ArraySize(lookbacks);
   if(cnt == 0) return 0.0;
   double sum = 0.0;
   for(int i = 0; i < cnt; i++)
      sum += DonchianState(r, total, lookbacks[i], maxScan);
   return sum / cnt;
  }

// True Range'in basit ortalaması (iATR ile aynı tanım); veri yetmezse 0
double AtrSimple(const MqlRates &r[], const int total, const int n)
  {
   if(n <= 0 || total < n + 1) return 0.0;
   double sum = 0.0;
   for(int k = 0; k < n; k++)
     {
      double pc = r[k + 1].close;
      double tr = MathMax(r[k].high - r[k].low, MathMax(MathAbs(r[k].high - pc), MathAbs(r[k].low - pc)));
      sum += tr;
     }
   return sum / n;
  }
