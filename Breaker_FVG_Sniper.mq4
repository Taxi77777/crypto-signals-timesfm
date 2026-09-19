//+------------------------------------------------------------------+
//|                                          Breaker_FVG_Sniper.mq4  |
//|   ORDER BLOCK casse par une bougie de deplacement = BREAKER      |
//|   -> attente du retest breaker / FVG -> entree, stop, objectifs  |
//|   + SCANNER : tous les actifs x toutes les unites de temps       |
//+------------------------------------------------------------------+
#property copyright "Rachid - Breaker FVG Sniper"
#property version   "3.60"
#property strict
#property description "Un Order Block casse (cloture + deplacement) devient un BREAKER."
#property description "Retest du breaker / FVG -> entree, stop, TP1, TP2. Tableau multi-actifs / multi-TF classe par score."
#property description "Buffers iCustom : 0=fleche ACHAT confirme 1=fleche VENTE confirme 2=signal entree(+1/-1) 3=entree 4=stop 5=TP1 6=TP2 7/8=point d'entree"
#property indicator_chart_window
#property indicator_buffers 9

#define PFX   "BFS_"     // objets du graphique
#define PPFX  "BFSP_"    // panneau du graphique
#define TPFX  "BFST_"    // tableau du scanner

#define ST_WAIT     0
#define ST_LIVE     1
#define ST_TP2      2
#define ST_BE       3
#define ST_SL       4
#define ST_EXPIRED  5
#define ST_REJECT   6
#define ST_CANCEL   7
#define ST_MISSED   8
#define ST_TOOFAR   9

enum ENUM_ENTRY_MODE
  {
   ENTRY_FVG50   = 0, // 50% du FVG (standard)
   ENTRY_FVGEDGE = 1, // Bord du FVG (agressif)
   ENTRY_BREAKER = 2, // Bord du breaker (conservateur)
   ENTRY_MEAN    = 3  // 50% du corps de la bougie breaker (ICT mean threshold)
  };

enum ENUM_TRIGGER
  {
   TRIG_CONFIRM = 0, // Retest dans l'OB + bougie de confirmation (le prix redevient acheteur/vendeur)
   TRIG_LIMIT   = 1  // Ordre limite : entree des que le prix touche la zone
  };

enum ENUM_SL_MODE
  {
   SL_BREAKER = 0, // Sous le breaker / FVG (serre)
   SL_SWEEP   = 1, // Sous la meche de la prise de liquidite (ICT)
   SL_AUTO    = 2  // Auto : breaker jusqu'a M30, meche de liquidite en H1 et plus
  };

enum ENUM_DIR
  {
   DIR_BUY  = 1,  // Achats seulement
   DIR_SELL = -1, // Ventes seulement
   DIR_BOTH = 0   // Achats et ventes
  };

//==================================================================== INPUTS
input string   _s0            = "===== ORDER BLOCKS =====";          // -----
input int      ATRPeriod      = 14;     // Periode ATR
input double   DispATR        = 1.2;    // Impulsion qui cree l'OB : corps >= x ATR
input int      OBSearchBars   = 5;      // Recherche de la derniere bougie opposee
input bool     OBUseWicks     = true;   // Zone OB = meches (true) / corps (false)
input int      OBMaxAge       = 150;    // Duree de vie max d'un OB (bougies)
input bool     OBAtSwing      = true;   // OB uniquement sur un plus haut / plus bas du marche
input int      SwingLB        = 20;     // ... plus haut / plus bas des N bougies precedentes
input string   _s1            = "===== CASSURE -> BREAKER =====";    // -----
input double   BreakBodyATR   = 1.0;    // Cassure franche : corps de la bougie >= x ATR
input double   BreakBeyondATR = 0.25;   // Cassure franche : cloture au-dela de l'OB d'au moins x ATR
input double   BreakCloseRatio= 0.60;   // Cassure franche : cloture dans le haut (achat) / bas (vente) de la bougie
input bool     RequireSweep   = true;   // Exiger une prise de liquidite avant la cassure
input int      SweepLookback  = 20;     // (zones OB opposees) profondeur
input int      SwingLeft      = 10;     // Plus haut / plus bas du marche : plus extreme que les N bougies avant
input int      SwingRight     = 3;      // ... et confirme par N bougies apres
input int      RallyLB        = 100;    // Recherche du creux (sommet) de depart du mouvement (bougies)
input double   MinLegATR      = 1.5;    // Taille mini de la hausse (baisse) jusqu'au plus haut (bas), x ATR
input double   WickKillATR    = 1.0;    // Une meche qui depasse le swing de moins de x ATR = simple chasse a la liquidite
input string   _s2            = "===== FVG =====";                   // -----
input bool     RequireFVG     = true;   // Exiger un FVG dans l'impulsion
input double   MinFVG_ATR     = 0.10;   // Taille mini du FVG (x ATR)
input int      FVGConfirmBars = 2;      // Bougies max pour confirmer le FVG apres la cassure
input string   _s3            = "===== ENTREE / STOP / OBJECTIFS ====="; // -----
input ENUM_TRIGGER EntryTrigger = TRIG_CONFIRM; // Declenchement de l'entree
input int      ConfirmMaxBars = 6;      // Bougies max dans la zone pour obtenir la confirmation
input double   ConfirmMaxR    = 0.5;    // Bougie de confirmation : cloture a moins de x R de l'entree
input double   ConfirmMaxATR  = 1.0;    // ... ou a moins de x ATR (utile en M1/M5 ou le risque est petit)
input bool     UsePremiumDiscount = true; // ACHAT seulement dans le bas du marche (discount), VENTE dans le haut (premium)
input int      PDLookback     = 500;    // Range du marche : plus haut / plus bas des N dernieres bougies
input double   PDMaxPct       = 55.0;   // Achat si l'entree est sous x % du range (vente au-dessus de 100-x %)
input bool     UseFlip        = true;   // Breaker rate (cloture de l'autre cote de l'OB) -> la zone s'inverse : vente/achat au retest
input ENUM_ENTRY_MODE EntryType = ENTRY_BREAKER; // Entree (si FVG + breaker se chevauchent = Unicorn : milieu du chevauchement)
input double   SLBufferATR    = 0.20;   // Marge du stop (x ATR)
input double   StopMinATR     = 1.0;    // Stop minimum (x ATR) : evite les stops trop serres
input ENUM_SL_MODE StopType    = SL_SWEEP; // Placement du stop
input double   StopMaxATR     = 3.0;    // Stop meche de liquidite : max (x ATR), sinon stop breaker
input double   TP1_R          = 2.0;    // TP1 en R
input double   TP2_R          = 3.0;    // TP2 en R (si pas de liquidite)
input bool     TP2OnLiquidity = true;   // TP2 sur la liquidite (plus haut / plus bas)
input int      LiqLookback    = 100;    // Recherche de la liquidite (bougies)
input double   TPMaxR         = 6.0;    // TP2 maximum en R
input double   MinRR          = 2.0;    // RR minimum pour valider le setup
input bool     MoveBEAtTP1    = true;   // Stop au point d'entree apres TP1
input int      MaxBarsWait    = 40;     // Attente max du retest (bougies)
input string   _s4            = "===== FILTRE TENDANCE (HTF auto) ====="; // -----
input bool     FilterHTFTrend = false;  // BLOQUER les signaux contre la tendance HTF (false = les retournements breaker passent)
input bool     RejectOppWall  = true;   // Rejeter si une zone OB opposee intacte est a moins de 1R
input double   OppWallR       = 1.0;    // Distance de la zone opposee (en R)
input bool     KillzoneBonus  = true;   // Bonus de score si cassure en session Londres / New York
input int      BiasEMA        = 50;     // EMA de tendance
input string   _s5            = "===== SCANNER TOUS ACTIFS / TOUS TF ====="; // -----
input bool     UseScanner     = true;   // Afficher le tableau des opportunites
input ENUM_DIR ScanDirection  = DIR_BOTH; // Sens scanne
input string   ScanSymbols    = "";     // Actifs (vide = Observation du marche) ex: GBPJPY,EURUSD
input string   ScanTFs        = "M5,M15,M30,H1,H4,D1"; // Unites de temps scannees
input int      ScanBars       = 300;    // Bougies analysees par actif / TF
input int      ScanSeconds    = 30;     // Frequence du scan complet (secondes)
input int      MaxRows        = 10;     // Lignes du tableau
input double   MinScore       = 40;     // Score minimum affiche (0-100)
input int      ConfirmBars    = 1;      // Tableau : setup confirme il y a N bougies max
input double   NearCandles    = 1.0;    // Tableau : ou prix a moins de N bougies (ATR) de l'entree
input bool     TableAutoShift = true;   // Decaler le graphique pour liberer la place du tableau
input int      TableFontSize  = 8;      // Taille du texte du tableau
input bool     ScanAlerts     = true;   // Alertes scanner (nouveau breaker / GO)
input double   TableMaxR      = 1.0;    // Tableau : prix a moins de x R de l'entree (sinon trop tard)
input double   GoMaxR         = 0.33;   // GO seulement si le prix n'a pas depasse l'entree de plus de x R
input string   TemplateName   = "";     // Modele applique au clic sur une ligne (ex: breaker.tpl)
input ENUM_BASE_CORNER TablePosition = CORNER_LEFT_UPPER; // Coin du tableau (si pas de fenetre BREAKER TABLE)
input int      TableX         = 8;      // Decalage X du tableau
input int      TableY         = 25;     // Decalage Y du tableau
input string   _s6            = "===== AFFICHAGE GRAPHIQUE =====";   // -----
input ENUM_DIR ChartDirection = DIR_BOTH; // Sens affiche sur ce graphique
input int      MaxHistoryBars = 1500;   // Bougies analysees sur ce graphique
input int      MaxSetupsDrawn = 6;      // Setups dessines
input bool     ShowDone       = false;  // Afficher aussi les setups termines (historique)
input int      KeepDoneBars   = 30;     // Un setup termine reste dessine encore N bougies
input bool     ShowOpenOB     = false;  // Afficher les OB encore actifs
input int      MaxActiveOB    = 4;      // OB actifs affiches (par sens)
input bool     ShowPanel      = true;   // Panneau "QUE FAIRE" du graphique
input ENUM_BASE_CORNER PanelCorner = CORNER_LEFT_LOWER; // Coin du panneau
input color    ColBreakerBuy  = C'0,90,50';    // Breaker achat
input color    ColBreakerSell = C'110,25,25';  // Breaker vente
input color    ColFVG         = C'25,55,120';  // FVG
input color    ColOBBuy       = C'0,45,25';    // OB achat actif
input color    ColOBSell      = C'65,15,15';   // OB vente actif
input color    ColDone        = C'55,55,55';   // Setup perdu / expire
input color    ColEntry       = clrGold;       // Ligne entree
input color    ColSL          = clrRed;        // Ligne stop
input color    ColTP          = clrLime;       // Lignes objectifs
input color    ColArrowBuy    = clrLime;       // Fleche achat
input color    ColArrowSell   = clrRed;        // Fleche vente
input string   _s7            = "===== ALERTES =====";               // -----
input bool     AlertOnBreaker = true;   // Graphique : breaker forme
input bool     AlertOnRetest  = true;   // Graphique : prix dans la zone d'entree
input bool     AlertOnExit    = true;   // Graphique : TP1 / TP2 / stop
input bool     AlertPopup     = true;   // Popup + son
input bool     AlertPush      = false;  // Notification mobile MT4
input bool     AlertMail      = false;  // Email

//==================================================================== STRUCTURES
struct OBZone
  {
   int      dir;        // -1 = OB VENTE (casse haussiere -> BREAKER ACHAT) / +1 = OB ACHAT
   double   hi, lo;
   double   mHi, mLo;   // corps de la bougie OB
   int      sh;
   datetime t;
   double   sweepRef;
   double   ext;
   int      shExt;
   bool     alive;
  };

struct SwingPt
  {
   int      dir;        // +1 = plus haut du marche (-> breaker ACHAT) / -1 = plus bas (-> breaker VENTE)
   int      sh;
   double   price;
   double   ref;        // creux (sommet) d'ou est parti le mouvement = liquidite a prendre
   double   ext;        // extreme atteint depuis le swing (avant la cassure)
   int      shExt;
   bool     alive;
  };

