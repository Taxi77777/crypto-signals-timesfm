//+------------------------------------------------------------------+
//|                                     Belkhayate_Retest_EA.mq4    |
//|               Copyright 2026, Mostafa Belkhayate & IA System     |
//|      Robot Expert Advisor 100% Autonome MT4 Retest & Mèches     |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Belkhayate AI"
#property link      "https://github.com/Taxi77777/crypto-signals-timesfm"
#property version   "2.00"
#property strict

//--- Inputs Paramètres Stratégie Belkhayate
input string   InpGroupStrategy  = "=== PARAMÈTRES BELKHAYATE RETEST ===";
input int      InpBaryPeriod     = 30;     // Période du Barycentre Belkhayate
input double   InpBaryDev        = 2.9124; // Déviation des Bandes Belkhayate (1.618 * 1.8)
input double   InpMinWickPct     = 15.0;   // Mèche de Rejet Minimale (%)
input double   InpMaxRetestDist  = 1.5;    // Écartement max du Retest par rapport au Sommet/Creux (%)
input int      InpRetestLookback = 24;     // Nombre de bougies pour détecter Sommet/Creux récent

//--- Inputs Gestion des Risques & Capital
input string   InpGroupRisk      = "=== GESTION CAPITAL & RISQUE ===";
input double   InpRiskPercent    = 1.0;    // Risque % du Capital par trade
input double   InpFixedLot       = 0.0;    // Lot fixe (si 0.0 -> utilise InpRiskPercent)
input double   InpATR_TP_Mult    = 1.5;    // Multiplicateur ATR pour Take Profit
input double   InpATR_SL_Mult    = 1.2;    // Multiplicateur ATR pour Stop Loss
input int      InpMaxOpenTrades  = 3;      // Nombre max de trades simultanés
input int      InpMagicNumber    = 88888;  // Magic Number unique du Robot

//--- Variables Globales
datetime g_lastBarTime = 0;

//+------------------------------------------------------------------+
//| Expert Initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("👑 Belkhayate Retest EA v2.0 initialisé avec succès !");
   Print("🏛️ Stratégie : 100% Retest de Plus Haut/Bas + Mèche >= ", InpMinWickPct, "%");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert Deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   Comment("");
}

