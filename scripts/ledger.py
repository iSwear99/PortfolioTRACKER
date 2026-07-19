# -*- coding: utf-8 -*-
"""
ledger.py — měnový a akciový ledger metodou průměrných nákladů (metoda A:
skutečné konverze). Reprodukuje historické CZK přepočty nezávisle na čemkoli
mimo repozitář.

Vstupy:  data/portfolio.json   (trades, others, flows, positions, cash)
         data/conversions.json (měnové konverze IBKR + Patria)
         data/fx.json          (aktuální kurzy — pro nerealizovaný FX a fallback)
Výstupy: - do data/portfolio.json zapíše u obchodů `kurz_hist` a `rplczk`
           (realizovaný P/L v CZK historickými kurzy) a u položek `others`
           pole `czk` (hist. přepočet),
         - přepíše `breakdown_hist` (rozpad P/L pro Dashboard/tracker),
         - uloží data/ledger_summary.json (kontrolní součty, zůstatky).

Spuštění:  python scripts/ledger.py
Vždy spouštět PO přidání obchodu/konverze/vkladu a PŘED scripts/update.py.

Metodika (shodná s původní validovanou verzí):
- měny: průměrné pořizovací náklady; příliv z prodejů/dividend se účtuje
  aktuálním Ø kurzem ledgeru (nevzniká FX P/L při přílivu),
- kříže (EUR→USD apod.): přenos CZK nákladové hodnoty, bez realizace,
- konverze do CZK: realizovaný FX = přijaté CZK − nákladová hodnota,
- akcie: průměrné náklady; realizovaný P/L CZK = výnos × Ø kurz měny v den
  prodeje − CZK pořizovací cena prodaného podílu,
- Patria konverze se řadí o 3 dny dříve (probíhají k vypořádání, obchod je
  datem pokynu — jinak by nákup předběhl vlastní konverzi),
- nekryté čerpání měny (chybějící konverze v datech) se ocení aktuálním
  kurzem a vykáže v summary jako `shortfall`.
"""
import json, os, sys, collections
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def p(*a): return os.path.join(ROOT, *a)
def jload(path):
    with open(path, encoding='utf-8') as f: return json.load(f)
def jsave(path, obj, indent=1):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)

EPS = 0.01

# Sané pásmo Ø kurzu (CZK za jednotku měny) — ochrana proti degeneraci CZK báze
# na mikrozůstatcích (viz README_DIAGNOSTIKA: IBKR EUR Ø 74, Patria USD Ø 24,9).
BANDS = {'USD': (15, 30), 'EUR': (20, 30), 'GBP': (24, 35),
         'SEK': (1.5, 3.5), 'DKK': (2.5, 4.5), 'HKD': (2.0, 4.0)}
MICRO_UNITS = 5.0   # zůstatek pod tolik jednotek měny je nemateriální (fallback + odpis)

def shift(d, n=-3):
    y, m, dd = map(int, d.split('-'))
    return str(date(y, m, dd) + timedelta(days=n))

