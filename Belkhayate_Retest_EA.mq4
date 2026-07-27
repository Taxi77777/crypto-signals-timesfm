//+------------------------------------------------------------------+
//|                                     Belkhayate_Retest_EA.mq4    |
//|               Copyright 2026, Mostafa Belkhayate & IA System     |
//|    Robot Expert MT4 v10.00 : Rescan Instantané Fermeture Trade  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Belkhayate AI"
#property link      "https://github.com/Taxi77777/crypto-signals-timesfm"
#property version   "10.00"
#property strict

//--- ENUM DES TIMEFRAMES SELECTIONNABLES
enum ENUM_CUSTOM_TIMEFRAME
{
   TF_CURRENT = 0,    // Timeframe du Graphique Actuel
   TF_M5      = 5,    // 5 Minutes
   TF_M15     = 15,   // 15 Minutes
   TF_M30     = 30,   // 30 Minutes
   TF_H1      = 60    // 1 Heure
};

//--- Inputs Paramètres Stratégie Belkhayate Cassure Impulsionnelle
input string                InpGroupStrategy   = "=== STRATÉGIE BELKHAYATE CASSURE (RESCAN INSTANTANÉ) ===";
input ENUM_CUSTOM_TIMEFRAME InpTimeframe       = TF_M15;   // Unité de temps choisie pour le Robot
input int                   InpBaryPeriod      = 30;      // Période du Barycentre Belkhayate
input double                InpMaxRetestDist   = 1.5;     // Écartement max de Cassure (%)
input int                   InpRetestLookback  = 24;      // Bougies pour détecter le Sommet/Creux
input double                InpMinImpulseBody  = 35.0;    // Corps d'impulsion minimal (% du range)

//--- Inputs Configuration Multi-Paires
input string                InpGroupMulti      = "=== MULTI-PAIRES BACKGROUND SCANNER ===";
input bool                  InpScanAllWatchlist= true;    // Scanner et trader les 28 paires Forex automatiquement

//--- Inputs Configuration Risque & Exécution
input string                InpGroupRisk       = "=== GESTION CAPITAL, LOT & TRADES ===";
input double                InpFixedLot        = 0.01;    // Taille du Lot (ex: 0.01, 0.10, 1.00)
input double                InpRiskPercent     = 0.0;     // Risque % par trade (0.0 = utilise le Lot Fixe)
input int                   InpMaxOpenTrades   = 3;       // Nombre Max de trades autorisés
input double                InpATR_TP_Mult     = 1.5;     // Multiplicateur ATR pour Take Profit
input double                InpATR_SL_Mult     = 1.2;     // Multiplicateur ATR pour Stop Loss
input int                   InpMagicNumber     = 88888;   // Magic Number du Robot

//--- Inputs Trailing Stop & Break-Even
input string                InpGroupTrailing       = "=== TRAILING STOP & BREAK-EVEN ===";
input bool                  InpUseTrailingStop     = true;    // Activer le Trailing Stop Automatique
input int                   InpTrailingStopPips    = 15;      // Distance de Trailing Stop (Pips)
input int                   InpTrailingStepPips    = 5;       // Pas de Trailing (Pips)
input bool                  InpUseBreakEven        = true;    // Activer le Break-Even Automatique
input int                   InpBreakEvenTriggerPips= 10;      // Gains en Pips pour passer en Break-Even

//--- Inputs Visuels & Graphique
input string                InpGroupVisual     = "=== VISUEL CHART & DASHBOARD ===";
input bool                  InpShowDashboard   = true;    // Afficher le Tableau Dashboard Original
input bool                  InpDrawArrows      = true;    // Dessiner les flèches NON-REPAINTING
input bool                  InpDrawRetestLines = true;    // Dessiner les 2 lignes Horizontales (Rouge Haut / Vert Bas)

//--- Variables Globales
datetime g_lastBarTime    = 0;
int      g_lastOpenTrades = 0;
ENUM_TIMEFRAMES g_tf;

// Liste complète des 28 paires Forex principales scannées automatiquement
string g_watchlist[28] = {
   "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
   "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPCAD", "EURCAD", "EURCHF",
   "GBPAUD", "GBPCHF", "GBPNZD", "AUDCAD", "AUDCHF", "CADCHF", "NZDCHF",
   "CADJPY", "CHFJPY", "NZDCAD", "NZDJPY", "EURNZD", "AUDNZD", "USDCAD"
};

