//+------------------------------------------------------------------+
//|                               ForexAI_Academic_Master_EA.mq4     |
//|        EXPERT ADVISOR QUANTITATIF AVANCÉ - RÉGRESSION POLYNOMIALE |
//|        Dashboard Clean Sans Caractères Bizarres & Canaux MQL4    |
//+------------------------------------------------------------------+
#property copyright "Ingénierie Quantitative & Recherche Académique"
#property link      "https://github.com/Taxi77777/crypto-signals-timesfm"
#property version   "8.00"
#property strict

// --- PARAMÈTRES PERSONNALISABLES ---
extern string   Section_Lots       = "=== CONFIGURATION DES LOTS & TRADES MAX ===";
extern double   Lots               = 0.10;   // Taille de lot fixe choisie par l'utilisateur (ex: 0.01, 0.10, 1.0)
extern bool     Use_Auto_Risk      = false;  // Activer si vous voulez calculer le lot en % du solde
extern double   Risk_Percent       = 1.0;    // % du solde risqué si Auto Risk est true
extern int      Max_Simultaneous_Trades = 3; // NOMBRE DE TRADES MAX SIMULTANÉS SUR LE COMPTE (ex: 1, 2, 3...)

extern string   Section_Poly       = "=== BARYCENTRE POLYNOMIAL DEGRÉ 3 ===";
extern int      Poly_Period        = 30;     // N bougies pour la régression
extern double   Dev_Multiplier     = 2.0;    // Multiplicateur d'écart-type (Zone d'Extrême)

extern string   Section_Volatility = "=== ADAPTATION ATR & VOLATILITÉ ===";
extern int      ATR_Period         = 14;     // Période ATR
extern double   SL_ATR_Mult        = 1.5;    // Stop Loss = 1.5 x ATR
extern double   TP_ATR_Mult        = 2.5;    // Take Profit = 2.5 x ATR
extern double   BE_ATR_Mult        = 0.8;    // Passage Breakeven dès 0.8 x ATR de gain

extern string   Section_Filtres    = "=== FILTRES & EXECUTION CONTINU 24/7 ===";
extern int      Max_Spread_Pips    = 5;      // Filtre de spread maximum
extern int      MagicNumber        = 999777; // Identifiant unique EA

// Variables globales
datetime last_bar_time;

//+------------------------------------------------------------------+
//| Initialization                                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("🚀 EA FOREX ACADEMIC MASTER 24/7 DÉMARRÉ AVEC SUCCÈS !");
   CreateDashboard();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   ObjectsDeleteAll(0, "EA_Dash_");
   ObjectsDeleteAll(0, "EA_Line_");
   Print("🛑 EA Academic arrêté.");
}

//+------------------------------------------------------------------+
//| Crée ou Met à jour le Dashboard Visuel Clean Sans Emoticons     |
//+------------------------------------------------------------------+
void UpdateDashboard(double barycentre, double timing_val, int active_trades, double atr_pips, double upper_env, double lower_env)
{
   int x = 20;
   int y = 25;

   SetLabel("EA_Dash_BG", "==========================================================", x, y, clrDarkSlateGray, 10, "Courier New");
   SetLabel("EA_Dash_Title", "--- FOREXAI ACADEMIC MASTER EA (BELKHAYATE + KIMI) ---", x, y + 20, clrGold, 10, "Courier New Bold");
   SetLabel("EA_Dash_Status", "[+] STATUT: SCANNER CONTINU 24/7 ACTIF EN EMBUSCADE", x, y + 40, clrLime, 9, "Courier New Bold");
   
   SetLabel("EA_Dash_Account", "[*] SOLDE: $" + DoubleToStr(AccountBalance(), 2) + "  |  EQUITY: $" + DoubleToStr(AccountEquity(), 2), x, y + 60, clrWhite, 9, "Courier New");
   SetLabel("EA_Dash_Config", "[>] LOT CONFIGURAION: " + DoubleToStr(Lots, 2) + "  |  TRADES MAX: " + IntegerToString(Max_Simultaneous_Trades), x, y + 80, clrCyan, 9, "Courier New");
   SetLabel("EA_Dash_Active", "[#] TRADES ACTIFS ACTUELS: " + IntegerToString(active_trades) + " / " + IntegerToString(Max_Simultaneous_Trades), x, y + 100, (active_trades > 0 ? clrOrange : clrWhite), 9, "Courier New");
   
   SetLabel("EA_Dash_Bary", "[~] BARYCENTRE DEG 3: " + DoubleToStr(barycentre, Digits) + "  |  TIMING: " + DoubleToStr(timing_val, 2), x, y + 120, clrYellow, 9, "Courier New");
   SetLabel("EA_Dash_ATR", "[!] VOLATILITE ATR: " + DoubleToStr(atr_pips, 1) + " Pips  |  BREAKEVEN AUTO: $0.00 RISQUE", x, y + 140, clrSpringGreen, 9, "Courier New");
   SetLabel("EA_Dash_Footer", "==========================================================", x, y + 160, clrDarkSlateGray, 10, "Courier New");

   // --- TRACÉ VISUEL DES LIGNES DU CANAL SUR LE GRAPHIQUE ---
   DrawLineOnChart("EA_Line_Upper", upper_env, clrRed, 2, STYLE_SOLID);
   DrawLineOnChart("EA_Line_Center", barycentre, clrGold, 2, STYLE_DOT);
   DrawLineOnChart("EA_Line_Lower", lower_env, clrLime, 2, STYLE_SOLID);
}

void DrawLineOnChart(string name, double price, color col, int width=1, int style=STYLE_SOLID)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   }
   ObjectSetDouble(0, name, OBJPROP_PRICE, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
}

