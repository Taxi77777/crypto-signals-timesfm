//+------------------------------------------------------------------+
//|                                     Belkhayate_Retest_EA.mq4    |
//|               Copyright 2026, Mostafa Belkhayate & IA System     |
//|    Robot Expert MT4 v4.00 : Dashboard Visuel, Lignes & Flèches  |
//+------------------------------------------------------------------+
#property copyright "Copyright 2026, Belkhayate AI"
#property link      "https://github.com/Taxi77777/crypto-signals-timesfm"
#property version   "4.00"
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

//--- Inputs Paramètres Stratégie Belkhayate
input string                InpGroupStrategy   = "=== STRATÉGIE BELKHAYATE RETEST ===";
input ENUM_CUSTOM_TIMEFRAME InpTimeframe       = TF_M15;   // Unité de temps choisie pour le Robot
input int                   InpBaryPeriod      = 30;      // Période du Barycentre Belkhayate
input double                InpMinWickPct      = 15.0;    // Mèche de Rejet Minimale (%)
input double                InpMaxRetestDist   = 1.5;     // Écartement max du Retest (%)
input int                   InpRetestLookback  = 24;      // Bougies pour détecter le Sommet/Creux

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
input bool                  InpShowDashboard   = true;    // Afficher le Tableau Dashboard sur le Graphique
input bool                  InpDrawArrows      = true;    // Dessiner les flèches et textes de Rejet (Achat/Vente)
input bool                  InpDrawRetestLines = true;    // Dessiner les lignes Horizontales (Sommet/Creux)
input bool                  InpDrawVertLines   = true;    // Dessiner les Lignes Verticales sur la bougie du Rejet !

//--- Variables Globales
datetime g_lastBarTime = 0;
ENUM_TIMEFRAMES g_tf;

// Liste des 12 paires principales surveillées pour le Dashboard
string g_watchlist[12] = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPCAD"};

//+------------------------------------------------------------------+
//| Expert Initialization                                            |
//+------------------------------------------------------------------+
int OnInit()
{
   if(InpTimeframe == TF_CURRENT) g_tf = (ENUM_TIMEFRAMES)_Period;
   else g_tf = (ENUM_TIMEFRAMES)InpTimeframe;

   Print("👑 Belkhayate Retest EA v4.00 initialisé !");

   // Scan historique pour tracer toutes les bougies de rejet passées
   ScanHistoricalRejections();

   if(InpShowDashboard) DrawDashboardHUD();
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert Deinitialization                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, "BK_");
   Comment("");
}