//+------------------------------------------------------------------+
//| Expert Initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTimeframe == TF_CURRENT) g_tf = (ENUM_TIMEFRAMES)_Period;
   else g_tf = (ENUM_TIMEFRAMES)InpTimeframe;

   g_lastBarTime    = 0;
   g_lastOpenTrades = 0;

   // Pré-chargement des paires dans le Market Watch MT4
   for(int i = 0; i < 28; i++)
   {
      SymbolSelect(g_watchlist[i], true);
   }

   Print("👑 Belkhayate Breakout EA v10.00 (Rescan Instantané Fermeture Trade) initialisé !");

   UpdateChartVisuals();

   if(InpShowDashboard) DrawDashboardHUD();
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
//| Sur changement de graphique ou de symbole                        |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   UpdateChartVisuals();
   if(InpShowDashboard) DrawDashboardHUD();
}

//+------------------------------------------------------------------+
//| Expert Tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   ApplyTrailingStopAndBreakEven();

   UpdateChartVisuals();
   if(InpShowDashboard) DrawDashboardHUD();

   // Compter le nombre de trades ouverts actuellement
   int currentOpenTrades = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderMagicNumber() == InpMagicNumber) currentOpenTrades++;
      }
   }

   // DETECTER SI UN TRADE VIENT D'ETRE FERME MANUELLEMENT OU PAR TP/SL
   bool tradeJustClosed = (currentOpenTrades < g_lastOpenTrades);
   g_lastOpenTrades = currentOpenTrades;

   datetime currentBarTime = iTime(_Symbol, g_tf, 0);

   // SI AUCUN TRADE N'A ÉTÉ FERMÉ ET QUE C'EST LA MÊME BOUGIE, PASSER LE TICK
   if(currentBarTime == g_lastBarTime && !tradeJustClosed) return;
   g_lastBarTime = currentBarTime;

   if(tradeJustClosed)
   {
      Print("⚡ Trade fermé détecté ! Rescan instantané de l'ensemble des 28 paires Forex...");
   }

   // Scanner toutes les 28 paires Forex immédiatement
   if(InpScanAllWatchlist)
   {
      for(int i = 0; i < 28; i++)
      {
         ExecuteTradeForSymbol(g_watchlist[i]);
      }
   }
   else
   {
      ExecuteTradeForSymbol(_Symbol);
   }
}