struct Setup
  {
   int      dir;
   double   bHi, bLo;
   double   mHi, mLo;
   bool     kz;
   datetime tOB;
   int      shBreak;
   datetime tBreak;
   double   ext;
   int      shExt;
   double   atr;
   double   disp;       // force de la bougie de cassure (x ATR)
   bool     hasFVG;
   double   fHi, fLo;
   datetime tF;
   bool     conf;
   bool     ready;
   int      shReady;
   double   entry, sl, slCur, tp1, tp2, rr;
   int      status;
   bool     tp1Hit;
   bool     departed;   // le prix est sorti de la zone (nouveau plus haut / plus bas) -> retest valable
   bool     touched;    // le prix est revenu dans l'OB (retest en cours)
   bool     flip;       // setup inverse : breaker rate -> la zone change de camp
   bool     flipped;    // un flip a deja ete cree depuis ce setup
   double   pd;         // position de l'entree dans le range du marche (0 = plus bas, 100 = plus haut)
   int      shTouch;
   int      shFill;
   datetime tEnd;
  };

struct Opp
  {
   string   sym;
   string   key;
   int      tf;
   int      dir;
   int      status;
   bool     conf;
   bool     hasFVG;
   double   entry, sl, tp1, tp2, rr, atr;
   double   base, score, dist, rank;
   bool     go, dead, fresh, near, departed, flip;
   double   zFar;
   int      readyAgo, fillAgo;
   datetime tBreak;
   int      digits;
   double   point;
  };

//==================================================================== GLOBALS
double   BufBuy[], BufSell[], BufDir[], BufEntry[], BufSL[], BufTP1[], BufTP2[], BufInBuy[], BufInSell[];
int      gShiftPct = -1;
bool     gTableCollapsed = false;
int      gTableWin = -1;
bool     gPanelCollapsed = false;

// moteur (travaille sur n'importe quel actif / TF)
double   E_O[], E_H[], E_L[], E_C[], E_A[];
datetime E_T[];
int      gN = 0, gTF = 0, gDig = 5, gDirFilter = 0;
double   gPt = 0.00001;
string   gSym = "";
bool     gForChart = false;
OBZone   gOB[];
int      gOBn = 0;
Setup    gS[];
int      gSn  = 0;
SwingPt  gSW[];
int      gSWn = 0;
int      gLastSH = -1, gLastSL = -1;

// resultats du graphique courant
Setup    cS[];
int      cSn = 0;

// scanner
string   gSyms[];
int      gTFs[];
Opp      gOpp[];
int      gOppN = 0;
int      gOrder[];
int      gOrderN = 0;
int      gLineOpp[];
int      gScanned = 0;
datetime gLastScan = 0;
bool     gScanInit = false;
uint     gLastLive = 0;

string   gKeys[];
datetime gLastBar = 0;
bool     gInit    = false;

//+------------------------------------------------------------------+
int OnInit()
  {
   IndicatorBuffers(9);
   SetIndexBuffer(0,BufBuy);
   SetIndexStyle(0,DRAW_ARROW,EMPTY,3,ColArrowBuy);
   SetIndexArrow(0,233);
   SetIndexLabel(0,"ACHAT confirme");
   SetIndexBuffer(1,BufSell);
   SetIndexStyle(1,DRAW_ARROW,EMPTY,3,ColArrowSell);
   SetIndexArrow(1,234);
   SetIndexLabel(1,"VENTE confirme");
   SetIndexBuffer(2,BufDir);
   SetIndexBuffer(3,BufEntry);
   SetIndexBuffer(4,BufSL);
   SetIndexBuffer(5,BufTP1);
   SetIndexBuffer(6,BufTP2);
   for(int b = 2; b < 7; b++)
      SetIndexStyle(b,DRAW_NONE);
   SetIndexLabel(2,"Signal entree (+1/-1)");
   SetIndexLabel(3,"Entree");
   SetIndexLabel(4,"Stop");
   SetIndexLabel(5,"TP1");
   SetIndexLabel(6,"TP2");
   SetIndexBuffer(7,BufInBuy);
   SetIndexStyle(7,DRAW_ARROW,EMPTY,2,ColArrowBuy);
   SetIndexArrow(7,159);
   SetIndexLabel(7,"Entree achat (retest)");
   SetIndexBuffer(8,BufInSell);
   SetIndexStyle(8,DRAW_ARROW,EMPTY,2,ColArrowSell);
   SetIndexArrow(8,159);
   SetIndexLabel(8,"Entree vente (retest)");
   for(int b = 0; b < 9; b++)
      SetIndexEmptyValue(b,EMPTY_VALUE);
   IndicatorShortName("Breaker FVG Sniper");
   IndicatorDigits(Digits);
   gLastBar  = 0;
   gInit     = false;
   gScanInit = false;
   if(UseScanner)
      EventSetTimer(MathMax(ScanSeconds,5));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
   DeleteByPrefix(PFX);
   DeleteByPrefix(PPFX);
   DeleteByPrefix(TPFX);
   ChartRedraw();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < SweepLookback + OBSearchBars + ATRPeriod + 60)
      return(0);

   if(prev_calculated == 0 || Time[0] != gLastBar)
     {
      gLastBar = Time[0];
      RecalcChart();
      DrawAll();
      gInit = true;
     }

   if(UseScanner && !gScanInit)
      RunScanner();

   LiveCheck();
   if(ShowPanel)
      DrawPanel();

   if(UseScanner && gScanInit && GetTickCount() - gLastLive > 1000)
     {
      gLastLive = GetTickCount();
      UpdateLive();
     }
   return(rates_total);
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   if(UseScanner)
      RunScanner();
  }

//+------------------------------------------------------------------+
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam)
  {
   if(id != CHARTEVENT_OBJECT_CLICK)
      return;
   if(sparam == TPFX + "0" || sparam == TPFX + "0_b")
     {
      gTableCollapsed = !gTableCollapsed;
      DrawTable();
      return;
     }
   if(sparam == PPFX + "0" || sparam == PPFX + "0_b")
     {
      gPanelCollapsed = !gPanelCollapsed;
      DrawPanel();
      ChartRedraw();
      return;
     }
   if(StringFind(sparam,TPFX) != 0)
      return;
   string idx = StringSubstr(sparam,StringLen(TPFX));
   if(idx == "BG")
      return;
   int line = (int)StringToInteger(idx);
   if(line < 0 || line >= ArraySize(gLineOpp))
      return;
   int o = gLineOpp[line];
   if(o < 0 || o >= gOppN)
      return;
   long cid = ChartOpen(gOpp[o].sym,gOpp[o].tf);
   if(cid > 0 && TemplateName != "")
      ChartApplyTemplate(cid,TemplateName);
  }

//==================================================================== CHARGEMENT DES DONNEES
bool LoadRates(string sym,int tf,int count)
  {
   MqlRates r[];
   ArraySetAsSeries(r,true);
   int got = CopyRates(sym,tf,0,count,r);
   if(got < SweepLookback + OBSearchBars + ATRPeriod + 60)
      return(false);

   gN = got;
   ArrayResize(E_O,got);
   ArrayResize(E_H,got);
   ArrayResize(E_L,got);
   ArrayResize(E_C,got);
   ArrayResize(E_A,got);
   ArrayResize(E_T,got);
   for(int i = 0; i < got; i++)
     {
      E_O[i] = r[i].open;
      E_H[i] = r[i].high;
      E_L[i] = r[i].low;
      E_C[i] = r[i].close;
      E_T[i] = r[i].time;
      E_A[i] = 0;
     }
   for(int i = got - ATRPeriod - 2; i >= 0; i--)
     {
      double s = 0;
      for(int j = i; j < i + ATRPeriod; j++)
         s += MathMax(E_H[j],E_C[j + 1]) - MathMin(E_L[j],E_C[j + 1]);
      E_A[i] = s / ATRPeriod;
     }

   gSym = sym;
   gTF  = tf;
   gDig = (int)MarketInfo(sym,MODE_DIGITS);
   gPt  = MarketInfo(sym,MODE_POINT);
   if(gPt <= 0)
     {
      gPt  = Point;
      gDig = Digits;
     }
   return(true);
  }

int LowestIdx(int start,int count)
  {
   int e = MathMin(gN - 1,start + count - 1);
   int b = start;
   for(int i = start; i <= e; i++)
      if(E_L[i] < E_L[b])
         b = i;
   return(b);
  }

int HighestIdx(int start,int count)
  {
   int e = MathMin(gN - 1,start + count - 1);
   int b = start;
   for(int i = start; i <= e; i++)
      if(E_H[i] > E_H[b])
         b = i;
   return(b);
  }

//==================================================================== MOTEUR
void RunEngine(int bars)
  {
   gOBn = 0;
   gSn  = 0;
   gSWn = 0;
   gLastSH = -1;
   gLastSL = -1;
   gHistN  = 0;
   int start = MathMin(bars,gN - MathMax(SweepLookback,SwingLeft) - OBSearchBars - ATRPeriod - SwingRight - 10);
   for(int i = start; i >= 1; i--)
     {
      double atr = E_A[i];
      if(atr <= 0)
         continue;
      UpdateSetups(i);       // 1. setups existants
      UpdateOBs(i,atr);      // 2. zones OB opposees (murs)
      DetectBreakers(i,atr); // 3. structure ICT : plus haut -> plus bas plus bas -> cassure franche = BREAKER
      DetectOB(i,atr);       // 4. nouvelles zones OB
      if(gOBn > 300)
         CompactOB();
     }
  }

void RecalcChart()
  {
   ArrayInitialize(BufBuy,EMPTY_VALUE);
   ArrayInitialize(BufSell,EMPTY_VALUE);
   ArrayInitialize(BufDir,EMPTY_VALUE);
   ArrayInitialize(BufEntry,EMPTY_VALUE);
   ArrayInitialize(BufSL,EMPTY_VALUE);
   ArrayInitialize(BufTP1,EMPTY_VALUE);
   ArrayInitialize(BufTP2,EMPTY_VALUE);
   ArrayInitialize(BufInBuy,EMPTY_VALUE);
   ArrayInitialize(BufInSell,EMPTY_VALUE);

   gGmtOff    = (int)(MathRound((TimeCurrent() - TimeGMT()) / 3600.0) * 3600);
   gForChart  = true;
   gDirFilter = (int)ChartDirection;
   int need = MathMin(Bars,MaxHistoryBars + MathMax(SweepLookback,SwingLeft) + OBSearchBars + ATRPeriod + MathMax(LiqLookback,RallyLB) + 30);
   if(LoadRates(Symbol(),Period(),need))
      RunEngine(MaxHistoryBars);
   else
     {
      gSn  = 0;
      gOBn = 0;
     }
   gForChart = false;

   cSn = gSn;
   ArrayResize(cS,MathMax(cSn,1));
   for(int k = 0; k < cSn; k++)
      cS[k] = gS[k];
  }

bool DirAllowed(int dir) { return(gDirFilter == 0 || gDirFilter == dir); }

//--- 3. Order Blocks : derniere bougie opposee avant une impulsion
void DetectOB(int i,double atr)
  {
   double body = E_C[i] - E_O[i];
   if(-body >= DispATR * atr)
      for(int j = i + 1; j <= i + OBSearchBars && j < gN - 1; j++)
         if(E_C[j] > E_O[j])
           {
            AddOB(-1,j,i);
            break;
           }
   if(body >= DispATR * atr)
      for(int j = i + 1; j <= i + OBSearchBars && j < gN - 1; j++)
         if(E_C[j] < E_O[j])
           {
            AddOB(1,j,i);
            break;
           }
  }