//+------------------------------------------------------------------+
//| Scan Historique des bougies passées pour tracer les Rejets       |
//+------------------------------------------------------------------+
void ScanHistoricalRejections()
{
   int totalBars = iBars(_Symbol, g_tf);
   int lookback  = MathMin(100, totalBars - InpBaryPeriod - 1);

   for(int i = lookback; i >= 1; i--)
   {
      double curPrice = iClose(_Symbol, g_tf, i);
      double openP    = iOpen(_Symbol, g_tf, i);
      double highP    = iHigh(_Symbol, g_tf, i);
      double lowP     = iLow(_Symbol, g_tf, i);
      double cRange   = MathMax(highP - lowP, 0.00001);

      double bodyMin  = MathMin(openP, curPrice);
      double bodyMax  = MathMax(openP, curPrice);
      double lowerWickPct = ((bodyMin - lowP) / cRange) * 100.0;
      double upperWickPct = ((highP - bodyMax) / cRange) * 100.0;

      // Barycentre
      double sum = 0;
      for(int k = i; k < i + InpBaryPeriod; k++) sum += iClose(_Symbol, g_tf, k);
      double barycenter = sum / InpBaryPeriod;

      double sqSum = 0;
      for(int k = i; k < i + InpBaryPeriod; k++)
      {
         double diff = iClose(_Symbol, g_tf, k) - barycenter;
         sqSum += diff * diff;
      }
      double stdDev = MathSqrt(sqSum / InpBaryPeriod);
      if(stdDev == 0) stdDev = 0.00001;
      double timing = (curPrice - barycenter) / stdDev;

      // Sommet et Creux Récents à partir de i+1
      int highestIdx = iHighest(_Symbol, g_tf, MODE_HIGH, InpRetestLookback, i + 1);
      int lowestIdx  = iLowest(_Symbol, g_tf, MODE_LOW, InpRetestLookback, i + 1);
      double recentHigh = iHigh(_Symbol, g_tf, highestIdx);
      double recentLow  = iLow(_Symbol, g_tf, lowestIdx);

      double distHighPct = (MathAbs(curPrice - recentHigh) / curPrice) * 100.0;
      double distLowPct  = (MathAbs(curPrice - recentLow) / curPrice) * 100.0;

      bool isRetestHigh = (distHighPct <= InpMaxRetestDist) && (timing >= 1.0);
      bool isRetestLow  = (distLowPct <= InpMaxRetestDist) && (timing <= -1.0);

      bool isBuy  = isRetestLow  && (lowerWickPct >= InpMinWickPct);
      bool isSell = isRetestHigh && (upperWickPct >= InpMinWickPct);

      if(isBuy || isSell)
      {
         datetime bTime = iTime(_Symbol, g_tf, i);
         DrawRejectionMarker(bTime, isBuy, isBuy ? lowerWickPct : upperWickPct, highP, lowP);
      }
   }
}

