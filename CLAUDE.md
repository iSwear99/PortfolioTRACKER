# Pravidla projektu — portfolio tracker

Repozitář automaticky publikuje investiční tracker (docs/index.html přes
GitHub Pages). Data v CZK bázi, dva brokeři: IBKR (U13354925) a Patria
(22636233). Jazyk komunikace i commitů: čeština.

## Struktura dat

- `data/portfolio.json` — zdroj pravdy:
  - `positions`: otevřené pozice {ticker, broker, ccy, qty, avg}
    (`avg` = Ø nákupní cena v měně instrumentu)
  - `trades`: všechny obchody {date YYYY-MM-DD, broker, ticker,
    typ Stock|Option|Adj, ccy, qty (prodej záporně), price, mult
    (opce 100, jinak 1), fee (záporně, v měně obchodu), rpl
    (realizovaný P/L v měně dle brokera; u Patrie dopočet)}
    — pole `rplczk` a `kurz_hist` NIKDY needitovat ručně, plní je ledger.py
  - `others`: dividendy/daně/úroky/poplatky {date, broker, typ, desc, ccy, amt}
    — pole `czk` plní ledger.py
  - `flows`: vklady/výběry {date, broker, amt v CZK, výběr záporně}
  - `cash`: aktuální hotovost {broker, ccy, bal}
- `data/conversions.json` — měnové konverze {date, broker, base, qty
  (nákup base kladně), quote, proceeds (v quote, opačné znaménko)}
- `data/prices.json` — poslední ceny + mapování na Twelve Data
  {TICKER: {px, q, exchange?, divide?}}

## Povinný postup při každé změně dat

1. Uprav příslušné soubory v `data/`.
2. Spusť `python scripts/ledger.py` — přepočte historické CZK hodnoty
   a rozpad P/L. Nesmí skončit chybou; varování o nesouladu zůstatků
   znamená chybu v zadaných datech (oprav data, neignoruj).
3. Spusť `python scripts/update.py` — přegeneruje docs/index.html.
4. Commitni s výstižnou českou zprávou (např. "obchod: nákup 50 PYPL @44.20").

## Pravidla pro zápis obchodu

- Nákup: přidej řádek do `trades`; pokud jde o nový ticker, přidej ho do
  `positions` (avg = cena) a do `prices.json` (px = cena, q = ticker,
  exchange dle burzy). U existující pozice přepočti `qty` a `avg`
  metodou průměrných nákladů: avg_new = (qty*avg + qty_buy*price)/(qty+qty_buy).
- Prodej: řádek do `trades` (qty záporně), u pozice sniž `qty`
  (avg beze změny); při qty=0 pozici z `positions` odstraň
  (v `prices.json` může zůstat). `rpl` v měně = qty_sold*(price-avg)
  u Patrie; u IBKR převezmi hodnotu z konfirmace, je-li k dispozici.
- Sniž/zvyš odpovídající `cash` zůstatek o hodnotu obchodu vč. poplatku,
  pokud uživatel neuvede jinak.
- Měnová konverze: řádek do `conversions.json` + úprava obou `cash` zůstatků.
- Vklad/výběr: řádek do `flows` + úprava `cash`.
- Dividenda/daň/úrok: řádek do `others` + úprava `cash`.

## Validace (vždy zkontroluj po ledger.py)

- `data/ledger_summary.json`: `shortfalls` se nesmí zvětšit oproti
  předchozímu stavu (znamenalo by chybějící konverzi k nákupu).
- Kusy v `positions` musí sedět na součet `trades` per (ticker, broker)
  — ledger.py to kontroluje sám a při nesouladu selže.

## Čeho se nedotýkat

- `docs/template.html` needituj kvůli datům — data tečou přes __DATA__.
- Sekce `breakdown_hist` v portfolio.json přepisuje ledger.py.
- Necommituj API klíče; TWELVE_DATA_KEY žije jen v GitHub Secrets.