void AddOB(int dir,int j,int i)
  {
   for(int k = gOBn - 1; k >= 0 && k >= gOBn - 20; k--)
      if(gOB[k].sh == j && gOB[k].dir == dir)
         return;

   OBZone o;
   ZeroMemory(o);
   o.dir = dir;
   o.sh  = j;
   o.t   = E_T[j];
   o.hi  = OBUseWicks ? E_H[j] : MathMax(E_O[j],E_C[j]);
   o.lo  = OBUseWicks ? E_L[j] : MathMin(E_O[j],E_C[j]);
   o.mHi = MathMax(E_O[j],E_C[j]);
   o.mLo = MathMin(E_O[j],E_C[j]);

   // meilleur OB = celui qui part d'un PLUS HAUT (OB vente) ou d'un PLUS BAS (OB achat) du marche
   int zb = j + OBSearchBars;                        // petite tolerance autour de la bougie OB
   if(OBAtSwing && zb + 1 + SwingLB < gN)
     {
      if(dir < 0 && E_H[HighestIdx(i,zb - i + 1)] < E_H[HighestIdx(zb + 1,SwingLB)])
         return;
      if(dir > 0 && E_L[LowestIdx(i,zb - i + 1)] > E_L[LowestIdx(zb + 1,SwingLB)])
         return;
     }
   else
      if(OBAtSwing)
         return;

   if(dir < 0)
     {
      if(E_C[i] >= o.lo)
         return;
      o.sweepRef = E_L[LowestIdx(j + 1,SweepLookback)];
      int e = LowestIdx(i,j - i);
      o.ext = E_L[e];
      o.shExt = e;
     }
   else
     {
      if(E_C[i] <= o.hi)
         return;
      o.sweepRef = E_H[HighestIdx(j + 1,SweepLookback)];
      int e = HighestIdx(i,j - i);
      o.ext = E_H[e];
      o.shExt = e;
     }
   o.alive = true;
   if(gOBn >= ArraySize(gOB))
      ArrayResize(gOB,gOBn + 100);
   gOB[gOBn++] = o;
  }

void CompactOB()
  {
   int n = 0;
   for(int k = 0; k < gOBn; k++)
      if(gOB[k].alive)
        {
         if(n != k)
            gOB[n] = gOB[k];
         n++;
        }
   gOBn = n;
  }

//--- 2. Cassure : cloture au-dela + deplacement + liquidite prise = BREAKER
void UpdateOBs(int cur,double atr)
  {
   // les zones OB ne servent plus qu'a reperer les "murs" opposes (OppWall) et l'affichage
   for(int k = 0; k < gOBn; k++)
     {
      if(!gOB[k].alive)
         continue;
      if(gOB[k].sh - cur > OBMaxAge)
         gOB[k].alive = false;
      else
         if(gOB[k].dir < 0 && E_C[cur] > gOB[k].hi)
            gOB[k].alive = false;
         else
            if(gOB[k].dir > 0 && E_C[cur] < gOB[k].lo)
               gOB[k].alive = false;
     }
  }

//--- Breaker ICT :
//    ACHAT : creux 1 -> PLUS HAUT du marche (OB vente) -> PLUS BAS sous le creux 1 (liquidite)
//            -> 1ere cloture FRANCHE au-dessus du plus haut = BREAKER ACHAT. Vente = miroir.
void DetectBreakers(int cur,double atr)
  {
   int bestB = -1, bestS = -1;
   for(int k = 0; k < gSWn; k++)
     {
      if(!gSW[k].alive)
         continue;
      if(gSW[k].sh - cur > OBMaxAge)
        {
         gSW[k].alive = false;
         continue;
        }
      double rg = MathMax(E_H[cur] - E_L[cur],gPt);
      if(gSW[k].dir > 0)
        {
         if(E_C[cur] > gSW[k].price)                 // 1ere cloture au-dessus du plus haut
           {
            gSW[k].alive = false;
            bool swept   = !RequireSweep || gSW[k].ext < gSW[k].ref;   // liquidite prise AVANT la cassure
            bool franche = (E_C[cur] - E_O[cur]) >= BreakBodyATR * atr
                           && E_C[cur] - gSW[k].price >= BreakBeyondATR * atr
                           && (E_C[cur] - E_L[cur]) / rg >= BreakCloseRatio;
            if(swept && franche && (bestB < 0 || gSW[k].price > gSW[bestB].price))
               bestB = k;
           }
         else
            if(E_H[cur] > gSW[k].price + WickKillATR * atr) // le prix est reparti bien au-dessus sans cloture : nouveau plus haut
               gSW[k].alive = false;
            else
               if(E_L[cur] < gSW[k].ext)
                 {
                  gSW[k].ext = E_L[cur];
                  gSW[k].shExt = cur;
                 }
        }
      else
        {
         if(E_C[cur] < gSW[k].price)
           {
            gSW[k].alive = false;
            bool swept   = !RequireSweep || gSW[k].ext > gSW[k].ref;
            bool franche = (E_O[cur] - E_C[cur]) >= BreakBodyATR * atr
                           && gSW[k].price - E_C[cur] >= BreakBeyondATR * atr
                           && (E_H[cur] - E_C[cur]) / rg >= BreakCloseRatio;
            if(swept && franche && (bestS < 0 || gSW[k].price < gSW[bestS].price))
               bestS = k;
           }
         else
            if(E_L[cur] < gSW[k].price - WickKillATR * atr) // reparti bien en dessous sans cloture : nouveau plus bas
               gSW[k].alive = false;
            else
               if(E_H[cur] > gSW[k].ext)
                 {
                  gSW[k].ext = E_H[cur];
                  gSW[k].shExt = cur;
                 }
        }
     }
   if(bestB >= 0 && DirAllowed(1) && BiasOK(1,cur))
      BreakerFromSwing(bestB,cur,atr);
   if(bestS >= 0 && DirAllowed(-1) && BiasOK(-1,cur))
      BreakerFromSwing(bestS,cur,atr);

   // nouveaux plus hauts / plus bas confirmes (SwingRight bougies cloturees apres)
   int sp = cur + SwingRight;
   if(sp + SwingLeft + 1 >= gN)
      return;
   if(E_H[sp] > E_H[HighestIdx(sp + 1,SwingLeft)] && E_H[sp] >= E_H[HighestIdx(cur,SwingRight)])
     {
      // creux 1 = le plus bas entre le dernier plus bas du marche (swing) et ce plus haut
      int opp = LastSwingBefore(-1,sp);
      int span = (opp > sp && opp - sp <= RallyLB) ? opp - sp : SwingLeft * 2;
      span = MathMax(1,MathMin(span,gN - sp - 2));
      double ref = E_L[LowestIdx(sp + 1,span)];
      if(E_H[sp] - ref >= MinLegATR * atr)             // vraie hausse, pas un petit rebond
        {
         SwingPt w;
         ZeroMemory(w);
         w.dir   = 1;
         w.sh    = sp;
         w.price = E_H[sp];
         w.ref   = ref;
         int e   = LowestIdx(cur,SwingRight);
         w.ext   = E_L[e];
         w.shExt = e;
         w.alive = true;
         AddSwing(w);
        }
      PushHist(1,sp);
     }
   if(E_L[sp] < E_L[LowestIdx(sp + 1,SwingLeft)] && E_L[sp] <= E_L[LowestIdx(cur,SwingRight)])
     {
      int opp = LastSwingBefore(1,sp);
      int span = (opp > sp && opp - sp <= RallyLB) ? opp - sp : SwingLeft * 2;
      span = MathMax(1,MathMin(span,gN - sp - 2));
      double ref = E_H[HighestIdx(sp + 1,span)];
      if(ref - E_L[sp] >= MinLegATR * atr)
        {
         SwingPt w;
         ZeroMemory(w);
         w.dir   = -1;
         w.sh    = sp;
         w.price = E_L[sp];
         w.ref   = ref;
         int e   = HighestIdx(cur,SwingRight);
         w.ext   = E_H[e];
         w.shExt = e;
         w.alive = true;
         AddSwing(w);
        }
      PushHist(-1,sp);
     }
  }

//--- historique des swings confirmes (pour retrouver le swing oppose precedent)
int gHistDir[], gHistSh[];
int gHistN = 0;
void PushHist(int dir,int sh)
  {
   if(gHistN >= ArraySize(gHistSh))
     {
      ArrayResize(gHistSh,gHistN + 200);
      ArrayResize(gHistDir,gHistN + 200);
     }
   gHistDir[gHistN] = dir;
   gHistSh[gHistN]  = sh;
   gHistN++;
  }

int LastSwingBefore(int dir,int sh)
  {
   for(int i = gHistN - 1; i >= 0; i--)
      if(gHistDir[i] == dir && gHistSh[i] > sh)
         return(gHistSh[i]);
   return(-1);
  }

void AddSwing(SwingPt &w)
  {
   if(gSWn > 200)                                     // compactage
     {
      int n = 0;
      for(int k = 0; k < gSWn; k++)
         if(gSW[k].alive)
           {
            if(n != k)
               gSW[n] = gSW[k];
            n++;
           }
      gSWn = n;
     }
   if(gSWn >= ArraySize(gSW))
      ArrayResize(gSW,gSWn + 100);
   gSW[gSWn++] = w;
  }

//--- l'OB = derniere bougie haussiere au plus haut (achat) / baissiere au plus bas (vente)
void BreakerFromSwing(int k,int cur,double atr)
  {
   int d  = gSW[k].dir;                              // plus haut casse -> ACHAT (+1), plus bas casse -> VENTE (-1)
   int sp = gSW[k].sh;
   int j  = -1;
   for(int b = sp; b <= sp + OBSearchBars && b < gN - 1; b++)
      if((d > 0 && E_C[b] > E_O[b]) || (d < 0 && E_C[b] < E_O[b]))
        {
         j = b;
         break;
        }
   // l'OB doit etre AU plus haut (plus bas) : sinon on prend la bougie du swing elle-meme
   if(j < 0 || (d > 0 && E_H[j] < gSW[k].price - 0.5 * atr) || (d < 0 && E_L[j] > gSW[k].price + 0.5 * atr))
      j = sp;
   NewSetup(d,j,gSW[k].ext,gSW[k].shExt,cur,atr);
  }

void NewSetup(int d,int j,double ext,int shExt,int cur,double atr)
  {
   Setup s;
   ZeroMemory(s);
   s.dir     = d;
   s.bHi     = OBUseWicks ? E_H[j] : MathMax(E_O[j],E_C[j]);
   s.bLo     = OBUseWicks ? E_L[j] : MathMin(E_O[j],E_C[j]);
   s.mHi     = MathMax(E_O[j],E_C[j]);
   s.mLo     = MathMin(E_O[j],E_C[j]);
   s.kz      = InKillzone(E_T[cur]);
   s.tOB     = E_T[j];
   s.shBreak = cur;
   s.tBreak  = E_T[cur];
   s.ext     = ext;
   s.shExt   = shExt;
   s.atr     = atr;
   s.disp    = MathAbs(E_C[cur] - E_O[cur]) / atr;
   s.status  = ST_WAIT;
   s.shFill  = -1;
   s.shReady = -1;

   // pas de fleche sur la bougie de cassure : on attend la bougie suivante pour valider le FVG de l'impulsion
   if(gSn >= ArraySize(gS))
      ArrayResize(gS,gSn + 50);
   gS[gSn++] = s;
  }

//--- FVG de l'impulsion (priorite : chevauche le breaker, sinon le plus proche)
void FindFVG(Setup &s,int cur)
  {
   int newest = cur + 1;
   int oldest = MathMin(s.shExt,gN - 3);
   double minGap = MinFVG_ATR * s.atr;
   bool found = false, bestConf = false;
   double bestDist = 0, fh = 0, fl = 0;
   datetime ft = 0;

   for(int k = newest; k <= oldest; k++)
     {
      double lo, hi;
      if(s.dir > 0)
        {
         lo = E_H[k + 1];
         hi = E_L[k - 1];
        }
      else
        {
         lo = E_H[k - 1];
         hi = E_L[k + 1];
        }
      if(hi <= lo || hi - lo < minGap)
         continue;

      bool filled = false;
      for(int m = k - 2; m >= cur; m--)
        {
         if(s.dir > 0 && E_L[m] <= lo)
           {
            filled = true;
            break;
           }
         if(s.dir < 0 && E_H[m] >= hi)
           {
            filled = true;
            break;
           }
        }
      if(filled)
         continue;

      bool conf = MathMin(hi,s.bHi) > MathMax(lo,s.bLo);
      double dist = 0;
      if(!conf)
         dist = (lo > s.bHi) ? lo - s.bHi : s.bLo - hi;
      if(!found || (conf && !bestConf) || (conf == bestConf && dist < bestDist))
        {
         found = true;
         bestConf = conf;
         bestDist = dist;
         fh = hi;
         fl = lo;
         ft = E_T[k + 1];
        }
     }
   if(found)
     {
      s.hasFVG = true;
      s.fHi = fh;
      s.fLo = fl;
      s.tF  = ft;
     }
  }

