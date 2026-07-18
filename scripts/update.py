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
