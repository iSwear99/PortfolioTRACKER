# -*- coding: utf-8 -*-
"""
Aktualizace portfolia: ceny (Twelve Data, free tier) + kurzy (ČNB denní fixing).
Přepočte pozice, NAV, uloží historii a vygeneruje docs/index.html z šablony.
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def p(*a): return os.path.join(ROOT, *a)

def jload(path):
    with open(path, encoding='utf-8') as f: return json.load(f)
def jsave(path, obj):
    with open(path, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False, indent=1)

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': 'portfolio-tracker/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8')

# ---------- ČNB denní fixing ----------
def fetch_cnb_fx(old_fx):
    fx = dict(old_fx); src = 'poslední známé kurzy'
    try:
        txt = http_get('https://www.cnb.cz/cs/financni-trhy/devizovy-trh/'
                       'kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt')
        lines = txt.strip().split('\n')
        date_line = lines[0].split(' ')[0]
        for line in lines[2:]:
            parts = line.split('|')
            if len(parts) != 5: continue
            qty = float(parts[2]); code = parts[3]
            rate = float(parts[4].replace(',', '.')) / qty
            if code in ('USD', 'EUR', 'GBP', 'SEK', 'DKK', 'HKD'):
                fx[code] = round(rate, 4)
        fx['CZK'] = 1.0
        src = f'ČNB fixing {date_line}'
    except Exception as e:
        print(f'[WARN] ČNB nedostupná ({e}), používám poslední kurzy.', file=sys.stderr)
    return fx, src

# ---------- Twelve Data (free tier: 8 kreditů/min -> nutné zpomalit) ----------
def fetch_prices(seed):
    key = os.environ.get('TWELVE_DATA_KEY', '').strip()
    prices = {t: dict(v) for t, v in seed.items()}
    fetched, failed = [], []
    if not key:
        print('[WARN] TWELVE_DATA_KEY není nastaven — ceny zůstávají poslední známé.', file=sys.stderr)
        return prices, fetched, list(prices)
    for i, (t, cfg) in enumerate(prices.items()):
        if i: time.sleep(8.5)               # limit 8 dotazů/min
        for attempt in (1, 2):
            try:
                params = {'symbol': cfg['q'], 'apikey': key}
                if cfg.get('exchange'): params['exchange'] = cfg['exchange']
                raw = json.loads(http_get('https://api.twelvedata.com/price?'
                                          + urllib.parse.urlencode(params)))
                if isinstance(raw, dict) and raw.get('code') == 429:
                    if attempt == 1: time.sleep(61); continue
                    raise RuntimeError('rate limit')
                px = float(raw['price'])
                if cfg.get('divide'): px /= cfg['divide']   # LSE: GBX -> GBP
                if px > 0:
                    cfg['px'] = round(px, 4); fetched.append(t)
                else:
                    failed.append(t)
                break
            except Exception as e:
                if attempt == 2:
                    failed.append(t)
                    print(f'[WARN] {t}: {e} — ponechávám poslední cenu {cfg["px"]}', file=sys.stderr)
    return prices, fetched, failed

# ---------- výpočet ----------
def compute(static, prices, fx):
    positions = []
    for pos in static['positions']:
        px = prices[pos['ticker']]['px']; r = fx[pos['ccy']]
        mv = pos['qty'] * px * r
        pl = pos['qty'] * (px - pos['avg']) * r
        positions.append(dict(pos, price=px, mv=round(mv), pl=round(pl),
                              plpct=round((px - pos['avg']) / pos['avg'] * 100, 1) if pos['avg'] else 0))
    cash = [dict(c, czk=round(c['bal'] * fx[c['ccy']])) for c in static['cash']]
    mv = sum(x['mv'] for x in positions); csh = sum(x['czk'] for x in cash)
    nav = mv + csh; dep = static['deposits']
    return positions, cash, dict(nav=round(nav), mv=round(mv), cash=round(csh),
                                 deposits=dep, gain=round(nav - dep),
                                 gainpct=round((nav - dep) / dep * 100, 1))

def xirr(flows, nav, asof):
    from datetime import date
    def d(s): y, m, dd = map(int, s.split('-')); return date(y, m, dd)
    cf = [(d(f['date']), -f['amt']) for f in flows] + [(d(asof), nav)]
    t0 = cf[0][0]
    def npv(r):
        return sum(a / (1 + r) ** ((dt - t0).days / 365.25) for dt, a in cf)
    lo, hi = -0.95, 5.0
    for _ in range(120):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0: hi = mid
        else: lo = mid
    return round((lo + hi) / 2 * 100, 1)

def main():
    static = jload(p('data', 'portfolio.json'))
    seed = jload(p('data', 'prices.json'))
    fxold = jload(p('data', 'fx.json'))['fx']

    fx, fx_src = fetch_cnb_fx(fxold)
    prices, ok, failed = fetch_prices(seed)

    positions, cash, meta = compute(static, prices, fx)
    now = datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%dT%H:%M')
    meta['asof'] = now
    meta['irr'] = xirr(static['flows'], meta['nav'], now[:10])
    meta['fx_src'] = fx_src
    meta['price_note'] = (f'{len(ok)}/{len(prices)} cen aktualizováno'
                          + (f'; bez aktualizace: {", ".join(failed)}' if failed else ''))

    hist = jload(p('data', 'nav_history.json'))
    hist.append(dict(ts=now, nav=meta['nav'], mv=meta['mv'], cash=meta['cash']))
    hist = hist[-1500:]

    jsave(p('data', 'prices.json'), prices)
    jsave(p('data', 'fx.json'), dict(fx=fx, src=fx_src))
    jsave(p('data', 'nav_history.json'), hist)

    payload = dict(meta=meta, fx=fx, history=hist,
                   breakdown=static['breakdown_hist'], fees=static['fees'],
                   positions=positions, cash=cash,
                   trades=static['trades'], others=static['others'], flows=static['flows'])

    tpl = open(p('docs', 'template.html'), encoding='utf-8').read()
    html = tpl.replace('__DATA__', json.dumps(payload, ensure_ascii=False))
    open(p('docs', 'index.html'), 'w', encoding='utf-8').write(html)
    print(f'OK: NAV {meta["nav"]:,} Kč | {meta["price_note"]} | {fx_src}')

if __name__ == '__main__':
    main()