def run():
    port = jload(p('data', 'portfolio.json'))
    conv = jload(p('data', 'conversions.json'))
    NOW = jload(p('data', 'fx.json'))['fx']          # aktuální kurzy (fallback + ocenění)

    # ---------- fronta událostí (datum, priorita) ----------
    # priorita: 0 vklad/výběr, 1 konverze, 2 obchod, 3 ostatní položky
    ev = []
    for f in port['flows']:
        ev.append((f['date'], 0, 'cash', ('CZK', f['amt'], f['broker'])))
    for c in conv:
        d = shift(c['date']) if c['broker'] == 'Patria' else c['date']
        ev.append((d, 1, 'fx', c))
    for i, t in enumerate(port['trades']):
        ev.append((t['date'], 2, 'trade', i))
    for i, x in enumerate(port['others']):
        if x['amt'] != 0:
            ev.append((x['date'], 3, 'item', i))
    ev.sort(key=lambda e: (e[0], e[1]))

    # ---------- ledger ----------
    cur = {}                                  # (broker, ccy) -> {q, czk}
    stk = {}                                  # (broker, ticker) -> {q, czk_cost, ccy_cost}
    fx_realized = collections.defaultdict(float)
    shortfall = collections.defaultdict(float)
    fx_writeoff = collections.defaultdict(float)   # odpis fantomové báze mikrozůstatků

    def normalize(br, ccy):
        """Brání degeneraci CZK báze na mikrozůstatcích. Vyčerpaný zůstatek odepíše
        zbytkovou bázi, mikrozůstatek (< MICRO_UNITS) přecení fallback kurzem a přebytek
        odepíše do fx_writeoff — tím se zastaví šíření nafouklého Ø kurzu do prodejů
        a křížových konverzí (kořen chyby BOSS Ø 74 / Patria USD Ø 24,9)."""
        if ccy == 'CZK': return
        s = cur.get((br, ccy))
        if not s: return
        if s['q'] <= EPS:                              # zůstatek vyčerpán/mikro/záporný
            if abs(s['czk']) > EPS: fx_writeoff[(br, ccy)] += s['czk']
            if -EPS < s['q'] < EPS: s['q'] = 0.0
            s['czk'] = 0.0
            return
        if s['q'] < MICRO_UNITS:                       # nemateriální zbytek → fallback
            target = s['q'] * NOW[ccy]
            if abs(s['czk'] - target) > EPS:
                fx_writeoff[(br, ccy)] += s['czk'] - target
                s['czk'] = target

    def rate(br, ccy):
        if ccy == 'CZK': return 1.0
        s = cur.get((br, ccy))
        if not s or s['q'] <= EPS: return NOW[ccy]
        r = s['czk'] / s['q']
        lo, hi = BANDS.get(ccy, (None, None))
        if lo is not None and not (lo <= r <= hi): return NOW[ccy]  # Ø mimo pásmo → fallback
        return r

    def add(br, ccy, amt, czk):
        s = cur.setdefault((br, ccy), {'q': 0.0, 'czk': 0.0})
        # Materialita přílivu: je-li stávající zůstatek zanedbatelný vůči příchozí
        # částce (< max(MICRO_UNITS, 1 % amt)), odepiš jeho zbytkovou bázi předem —
        # jinak nedůvěryhodný Ø kurz mikrozůstatku kontaminuje nový příliv (kořen
        # Patria USD Ø 24,9: přežívající ~6 USD z 2022 přebíjel čerstvé USD z 2024).
        if ccy != 'CZK' and amt > EPS and EPS < s['q'] < max(MICRO_UNITS, 0.01 * amt):
            target = s['q'] * NOW[ccy]
            fx_writeoff[(br, ccy)] += s['czk'] - target
            s['czk'] = target
        s['q'] += amt; s['czk'] += czk
        if -EPS < s['q'] < EPS: s['q'] = 0.0; s['czk'] = 0.0
        normalize(br, ccy)

    def consume(br, ccy, amt):
        """Odebere amt jednotek měny, vrátí jejich CZK nákladovou hodnotu."""
        if ccy == 'CZK':
            add(br, 'CZK', -amt, -amt); return amt
        s = cur.setdefault((br, ccy), {'q': 0.0, 'czk': 0.0})
        if s['q'] > EPS and amt <= s['q'] + EPS:
            r = s['czk'] / s['q']; czk = min(amt, s['q']) * r
            s['q'] -= amt; s['czk'] -= czk
        else:
            sh = amt - max(s['q'], 0)
            shortfall[(br, ccy)] += sh
            czk = (s['czk'] if s['q'] > EPS else 0) + sh * NOW[ccy]
            s['q'] -= amt
            s['czk'] = 0.0
        if -EPS < s['q'] < EPS: s['q'] = 0.0; s['czk'] = 0.0
        normalize(br, ccy)
        return czk

    for d, prio, kind, pl in ev:
        if kind == 'cash':
            ccy, a, br = pl
            add(br, ccy, a, a if ccy == 'CZK' else a * NOW[ccy])

        elif kind == 'fx':
            br = pl['broker']; b = pl['base']; q = pl['quote']
            bq = pl['qty']; qa = pl['proceeds']
            if bq > 0:                                   # nákup base za quote
                czk = consume(br, q, -qa)
                if b == 'CZK':
                    add(br, 'CZK', bq, bq)
                    fx_realized[(br, q)] += bq - czk
                else:
                    add(br, b, bq, czk)
            else:                                        # prodej base za quote
                czk = consume(br, b, -bq)
                if q == 'CZK':
                    add(br, 'CZK', qa, qa)
                    fx_realized[(br, b)] += qa - czk
                else:
                    add(br, q, qa, czk)

        elif kind == 'trade':
            t = port['trades'][pl]
            br = t['broker']; ccy = t['ccy']; tk = t['ticker']
            gross = t['qty'] * t['price'] * t.get('mult', 1)
            s = stk.setdefault((br, tk), {'q': 0.0, 'ccy_cost': 0.0, 'czk_cost': 0.0})
            if t['qty'] > 0:                             # nákup / ADJ kladný
                czk = consume(br, ccy, gross)
                s['q'] += t['qty']; s['ccy_cost'] += gross; s['czk_cost'] += czk
                t['rplczk'] = 0
                t['kurz_hist'] = round(czk / gross, 4) if gross else None
            elif t['qty'] < 0:                           # prodej / ADJ záporný
                sold = -t['qty']
                fr = min(sold / s['q'], 1.0) if s['q'] > 1e-9 else 0
                cost_czk = s['czk_cost'] * fr
                s['q'] -= sold; s['czk_cost'] *= (1 - fr); s['ccy_cost'] *= (1 - fr)
                if s['q'] < 1e-6:
                    s['q'] = 0.0; s['czk_cost'] = 0.0; s['ccy_cost'] = 0.0
                r = rate(br, ccy)
                proceeds = -gross
                add(br, ccy, proceeds, proceeds * (1 if ccy == 'CZK' else r))
                t['rplczk'] = round(proceeds * (1 if ccy == 'CZK' else r) - cost_czk)
                t['kurz_hist'] = round(r, 4) if ccy != 'CZK' else 1.0
            else:
                t['rplczk'] = 0; t['kurz_hist'] = None
            if t.get('fee'):
                consume(br, ccy, -t['fee'])              # fee je záporné

        elif kind == 'item':
            x = port['others'][pl]
            br = x['broker']; ccy = x['ccy']; a = x['amt']
            r = rate(br, ccy)
            x['czk'] = round(a * (1 if ccy == 'CZK' else r))
            if a > 0: add(br, ccy, a, a * (1 if ccy == 'CZK' else r))
            else: consume(br, ccy, -a)

    # ---------- souhrny ----------
    fx_real = round(sum(fx_realized.values()))
    fx_unreal = round(sum(s['q'] * NOW[c] - s['czk']
                          for (b, c), s in cur.items() if c != 'CZK'))
    basis_hist = round(sum(s['czk_cost'] for s in stk.values() if s['q'] > 1e-6))
    real_hist = round(sum(t.get('rplczk', 0) for t in port['trades']))
    other_hist = round(sum(x.get('czk', 0) for x in port['others']))

    # tržní hodnota + hotovost aktuálními kurzy (pro uzávěr rozpadu)
    prices = jload(p('data', 'prices.json'))
    mv = round(sum(pos['qty'] * prices[pos['ticker']]['px'] * NOW[pos['ccy']]
                   for pos in port['positions']))
    cash = round(sum(c['bal'] * NOW[c['ccy']] for c in port['cash']))
    # Odpis fantomové báze se už projevil ve fx_unreal (snížením czk báze měn),
    # proto ho v residualu NEODEČÍTÁME (jinak by se počítal dvakrát). fx_wo je jen
    # informativní ukazatel, kolik degenerované báze engine odepsal.
    fx_wo = round(sum(fx_writeoff.values()))
    total_pl = mv + cash - port['deposits']
    unreal_hist = mv - basis_hist
    residual = round(total_pl - unreal_hist - real_hist - other_hist
                     - fx_real - fx_unreal)

    port['breakdown_hist'] = [
        ['Nerealizovaný P/L (hist.)', unreal_hist],
        ['Realizovaný P/L (hist.)', real_hist],
        ['Ostatní (div./daně/úroky/popl.)', other_hist],
        ['FX realizovaný', fx_real],
        ['FX nerealizovaný', fx_unreal],
        ['Aproximace metody', residual],
    ]
    jsave(p('data', 'portfolio.json'), port)

    summary = dict(
        basis_hist_czk=basis_hist,
        realized_hist_czk=real_hist,
        others_hist_czk=other_hist,
        fx_realized_czk={f'{b}|{c}': round(v) for (b, c), v in fx_realized.items()},
        fx_unrealized_cash_czk=fx_unreal,
        fx_writeoff_czk={f'{b}|{c}': round(v) for (b, c), v in fx_writeoff.items() if abs(v) >= 1},
        fx_writeoff_total_czk=fx_wo,
        residual_czk=residual,
        currency_ledger={f'{b}|{c}': dict(q=round(s['q'], 2),
                                          czk=round(s['czk'], 2),
                                          avg=round(s['czk'] / s['q'], 4) if s['q'] > EPS else None)
                         for (b, c), s in cur.items()},
        stock_ledger={f'{b}|{t}': dict(q=round(s['q'], 4), czk_cost=round(s['czk_cost'], 2))
                      for (b, t), s in stk.items() if s['q'] > 1e-6},
        shortfalls={f'{b}|{c}': round(v, 2) for (b, c), v in shortfall.items()},
    )
    jsave(p('data', 'ledger_summary.json'), summary)

    # ---------- validace ----------
    warn = []
    for c in port['cash']:
        led = cur.get((c['broker'], c['ccy']), {'q': 0})['q']
        if abs(led - c['bal']) > max(2, abs(c['bal']) * 0.02):
            warn.append(f"{c['broker']} {c['ccy']}: ledger {led:.2f} vs cash {c['bal']:.2f}")
    for pos in port['positions']:
        led = stk.get((pos['broker'], pos['ticker']), {'q': 0})['q']
        if abs(led - pos['qty']) > 1e-6:
            warn.append(f"{pos['broker']} {pos['ticker']}: ledger {led} ks vs positions {pos['qty']} ks")
    if abs(residual) > abs(total_pl) * 0.05 + 5000:
        warn.append(f'vysoký zbytek rozpadu: {residual:+,} CZK — zkontrolujte úplnost konverzí/položek')

    print(f'Ledger OK | real.hist {real_hist:+,} | ostatní {other_hist:+,} | '
          f'FX {fx_real:+,}/{fx_unreal:+,} | odpis reziduí {fx_wo:+,} | zbytek {residual:+,} CZK')
    if warn:
        print('VAROVÁNÍ:'); [print(' -', w) for w in warn]
        sys.exit(2)

if __name__ == '__main__':
    run()