//--- Entree / stop / objectifs
bool ComputeLevels(Setup &s)
  {
   double e;
   bool conf = false;
   if(s.hasFVG)
     {
      double oLo = MathMax(s.fLo,s.bLo), oHi = MathMin(s.fHi,s.bHi);
      conf = (oHi > oLo);
      if(conf)
         e = (oLo + oHi) / 2.0;
      else
         if(EntryType == ENTRY_FVG50)
            e = (s.fHi + s.fLo) / 2.0;
         else
            if(EntryType == ENTRY_FVGEDGE)
               e = (s.dir > 0) ? s.fHi : s.fLo;
            else
               if(EntryType == ENTRY_MEAN)
                  e = (s.mHi + s.mLo) / 2.0;
               else
                  e = (s.dir > 0) ? s.bHi : s.bLo;
     }
   else
      e = (EntryType == ENTRY_MEAN) ? (s.mHi + s.mLo) / 2.0 : ((s.dir > 0) ? s.bHi : s.bLo);

   double buf = SLBufferATR * s.atr, sl;
   bool sweepSL = (StopType == SL_SWEEP) || (StopType == SL_AUTO && gTF >= PERIOD_H1);
   if(s.dir > 0)
     {
      double base = s.bLo;
      if(s.hasFVG)
         base = MathMin(base,s.fLo);
      sl = base - buf;
      if(sweepSL && e - (s.ext - buf) <= StopMaxATR * s.atr)
         sl = MathMin(sl,s.ext - buf);          // sous la meche de la prise de liquidite
     }
   else
     {
      double base = s.bHi;
      if(s.hasFVG)
         base = MathMax(base,s.fHi);
      sl = base + buf;
      if(sweepSL && (s.ext + buf) - e <= StopMaxATR * s.atr)
         sl = MathMax(sl,s.ext + buf);
     }

   double minRisk = StopMinATR * s.atr;
   if(MathAbs(e - sl) < minRisk)
      sl = e - s.dir * minRisk;
   double risk = MathAbs(e - sl);
   if(risk < gPt * 5)
      return(false);

   double tp1 = e + s.dir * TP1_R * risk;
   double tp2 = e + s.dir * TP2_R * risk;
   if(TP2OnLiquidity)
     {
      int cnt = MathMin(LiqLookback,gN - s.shBreak - 2);
      if(cnt > 0)
        {
         if(s.dir > 0)
           {
            double liq = E_H[HighestIdx(s.shBreak,cnt)];
            if(liq > tp1 && liq <= e + TPMaxR * risk)
               tp2 = liq;
           }
         else
           {
            double liq = E_L[LowestIdx(s.shBreak,cnt)];
            if(liq < tp1 && liq >= e - TPMaxR * risk)
               tp2 = liq;
           }
        }
     }
   if(s.dir * (tp2 - tp1) < 0)
      tp2 = tp1;

   double rr = MathAbs(tp2 - e) / risk;
   if(rr < MinRR)
      return(false);

   s.conf  = conf;
   s.entry = NormalizeDouble(e,gDig);
   s.sl    = NormalizeDouble(sl,gDig);
   s.slCur = s.sl;
   s.tp1   = NormalizeDouble(tp1,gDig);
   s.tp2   = NormalizeDouble(tp2,gDig);
   s.rr    = rr;
   return(true);
  }

//--- 1. Gestion : attente retest -> position -> TP / stop
void UpdateSetups(int cur)
  {
   for(int k = 0; k < gSn; k++)
     {
      int st = gS[k].status;
      if(st != ST_WAIT && st != ST_LIVE)
         continue;
      if(gS[k].shBreak <= cur)
         continue;
      int since = gS[k].shBreak - cur;
      int d = gS[k].dir;

      if(st == ST_WAIT)
        {
         if(since <= FVGConfirmBars && !gS[k].ready)
           {
            // le FVG de la bougie de cassure se confirme a la bougie suivante (niveaux figes une fois la fleche affichee)
            bool   hadF = gS[k].hasFVG;
            double oH = gS[k].fHi, oL = gS[k].fLo;
            datetime oT = gS[k].tF;
            FindFVG(gS[k],cur);
            if(gS[k].hasFVG || !RequireFVG)
              {
               if(ComputeLevels(gS[k]))
                 {
                  if(!gS[k].ready)
                     MakeReady(gS[k],cur);
                 }
               else
                  if(hadF)
                    {
                     gS[k].fHi = oH;
                     gS[k].fLo = oL;
                     gS[k].tF  = oT;
                    }
              }
           }
         if(gS[k].status == ST_REJECT)
            continue;
         if(!gS[k].ready)
           {
            if(since >= FVGConfirmBars)
               gS[k].status = ST_REJECT;
            continue;
           }
         // ICT : cassure -> le prix PART de la zone (nouveau plus haut) -> il REVIENT -> entree.
         // Tant que le prix n'a pas quitte la zone, pas d'entree.
         if(!gS[k].departed)
           {
            double rk = MathAbs(gS[k].entry - gS[k].sl);
            bool dep = (d > 0) ? (E_L[cur] > gS[k].bHi || E_H[cur] >= gS[k].entry + rk)
                               : (E_H[cur] < gS[k].bLo || E_L[cur] <= gS[k].entry - rk);
            if(dep)
               gS[k].departed = true;
            if((d > 0) ? E_H[cur] >= gS[k].tp2 : E_L[cur] <= gS[k].tp2)
              {
               CloseSetup(k,ST_MISSED,cur);             // objectif atteint sans retest = trade rate
               continue;
              }
            if((d > 0 && E_C[cur] < gS[k].ext) || (d < 0 && E_C[cur] > gS[k].ext))
               CloseSetup(k,ST_CANCEL,cur);
            else
               if(since > MaxBarsWait)
                  CloseSetup(k,ST_EXPIRED,cur);
            continue;
           }
         if(gS[k].shReady == cur)
            continue;

         // ===== ENTREE AVEC CONFIRMATION : retour dans l'OB puis bougie qui repart dans le sens du breaker
         if(EntryTrigger == TRIG_CONFIRM)
           {
            bool inZone = (d > 0) ? E_L[cur] <= gS[k].entry : E_H[cur] >= gS[k].entry;
            if(!gS[k].touched && inZone)
              {
               gS[k].touched = true;
               gS[k].shTouch = cur;
              }
            if(gS[k].touched)
              {
               // l'OB ne tient pas : cloture de l'autre cote de la zone ou stop touche -> breaker rate
               double far = ZoneFar(k);
               if((d > 0) ? (E_C[cur] < far || E_L[cur] <= gS[k].sl) : (E_C[cur] > far || E_H[cur] >= gS[k].sl))
                 {
                  bool beyond = (d > 0) ? E_C[cur] < far : E_C[cur] > far;
                  CloseSetup(k,ST_CANCEL,cur);
                  if(beyond)
                     CreateFlip(k,cur);
                  continue;
                 }
               bool conf = (d > 0) ? (E_C[cur] > E_O[cur] && E_C[cur] > gS[k].entry)
                                   : (E_C[cur] < E_O[cur] && E_C[cur] < gS[k].entry);
               // limite : ConfirmMaxR x risque, mais jamais moins d'1/2 ATR (sinon impossible en M1/M5)
               double maxDist = MathMax(ConfirmMaxR * MathAbs(gS[k].entry - gS[k].sl),ConfirmMaxATR * gS[k].atr);
               if(conf && MathAbs(E_C[cur] - gS[k].entry) > maxDist)
                 {
                  CloseSetup(k,ST_TOOFAR,cur);                // confirmation trop loin de l'OB : risque trop grand
                  continue;
                 }
               if(conf)
                 {
                  // achat a la cloture de la bougie de confirmation, stop du setup, objectifs recalcules
                  gS[k].entry = NormalizeDouble(E_C[cur],gDig);
                  double rk = MathAbs(gS[k].entry - gS[k].sl);
                  gS[k].tp1 = NormalizeDouble(gS[k].entry + d * TP1_R * rk,gDig);
                  double t2 = gS[k].entry + d * TP2_R * rk;
                  if(d * (gS[k].tp2 - t2) < 0)
                     gS[k].tp2 = NormalizeDouble(t2,gDig);
                  gS[k].rr = MathAbs(gS[k].tp2 - gS[k].entry) / MathMax(rk,gPt);
                  gS[k].status = ST_LIVE;
                  gS[k].shFill = cur;
                  MarkFill(k,cur);
                  if(AlertOnRetest)
                     ChartNotify("F" + SKey(gS[k]),(d > 0 ? "ACHAT CONFIRME" : "VENTE CONFIRMEE") + " apres retest de l'OB | Entree " +
                                 Px(gS[k].entry) + " Stop " + Px(gS[k].sl) + " TP1 " + Px(gS[k].tp1) + " TP2 " + Px(gS[k].tp2),cur);
                  continue;
                 }
               if(gS[k].shTouch - cur >= ConfirmMaxBars)
                  CloseSetup(k,ST_EXPIRED,cur);
               continue;
              }
           }

         bool touch = (EntryTrigger == TRIG_LIMIT) && ((d > 0) ? E_L[cur] <= gS[k].entry : E_H[cur] >= gS[k].entry);
         if(touch)
           {
            gS[k].status = ST_LIVE;
            gS[k].shFill = cur;
            MarkFill(k,cur);
            if(AlertOnRetest)
               ChartNotify("F" + SKey(gS[k]),(d > 0 ? "ACHAT" : "VENTE") + " declenche au retest | Entree " +
                           Px(gS[k].entry) + " Stop " + Px(gS[k].sl) + " TP1 " + Px(gS[k].tp1) + " TP2 " + Px(gS[k].tp2),cur);
            bool hit = (d > 0) ? E_L[cur] <= gS[k].sl : E_H[cur] >= gS[k].sl;
            if(hit)
               CloseSetup(k,ST_SL,cur);
            continue;
           }
         // pas de retest et objectif deja atteint : trop tard, le setup est clos
         if((d > 0) ? E_H[cur] >= gS[k].tp2 : E_L[cur] <= gS[k].tp2)
           {
            CloseSetup(k,ST_MISSED,cur);
            continue;
           }
         if((d > 0 && E_C[cur] < gS[k].ext) || (d < 0 && E_C[cur] > gS[k].ext))
            CloseSetup(k,ST_CANCEL,cur);
         else
            if(since > MaxBarsWait)
               CloseSetup(k,ST_EXPIRED,cur);
         continue;
        }

      // ---- EN POSITION
      bool hitSL = (d > 0) ? E_L[cur] <= gS[k].slCur : E_H[cur] >= gS[k].slCur;
      if(hitSL)
        {
         bool wasTP1 = gS[k].tp1Hit;
         CloseSetup(k,wasTP1 ? ST_BE : ST_SL,cur);
         // stop touche ET cloture de l'autre cote de l'OB : le breaker a rate -> la zone change de camp
         if(!wasTP1 && ((d > 0) ? E_C[cur] < ZoneFar(k) : E_C[cur] > ZoneFar(k)))
            CreateFlip(k,cur);
         continue;
        }
      bool hitTP2 = (d > 0) ? E_H[cur] >= gS[k].tp2 : E_L[cur] <= gS[k].tp2;
      if(hitTP2)
        {
         CloseSetup(k,ST_TP2,cur);
         continue;
        }
      if(!gS[k].tp1Hit)
        {
         bool h1 = (d > 0) ? E_H[cur] >= gS[k].tp1 : E_L[cur] <= gS[k].tp1;
         if(h1)
           {
            gS[k].tp1Hit = true;
            if(MoveBEAtTP1)
               gS[k].slCur = gS[k].entry;
            if(AlertOnExit)
               ChartNotify("1" + SKey(gS[k]),"TP1 atteint " + Px(gS[k].tp1) +
                           (MoveBEAtTP1 ? " -> stop au point d'entree" : ""),cur);
           }
        }
     }
  }