//+------------------------------------------------------------------+
//| Update visuel sur le symbole actif                               |
//+------------------------------------------------------------------+
void UpdateChartVisuals()
{
   // ── 1. TRACÉ DES 2 LIGNES BLEUES HORIZONTALES (Sommet & Creux) ──
   int hIdxCurrent = iHighest(_Symbol, g_tf, MODE_HIGH, InpRetestLookback, 1);
   int lIdxCurrent = iLowest(_Symbol, g_tf, MODE_LOW, InpRetestLookback, 1);
   double recentHighCurrent = iHigh(_Symbol, g_tf, hIdxCurrent);
   double recentLowCurrent  = iLow(_Symbol, g_tf, lIdxCurrent);

   if(InpDrawRetestLines)
   {
      // Ligne Bleue Supérieure (Sommet)
      string lineHighName = "BK_Line_High";
      if(ObjectFind(0, lineHighName) < 0) ObjectCreate(0, lineHighName, OBJ_HLINE, 0, 0, recentHighCurrent);
      else ObjectMove(0, lineHighName, 0, 0, recentHighCurrent);
      ObjectSetInteger(0, lineHighName, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, lineHighName, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, lineHighName, OBJPROP_WIDTH, 2);

      // Ligne Bleue Inférieure (Creux)
      string lineLowName = "BK_Line_Low";
      if(ObjectFind(0, lineLowName) < 0) ObjectCreate(0, lineLowName, OBJ_HLINE, 0, 0, recentLowCurrent);
      else ObjectMove(0, lineLowName, 0, 0, recentLowCurrent);
      ObjectSetInteger(0, lineLowName, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, lineLowName, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, lineLowName, OBJPROP_WIDTH, 2);

      // Ligne Transverse Oblique Bleue (Trendline Transverse)
      string lineTransName = "BK_Line_Transverse";
      datetime t1 = iTime(_Symbol, g_tf, MathMin(InpRetestLookback*2, iBars(_Symbol, g_tf)-1));
      double   p1 = iLow(_Symbol, g_tf, iLowest(_Symbol, g_tf, MODE_LOW, InpRetestLookback*2, InpRetestLookback));
      datetime t2 = iTime(_Symbol, g_tf, lIdxCurrent);
      double   p2 = recentLowCurrent;

      if(ObjectFind(0, lineTransName) < 0) ObjectCreate(0, lineTransName, OBJ_TREND, 0, t1, p1, t2, p2);
      else { ObjectMove(0, lineTransName, 0, t1, p1); ObjectMove(0, lineTransName, 1, t2, p2); }
      ObjectSetInteger(0, lineTransName, OBJPROP_COLOR, clrDodgerBlue);
      ObjectSetInteger(0, lineTransName, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, lineTransName, OBJPROP_WIDTH, 3);
      ObjectSetInteger(0, lineTransName, OBJPROP_RAY_RIGHT, true);
   }

   // ── 2. DESSIN ET DÉTECTION DES DEUX SENS (BUY ET SELL SUR DÉPASSEMENT / CASSURE / REJET) ──
   if(!InpDrawArrows) return;

   datetime bTime = iTime(_Symbol, g_tf, 1);
   string timeID  = IntegerToString((long)bTime);
   string arrowName = "BK_Arrow_Sig_" + timeID;
   string textName  = "BK_Text_Sig_"  + timeID;

   if(ObjectFind(0, arrowName) >= 0) return;

   double curPrice = iClose(_Symbol, g_tf, 1);
   double openP    = iOpen(_Symbol, g_tf, 1);
   double highP    = iHigh(_Symbol, g_tf, 1);
   double lowP     = iLow(_Symbol, g_tf, 1);
   double cRange   = MathMax(highP - lowP, 0.00001);

   double bodyRatio = (MathAbs(curPrice - openP) / cRange) * 100.0;
   bool isBullishImpulse = (curPrice > openP) && (bodyRatio >= InpMinImpulseBody);
   bool isBearishImpulse = (curPrice < openP) && (bodyRatio >= InpMinImpulseBody);

   // Volume SMA 9 Filter (Moyenne 9 périodes du volume)
   double volSum = 0;
   for(int v = 1; v <= 9; v++) volSum += (double)iVolume(_Symbol, g_tf, v);
   double volSMA9 = volSum / 9.0;
   bool isVolOK = ((double)iVolume(_Symbol, g_tf, 1) >= volSMA9 * 0.85);

   double distHighPct = (MathAbs(curPrice - recentHighCurrent) / curPrice) * 100.0;
   double distLowPct  = (MathAbs(curPrice - recentLowCurrent) / curPrice) * 100.0;

   double uWickPct = ((highP - MathMax(openP, curPrice)) / cRange) * 100.0;
   double lWickPct = ((MathMin(openP, curPrice) - lowP) / cRange) * 100.0;

   bool touchesHigh = (highP >= recentHighCurrent) || (distHighPct <= InpMaxRetestDist);
   bool touchesLow  = (lowP  <= recentLowCurrent)  || (distLowPct  <= InpMaxRetestDist);

   // STRATÉGIE MOSTAFA BELKHAYATE (CASSURE IMPULSION + REJET MÈCHE DE LIGNE) :
   // 🟢 ACHAT (BUY) : Retest/Cassure Sommet OU Rejet Mèche Basse (>=20%) / Impulsion Verte sur Ligne Creux
   // 🔴 VENTE (SELL) : Retest/Cassure Creux OU Rejet Mèche Haute (>=20%) / Impulsion Rouge sur Ligne Sommet
   bool isBuySignal  = (touchesLow && (lWickPct >= 20.0 || isBullishImpulse)) || (touchesHigh && isBullishImpulse);
   bool isSellSignal = (touchesHigh && (uWickPct >= 20.0 || isBearishImpulse)) || (touchesLow && isBearishImpulse);

   double arrowOffset = 3.0 * _Point; 
   double textOffset  = 12.0 * _Point;

   if(isBuySignal)
   {
      ObjectCreate(0, arrowName, OBJ_ARROW, 0, bTime, lowP - arrowOffset);
      ObjectSetInteger(0, arrowName, OBJPROP_ARROWCODE, 233); // Flèche Haut Wingdings
      ObjectSetInteger(0, arrowName, OBJPROP_COLOR, clrLime);
      ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 5);

      ObjectCreate(0, textName, OBJ_TEXT, 0, bTime, lowP - textOffset);
      ObjectSetString(0, textName, OBJPROP_TEXT, "[IMPULSION / REJET - ACHAT]");
      ObjectSetInteger(0, textName, OBJPROP_COLOR, clrLime);
      ObjectSetInteger(0, textName, OBJPROP_FONTSIZE, 9);
      ObjectSetString(0, textName, OBJPROP_FONT, "Arial Bold");
   }
   else if(isSellSignal)
   {
      ObjectCreate(0, arrowName, OBJ_ARROW, 0, bTime, highP + arrowOffset);
      ObjectSetInteger(0, arrowName, OBJPROP_ARROWCODE, 234); // Flèche Bas Wingdings
      ObjectSetInteger(0, arrowName, OBJPROP_COLOR, clrRed);
      ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 5);

      ObjectCreate(0, textName, OBJ_TEXT, 0, bTime, highP + textOffset);
      ObjectSetString(0, textName, OBJPROP_TEXT, "[IMPULSION / REJET - VENTE]");
      ObjectSetInteger(0, textName, OBJPROP_COLOR, clrRed);
      ObjectSetInteger(0, textName, OBJPROP_FONTSIZE, 9);
      ObjectSetString(0, textName, OBJPROP_FONT, "Arial Bold");
   }
}