//+------------------------------------------------------------------+
//| Expert Tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   // Vérifier si une nouvelle bougie s'est ouverte sur le timeframe courant
   datetime currentBarTime = iTime(_Symbol, _Period, 0);
   if(currentBarTime == g_lastBarTime) return; // Uniquement 1 analyse par bougie clôturée
   g_lastBarTime = currentBarTime;

   // Compter le nombre de trades ouverts pour ce Magic Number
   int openTrades = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol() == _Symbol && OrderMagicNumber() == InpMagicNumber) openTrades++;
      }
   }
   if(openTrades >= InpMaxOpenTrades) return;

   // ── CALCUL DES INDICATEURS BELKHAYATE SUR LA BOUGIE PRÉCÉDENTE (Clôturée = shift 1) ──
   double curPrice = iClose(_Symbol, _Period, 1);
   double openP    = iOpen(_Symbol, _Period, 1);
   double highP    = iHigh(_Symbol, _Period, 1);
   double lowP     = iLow(_Symbol, _Period, 1);
   double cRange   = MathMax(highP - lowP, 0.00001);

   // Mèches (%)
   double bodyMin  = MathMin(openP, curPrice);
   double bodyMax  = MathMax(openP, curPrice);
   double lowerWickPct = ((bodyMin - lowP) / cRange) * 100.0;
   double upperWickPct = ((highP - bodyMax) / cRange) * 100.0;

   // Barycentre Belkhayate (Moyenne 30) & Ecart-type
   double sum = 0;
   for(int k = 1; k <= InpBaryPeriod; k++) sum += iClose(_Symbol, _Period, k);
   double barycenter = sum / InpBaryPeriod;

   double sqSum = 0;
   for(int k = 1; k <= InpBaryPeriod; k++)
   {
      double diff = iClose(_Symbol, _Period, k) - barycenter;
      sqSum += diff * diff;
   }
   double stdDev = MathSqrt(sqSum / InpBaryPeriod);
   if(stdDev == 0) stdDev = 0.00001;

   double timing = (curPrice - barycenter) / stdDev;

   // Sommet et Creux Récents (Retest Lookback)
   int highestIdx = iHighest(_Symbol, _Period, MODE_HIGH, InpRetestLookback, 1);
   int lowestIdx  = iLowest(_Symbol, _Period, MODE_LOW, InpRetestLookback, 1);
   double recentHigh = iHigh(_Symbol, _Period, highestIdx);
   double recentLow  = iLow(_Symbol, _Period, lowestIdx);

   double distHighPct = (MathAbs(curPrice - recentHigh) / curPrice) * 100.0;
   double distLowPct  = (MathAbs(curPrice - recentLow) / curPrice) * 100.0;

   bool isRetestHigh = (distHighPct <= InpMaxRetestDist) && (timing >= 1.0);
   bool isRetestLow  = (distLowPct <= InpMaxRetestDist) && (timing <= -1.0);

   // RÈGLE 100% RETEST EXCLUSIF + MÈCHE DE REJET
   bool isBuySignal  = isRetestLow  && (lowerWickPct >= InpMinWickPct);
   bool isSellSignal = isRetestHigh && (upperWickPct >= InpMinWickPct);

   if(!isBuySignal && !isSellSignal) return;

   // ATR pour SL / TP
   double atr = iATR(_Symbol, _Period, 14, 1);
   if(atr == 0) atr = curPrice * 0.002;

   double askPrice = MarketInfo(_Symbol, MODE_ASK);
   double bidPrice = MarketInfo(_Symbol, MODE_BID);

   // Calcul de la taille de Lot
   double lotSize = InpFixedLot;
   if(lotSize <= 0)
   {
      double balance   = AccountBalance();
      double riskMoney = balance * (InpRiskPercent / 100.0);
      double tickValue = MarketInfo(_Symbol, MODE_TICKVALUE);
      double slPips    = (1.2 * atr) / MarketInfo(_Symbol, MODE_POINT);
      if(slPips > 0 && tickValue > 0)
      {
         lotSize = NormalizeDouble(riskMoney / (slPips * tickValue), 2);
      }
      else lotSize = 0.01;
   }
   double minLot = MarketInfo(_Symbol, MODE_MINLOT);
   double maxLot = MarketInfo(_Symbol, MODE_MAXLOT);
   lotSize = MathMax(minLot, MathMin(maxLot, lotSize));

   // EXÉCUTION DU TRADE
   if(isBuySignal)
   {
      double tp = NormalizeDouble(MathMax(barycenter, askPrice + (InpATR_TP_Mult * atr)), _Digits);
      double sl = NormalizeDouble(askPrice - (InpATR_SL_Mult * atr), _Digits);

      int ticket = OrderSend(_Symbol, OP_BUY, lotSize, askPrice, 3, sl, tp, "Belkhayate Retest BUY", InpMagicNumber, 0, clrGreen);
      if(ticket > 0) Print("🟢 ORDER BUY EXECUTE ! Ticket: ", ticket, " | Retest Low (", NormalizeDouble(recentLow, _Digits), ") | Mèche: ", NormalizeDouble(lowerWickPct, 1), "%");
   }
   else if(isSellSignal)
   {
      double tp = NormalizeDouble(MathMin(barycenter, bidPrice - (InpATR_TP_Mult * atr)), _Digits);
      double sl = NormalizeDouble(bidPrice + (InpATR_SL_Mult * atr), _Digits);

      int ticket = OrderSend(_Symbol, OP_SELL, lotSize, bidPrice, 3, sl, tp, "Belkhayate Retest SELL", InpMagicNumber, 0, clrRed);
      if(ticket > 0) Print("🔴 ORDER SELL EXECUTE ! Ticket: ", ticket, " | Retest High (", NormalizeDouble(recentHigh, _Digits), ") | Mèche: ", NormalizeDouble(upperWickPct, 1), "%");
   }
}
