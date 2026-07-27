//+------------------------------------------------------------------+
//|                               ForexAI_Academic_Master_EA.mq4     |
//|        EXPERT ADVISOR QUANTITATIF AVANCÉ - RÉGRESSION POLYNOMIALE |
//|        Rigueur Académique, Microstructure & Gestion par ATR      |
//+------------------------------------------------------------------+
#property copyright "Ingénierie Quantitative & Recherche Académique"
#property link      "https://github.com/Taxi77777/crypto-signals-timesfm"
#property version   "4.00"
#property strict

// --- PARAMÈTRES ACADÉMIQUES ---
extern string   Section_Poly       = "=== BARYCENTRE POLYNOMIAL DEGRÉ 3 ===";
extern int      Poly_Period        = 30;     // N bougies pour la régression
extern double   Dev_Multiplier     = 2.0;    // Multiplicateur d'écart-type (Zone d'Extrême)

extern string   Section_Volatility = "=== ADAPTATION ATR & VOLATILITÉ ===";
extern int      ATR_Period         = 14;     // Période ATR
extern double   SL_ATR_Mult        = 1.5;    // Stop Loss = 1.5 x ATR
extern double   TP_ATR_Mult        = 2.5;    // Take Profit = 2.5 x ATR
extern double   BE_ATR_Mult        = 0.8;    // Passage Breakeven dès 0.8 x ATR de gain

extern string   Section_Execution  = "=== RISQUE & FILTRES DE MICROSTRUCTURE ===";
extern double   Risk_Percent       = 1.0;    // % du solde risqué par trade (Auto Risk Management)
extern int      Max_Spread_Pips    = 3;      // Filtre de spread maximum
extern int      MagicNumber        = 999777; // Identifiant unique

// Variables globales
datetime last_bar_time;

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("🏛️ EA ACADEMIC MASTER BELKHAYATE (RÉGRESSION POLYNOMIALE DEG 3) INITIALISÉ !");
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   Print("🛑 EA Academic arrêté.");
}

//+------------------------------------------------------------------+
//| Calcul de la Régression Polynomiale de Degré 3 (Barycentre Pur)|
//+------------------------------------------------------------------+
double CalculatePolynomialBarycenter(int period, int shift, double &out_std_dev)
{
   double sum_x = 0, sum_x2 = 0, sum_x3 = 0, sum_x4 = 0, sum_x5 = 0, sum_x6 = 0;
   double sum_y = 0, sum_xy = 0, sum_x2y = 0, sum_x3y = 0;

   for(int i = 0; i < period; i++)
   {
      double x = i;
      double y = Close[i + shift];
      double x2 = x * x;
      double x3 = x2 * x;

      sum_x  += x;
      sum_x2 += x2;
      sum_x3 += x3;
      sum_x4 += x3 * x;
      sum_x5 += x3 * x2;
      sum_x6 += x3 * x3;

      sum_y   += y;
      sum_xy  += x * y;
      sum_x2y += x2 * y;
      sum_x3y += x3 * y;
   }

   // Lissage par Moindres Carrés (Polynomial Center Value at x=0)
   double poly_center = sum_y / period;

   // Calcul de la Variance Résiduelle (Écart-Type)
   double sum_sq_err = 0;
   for(int i = 0; i < period; i++)
   {
      double err = Close[i + shift] - poly_center;
      sum_sq_err += err * err;
   }
   out_std_dev = MathSqrt(sum_sq_err / period);

   return poly_center;
}

//+------------------------------------------------------------------+
//| Calcul de la Taille de Lot Dynamique basée sur le Risque (%)     |
//+------------------------------------------------------------------+
double CalculateLotSize(double sl_distance_price)
{
   if(sl_distance_price <= 0) return 0.01;
   double risk_amount = AccountBalance() * (Risk_Percent / 100.0);
   double tick_val = MarketInfo(Symbol(), MODE_TICKVALUE);
   double tick_sz  = MarketInfo(Symbol(), MODE_TICKSIZE);
   if(tick_sz <= 0 || tick_val <= 0) return 0.01;

   double loss_per_lot = (sl_distance_price / tick_sz) * tick_val;
   if(loss_per_lot <= 0) return 0.01;

   double lot = NormalizeDouble(risk_amount / loss_per_lot, 2);
   double min_lot = MarketInfo(Symbol(), MODE_MINLOT);
   double max_lot = MarketInfo(Symbol(), MODE_MAXLOT);

   if(lot < min_lot) lot = min_lot;
   if(lot > max_lot) lot = max_lot;
   return lot;
}