//+------------------------------------------------------------------+
//| Exécution de Trade Universelle pour n'importe quel Symbole MT4   |
//+------------------------------------------------------------------+
void ExecuteTradeForSymbol(string sym)
{
   if(MarketInfo(sym, MODE_BID) == 0) return;

   int openTrades = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderMagicNumber() == InpMagicNumber) openTrades++;
      }
   }
   if(openTrades >= InpMaxOpenTrades) return;

   double curPrice = iClose(sym, g_tf, 1);
   double openP    = iOpen(sym, g_tf, 1);
   double highP    = iHigh(sym, g_tf, 1);
   double lowP     = iLow(sym, g_tf, 1);
   double cRange   = MathMax(highP - lowP, 0.00001);

   double bodyRatio = (MathAbs(curPrice - openP) / cRange) * 100.0;
   bool isBullishImpulse = (curPrice > openP) && (bodyRatio >= InpMinImpulseBody);
   bool isBearishImpulse = (curPrice < openP) && (bodyRatio >= InpMinImpulseBody);

   double sum = 0;
   for(int k = 1; k <= InpBaryPeriod; k++) sum += iClose(sym, g_tf, k);
   double barycenter = sum / InpBaryPeriod;

   double sqSum = 0;
   for(int k = 1; k <= InpBaryPeriod; k++)
   {
      double diff = iClose(sym, g_tf, k) - barycenter;
      sqSum += diff * diff;
   }
   double stdDev = MathSqrt(sqSum / InpBaryPeriod);
   if(stdDev == 0) stdDev = 0.00001;
   double timing = (curPrice - barycenter) / stdDev;

   int highestIdx = iHighest(sym, g_tf, MODE_HIGH, InpRetestLookback, 1);
   int lowestIdx  = iLowest(sym, g_tf, MODE_LOW, InpRetestLookback, 1);
   double recentHigh = iHigh(sym, g_tf, highestIdx);
   // Volume SMA 9 Filter (Moyenne 9 périodes du volume)
   double volSum = 0;
   for(int v = 1; v <= 9; v++) volSum += (double)iVolume(sym, g_tf, v);
   double volSMA9 = volSum / 9.0;
   bool isVolOK = ((double)iVolume(sym, g_tf, 1) >= volSMA9 * 0.85);

   double distHighPct = (MathAbs(curPrice - recentHigh) / curPrice) * 100.0;
   double distLowPct  = (MathAbs(curPrice - recentLow) / curPrice) * 100.0;

   double uWickPct = ((highP - MathMax(openP, curPrice)) / cRange) * 100.0;
   double lWickPct = ((MathMin(openP, curPrice) - lowP) / cRange) * 100.0;

   bool touchesHigh = (highP >= recentHigh) || (distHighPct <= InpMaxRetestDist);
   bool touchesLow  = (lowP  <= recentLow)  || (distLowPct  <= InpMaxRetestDist);

   // STRATÉGIE MOSTAFA BELKHAYATE (CASSURE IMPULSION + REJET MÈCHE DE LIGNE) :
   // 🟢 ACHAT (BUY) : Retest/Cassure Sommet OU Rejet Mèche Basse (>=20%) / Impulsion Verte sur Ligne Creux
   // 🔴 VENTE (SELL) : Retest/Cassure Creux OU Rejet Mèche Haute (>=20%) / Impulsion Rouge sur Ligne Sommet
   bool isBuySignal  = (touchesLow && (lWickPct >= 20.0 || isBullishImpulse)) || (touchesHigh && isBullishImpulse);
   bool isSellSignal = (touchesHigh && (uWickPct >= 20.0 || isBearishImpulse)) || (touchesLow && isBearishImpulse);

   if(!isBuySignal && !isSellSignal) return;

   double atr = iATR(sym, g_tf, 14, 1);
   if(atr == 0) atr = curPrice * 0.002;

   double askPrice = MarketInfo(sym, MODE_ASK);
   double bidPrice = MarketInfo(sym, MODE_BID);
   double pointVal = MarketInfo(sym, MODE_POINT);
   int digitsVal   = (int)MarketInfo(sym, MODE_DIGITS);

   double lotSize = InpFixedLot;
   if(InpRiskPercent > 0)
   {
      double balance   = AccountBalance();
      double riskMoney = balance * (InpRiskPercent / 100.0);
      double tickValue = MarketInfo(sym, MODE_TICKVALUE);
      double slPips    = (1.2 * atr) / pointVal;
      if(slPips > 0 && tickValue > 0) lotSize = NormalizeDouble(riskMoney / (slPips * tickValue), 2);
   }
   lotSize = MathMax(MarketInfo(sym, MODE_MINLOT), MathMin(MarketInfo(sym, MODE_MAXLOT), lotSize));

   if(isBuySignal)
   {
      double tp = NormalizeDouble(askPrice + (InpATR_TP_Mult * atr), digitsVal);
      double sl = NormalizeDouble(askPrice - (InpATR_SL_Mult * atr), digitsVal);
      OrderSend(sym, OP_BUY, lotSize, askPrice, 3, sl, tp, "Belkhayate Breakout BUY", InpMagicNumber, 0, clrLime);
   }
   else if(isSellSignal)
   {
      double tp = NormalizeDouble(bidPrice - (InpATR_TP_Mult * atr), digitsVal);
      double sl = NormalizeDouble(bidPrice + (InpATR_SL_Mult * atr), digitsVal);
      OrderSend(sym, OP_SELL, lotSize, bidPrice, 3, sl, tp, "Belkhayate Breakout SELL", InpMagicNumber, 0, clrRed);
   }
}