//--- zone OB opposee intacte juste devant l'entree = mur -> setup rejete
bool OppWall(Setup &s)
  {
   double risk = MathAbs(s.entry - s.sl);
   for(int k = 0; k < gOBn; k++)
     {
      if(!gOB[k].alive || gOB[k].dir != -s.dir)   // OB vente pour un achat, OB achat pour une vente
         continue;
      if(s.dir > 0 && gOB[k].lo > s.entry && gOB[k].lo - s.entry < OppWallR * risk)
         return(true);
      if(s.dir < 0 && gOB[k].hi < s.entry && s.entry - gOB[k].hi < OppWallR * risk)
         return(true);
     }
   return(false);
  }

void ExtendTP2(int k,int cur)
  {
   int d = gS[k].dir;
   double rk = MathMax(MathAbs(gS[k].entry - gS[k].sl),gPt);
   if(d > 0 && E_H[cur] > gS[k].tp2)
      gS[k].tp2 = MathMin(E_H[cur],gS[k].entry + TPMaxR * rk);
   if(d < 0 && E_L[cur] < gS[k].tp2)
      gS[k].tp2 = MathMax(E_L[cur],gS[k].entry - TPMaxR * rk);
   gS[k].rr = MathAbs(gS[k].tp2 - gS[k].entry) / rk;
  }

//--- cote oppose de la zone (OB + FVG) : une cloture au-dela = breaker rate
double ZoneFar(int k)
  {
   if(gS[k].dir > 0)
      return(gS[k].hasFVG ? MathMin(gS[k].bLo,gS[k].fLo) : gS[k].bLo);
   return(gS[k].hasFVG ? MathMax(gS[k].bHi,gS[k].fHi) : gS[k].bHi);
  }

//--- BREAKER RATE -> FLIP : l'OB achat casse par le bas devient une resistance (vente au retest), et inversement
void CreateFlip(int k,int cur)
  {
   if(!UseFlip || gS[k].flip || gS[k].flipped || !DirAllowed(-gS[k].dir))
      return;
   gS[k].flipped = true;
   double atr = E_A[cur];
   if(atr <= 0)
      return;
   Setup s;
   ZeroMemory(s);
   s.dir     = -gS[k].dir;
   s.flip    = true;
   s.bHi     = gS[k].bHi;
   s.bLo     = gS[k].bLo;
   s.mHi     = gS[k].mHi;
   s.mLo     = gS[k].mLo;
   s.tOB     = gS[k].tOB;
   s.shBreak = cur;
   s.tBreak  = E_T[cur];
   s.atr     = atr;
   s.disp    = MathAbs(E_C[cur] - E_O[cur]) / atr;
   s.kz      = InKillzone(E_T[cur]);
   s.status  = ST_WAIT;
   s.shFill  = -1;
   // invalidation du flip = le plus haut (plus bas) atteint depuis la cassure d'origine
   int cnt = MathMax(1,gS[k].shBreak - cur + 1);
   if(s.dir < 0)
     {
      int e = HighestIdx(cur,cnt);
      s.ext = E_H[e];
      s.shExt = e;
      s.entry = s.bLo;                                // retest de l'OB par en dessous
      s.sl    = s.bHi + SLBufferATR * atr;
      if(s.sl - s.entry < StopMinATR * atr)
         s.sl = s.entry + StopMinATR * atr;
     }
   else
     {
      int e = LowestIdx(cur,cnt);
      s.ext = E_L[e];
      s.shExt = e;
      s.entry = s.bHi;
      s.sl    = s.bLo - SLBufferATR * atr;
      if(s.entry - s.sl < StopMinATR * atr)
         s.sl = s.entry - StopMinATR * atr;
     }
   double rk = MathAbs(s.entry - s.sl);
   s.tp1 = s.entry + s.dir * TP1_R * rk;
   s.tp2 = s.entry + s.dir * TP2_R * rk;
   // objectif = la liquidite sous (sur) l'ancien stop du breaker rate, si elle est assez loin
   double liq = gS[k].sl;
   bool taken = (s.dir < 0) ? E_L[cur] <= liq : E_H[cur] >= liq;   // deja prise sur la bougie d'echec ?
   if(!taken && s.dir * (liq - s.entry) >= TP1_R * rk && s.dir * (liq - s.entry) <= TPMaxR * rk)
      s.tp2 = liq;
   if(s.dir * (s.tp2 - s.tp1) < 0)
      s.tp1 = (s.entry + s.tp2) / 2.0;
   s.entry = NormalizeDouble(s.entry,gDig);
   s.sl    = NormalizeDouble(s.sl,gDig);
   s.slCur = s.sl;
   s.tp1   = NormalizeDouble(s.tp1,gDig);
   s.tp2   = NormalizeDouble(s.tp2,gDig);
   s.rr    = MathAbs(s.tp2 - s.entry) / MathMax(rk,gPt);
   s.departed = true;                                // le prix vient deja de sortir de la zone
   s.pd = RangePos(s.entry,cur);
   if(!PDOk(s.dir,s.pd))
      return;
   s.ready    = true;
   OnReady(s,cur);
   if(gSn >= ArraySize(gS))
      ArrayResize(gS,gSn + 50);
   gS[gSn++] = s;
  }

//--- position de l'entree dans le range (0 % = plus bas du marche, 100 % = plus haut)
double RangePos(double price,int cur)
  {
   int cnt = MathMax(10,MathMin(PDLookback,gN - cur - 2));
   double hi = E_H[HighestIdx(cur,cnt)], lo = E_L[LowestIdx(cur,cnt)];
   if(hi - lo <= gPt)
      return(50.0);
   return(100.0 * (price - lo) / (hi - lo));
  }

bool PDOk(int dir,double pos)
  {
   if(!UsePremiumDiscount)
      return(true);
   return(dir > 0 ? pos <= PDMaxPct : pos >= 100.0 - PDMaxPct);
  }

void MakeReady(Setup &s,int cur)
  {
   s.pd = RangePos(s.entry,cur);
   if(!PDOk(s.dir,s.pd))
     {
      s.status = ST_REJECT;                          // achat dans le haut / vente dans le bas du marche : refuse
      return;
     }
   if(RejectOppWall && OppWall(s))
     {
      s.status = ST_REJECT;
      return;
     }
   // depart deja fait entre la cassure et la confirmation ?
   double rk = MathAbs(s.entry - s.sl);
   for(int b = s.shBreak - 1; b > cur; b--)
     {
      if(s.dir > 0 ? (E_L[b] > s.bHi || E_H[b] >= s.entry + rk) : (E_H[b] < s.bLo || E_L[b] <= s.entry - rk))
         s.departed = true;
      if(s.dir > 0 ? E_H[b] >= s.tp2 : E_L[b] <= s.tp2)
        {
         s.status = ST_MISSED;                        // objectif deja atteint avant la confirmation
         s.tEnd   = E_T[b];
         return;
        }
     }
   s.ready = true;
   OnReady(s,cur);
  }

//--- sessions Londres / New York (heures GMT)
int gGmtOff = 0;
bool InKillzone(datetime t)
  {
   int h = TimeHour(t - gGmtOff);
   return((h >= 6 && h < 10) || (h >= 11 && h < 15));
  }

void OnReady(Setup &s,int cur)
  {
   s.shReady = cur;
   if(gForChart && cur < ArraySize(BufBuy))           // fleche au moment de la confirmation
     {
      if(s.dir > 0)
         BufBuy[cur] = E_L[cur] - 0.4 * s.atr;
      else
         BufSell[cur] = E_H[cur] + 0.4 * s.atr;
     }
   if(AlertOnBreaker)
      ChartNotify("R" + SKey(s),(s.flip ? (s.dir > 0 ? "FLIP ACHAT (breaker vente rate)" : "FLIP VENTE (breaker achat rate)")
                                        : (s.dir > 0 ? "BREAKER ACHAT" : "BREAKER VENTE")) + (s.conf ? " + FVG" : "") +
                  " forme -> attendre le retest | Entree " + Px(s.entry) + " Stop " + Px(s.sl) +
                  " TP1 " + Px(s.tp1) + " TP2 " + Px(s.tp2),cur);
  }

void CloseSetup(int k,int st,int cur)
  {
   gS[k].status = st;
   gS[k].tEnd   = E_T[cur];
   if(AlertOnExit && st != ST_EXPIRED && st != ST_MISSED && st != ST_TOOFAR)
      ChartNotify("X" + SKey(gS[k]),StatusText(gS[k]),cur);
  }

void MarkFill(int k,int cur)
  {
   if(!gForChart || cur >= ArraySize(BufBuy))
      return;
   if(gS[k].dir > 0)
      BufInBuy[cur] = gS[k].entry;
   else
      BufInSell[cur] = gS[k].entry;
   BufDir[cur]   = gS[k].dir;
   BufEntry[cur] = gS[k].entry;
   BufSL[cur]    = gS[k].sl;
   BufTP1[cur]   = gS[k].tp1;
   BufTP2[cur]   = gS[k].tp2;
  }

//--- Tendance HTF automatique (bougie HTF cloturee = pas de repaint)
int HTF(int tf)
  {
   if(tf <= PERIOD_M5)
      return(PERIOD_H1);
   if(tf <= PERIOD_M30)
      return(PERIOD_H4);
   if(tf <= PERIOD_H4)
      return(PERIOD_D1);
   return(PERIOD_W1);
  }

bool BiasOK(int dir,int i)
  {
   if(!FilterHTFTrend)
      return(true);
   int htf = HTF(gTF);
   int sh = iBarShift(gSym,htf,E_T[i],false);
   if(sh < 0)
      return(true);
   sh++;
   double c = iClose(gSym,htf,sh);
   double e = iMA(gSym,htf,BiasEMA,0,MODE_EMA,PRICE_CLOSE,sh);
   if(c <= 0 || e <= 0)
      return(true);
   return(dir > 0 ? c > e : c < e);
  }

int BiasNowFor(string sym,int tf)
  {
   int htf = HTF(tf);
   double c = iClose(sym,htf,1);
   double e = iMA(sym,htf,BiasEMA,0,MODE_EMA,PRICE_CLOSE,1);
   if(c <= 0 || e <= 0)
      return(0);
   return(c > e ? 1 : -1);
  }

//==================================================================== ALERTES
void ChartNotify(string key,string msg,int cur)
  {
   if(!gForChart || cur != 1)
      return;
   Notify("C" + key,Symbol() + " " + TFName(Period()) + " | " + msg,!gInit);
  }

void LiveCheck()
  {
   if(!gInit || !AlertOnRetest)
      return;
   for(int k = cSn - 1; k >= 0 && k >= cSn - 30; k--)
     {
      if(cS[k].status != ST_WAIT || !cS[k].ready || !cS[k].departed)
         continue;
      bool touch = InEntryZone(cS[k].dir,Bid,cS[k].entry,cS[k].sl);
      if(touch)
         Notify("CF" + SKey(cS[k]),Symbol() + " " + TFName(Period()) + " | " +
                (EntryTrigger == TRIG_CONFIRM ? "PRIX DANS L'OB - attendre la bougie de confirmation" : (cS[k].dir > 0 ? "GO ACHAT" : "GO VENTE")) + " | Zone " +
                DoubleToString(cS[k].entry,Digits) + " Stop " + DoubleToString(cS[k].sl,Digits) +
                " TP1 " + DoubleToString(cS[k].tp1,Digits) + " TP2 " + DoubleToString(cS[k].tp2,Digits),false);
     }
  }

void Notify(string key,string msg,bool silent)
  {
   int n = ArraySize(gKeys);
   for(int i = 0; i < n; i++)
      if(gKeys[i] == key)
         return;
   if(n > 2000)
     {
      ArrayCopy(gKeys,gKeys,0,1000);
      ArrayResize(gKeys,n - 1000);
      n = n - 1000;
     }
   ArrayResize(gKeys,n + 1);
   gKeys[n] = key;
   if(silent)
      return;
   if(AlertPopup)
      Alert(msg);
   if(AlertPush)
      SendNotification(msg);
   if(AlertMail)
      SendMail("Breaker FVG Sniper",msg);
  }

