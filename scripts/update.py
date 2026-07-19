# -*- coding: utf-8 -*-
"""Aktualizace portfolia: Twelve Data + záloha Yahoo Finance + kurzy ČNB."""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    PRAGUE = ZoneInfo('Europe/Prague')
except Exception:  # fallback, kdyby chyběla tzdata
    PRAGUE = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def p(*a): return os.path.join(ROOT, *a)

def jload(path):
    with open(path, encoding='utf-8') as f: return json.load(f)
def jsave(path, obj):
    with open(path, 'w', encoding='utf-8') as f: json.dump(obj, f, ensure_ascii=False, indent=1)

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) portfolio'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8')

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
        print(f'[WARN] ČNB nedostupná ({e}).', file=sys.stderr)
    return fx, src

def fetch_cnb_prev(old_fx):
    """Předchozí ČNB fixing (předposlední vyhlášený den) — pro denní změnu s kurzem.
    Na přelomu roku dotáhne i loňský soubor. Když se nepodaří, vrátí old_fx
    (kurzová složka denní změny pak vyjde ~0)."""
    prev = dict(old_fx)
    try:
        yr = datetime.now(timezone.utc).astimezone(PRAGUE or timezone.utc).year
        days = []                                          # [(datum, {code: rate})] chronologicky
        for y in (yr - 1, yr):                             # loni + letos (kvůli přelomu roku)
            try:
                txt = http_get('https://www.cnb.cz/cs/financni-trhy/devizovy-trh/'
                               f'kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/rok.txt?rok={y}')
                lines = [l for l in txt.strip().split('\n') if '|' in l]
                if len(lines) < 2: continue
                cols = [(float(h.split(' ')[0]), h.split(' ')[1]) for h in lines[0].split('|')[1:]]
                for ln in lines[1:]:                       # každý rok s vlastní hlavičkou
                    parts = ln.split('|')
                    if len(parts) != len(cols) + 1: continue
                    rec = {'CZK': 1.0}
                    for (qty, code), val in zip(cols, parts[1:]):
                        try: rec[code] = round(float(val.replace(',', '.')) / qty, 4)
                        except ValueError: pass
                    days.append((parts[0], rec))
            except Exception:
                pass
        if len(days) >= 2:
            prev.update(days[-2][1])                       # předposlední vyhlášený den
    except Exception as e:
        print(f'[WARN] ČNB předchozí fixing nedostupný ({e}).', file=sys.stderr)
    return prev

def fetch_prices(seed):
    key = os.environ.get('TWELVE_DATA_KEY', '').strip()
    prices = {t: dict(v) for t, v in seed.items()}
    fetched, failed = [], []
    if not key:
        return prices, fetched, list(prices)
    for i, (t, cfg) in enumerate(prices.items()):
        if i: time.sleep(8.5)
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
                if cfg.get('divide'): px /= cfg['divide']
                if px > 0:
                    cfg['px'] = round(px, 4); fetched.append(t)
                else:
                    failed.append(t)
                break
            except Exception as e:
                if attempt == 2:
                    failed.append(t)
                    print(f'[WARN] TD {t}: {e}', file=sys.stderr)
    return prices, fetched, failed

# ---------- Yahoo Finance záloha ----------
YAHOO = {'BOSS': ('BOSS.DE', 1), 'P911': ('P911.DE', 1), 'NOV': ('NOV.DE', 1),
         'VOW3': ('VOW3.DE', 1), 'CSPX': ('CSPX.L', 1), 'WIZZ': ('WIZZ.L', 100),
         'EVO': ('EVO.ST', 1), '4GLD': ('4GLD.DE', 1)}

def fetch_yahoo(prices, failed):
    recovered = []
    for t in list(failed):
        sym, div = YAHOO.get(t, (t, 1))
        try:
            time.sleep(1.5)
            raw = json.loads(http_get(
                f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}'
                '?range=1d&interval=1d'))
            px = raw['chart']['result'][0]['meta']['regularMarketPrice'] / div
            last = prices[t]['px']
            if px > 0 and (last <= 0 or 0.33 < px / last < 3):
                prices[t]['px'] = round(px, 4)
                recovered.append(t); failed.remove(t)
            else:
                print(f'[WARN] Yahoo {t}: {px} neprošlo kontrolou vs {last}', file=sys.stderr)
        except Exception as e:
            print(f'[WARN] Yahoo {t} ({sym}): {e}', file=sys.stderr)
    return recovered

