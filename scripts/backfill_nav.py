# -*- coding: utf-8 -*-
"""Zpětný dopočet historického NAV do data/nav_history.json.

Rozsah: konce měsíců 2022–2025 + každý den 2026 (od teď dál plní update.py).
Metoda:
- držení a hotovost k datu = přehrání trades/flows/others/conversions,
- ceny: Yahoo denní close (forward-fill), fallback = interpolace vlastních
  obchodních cen daného titulu (funguje i pro opce, delistované, chybný 'nan'),
- kurzy: roční soubory ČNB (forward-fill).
Existující (naměřené) body v nav_history se ctí — doplňují se jen chybějící dny.
Idempotentní: opakované spuštění přidá jen nové dny.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def p(*a): return os.path.join(ROOT, *a)
def jload(name):
    with open(p('data', name), encoding='utf-8') as f: return json.load(f)
def jsave(name, obj):
    with open(p('data', name), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
def http(u, t=25):
    req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0 portfolio-backfill'})
    return urllib.request.urlopen(req, timeout=t).read().decode('utf-8')

port = jload('portfolio.json'); conv = jload('conversions.json'); pnow = jload('prices.json')

# ---------- statické mapy z obchodů ----------
ccy_of, mult_of = {}, {}
tprice = {}                                   # ticker -> [(date, price)] pro fallback
for t in port['trades']:
    tk = str(t['ticker']); ccy_of[tk] = t['ccy']; mult_of[tk] = t.get('mult', 1)
    tprice.setdefault(tk, []).append((t['date'], t['price']))
for k in tprice: tprice[k].sort()

# ---------- Yahoo symbol mapping ----------
EXSUF = {'XETR': '.DE', 'LSE': '.L', 'OMX': '.ST'}
YMAP = {'175': '0175.HK', '2318': '2318.HK', 'BAYN': 'BAYN.DE', 'CONd': 'CON.DE',
        'TUI1': 'TUI1.DE', 'ZAL': 'ZAL.DE', 'CSG': 'CSGN.SW', 'CEZ': 'CEZ.PR',
        'KOMB': 'KOMB.PR', 'VUAA': 'VUAA.L', 'DTLA': 'DTLA.L', 'WIZZ': 'WIZZ.L',
        'CSPX': 'CSPX.L', 'BOSS': 'BOSS.DE', 'NOV': 'NOV.DE', 'P911': 'P911.DE',
        'VOW3': 'VOW3.DE', '4GLD': '4GLD.DE', 'EVO': 'EVO.ST'}
DIVIDE = {'WIZZ': 100}
def ysym(tk):
    if tk in YMAP: return YMAP[tk]
    cfg = pnow.get(tk, {})
    ex = cfg.get('exchange'); q = cfg.get('q', tk)
    return q + EXSUF[ex] if ex in EXSUF else q

def yahoo_hist(sym):
    p1 = int((date(2022, 1, 1) - date(1970, 1, 1)).total_seconds())
    p2 = int(time.time()) + 86400
    u = (f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}'
         f'?period1={p1}&period2={p2}&interval=1d')
    raw = json.loads(http(u)); r = raw['chart']['result'][0]
    ts = r.get('timestamp') or []; cl = r['indicators']['quote'][0].get('close') or []
    out = []
    for t, c in zip(ts, cl):
        if c is None: continue
        out.append(((date(1970, 1, 1) + timedelta(seconds=t)).isoformat(), float(c)))
    out.sort()
    return out

# stáhni ceny pro všechny držené tituly (co selže → jen fallback interpolací)
yhist, ok, fail = {}, [], []
tickers = sorted(ccy_of.keys())
for i, tk in enumerate(tickers):
    if i: time.sleep(1.2)
    try:
        h = yahoo_hist(ysym(tk))
        if h: yhist[tk] = h; ok.append(tk)
        else: fail.append(tk)
    except Exception as e:
        fail.append(tk); print(f'[yahoo] {tk} ({ysym(tk)}): {e}', file=sys.stderr)
print(f'Yahoo ceny: {len(ok)}/{len(tickers)} OK | fallback interpolace: {", ".join(fail)}')

def ff_lookup(pairs, D):
    """poslední hodnota s datem <= D (forward-fill); pairs je vzestupně setříděné"""
    lo, hi, res = 0, len(pairs) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if pairs[mid][0] <= D: res = pairs[mid][1]; lo = mid + 1
        else: hi = mid - 1
    return res

def trade_price(tk, D):
    pts = tprice.get(tk)
    if not pts: return None
    prev = None
    for d, pr in pts:
        if d <= D: prev = (d, pr)
        else:
            if prev:
                d0 = date.fromisoformat(prev[0]); d1 = date.fromisoformat(d); dd = date.fromisoformat(D)
                f = (dd - d0).days / max((d1 - d0).days, 1)
                return prev[1] + (pr - prev[1]) * f
            return pr
    return prev[1] if prev else pts[-1][1]

def price(tk, D):
    if tk in yhist:
        v = ff_lookup(yhist[tk], D)
        if v is not None:
            return v / DIVIDE.get(tk, 1)
    return trade_price(tk, D)

# ---------- ČNB roční kurzy ----------
fx = {}                                       # date -> {code: rate}
for y in range(2022, date.today().year + 1):
    try:
        txt = http('https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/'
                   f'kurzy-devizoveho-trhu/rok.txt?rok={y}')
    except Exception as e:
        print(f'[cnb] rok {y}: {e}', file=sys.stderr); continue
    lines = txt.splitlines(); hdr = lines[0].split('|')
    cols = [(float(h.split(' ')[0]), h.split(' ')[1]) for h in hdr[1:]]
    for ln in lines[1:]:
        parts = ln.split('|')
        if len(parts) != len(hdr): continue
        dd = parts[0]; D = f'{dd[6:10]}-{dd[3:5]}-{dd[0:2]}'
        rec = {'CZK': 1.0}
        for (qty, code), val in zip(cols, parts[1:]):
            try: rec[code] = float(val.replace(',', '.')) / qty
            except ValueError: pass
        fx[D] = rec
fx_dates = sorted(fx.keys())
def fxrate(ccy, D):
    if ccy == 'CZK': return 1.0
    lo, hi, res = 0, len(fx_dates) - 1, fx_dates[0]
    while lo <= hi:
        mid = (lo + hi) // 2
        if fx_dates[mid] <= D: res = fx_dates[mid]; lo = mid + 1
        else: hi = mid - 1
    return fx[res].get(ccy, fx[fx_dates[-1]].get(ccy, 1.0))

# ---------- rekonstrukce k datu ----------
def hold_asof(D):
    h = {}
    for t in port['trades']:
        if t['date'] <= D:
            tk = str(t['ticker']); h[tk] = h.get(tk, 0) + t['qty']
    return {k: v for k, v in h.items() if abs(v) > 1e-9}

def cash_asof(D):
    b = {}
    def a(br, ccy, x): b[(br, ccy)] = b.get((br, ccy), 0) + x
    for f in port['flows']:
        if f['date'] <= D: a(f['broker'], 'CZK', f['amt'])
    for t in port['trades']:
        if t['date'] <= D:
            a(t['broker'], t['ccy'], -t['qty'] * t['price'] * t.get('mult', 1))
            if t.get('fee'): a(t['broker'], t['ccy'], t['fee'])
    for o in port['others']:
        if o['date'] <= D: a(o['broker'], o['ccy'], o['amt'])
    for c in conv:
        if c['date'] <= D:
            a(c['broker'], c['base'], c['qty']); a(c['broker'], c['quote'], c['proceeds'])
    return b

def nav_asof(D):
    mv = 0.0
    for tk, q in hold_asof(D).items():
        pr = price(tk, D)
        if pr is None: continue
        mv += q * pr * mult_of.get(tk, 1) * fxrate(ccy_of[tk], D)
    csh = sum(bal * fxrate(ccy, D) for (br, ccy), bal in cash_asof(D).items())
    return mv + csh, mv, csh

# ---------- cílové dny ----------
START = min(min(t['date'] for t in port['trades']), min(f['date'] for f in port['flows']))
targets = []
y, m = 2022, 2
while (y, m) <= (2025, 12):
    nd = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    me = (nd - timedelta(days=1)).isoformat()
    if me >= START: targets.append(me)
    m += 1
    if m > 12: m, y = 1, y + 1
d = date(2026, 1, 1); today = date.today()
while d <= today:
    targets.append(d.isoformat()); d += timedelta(days=1)

# ---------- validace vůči skutečnému stavu ----------
cash_today = cash_asof(today.isoformat())
print('\nKontrola hotovosti (rekonstrukce vs portfolio.json):')
for c in port['cash']:
    rec = cash_today.get((c['broker'], c['ccy']), 0.0)
    flag = '' if abs(rec - c['bal']) <= max(2, abs(c['bal']) * 0.03) else '  <-- NESEDÍ'
    print(f"  {c['broker']:6} {c['ccy']}: rekonstrukce {rec:12.2f} vs {c['bal']:12.2f}{flag}")

# ---------- zápis chybějících dnů ----------
hist = jload('nav_history.json')
have = {x['ts'][:10] for x in hist}
added = 0
for D in targets:
    if D in have: continue
    tot, mv, csh = nav_asof(D)
    hist.append({'ts': D + 'T17:00', 'nav': round(tot), 'mv': round(mv), 'cash': round(csh)})
    added += 1
hist.sort(key=lambda x: x['ts'])
hist = hist[-3000:]
jsave('nav_history.json', hist)

# kontrolní výpisy proti známým bodům
def rec_nav(D): return round(nav_asof(D)[0])
print(f'\nDoplněno {added} nových dnů | nav_history má nyní {len(hist)} bodů')
print('Ověření rekonstrukce vůči známým bodům:')
for D, real in [('2026-07-03', 4413157), ('2026-07-18', 4475291)]:
    print(f'  {D}: rekonstrukce {rec_nav(D):,} vs naměřeno {real:,}')
print(f'  dnes {today}: rekonstrukce {rec_nav(today.isoformat()):,}')