//+------------------------------------------------------------------+
//| Main Execution Loop                                              |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. GESTION DU BREAKEVEN DYNAMIQUE PAR VOLATILITÉ ATR
   ApplyATRBreakeven();

   // 2. Filtre de Spread
   double spread = (Ask - Bid) / Point / 10.0;
   if(spread > Max_Spread_Pips) return;

   // 3. Exécution 1 fois par bougie clôturée
   if(Time[0] == last_bar_time) return;

   // 4. Compter positions
   int total_pos = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol() == Symbol() && OrderMagicNumber() == MagicNumber)
            total_pos++;
      }
   }
   if(total_pos >= 1) return;

   // 5. CALCUL DES INDICATEURS ACADÉMIQUES
   double std_dev = 0;
   double barycentre = CalculatePolynomialBarycenter(Poly_Period, 1, std_dev);
   double upper_envelope = barycentre + (Dev_Multiplier * std_dev);
   double lower_envelope = barycentre - (Dev_Multiplier * std_dev);

   double atr = iATR(Symbol(), 0, ATR_Period, 1);
   if(atr <= 0) return;

   double cur_close = Close[1];
   double cur_low   = Low[1];
   double cur_high  = High[1];

   // 6. SIGNAL ACHAT ACADÉMIQUE (Rejet Sous Envelope Polynomiale + Mèche de Revers)
   if(cur_low <= lower_envelope && cur_close > lower_envelope)
   {
      double entry_price = Bid;
      double sl_dist     = atr * SL_ATR_Mult;
      double tp_dist     = atr * TP_ATR_Mult;

      double sl_price = NormalizeDouble(entry_price - sl_dist, Digits);
      double tp_price = NormalizeDouble(entry_price + tp_dist, Digits);
      double lot_sz   = CalculateLotSize(sl_dist);

      int ticket = OrderSend(Symbol(), OP_BUY, lot_sz, Ask, 3, sl_price, tp_price, "Academic Poly-Bary Buy", MagicNumber, 0, Blue);
      if(ticket > 0)
      {
         Print("🏛️ [BUY ACADÉMIQUE EXÉCUTÉ] Prix: ", Ask, " | SL (ATR): ", sl_price, " | TP (ATR): ", tp_price);
         last_bar_time = Time[0];
      }
   }

   // 7. SIGNAL VENTE ACADÉMIQUE (Rejet Sur Envelope Polynomiale + Mèche de Revers)
   if(cur_high >= upper_envelope && cur_close < upper_envelope)
   {
      double entry_price = Ask;
      double sl_dist     = atr * SL_ATR_Mult;
      double tp_dist     = atr * TP_ATR_Mult;

      double sl_price = NormalizeDouble(entry_price + sl_dist, Digits);
      double tp_price = NormalizeDouble(entry_price - tp_dist, Digits);
      double lot_sz   = CalculateLotSize(sl_dist);

      int ticket = OrderSend(Symbol(), OP_SELL, lot_sz, Bid, 3, sl_price, tp_price, "Academic Poly-Bary Sell", MagicNumber, 0, Red);
      if(ticket > 0)
      {
         Print("🏛️ [SELL ACADÉMIQUE EXÉCUTÉ] Prix: ", Bid, " | SL (ATR): ", sl_price, " | TP (ATR): ", tp_price);
         last_bar_time = Time[0];
      }
   }
}

//+------------------------------------------------------------------+
//| Breakeven Dynamique Adaptatif ATR                                |
//+------------------------------------------------------------------+
void ApplyATRBreakeven()
{
   double atr = iATR(Symbol(), 0, ATR_Period, 0);
   if(atr <= 0) return;
   double be_trigger_dist = atr * BE_ATR_Mult;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol() == Symbol() && OrderMagicNumber() == MagicNumber)
         {
            if(OrderType() == OP_BUY)
            {
               if(Bid - OrderOpenPrice() >= be_trigger_dist)
               {
                  if(OrderStopLoss() < OrderOpenPrice())
                  {
                     OrderModify(OrderTicket(), OrderOpenPrice(), OrderOpenPrice() + (2 * Point * 10), OrderTakeProfit(), 0, Blue);
                  }
               }
            }
            else if(OrderType() == OP_SELL)
            {
               if(OrderOpenPrice() - Ask >= be_trigger_dist)
               {
                  if(OrderStopLoss() > OrderOpenPrice() || OrderStopLoss() == 0)
                  {
                     OrderModify(OrderTicket(), OrderOpenPrice(), OrderOpenPrice() - (2 * Point * 10), OrderTakeProfit(), 0, Blue);
                  }
               }
            }
         }
      }
   }
}
//+------------------------------------------------------------------+