def fetch_prev_close(positions):
    """Předchozí close (Yahoo chartPreviousClose) pro tickery pozic — pro denní změnu."""
    out = {}
    for i, tk in enumerate(sorted({pos['ticker'] for pos in positions})):
        sym, div = YAHOO.get(tk, (tk, 1))
        if i: time.sleep(1.3)
        try:
            res = json.loads(http_get(
                f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}'
                '?range=5d&interval=1d'))['chart']['result'][0]
            pc = res['meta'].get('previousClose')          # skutečný včerejší close
            if not pc:                                      # fallback: předposlední denní close z řady
                cl = [c for c in (res['indicators']['quote'][0].get('close') or []) if c is not None]
                pc = cl[-2] if len(cl) >= 2 else None
            if pc and pc > 0: out[tk] = round(float(pc) / div, 4)
        except Exception as e:
            print(f'[WARN] prev-close {tk} ({sym}): {e}', file=sys.stderr)
    return out

def compute(static, prices, fx, prev_px, fx_prev):
    positions = []
    for pos in static['positions']:
        px = prices[pos['ticker']]['px']; r = fx[pos['ccy']]
        mv = pos['qty'] * px * r
        pl = pos['qty'] * (px - pos['avg']) * r
        # denní změna (varianta C): dnešní hodnota − včerejší hodnota téže pozice,
        # cena vs. předchozí close, každý konec svým ČNB fixingem (mid, bez poplatku)
        pc = prev_px.get(pos['ticker']); rp = fx_prev.get(pos['ccy'], r)
        dch = round(pos['qty'] * (px * r - pc * rp)) if pc else None
        positions.append(dict(pos, price=px, mv=round(mv), pl=round(pl),
                              plpct=round((px - pos['avg']) / pos['avg'] * 100, 1) if pos['avg'] else 0,
                              dch=dch))
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
    fx_prev = fetch_cnb_prev(fxold)
    prices, ok, failed = fetch_prices(seed)
    rec = fetch_yahoo(prices, failed)
    prev_px = fetch_prev_close(static['positions'])

    positions, cash, meta = compute(static, prices, fx, prev_px, fx_prev)
    tz = PRAGUE or timezone.utc
    now = datetime.now(timezone.utc).astimezone(tz).strftime('%Y-%m-%dT%H:%M')
    meta['asof'] = now
    meta['irr'] = xirr(static['flows'], meta['nav'], now[:10])
    meta['fx_src'] = fx_src
    note = f'{len(ok)}/{len(prices)} cen z Twelve Data'
    if rec: note += f'; {len(rec)} z Yahoo ({", ".join(rec)})'
    if failed: note += f'; bez aktualizace: {", ".join(failed)}'
    meta['price_note'] = note

    hist = jload(p('data', 'nav_history.json'))
    hist.append(dict(ts=now, nav=meta['nav'], mv=meta['mv'], cash=meta['cash']))
    hist = hist[-3000:]

    jsave(p('data', 'prices.json'), prices)
    jsave(p('data', 'fx.json'), dict(fx=fx, src=fx_src))
    jsave(p('data', 'nav_history.json'), hist)

    payload = dict(meta=meta, history=hist, positions=positions, cash=cash,
                   trades=static['trades'], others=static['others'], flows=static['flows'])

    tpl = open(p('docs', 'template.html'), encoding='utf-8').read()
    html = tpl.replace('__DATA__', json.dumps(payload, ensure_ascii=False))
    open(p('docs', 'index.html'), 'w', encoding='utf-8').write(html)
    print(f'OK: NAV {meta["nav"]:,} Kč | {note} | {fx_src}')

if __name__ == '__main__':
    main()