//==================================================================== SCANNER
int TFFromName(string s)
  {
   StringToUpper(s);
   if(s == "M1")  return(PERIOD_M1);
   if(s == "M5")  return(PERIOD_M5);
   if(s == "M15") return(PERIOD_M15);
   if(s == "M30") return(PERIOD_M30);
   if(s == "H1")  return(PERIOD_H1);
   if(s == "H4")  return(PERIOD_H4);
   if(s == "D1")  return(PERIOD_D1);
   if(s == "W1")  return(PERIOD_W1);
   return(0);
  }

void BuildScanLists()
  {
   ArrayResize(gSyms,0);
   if(StringLen(ScanSymbols) > 0)
     {
      string p[];
      int n = StringSplit(ScanSymbols,',',p);
      for(int i = 0; i < n; i++)
        {
         string s = StringTrimLeft(StringTrimRight(p[i]));
         if(s == "")
            continue;
         SymbolSelect(s,true);
         int m = ArraySize(gSyms);
         ArrayResize(gSyms,m + 1);
         gSyms[m] = s;
        }
     }
   else
     {
      int n = SymbolsTotal(true);
      ArrayResize(gSyms,n);
      for(int i = 0; i < n; i++)
         gSyms[i] = SymbolName(i,true);
     }

   ArrayResize(gTFs,0);
   string t[];
   int nt = StringSplit(ScanTFs,',',t);
   for(int i = 0; i < nt; i++)
     {
      int tf = TFFromName(StringTrimLeft(StringTrimRight(t[i])));
      if(tf <= 0)
         continue;
      int m = ArraySize(gTFs);
      ArrayResize(gTFs,m + 1);
      gTFs[m] = tf;
     }
  }

int TFWeight(int tf)
  {
   switch(tf)
     {
      case PERIOD_M1:  return(0);
      case PERIOD_M5:  return(2);
      case PERIOD_M15: return(4);
      case PERIOD_M30: return(6);
      case PERIOD_H1:  return(8);
     }
   return(10);
  }

void RunScanner()
  {
   gGmtOff = (int)(MathRound((TimeCurrent() - TimeGMT()) / 3600.0) * 3600);
   BuildScanLists();
   gOppN = 0;
   gScanned = 0;
   int ns = ArraySize(gSyms), nt = ArraySize(gTFs);
   int need = ScanBars + MathMax(SweepLookback,SwingLeft) + OBSearchBars + ATRPeriod + MathMax(LiqLookback,RallyLB) + 30;

   for(int a = 0; a < ns; a++)
      for(int b = 0; b < nt; b++)
        {
         gForChart  = false;
         gDirFilter = (int)ScanDirection;
         if(!LoadRates(gSyms[a],gTFs[b],need))
            continue;
         gScanned++;
         RunEngine(ScanBars);

         bool tookB = false, tookS = false;
         for(int k = gSn - 1; k >= 0; k--)
           {
            bool active = (gS[k].status == ST_WAIT && gS[k].ready) || (gS[k].status == ST_LIVE && !gS[k].tp1Hit);
            if(!active)
               continue;
            if(gS[k].dir > 0)
              {
               if(tookB)
                  continue;
               tookB = true;
              }
            else
              {
               if(tookS)
                  continue;
               tookS = true;
              }
            AddOpp(k);
           }
        }
   gLastScan = TimeLocal();
   UpdateLive();
   gScanInit = true;
  }

void AddOpp(int k)
  {
   if(gOppN >= ArraySize(gOpp))
      ArrayResize(gOpp,gOppN + 50);
   int n = gOppN++;
   gOpp[n].sym    = gSym;
   gOpp[n].tf     = gTF;
   gOpp[n].dir    = gS[k].dir;
   gOpp[n].status = gS[k].status;
   gOpp[n].conf   = gS[k].conf;
   gOpp[n].hasFVG = gS[k].hasFVG;
   gOpp[n].entry  = gS[k].entry;
   gOpp[n].sl     = gS[k].sl;
   gOpp[n].tp1    = gS[k].tp1;
   gOpp[n].tp2    = gS[k].tp2;
   gOpp[n].rr     = gS[k].rr;
   gOpp[n].atr    = E_A[1];
   gOpp[n].tBreak = gS[k].tBreak;
   gOpp[n].digits = gDig;
   gOpp[n].point  = gPt;
   gOpp[n].go     = false;
   gOpp[n].dead   = false;
   gOpp[n].dist   = 0;
   gOpp[n].rank   = 0;
   gOpp[n].readyAgo = gS[k].shReady;
   gOpp[n].departed = gS[k].departed;
   gOpp[n].flip     = gS[k].flip;
   gOpp[n].zFar     = ZoneFar(k);
   gOpp[n].fillAgo  = gS[k].shFill;
   gOpp[n].fresh  = false;
   gOpp[n].near   = false;

   // ---- SCORE (0-90 ici, +10 de proximite en temps reel)
   double sc = 0;
   if(gS[k].conf)
      sc += 25;                                          // FVG + breaker superposes
   else
      if(gS[k].hasFVG)
         sc += 12;
   sc += MathMin(gS[k].rr,5.0) / 5.0 * 20.0;             // ratio gain / risque
   sc += MathMin(gS[k].disp,3.0) / 3.0 * 15.0;           // force de la cassure
   sc += TFWeight(gTF);                                  // unite de temps
   if(BiasNowFor(gSym,gTF) == gS[k].dir)
      sc += 15;                                          // tendance HTF alignee
   sc += 5.0 * (1.0 - MathMin((double)gS[k].shBreak / MathMax(MaxBarsWait,1),1.0)); // fraicheur
   if(KillzoneBonus && gS[k].kz)
      sc += 10;                                          // cassure en session Londres / New York
   sc = MathMin(sc,90.0);
   gOpp[n].base  = sc;
   gOpp[n].score = sc;
   gOpp[n].key   = "S" + gSym + "_" + IntegerToString(gTF) + "_" + IntegerToString((long)gS[k].tBreak) + "_" + IntegerToString(gS[k].dir) + (gS[k].flip ? "_F" : "");

   if(ScanAlerts && gS[k].status == ST_LIVE && gS[k].shFill == 1 && !(gSym == Symbol() && gTF == Period()))
      Notify(gOpp[n].key + "E",gSym + " " + TFName(gTF) + " | " + (gS[k].dir > 0 ? "ACHAT CONFIRME" : "VENTE CONFIRMEE") +
             " apres retest de l'OB | Entree " + DoubleToString(gS[k].entry,gDig) + " Stop " + DoubleToString(gS[k].sl,gDig) +
             " TP1 " + DoubleToString(gS[k].tp1,gDig) + " TP2 " + DoubleToString(gS[k].tp2,gDig),!gScanInit);
   if(ScanAlerts && gS[k].status == ST_WAIT && gS[k].shReady <= ConfirmBars && sc >= MinScore && !(gSym == Symbol() && gTF == Period()))
      Notify(gOpp[n].key + "N",gSym + " " + TFName(gTF) + " | CONFIRME " + (gS[k].dir > 0 ? "BREAKER ACHAT" : "BREAKER VENTE") +
             (gS[k].conf ? " + FVG" : "") + " (score " + DoubleToString(sc,0) + ") | Entree " + DoubleToString(gS[k].entry,gDig) +
             " Stop " + DoubleToString(gS[k].sl,gDig) + " TP2 " + DoubleToString(gS[k].tp2,gDig),!gScanInit);
  }

//--- "GO" = prix a l'entree, pas plus loin que GoMaxR x risque vers le stop
bool InEntryZone(int d,double px,double entry,double sl)
  {
   double r = MathAbs(entry - sl);
   if(d > 0)
      return(px <= entry && px >= entry - GoMaxR * r);
   return(px >= entry && px <= entry + GoMaxR * r);
  }

double PipFor(int dig,double pt) { return((dig == 3 || dig == 5) ? pt * 10 : pt); }

void UpdateLive()
  {
   ArrayResize(gOrder,MathMax(gOppN,1));
   int m = 0;
   for(int n = 0; n < gOppN; n++)
     {
      int d = gOpp[n].dir;
      double bid = MarketInfo(gOpp[n].sym,MODE_BID);
      if(bid > 0)
        {
         double pip = PipFor(gOpp[n].digits,gOpp[n].point);
         gOpp[n].dist = (d > 0 ? bid - gOpp[n].entry : gOpp[n].entry - bid) / pip;
         gOpp[n].dead = (d > 0) ? bid <= gOpp[n].sl : bid >= gOpp[n].sl;
         gOpp[n].go   = gOpp[n].status == ST_WAIT && gOpp[n].departed && InEntryZone(d,bid,gOpp[n].entry,gOpp[n].sl);
         // prix deja trop loin au-dela de l'entree (plus d'1/3 du risque) : trade rate, on ne l'affiche plus
         if(gOpp[n].status == ST_WAIT)
           {
            if(EntryTrigger == TRIG_CONFIRM)
              {
               // en mode confirmation, le prix peut rester dans l'OB : mort seulement s'il sort de l'autre cote
               if((d > 0) ? bid < gOpp[n].zFar : bid > gOpp[n].zFar)
                  gOpp[n].dead = true;
               gOpp[n].go = gOpp[n].departed && !gOpp[n].dead && ((d > 0) ? bid <= gOpp[n].entry : bid >= gOpp[n].entry);
              }
            else
               if((d > 0) ? bid < gOpp[n].entry - GoMaxR * MathAbs(gOpp[n].entry - gOpp[n].sl)
                          : bid > gOpp[n].entry + GoMaxR * MathAbs(gOpp[n].entry - gOpp[n].sl))
                  gOpp[n].dead = true;
           }
         double prox  = (gOpp[n].atr > 0) ? 10.0 * (1.0 - MathMin(MathAbs(bid - gOpp[n].entry) / (3.0 * gOpp[n].atr),1.0)) : 0;
         gOpp[n].score = gOpp[n].base + prox;
         gOpp[n].near  = gOpp[n].status == ST_WAIT && gOpp[n].departed && gOpp[n].atr > 0 && MathAbs(bid - gOpp[n].entry) <= NearCandles * gOpp[n].atr;

         if(gOpp[n].go && ScanAlerts && gOpp[n].score >= MinScore && !(gOpp[n].sym == Symbol() && gOpp[n].tf == Period()))
            Notify(gOpp[n].key + "G",gOpp[n].sym + " " + TFName(gOpp[n].tf) + " | " + (EntryTrigger == TRIG_CONFIRM ? "RETEST " : "GO ") + (d > 0 ? "ACHAT" : "VENTE") +
                   (EntryTrigger == TRIG_CONFIRM ? " - prix dans l'OB, attendre la bougie de confirmation | Entree " : " - prix dans le breaker/FVG | Entree ") + DoubleToString(gOpp[n].entry,gOpp[n].digits) +
                   " Stop " + DoubleToString(gOpp[n].sl,gOpp[n].digits) + " TP2 " + DoubleToString(gOpp[n].tp2,gOpp[n].digits),!gScanInit);
        }
      gOpp[n].fresh = (gOpp[n].status == ST_WAIT) ? (gOpp[n].readyAgo >= 1 && gOpp[n].readyAgo <= ConfirmBars)
                                                  : (gOpp[n].fillAgo >= 1 && gOpp[n].fillAgo <= ConfirmBars);
      // Tableau : uniquement les opportunites confirmees a 1 bougie pres (ou prix dans / pres de la zone)
      // uniquement ce qui est encore prenable : prix a moins de TableMaxR x risque de l'entree
      bool reach = bid > 0 && MathAbs(bid - gOpp[n].entry) <= TableMaxR * MathAbs(gOpp[n].entry - gOpp[n].sl);
      bool show = gOpp[n].go || ((gOpp[n].fresh || gOpp[n].near) && reach);
      if(gOpp[n].dead || !show || gOpp[n].score < MinScore)
         continue;
      int grp = gOpp[n].go ? 4 : (gOpp[n].status == ST_LIVE ? 3 : (gOpp[n].fresh ? 2 : 1));
      gOpp[n].rank = grp * 1000.0 + gOpp[n].score;
      gOrder[m++] = n;
     }
   gOrderN = m;
   for(int i = 1; i < m; i++)
     {
      int v = gOrder[i];
      int j = i - 1;
      while(j >= 0 && gOpp[gOrder[j]].rank < gOpp[v].rank)
        {
         gOrder[j + 1] = gOrder[j];
         j--;
        }
      gOrder[j + 1] = v;
     }
   DrawTable();
  }