//+------------------------------------------------------------------+
//| Traceur Universel de Marqueurs de Rejet (Ligne Vert. + Flèche)  |
//+------------------------------------------------------------------+
void DrawRejectionMarker(datetime time, bool isBuy, double wickPct, double highP, double lowP)
{
   string timeID = IntegerToString((long)time);

   // 1. Ligne Verticale traversant la bougie de Rejet exacte (OBJ_VLINE)
   if(InpDrawVertLines)
   {
      string vlineName = "BK_VLine_" + timeID;
      if(ObjectFind(0, vlineName) < 0)
      {
         ObjectCreate(0, vlineName, OBJ_VLINE, 0, time, 0);
         ObjectSetInteger(0, vlineName, OBJPROP_COLOR, isBuy ? clrLime : clrRed);
         ObjectSetInteger(0, vlineName, OBJPROP_STYLE, STYLE_DOT);
         ObjectSetInteger(0, vlineName, OBJPROP_WIDTH, 2);
         ObjectSetInteger(0, vlineName, OBJPROP_BACK, true);
      }
   }

   // 2. Flèches et Textes explicites (OBJ_ARROW & OBJ_TEXT)
   if(InpDrawArrows)
   {
      if(isBuy)
      {
         string arrowName = "BK_Arrow_Buy_" + timeID;
         if(ObjectFind(0, arrowName) < 0)
         {
            ObjectCreate(0, arrowName, OBJ_ARROW, 0, time, lowP - (20 * _Point));
            ObjectSetInteger(0, arrowName, OBJPROP_ARROWCODE, 233); // Flèche Haut
            ObjectSetInteger(0, arrowName, OBJPROP_COLOR, clrLime);
            ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 4);
         }

         string textName = "BK_Text_Buy_" + timeID;
         if(ObjectFind(0, textName) < 0)
         {
            ObjectCreate(0, textName, OBJ_TEXT, 0, time, lowP - (45 * _Point));
            ObjectSetString(0, textName, OBJPROP_TEXT, "  [RETEST ACHAT " + DoubleToString(wickPct, 1) + "%]");
            ObjectSetInteger(0, textName, OBJPROP_COLOR, clrLime);
            ObjectSetInteger(0, textName, OBJPROP_FONTSIZE, 10);
            ObjectSetString(0, textName, OBJPROP_FONT, "Arial Bold");
         }
      }
      else
      {
         string arrowName = "BK_Arrow_Sell_" + timeID;
         if(ObjectFind(0, arrowName) < 0)
         {
            ObjectCreate(0, arrowName, OBJ_ARROW, 0, time, highP + (20 * _Point));
            ObjectSetInteger(0, arrowName, OBJPROP_ARROWCODE, 234); // Flèche Bas
            ObjectSetInteger(0, arrowName, OBJPROP_COLOR, clrRed);
            ObjectSetInteger(0, arrowName, OBJPROP_WIDTH, 4);
         }

         string textName = "BK_Text_Sell_" + timeID;
         if(ObjectFind(0, textName) < 0)
         {
            ObjectCreate(0, textName, OBJ_TEXT, 0, time, highP + (45 * _Point));
            ObjectSetString(0, textName, OBJPROP_TEXT, "  [RETEST VENTE " + DoubleToString(wickPct, 1) + "%]");
            ObjectSetInteger(0, textName, OBJPROP_COLOR, clrRed);
            ObjectSetInteger(0, textName, OBJPROP_FONTSIZE, 10);
            ObjectSetString(0, textName, OBJPROP_FONT, "Arial Bold");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Expert Tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
{
   ApplyTrailingStopAndBreakEven();

   if(InpShowDashboard) DrawDashboardHUD();

   datetime currentBarTime = iTime(_Symbol, g_tf, 0);
   if(currentBarTime == g_lastBarTime) return;
   g_lastBarTime = currentBarTime;

   int openTrades = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol() == _Symbol && OrderMagicNumber() == InpMagicNumber) openTrades++;
      }
   }

   // ── CALCULS SUR LA BOUGIE PRÉCÉDENTE (Shift 1) ──
   double curPrice = iClose(_Symbol, g_tf, 1);
   double openP    = iOpen(_Symbol, g_tf, 1);
   double highP    = iHigh(_Symbol, g_tf, 1);
   double lowP     = iLow(_Symbol, g_tf, 1);
   double cRange   = MathMax(highP - lowP, 0.00001);

   double bodyMin  = MathMin(openP, curPrice);
   double bodyMax  = MathMax(openP, curPrice);
   double lowerWickPct = ((bodyMin - lowP) / cRange) * 100.0;
   double upperWickPct = ((highP - bodyMax) / cRange) * 100.0;

   // Barycentre
   double sum = 0;
   for(int k = 1; k <= InpBaryPeriod; k++) sum += iClose(_Symbol, g_tf, k);
   double barycenter = sum / InpBaryPeriod;

   double sqSum = 0;
   for(int k = 1; k <= InpBaryPeriod; k++)
   {
      double diff = iClose(_Symbol, g_tf, k) - barycenter;
      sqSum += diff * diff;
   }
   double stdDev = MathSqrt(sqSum / InpBaryPeriod);
   if(stdDev == 0) stdDev = 0.00001;
   double timing = (curPrice - barycenter) / stdDev;

   // Retest Sommet et Creux Récents
   int highestIdx = iHighest(_Symbol, g_tf, MODE_HIGH, InpRetestLookback, 1);
   int lowestIdx  = iLowest(_Symbol, g_tf, MODE_LOW, InpRetestLookback, 1);
   double recentHigh = iHigh(_Symbol, g_tf, highestIdx);
   double recentLow  = iLow(_Symbol, g_tf, lowestIdx);

   double distHighPct = (MathAbs(curPrice - recentHigh) / curPrice) * 100.0;
   double distLowPct  = (MathAbs(curPrice - recentLow) / curPrice) * 100.0;

   bool isRetestHigh = (distHighPct <= InpMaxRetestDist) && (timing >= 1.0);
   bool isRetestLow  = (distLowPct <= InpMaxRetestDist) && (timing <= -1.0);

   bool isBuySignal  = isRetestLow  && (lowerWickPct >= InpMinWickPct);
   bool isSellSignal = isRetestHigh && (upperWickPct >= InpMinWickPct);

   datetime candleTime = iTime(_Symbol, g_tf, 1);

   // ── TRACÉ VISUEL DU LEVEL HORIZONTAL DE RETEST ──
   if(InpDrawRetestLines)
   {
      string lineHighName = "BK_Line_High";
      if(ObjectFind(0, lineHighName) < 0) ObjectCreate(0, lineHighName, OBJ_HLINE, 0, 0, recentHigh);
      else ObjectMove(0, lineHighName, 0, 0, recentHigh);
      ObjectSetInteger(0, lineHighName, OBJPROP_COLOR, clrRed);
      ObjectSetInteger(0, lineHighName, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, lineHighName, OBJPROP_WIDTH, 2);

      string lineLowName = "BK_Line_Low";
      if(ObjectFind(0, lineLowName) < 0) ObjectCreate(0, lineLowName, OBJ_HLINE, 0, 0, recentLow);
      else ObjectMove(0, lineLowName, 0, 0, recentLow);
      ObjectSetInteger(0, lineLowName, OBJPROP_COLOR, clrLime);
      ObjectSetInteger(0, lineLowName, OBJPROP_STYLE, STYLE_SOLID);
      ObjectSetInteger(0, lineLowName, OBJPROP_WIDTH, 2);
   }

   // Tracer le marqueur sur la bougie en cours si valide
   if(isBuySignal || isSellSignal)
   {
      DrawRejectionMarker(candleTime, isBuySignal, isBuySignal ? lowerWickPct : upperWickPct, highP, lowP);
   }

   if(openTrades >= InpMaxOpenTrades) return;
   if(!isBuySignal && !isSellSignal) return;

   // ── EXECUTION DU TRADE ──
   double atr = iATR(_Symbol, g_tf, 14, 1);
   if(atr == 0) atr = curPrice * 0.002;

   double askPrice = MarketInfo(_Symbol, MODE_ASK);
   double bidPrice = MarketInfo(_Symbol, MODE_BID);

   double lotSize = InpFixedLot;
   if(InpRiskPercent > 0)
   {
      double balance   = AccountBalance();
      double riskMoney = balance * (InpRiskPercent / 100.0);
      double tickValue = MarketInfo(_Symbol, MODE_TICKVALUE);
      double slPips    = (1.2 * atr) / MarketInfo(_Symbol, MODE_POINT);
      if(slPips > 0 && tickValue > 0) lotSize = NormalizeDouble(riskMoney / (slPips * tickValue), 2);
   }
   lotSize = MathMax(MarketInfo(_Symbol, MODE_MINLOT), MathMin(MarketInfo(_Symbol, MODE_MAXLOT), lotSize));

   if(isBuySignal)
   {
      double tp = NormalizeDouble(MathMax(barycenter, askPrice + (InpATR_TP_Mult * atr)), _Digits);
      double sl = NormalizeDouble(askPrice - (InpATR_SL_Mult * atr), _Digits);
      OrderSend(_Symbol, OP_BUY, lotSize, askPrice, 3, sl, tp, "Belkhayate Retest BUY", InpMagicNumber, 0, clrLime);
   }
   else if(isSellSignal)
   {
      double tp = NormalizeDouble(MathMin(barycenter, bidPrice - (InpATR_TP_Mult * atr)), _Digits);
      double sl = NormalizeDouble(bidPrice + (InpATR_SL_Mult * atr), _Digits);
      OrderSend(_Symbol, OP_SELL, lotSize, bidPrice, 3, sl, tp, "Belkhayate Retest SELL", InpMagicNumber, 0, clrRed);
   }
}