//+------------------------------------------------------------------+
//| Trailing Stop & Break-Even                                       |
//+------------------------------------------------------------------+
void ApplyTrailingStopAndBreakEven()
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderMagicNumber() != InpMagicNumber) continue;

      string sym = OrderSymbol();
      double point = MarketInfo(sym, MODE_POINT);
      double ask   = MarketInfo(sym, MODE_ASK);
      double bid   = MarketInfo(sym, MODE_BID);
      int digits   = (int)MarketInfo(sym, MODE_DIGITS);

      double trailingDist = InpTrailingStopPips * point * 10;
      double trailingStep = InpTrailingStepPips * point * 10;
      double breakEvenTrig= InpBreakEvenTriggerPips * point * 10;

      if(OrderType() == OP_BUY)
      {
         if(InpUseBreakEven)
         {
            if((bid - OrderOpenPrice()) >= breakEvenTrig && OrderStopLoss() < OrderOpenPrice())
            {
               OrderModify(OrderTicket(), OrderOpenPrice(), NormalizeDouble(OrderOpenPrice() + (10 * point), digits), OrderTakeProfit(), 0, clrBlue);
            }
         }

         if(InpUseTrailingStop)
         {
            if((bid - OrderOpenPrice()) > trailingDist)
            {
               double newSL = NormalizeDouble(bid - trailingDist, digits);
               if(newSL > OrderStopLoss() + trailingStep)
               {
                  OrderModify(OrderTicket(), OrderOpenPrice(), newSL, OrderTakeProfit(), 0, clrBlue);
               }
            }
         }
      }
      else if(OrderType() == OP_SELL)
      {
         if(InpUseBreakEven)
         {
            if((OrderOpenPrice() - ask) >= breakEvenTrig && (OrderStopLoss() > OrderOpenPrice() || OrderStopLoss() == 0))
            {
               OrderModify(OrderTicket(), OrderOpenPrice(), NormalizeDouble(OrderOpenPrice() - (10 * point), digits), OrderTakeProfit(), 0, clrBlue);
            }
         }

         if(InpUseTrailingStop)
         {
            if((OrderOpenPrice() - ask) > trailingDist)
            {
               double newSL = NormalizeDouble(ask + trailingDist, digits);
               if(OrderStopLoss() == 0 || newSL < OrderStopLoss() - trailingStep)
               {
                  OrderModify(OrderTicket(), OrderOpenPrice(), newSL, OrderTakeProfit(), 0, clrBlue);
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Dashboard HUD (28 Symboles)                                      |
//+------------------------------------------------------------------+
void DrawDashboardHUD()
{
   int x = 20;
   int y = 25;

   CreateLabel("BK_HUD_TITLE", "=== BELKHAYATE CASSURE & LEVIER 50X HUD ===", x, y, clrGold, 10, true);
   y += 18;
   CreateLabel("BK_HUD_SUB", "TF: " + EnumToString(g_tf) + " | Lot: " + DoubleToString(InpFixedLot, 2) + " | Scan Multi-Paires: " + (InpScanAllWatchlist ? "28 Paires" : "1 Paire"), x, y, clrWhite, 9);
   y += 18;
   CreateLabel("BK_HUD_SEP1", "--------------------------------------------------------", x, y, clrGray, 9);
   y += 15;

   CreateLabel("BK_HUD_HDR", "PAIRE      STATUT CASSURE         TIMING", x, y, clrCyan, 9, true);
   y += 16;

   for(int i = 0; i < 12; i++)
   {
      string sym = g_watchlist[i];
      if(MarketInfo(sym, MODE_BID) == 0) continue;

      double curP = iClose(sym, g_tf, 1);
      double openP = iOpen(sym, g_tf, 1);
      double highP = iHigh(sym, g_tf, 1);
      double lowP  = iLow(sym, g_tf, 1);
      double cRange = MathMax(highP - lowP, 0.00001);

      double bodyRatio = (MathAbs(curP - openP) / cRange) * 100.0;
      bool isBullImp = (curP > openP) && (bodyRatio >= InpMinImpulseBody);
      bool isBearImp = (curP < openP) && (bodyRatio >= InpMinImpulseBody);

      double sum = 0;
      for(int k = 1; k <= 30; k++) sum += iClose(sym, g_tf, k);
      double bary = sum / 30.0;

      double sqSum = 0;
      for(int k = 1; k <= 30; k++) { double d = iClose(sym, g_tf, k) - bary; sqSum += d*d; }
      double stdDev = MathSqrt(sqSum / 30.0);
      if(stdDev == 0) stdDev = 0.00001;
      double tim = (curP - bary) / stdDev;

      int hIdx = iHighest(sym, g_tf, MODE_HIGH, 24, 1);
      int lIdx = iLowest(sym, g_tf, MODE_LOW, 24, 1);
      double recHigh = iHigh(sym, g_tf, hIdx);
      double recLow  = iLow(sym, g_tf, lIdx);

      double dHighPct = (MathAbs(curP - recHigh) / curP) * 100.0;
      double dLowPct  = (MathAbs(curP - recLow) / curP) * 100.0;

      bool isBuySig  = (dHighPct <= InpMaxRetestDist) && (tim >= 1.0) && isBullImp;
      bool isSellSig = (dLowPct <= InpMaxRetestDist) && (tim <= -1.0) && isBearImp;

      string statusStr = "Neutre";
      color clrStatus  = clrSilver;

      if(isBuySig)
      {
         statusStr = "[CASSURE SOMMET - ACHAT]";
         clrStatus = clrLime;
      }
      else if(isSellSig)
      {
         statusStr = "[CASSURE CREUX - VENTE]";
         clrStatus = clrRed;
      }

      string rowText = StringFormat("%-8s   %-22s   %+5.2f", sym, statusStr, tim);
      CreateLabel("BK_HUD_ROW_" + IntegerToString(i), rowText, x, y, clrStatus, 9);
      y += 15;
   }
}

void CreateLabel(string name, string text, int x, int y, color clr, int fontSize, bool isBold = false)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
   ObjectSetString(0, name, OBJPROP_FONT, isBold ? "Arial Bold" : "Arial");
}