void DrawTable()
  {
   int rows = MathMin(gOrderN,MaxRows);
   int total = rows + 7;
   string L[];
   color  C[];
   ArrayResize(L,total);
   ArrayResize(C,total);
   ArrayResize(gLineOpp,total);
   for(int i = 0; i < total; i++)
      gLineOpp[i] = -1;

   int n = 0;
   string dirTxt = (ScanDirection == DIR_BUY ? "BREAKER ACHAT" : (ScanDirection == DIR_SELL ? "BREAKER VENTE" : "BREAKERS ACHAT+VENTE"));
   L[n] = "=== " + dirTxt + " CONFIRMES - TOUS TF === " + (gTableCollapsed ? "[+] ouvrir" : "[-] reduire");
   C[n] = clrGold;
   n++;
   if(gTableCollapsed)
     {
      L[0] = "=== BREAKERS : " + IntegerToString(gOrderN) + " opportunite(s) === [+] ouvrir";
      rows = 0;
     }
   else
     {
   L[n] = IntegerToString(ArraySize(gSyms)) + " actifs x " + IntegerToString(ArraySize(gTFs)) + " TF | " +
          IntegerToString(gOrderN) + " opportunite(s) | maj " + TimeToString(gLastScan,TIME_SECONDS);
   C[n] = clrSilver;
   n++;
   L[n] = StringFormat("%-2s %-10s %-3s %-5s %-9s %-9s %-9s %-9s %-9s %4s %6s %6s",
                       "#","ACTIF","TF","SENS","STATUT","ENTREE","STOP","TP1","TP2","RR","DIST","SCORE");
   C[n] = clrDarkGray;
   n++;

   if(rows == 0)
     {
      L[n] = "Aucune opportunite confirmee - ATTENDRE";
      C[n] = clrSilver;
      n++;
     }
   for(int r = 0; r < rows; r++)
     {
      int o = gOrder[r];
      string st = gOpp[o].go ? (EntryTrigger == TRIG_CONFIRM ? "RETEST" : "GO !") : (gOpp[o].status == ST_LIVE ? (EntryTrigger == TRIG_CONFIRM ? (gOpp[o].dir > 0 ? "ACHAT OK" : "VENTE OK") : "ENTREE OK") : (gOpp[o].fresh ? (gOpp[o].flip ? "FLIP" : (gOpp[o].departed ? "CONFIRME" : "CASSURE")) : "PROCHE"));
      double sc = gOpp[o].score;
      string gr = (sc >= 80 ? "A+" : (sc >= 65 ? "A" : (sc >= 50 ? "B" : "C")));
      string sym = gOpp[o].sym;
      if(StringLen(sym) > 10)
         sym = StringSubstr(sym,0,10);
      int dg = gOpp[o].digits;
      L[n] = StringFormat("%-2d %-10s %-3s %-5s %-9s %-9s %-9s %-9s %-9s %4.1f %6.1f %3.0f %-2s",
                          r + 1,sym,TFName(gOpp[o].tf),(gOpp[o].dir > 0 ? "ACHAT" : "VENTE"),st,
                          DoubleToString(gOpp[o].entry,dg),DoubleToString(gOpp[o].sl,dg),
                          DoubleToString(gOpp[o].tp1,dg),DoubleToString(gOpp[o].tp2,dg),
                          gOpp[o].rr,gOpp[o].dist,sc,gr);
      C[n] = gOpp[o].go ? clrYellow : (gOpp[o].status == ST_LIVE ? clrAqua : (gOpp[o].dir > 0 ? clrLime : clrTomato));
      gLineOpp[n] = o;
      n++;
     }
   L[n] = "RETEST=prix dans l'OB  ACHAT/VENTE OK=confirme";
   C[n] = clrDimGray;
   n++;
   L[n] = "PROCHE=a 1 bougie  DIST=pips jusqu'a l'entree";
   C[n] = clrDimGray;
   n++;
   L[n] = "Clic sur une ligne = ouvrir le graphique";
   C[n] = clrDimGray;
   n++;
     }

   // Fenetre "BREAKER TABLE" presente sous le graphique -> le tableau y va et ne cache plus aucune bougie
   int win = ChartWindowFind(0,"BREAKER TABLE");
   if(win < 0)
      win = 0;
   if(win != gTableWin)
     {
      DeleteByPrefix(TPFX);
      gTableWin = win;
     }
   int W;
   if(win > 0)
     {
      W = DrawBox(TPFX,L,C,n,CORNER_LEFT_UPPER,8,4,TableFontSize,53,win);
      if(gShiftPct > 10)
        {
         ChartSetDouble(0,CHART_SHIFT_SIZE,10);
         gShiftPct = 10;
        }
      ChartRedraw();
      return;
     }
   W = DrawBox(TPFX,L,C,n,TablePosition,TableX,TableY,TableFontSize,53);

   // sinon : libere la place a droite du graphique pour que le tableau ne cache pas les bougies
   if(TableAutoShift && (TablePosition == CORNER_RIGHT_UPPER || TablePosition == CORNER_RIGHT_LOWER))
     {
      long cw = ChartGetInteger(0,CHART_WIDTH_IN_PIXELS,0);
      if(cw > 0)
        {
         int pct = (int)MathCeil((W + TableX + 15) * 100.0 / (double)cw);
         pct = MathMax(10,MathMin(50,pct));
         if(pct != gShiftPct)
           {
            gShiftPct = pct;
            ChartSetInteger(0,CHART_SHIFT,true);
            ChartSetDouble(0,CHART_SHIFT_SIZE,pct);
           }
        }
     }
   ChartRedraw();
  }

//==================================================================== UTILITAIRES
string SKey(Setup &s) { return(IntegerToString((long)s.tBreak) + "_" + IntegerToString(s.dir) + (s.flip ? "_F" : "")); }
string Px(double v)   { return(DoubleToString(v,gDig)); }

string TFName(int tf)
  {
   if(tf == PERIOD_CURRENT)
      tf = Period();
   switch(tf)
     {
      case PERIOD_M1:  return("M1");
      case PERIOD_M5:  return("M5");
      case PERIOD_M15: return("M15");
      case PERIOD_M30: return("M30");
      case PERIOD_H1:  return("H1");
      case PERIOD_H4:  return("H4");
      case PERIOD_D1:  return("D1");
      case PERIOD_W1:  return("W1");
      case PERIOD_MN1: return("MN");
     }
   return(IntegerToString(tf));
  }

string StatusText(Setup &s)
  {
   int d = s.dir;
   switch(s.status)
     {
      case ST_WAIT:    if(!s.departed)
            return(d > 0 ? "CASSURE - ATTENDRE NOUVEAU PLUS HAUT" : "CASSURE - ATTENDRE NOUVEAU PLUS BAS");
         if(s.touched)
            return(d > 0 ? "RETEST DANS L'OB - ATTENDRE BOUGIE VERTE" : "RETEST DANS L'OB - ATTENDRE BOUGIE ROUGE");
         return(d > 0 ? "ACHETER - ATTENDRE RETEST" : "VENDRE - ATTENDRE RETEST");
      case ST_LIVE:    return(s.tp1Hit ? "EN POSITION - TP1 OK" : "EN POSITION");
      case ST_TP2:     return("TP2 ATTEINT");
      case ST_BE:      return("TP1 PUIS SORTIE");
      case ST_SL:      return("STOP TOUCHE");
      case ST_EXPIRED: return("EXPIRE (pas de retest)");
      case ST_CANCEL:  return(s.flipped ? "BREAKER RATE -> FLIP" : "ANNULE");
      case ST_MISSED:  return("RATE (TP atteint sans retest)");
      case ST_TOOFAR:  return("RATE (confirmation trop loin de l'OB)");
     }
   return("");
  }

int CurrentSetup()
  {
   for(int k = cSn - 1; k >= 0; k--)
      if(cS[k].status == ST_LIVE || (cS[k].status == ST_WAIT && cS[k].ready))
         return(k);
   for(int k = cSn - 1; k >= 0; k--)
      if(cS[k].ready && cS[k].status != ST_REJECT)
         return(k);
   return(-1);
  }

//==================================================================== DESSIN
void DeleteByPrefix(string p)
  {
   for(int i = ObjectsTotal(0,-1,-1) - 1; i >= 0; i--)
     {
      string n = ObjectName(0,i,-1,-1);
      if(StringFind(n,p) == 0)
         ObjectDelete(0,n);
     }
  }

void Rect(string n,datetime t1,double p1,datetime t2,double p2,color c)
  {
   ObjectCreate(0,n,OBJ_RECTANGLE,0,t1,p1,t2,p2);
   ObjectSetInteger(0,n,OBJPROP_COLOR,c);
   ObjectSetInteger(0,n,OBJPROP_BACK,true);
   ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,n,OBJPROP_HIDDEN,true);
  }

void TLine(string n,datetime t1,datetime t2,double p,color c,int style,int width)
  {
   ObjectCreate(0,n,OBJ_TREND,0,t1,p,t2,p);
   ObjectSetInteger(0,n,OBJPROP_RAY,false);
   ObjectSetInteger(0,n,OBJPROP_COLOR,c);
   ObjectSetInteger(0,n,OBJPROP_STYLE,style);
   ObjectSetInteger(0,n,OBJPROP_WIDTH,width);
   ObjectSetInteger(0,n,OBJPROP_BACK,false);
   ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,n,OBJPROP_HIDDEN,true);
  }

void Txt(string n,datetime t,double p,string s,color c,int anchor)
  {
   ObjectCreate(0,n,OBJ_TEXT,0,t,p);
   ObjectSetString(0,n,OBJPROP_TEXT,s);
   ObjectSetString(0,n,OBJPROP_FONT,"Arial");
   ObjectSetInteger(0,n,OBJPROP_FONTSIZE,8);
   ObjectSetInteger(0,n,OBJPROP_COLOR,c);
   ObjectSetInteger(0,n,OBJPROP_ANCHOR,anchor);
   ObjectSetInteger(0,n,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,n,OBJPROP_HIDDEN,true);
  }

void SetLabel(string nm,string txt,ENUM_BASE_CORNER corner,int x,int y,int fs,color c,int sub = 0)
  {
   if(ObjectFind(0,nm) < 0)
      ObjectCreate(0,nm,OBJ_LABEL,sub,0,0);
   ObjectSetInteger(0,nm,OBJPROP_CORNER,corner);
   ObjectSetInteger(0,nm,OBJPROP_ANCHOR,ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0,nm,OBJPROP_XDISTANCE,x);
   ObjectSetInteger(0,nm,OBJPROP_YDISTANCE,y);
   ObjectSetString(0,nm,OBJPROP_TEXT,txt);
   ObjectSetString(0,nm,OBJPROP_FONT,"Consolas");
   ObjectSetInteger(0,nm,OBJPROP_FONTSIZE,fs);
   ObjectSetInteger(0,nm,OBJPROP_COLOR,c);
   ObjectSetInteger(0,nm,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,nm,OBJPROP_HIDDEN,true);
  }