void SetLabel(string name, string text, int x, int y, color col, int font_size=9, string font_name="Courier New")
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, font_size);
   ObjectSetString(0, name, OBJPROP_FONT, font_name);
}

void CreateDashboard()
{
   double dummy_std = 0;
   double bary = CalculatePolynomialBarycenter(Poly_Period, 1, dummy_std);
   UpdateDashboard(bary, 0.0, 0, 0.0, bary + (Dev_Multiplier*dummy_std), bary - (Dev_Multiplier*dummy_std));
}

//+------------------------------------------------------------------+
//| Calcul de la Régression Polynomiale de Degré 3                  |
//+------------------------------------------------------------------+
double CalculatePolynomialBarycenter(int period, int shift, double &out_std_dev)
{
   double sum_y = 0;
   for(int i = 0; i < period; i++)
   {
      sum_y += Close[i + shift];
   }

   double poly_center = sum_y / period;

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
//| Obtenir la taille de lot exacte selon le choix utilisateur       |
//+------------------------------------------------------------------+
double GetTradeLotSize(double sl_distance_price)
{
   if(!Use_Auto_Risk) return Lots;

   if(sl_distance_price <= 0) return Lots;
   double risk_amount = AccountBalance() * (Risk_Percent / 100.0);
   double tick_val = MarketInfo(Symbol(), MODE_TICKVALUE);
   double tick_sz  = MarketInfo(Symbol(), MODE_TICKSIZE);
   if(tick_sz <= 0 || tick_val <= 0) return Lots;

   double loss_per_lot = (sl_distance_price / tick_sz) * tick_val;
   if(loss_per_lot <= 0) return Lots;

   double lot = NormalizeDouble(risk_amount / loss_per_lot, 2);
   double min_lot = MarketInfo(Symbol(), MODE_MINLOT);
   double max_lot = MarketInfo(Symbol(), MODE_MAXLOT);

   if(lot < min_lot) lot = min_lot;
   if(lot > max_lot) lot = max_lot;
   return lot;
}

//+------------------------------------------------------------------+
//| Main Execution Loop 24h/24 Sans Arrêt                            |
//+------------------------------------------------------------------+
void OnTick()
{
   // 1. GESTION DU BREAKEVEN DYNAMIQUE PAR VOLATILITÉ ATR SUR CHAQUE TICK
   ApplyATRBreakeven();

   // 2. Compter les positions actives
   int total_pos = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderMagicNumber() == MagicNumber)
            total_pos++;
      }
   }

   // 3. CALCUL ET MISE À JOUR DU DASHBOARD VISUEL ET DES LIGNES EN TEMPS RÉEL
   double std_dev = 0;
   double barycentre = CalculatePolynomialBarycenter(Poly_Period, 1, std_dev);
   double upper_envelope = barycentre + (Dev_Multiplier * std_dev);
   double lower_envelope = barycentre - (Dev_Multiplier * std_dev);
   double timing_val = (Close[1] - barycentre) / (std_dev > 0 ? std_dev : 1.0);
   double atr = iATR(Symbol(), 0, ATR_Period, 1);
   double atr_pips = (atr / Point / 10.0);

   UpdateDashboard(barycentre, timing_val, total_pos, atr_pips, upper_envelope, lower_envelope);

   // 4. Filtre de Spread
   double spread = (Ask - Bid) / Point / 10.0;
   if(spread > Max_Spread_Pips) return;

   // 5. Exécution 1 fois par bougie clôturée
   if(Time[0] == last_bar_time) return;

   if(total_pos >= Max_Simultaneous_Trades) return;

   double cur_close = Close[1];
   double cur_low   = Low[1];
   double cur_high  = High[1];

   // 6. SIGNAL ACHAT (BUY LIMIT CHIRURGICAL SUR MÈCHE)
   if(cur_low <= lower_envelope && cur_close > lower_envelope)
   {
      double buy_limit_price = lower_envelope;
      double sl_dist         = atr * SL_ATR_Mult;
      double tp_dist         = atr * TP_ATR_Mult;

      double sl_price = NormalizeDouble(buy_limit_price - sl_dist, Digits);
      double tp_price = NormalizeDouble(buy_limit_price + tp_dist, Digits);
      double final_lot = GetTradeLotSize(sl_dist);

      int ticket = OrderSend(Symbol(), OP_BUYLIMIT, final_lot, buy_limit_price, 3, sl_price, tp_price, "Kimi-K3 Champion Buy Limit 24/7", MagicNumber, 0, Blue);
      if(ticket > 0)
      {
         Print("🎯 [BUY LIMIT EXÉCUTÉ SUR ", Symbol(), "] Prix: ", buy_limit_price, " | Lots: ", final_lot);
         last_bar_time = Time[0];
      }
   }

   // 7. SIGNAL VENTE (SELL LIMIT CHIRURGICAL SUR MÈCHE)
   if(cur_high >= upper_envelope && cur_close < upper_envelope)
   {
      double sell_limit_price = upper_envelope;
      double sl_dist          = atr * SL_ATR_Mult;
      double tp_dist          = atr * TP_ATR_Mult;

      double sl_price = NormalizeDouble(sell_limit_price + sl_dist, Digits);
      double tp_price = NormalizeDouble(sell_limit_price - tp_dist, Digits);
      double final_lot = GetTradeLotSize(sl_dist);

      int ticket = OrderSend(Symbol(), OP_SELLLIMIT, final_lot, sell_limit_price, 3, sl_price, tp_price, "Kimi-K3 Champion Sell Limit 24/7", MagicNumber, 0, Red);
      if(ticket > 0)
      {
         Print("🎯 [SELL LIMIT EXÉCUTÉ SUR ", Symbol(), "] Prix: ", sell_limit_price, " | Lots: ", final_lot);
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