//+------------------------------------------------------------------+
//| Trailing Stop & Break-Even                                       |
//+------------------------------------------------------------------+
void ApplyTrailingStopAndBreakEven()
{
   double point = MarketInfo(_Symbol, MODE_POINT);
   double ask   = MarketInfo(_Symbol, MODE_ASK);
   double bid   = MarketInfo(_Symbol, MODE_BID);

   double trailingDist = InpTrailingStopPips * point * 10;
   double trailingStep = InpTrailingStepPips * point * 10;
   double breakEvenTrig= InpBreakEvenTriggerPips * point * 10;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES)) continue;
      if(OrderSymbol() != _Symbol || OrderMagicNumber() != InpMagicNumber) continue;

      if(OrderType() == OP_BUY)
      {
         if(InpUseBreakEven)
         {
            if((bid - OrderOpenPrice()) >= breakEvenTrig && OrderStopLoss() < OrderOpenPrice())
            {
               OrderModify(OrderTicket(), OrderOpenPrice(), NormalizeDouble(OrderOpenPrice() + (10 * point), _Digits), OrderTakeProfit(), 0, clrBlue);
            }
         }

         if(InpUseTrailingStop)
         {
            if((bid - OrderOpenPrice()) > trailingDist)
            {
               double newSL = NormalizeDouble(bid - trailingDist, _Digits);
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
               OrderModify(OrderTicket(), OrderOpenPrice(), NormalizeDouble(OrderOpenPrice() - (10 * point), _Digits), OrderTakeProfit(), 0, clrBlue);
            }
         }

         if(InpUseTrailingStop)
         {
            if((OrderOpenPrice() - ask) > trailingDist)
            {
               double newSL = NormalizeDouble(ask + trailingDist, _Digits);
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
//| Dashboard HUD                                                    |
//+------------------------------------------------------------------+
void DrawDashboardHUD()
{
   int x = 20;
   int y = 25;

   CreateLabel("BK_HUD_TITLE", "=== MOSTAFA BELKHAYATE & IA RETEST HUD ===", x, y, clrGold, 10, true);
   y += 18;
   CreateLabel("BK_HUD_SUB", "TF: " + EnumToString(g_tf) + " | Lot: " + DoubleToString(InpFixedLot, 2) + " | Trailing: " + (InpUseTrailingStop ? "15p" : "OFF"), x, y, clrWhite, 9);
   y += 18;
   CreateLabel("BK_HUD_SEP1", "--------------------------------------------------------", x, y, clrGray, 9);
   y += 15;

   CreateLabel("BK_HUD_HDR", "PAIRE      STATUT RETEST          TIMING     MECHE", x, y, clrCyan, 9, true);
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

      double bodyMin = MathMin(openP, curP);
      double bodyMax = MathMax(openP, curP);
      double lwPct   = ((bodyMin - lowP) / cRange) * 100.0;
      double uwPct   = ((highP - bodyMax) / cRange) * 100.0;

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

      bool isRetHigh = (dHighPct <= InpMaxRetestDist) && (tim >= 1.0);
      bool isRetLow  = (dLowPct <= InpMaxRetestDist) && (tim <= -1.0);

      string statusStr = "Neutre";
      color clrStatus  = clrSilver;
      double wickVal   = 0.0;

      if(isRetHigh && uwPct >= InpMinWickPct)
      {
         statusStr = "[RETEST SELL - HAUT]";
         clrStatus = clrRed;
         wickVal   = uwPct;
      }
      else if(isRetLow && lwPct >= InpMinWickPct)
      {
         statusStr = "[RETEST BUY - BAS]";
         clrStatus = clrLime;
         wickVal   = lwPct;
      }
      else if(isRetHigh)
      {
         statusStr = "[SOMMET - Attente]";
         clrStatus = clrOrange;
         wickVal   = uwPct;
      }
      else if(isRetLow)
      {
         statusStr = "[CREUX - Attente]";
         clrStatus = clrYellow;
         wickVal   = lwPct;
      }

      string rowText = StringFormat("%-8s   %-22s   %+5.2f    %4.1f%%", sym, statusStr, tim, (wickVal > 0 ? wickVal : MathMax(uwPct, lwPct)));
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