int DrawBox(string pfx,string &lines[],color &cols[],int n,ENUM_BASE_CORNER corner,int X,int Y,int fs,int splitAt,int sub = 0)
  {
   // largeur mesuree sur le vrai texte -> plus de colonnes coupees
   TextSetFont("Consolas",-fs * 10);
   uint tw = 0, th = 0, mw = 0, mh = 0;
   for(int i = 0; i < n; i++)
     {
      TextGetSize(lines[i],tw,th);
      if(tw > mw)
         mw = tw;
      if(th > mh)
         mh = th;
     }
   // correction du zoom Windows (125% / 150%) : le texte s'affiche plus large que mesure
   double dpiF = TerminalInfoInteger(TERMINAL_SCREEN_DPI) / 96.0;
   if(dpiF < 1.0)
      dpiF = 1.0;
   uint cw = 0, ch = 0;
   TextGetSize("MMMMMMMMMM",cw,ch);
   int maxChars = 0;
   for(int i = 0; i < n; i++)
      maxChars = MathMax(maxChars,StringLen(lines[i]));
   double wChars = maxChars * (cw / 10.0);
   double wText  = MathMax((double)mw,wChars) * dpiF;
   int W  = (int)(wText * 1.08) + 24;
   int lh = MathMax((int)(mh * dpiF * 1.15) + 2,(int)((fs + 7) * dpiF));
   int H  = n * lh + 12;
   bool right = (corner == CORNER_RIGHT_UPPER || corner == CORNER_RIGHT_LOWER);
   bool lower = (corner == CORNER_LEFT_LOWER  || corner == CORNER_RIGHT_LOWER);

   string bg = pfx + "BG";
   if(ObjectFind(0,bg) < 0)
      ObjectCreate(0,bg,OBJ_RECTANGLE_LABEL,sub,0,0);
   ObjectSetInteger(0,bg,OBJPROP_CORNER,corner);
   ObjectSetInteger(0,bg,OBJPROP_XDISTANCE,right ? X + W : X);
   ObjectSetInteger(0,bg,OBJPROP_YDISTANCE,lower ? Y + H : Y);
   ObjectSetInteger(0,bg,OBJPROP_XSIZE,W);
   ObjectSetInteger(0,bg,OBJPROP_YSIZE,H);
   ObjectSetInteger(0,bg,OBJPROP_BGCOLOR,C'15,15,28');
   ObjectSetInteger(0,bg,OBJPROP_BORDER_TYPE,BORDER_FLAT);
   ObjectSetInteger(0,bg,OBJPROP_COLOR,clrDimGray);
   ObjectSetInteger(0,bg,OBJPROP_BACK,false);
   ObjectSetInteger(0,bg,OBJPROP_SELECTABLE,false);
   ObjectSetInteger(0,bg,OBJPROP_HIDDEN,true);

   // MT4 limite une etiquette a 63 caracteres -> chaque ligne est coupee en 2 etiquettes alignees
   double cpx = (cw / 10.0) * dpiF;
   int off = (int)MathRound(splitAt * cpx);
   for(int i = 0; i < n; i++)
     {
      string t1 = lines[i], t2 = "";
      if(StringLen(t1) > splitAt)
        {
         t2 = StringSubstr(lines[i],splitAt);
         t1 = StringSubstr(lines[i],0,splitAt);
        }
      int y = lower ? Y + H - 6 - i * lh : Y + 6 + i * lh;
      int x1 = right ? X + W - 8 : X + 8;
      SetLabel(pfx + IntegerToString(i),t1,corner,x1,y,fs,cols[i],sub);
      string nm2 = pfx + IntegerToString(i) + "_b";
      if(t2 != "")
         SetLabel(nm2,t2,corner,right ? x1 - off : x1 + off,y,fs,cols[i],sub);
      else
         if(ObjectFind(0,nm2) >= 0)
            ObjectDelete(0,nm2);
     }
   for(int i = n; i < 60; i++)
     {
      string nm = pfx + IntegerToString(i);
      if(ObjectFind(0,nm) >= 0)
         ObjectDelete(0,nm);
      if(ObjectFind(0,nm + "_b") >= 0)
         ObjectDelete(0,nm + "_b");
     }
   return(W);
  }

void DrawAll()
  {
   DeleteByPrefix(PFX);
   datetime fut = Time[0] + PeriodSeconds() * 8;
   int drawn = 0;

   for(int k = cSn - 1; k >= 0 && drawn < MaxSetupsDrawn; k--)
     {
      int st = cS[k].status;
      if(st == ST_REJECT || !cS[k].ready)
         continue;
      bool fin = (st >= ST_TP2);
      // un setup termine reste affiche (grise) pendant KeepDoneBars bougies, pour ne pas disparaitre d'un coup
      if(fin && !ShowDone && Time[0] - cS[k].tEnd > (long)KeepDoneBars * PeriodSeconds())
         continue;
      drawn++;

      string id = PFX + SKey(cS[k]) + "_";
      int d = cS[k].dir;
      datetime te = fin ? cS[k].tEnd : fut;
      bool lost = (st == ST_SL || st == ST_EXPIRED || st == ST_CANCEL || st == ST_MISSED || st == ST_TOOFAR);
      color cb = lost ? ColDone : (d > 0 ? ColBreakerBuy : ColBreakerSell);

      Rect(id + "BRK",cS[k].tOB,cS[k].bHi,te,cS[k].bLo,cb);
      if(cS[k].hasFVG)
         Rect(id + "FVG",cS[k].tF,cS[k].fHi,te,cS[k].fLo,lost ? ColDone : ColFVG);

      string title = (cS[k].flip ? (d > 0 ? "FLIP ACHAT" : "FLIP VENTE")
                                 : (d > 0 ? "BREAKER ACHAT" : "BREAKER VENTE") + (cS[k].conf ? " + FVG" : "")) + " | " + StatusText(cS[k]);
      Txt(id + "T",cS[k].tOB,(d > 0 ? cS[k].bLo : cS[k].bHi),title,
          (d > 0 ? ColTP : ColSL),(d > 0 ? ANCHOR_LEFT_UPPER : ANCHOR_LEFT_LOWER));

      if(st == ST_EXPIRED || st == ST_CANCEL || st == ST_MISSED || st == ST_TOOFAR)
         continue;
      datetime ts = cS[k].tBreak;
      TLine(id + "E",ts,te,cS[k].entry,ColEntry,STYLE_SOLID,fin ? 1 : 2);
      TLine(id + "S",ts,te,cS[k].sl,ColSL,STYLE_SOLID,1);
      TLine(id + "T1",ts,te,cS[k].tp1,ColTP,STYLE_DOT,1);
      TLine(id + "T2",ts,te,cS[k].tp2,ColTP,STYLE_DASH,1);
      if(!fin)
        {
         Txt(id + "lE",te,cS[k].entry,"  ENTREE " + DoubleToString(cS[k].entry,Digits),ColEntry,ANCHOR_LEFT);
         Txt(id + "lS",te,cS[k].sl,"  STOP " + DoubleToString(cS[k].sl,Digits),ColSL,ANCHOR_LEFT);
         Txt(id + "l1",te,cS[k].tp1,"  TP1 " + DoubleToString(cS[k].tp1,Digits) + " (" + DoubleToString(TP1_R,1) + "R)",ColTP,ANCHOR_LEFT);
         Txt(id + "l2",te,cS[k].tp2,"  TP2 " + DoubleToString(cS[k].tp2,Digits) + " (" + DoubleToString(cS[k].rr,1) + "R)",ColTP,ANCHOR_LEFT);
        }
     }

   if(ShowOpenOB)
     {
      int nb = 0, ns = 0;
      for(int k = gOBn - 1; k >= 0; k--)
        {
         if(!gOB[k].alive)
            continue;
         string id = PFX + "OB" + IntegerToString((long)gOB[k].t) + "_" + IntegerToString(gOB[k].dir);
         if(gOB[k].dir < 0 && ns < MaxActiveOB && DirAllowedChart(1))
           {
            ns++;
            Rect(id,gOB[k].t,gOB[k].hi,fut,gOB[k].lo,ColOBSell);
            Txt(id + "t",gOB[k].t,gOB[k].hi,"OB VENTE (cassure = breaker achat)",clrSilver,ANCHOR_LEFT_LOWER);
           }
         if(gOB[k].dir > 0 && nb < MaxActiveOB && DirAllowedChart(-1))
           {
            nb++;
            Rect(id,gOB[k].t,gOB[k].hi,fut,gOB[k].lo,ColOBBuy);
            Txt(id + "t",gOB[k].t,gOB[k].lo,"OB ACHAT (cassure = breaker vente)",clrSilver,ANCHOR_LEFT_UPPER);
           }
        }
     }
   ChartRedraw();
  }

bool DirAllowedChart(int dir) { return(ChartDirection == DIR_BOTH || (int)ChartDirection == dir); }

//--- Panneau "QUE FAIRE" du graphique courant
void DrawPanel()
  {
   string lines[10];
   color  cols[10];
   int n = 0;

   lines[n] = "=== " + Symbol() + " " + TFName(Period()) + " - QUE FAIRE === " + (gPanelCollapsed ? "[+]" : "[-]");
   cols[n] = clrGold;
   n++;
   if(gPanelCollapsed)
     {
      DrawBox(PPFX,lines,cols,n,PanelCorner,10,20,8,62);
      return;
     }

   int b = BiasNowFor(Symbol(),Period());
   lines[n] = "Tendance " + TFName(HTF(Period())) + " (EMA" + IntegerToString(BiasEMA) + ") : " +
              (b > 0 ? "HAUSSIERE" : (b < 0 ? "BAISSIERE" : "indefinie")) + (FilterHTFTrend ? " (filtre ON)" : " (info)");
   cols[n] = (b > 0 ? clrLime : (b < 0 ? clrTomato : clrSilver));
   n++;

   int k = CurrentSetup();
   if(k < 0)
     {
      lines[n] = "Aucun breaker actif - ATTENDRE";
      cols[n] = clrSilver;
      n++;
     }
   else
     {
      int d = cS[k].dir;
      string act = StatusText(cS[k]);
      bool go = false;
      if(cS[k].status == ST_WAIT)
        {
         bool inZone = cS[k].departed && InEntryZone(d,Bid,cS[k].entry,cS[k].sl);
         if(inZone)
           {
            act = (EntryTrigger == TRIG_CONFIRM ? "DANS L'OB - ATTENDRE LA BOUGIE DE CONFIRMATION"
                                                : (d > 0 ? "ACHETER - GO MAINTENANT" : "VENDRE - GO MAINTENANT"));
            go = true;
           }
        }
      lines[n] = (cS[k].flip ? (d > 0 ? "FLIP ACHAT" : "FLIP VENTE") : (d > 0 ? "BREAKER ACHAT" : "BREAKER VENTE")) +
                 (cS[k].conf ? "  [FVG + BREAKER]" : (cS[k].hasFVG ? "  [FVG]" : "")) +
                 "  | cassure " + TimeToString(cS[k].tBreak,TIME_DATE | TIME_MINUTES);
      cols[n] = (d > 0 ? clrLime : clrTomato);
      n++;

      lines[n] = "ACTION : " + act;
      cols[n] = go ? clrYellow : (cS[k].status == ST_LIVE ? clrAqua : clrWhite);
      n++;

      lines[n] = "Entree " + DoubleToString(cS[k].entry,Digits) + "  Stop " + DoubleToString(cS[k].slCur,Digits) +
                 "  TP1 " + DoubleToString(cS[k].tp1,Digits) + "  TP2 " + DoubleToString(cS[k].tp2,Digits);
      cols[n] = clrWhite;
      n++;

      double pip  = PipFor(Digits,Point);
      double risk = MathAbs(cS[k].entry - cS[k].sl) / pip;
      double dist = MathAbs(Bid - cS[k].entry) / pip;
      lines[n] = "Position dans le marche : " + DoubleToString(cS[k].pd,0) + "% " +
                 (cS[k].pd <= 50 ? "(DISCOUNT = zone d'achat)" : "(PREMIUM = zone de vente)");
      cols[n] = ((cS[k].dir > 0) == (cS[k].pd <= 50)) ? clrLime : clrOrange;
      n++;
      lines[n] = "Risque " + DoubleToString(risk,1) + " pips | RR " + DoubleToString(cS[k].rr,1) +
                 " | prix a " + DoubleToString(dist,1) + " pips de l'entree";
      cols[n] = clrSilver;
      n++;
     }

   int w = 0, l = 0, o = 0;
   for(int i = 0; i < cSn; i++)
     {
      int st = cS[i].status;
      if(st == ST_TP2 || st == ST_BE)
         w++;
      else
         if(st == ST_SL)
            l++;
         else
            if(st == ST_LIVE)
               o++;
     }
   int tot = w + l;
   lines[n] = "Histo : " + IntegerToString(tot) + " trades | " + IntegerToString(w) + " gagnes | " +
              IntegerToString(l) + " perdus | " + (tot > 0 ? DoubleToString(100.0 * w / tot,0) + "%" : "-") +
              (o > 0 ? " | " + IntegerToString(o) + " en cours" : "");
   cols[n] = clrSilver;
   n++;

   DrawBox(PPFX,lines,cols,n,PanelCorner,10,20,8,62);
  }
//+------------------------------------------------------------------+
