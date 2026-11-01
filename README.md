# Portfolio tracker

Statický investiční tracker (`docs/index.html`) publikovaný přes GitHub Pages
a přegenerovaný GitHub Actions **3× denně** (crony 7:20 / 18:20 / 21:20 UTC —
ráno, večer a po závěru US seance). Vše v CZK bázi, tři účty: IBKR, Patria
a Anycoin (BTC). Ceny: Twelve Data (free tier) se zálohou Yahoo Finance;
evropské tituly jdou na Yahoo rovnou. Kurzy: ČNB denní fixing.

## Co dělá každý běh (`scripts/update.py`)

- stáhne ČNB fixing (aktuální i předchozí — pro kurzovou složku denní změny),
- stáhne ceny držených titulů a poslední dvě uzavřené denní close z Yahoo
  (sloupec „Denní změna" u pozic = včerejší close × dnešní fixing − předvčerejší
  close × včerejší fixing),
- přepočte pozice, hotovost, NAV, zisk a IRR (XIRR z toků),
- připíše snapshot do `data/nav_history.json` (NAV, ceny, kurzy, cash per měna)
  a historii ředí: posledních 120 dní vše, starší dny 2026 jeden bod/den,
  před 2026 jen konce měsíců (backfill 2022–2025 se nikdy nemaže),
- zjistí pohyby ≥ 2,5 % (BTC ≥ 5 %) vs předchozí snapshot u **držených**
  titulů → `data/alerts_pending.json`,
- vygeneruje `docs/index.html` a commitne.

Po úspěšném běhu se spustí workflow **Breaking news**: pokud čekají alerty,
Claude agent (předplatné přes `CLAUDE_CODE_OAUTH_TOKEN`, model Opus 5)
dohledá na webu příčinu pohybu, zapíše ji do `data/news.json`, alerty
vyprázdní, přegeneruje web a commitne. Zprávy se zobrazují v pruhu
BREAKING (24 h) a u hybatelů období (podtržený ticker → klik).

## Web (`docs/template.html`)

- KPI: NAV, **Denní změna** (NAV posledních dvou uzavřených obchodních dnů,
  po–pá, očištěno o toky), IRR dle období, vložený kapitál.
- Přepínač Day / 7D / 30D / YTD / All-time řídí levý box změny NAV, hybatele
  (TOP ±5 s kontrolním řádkem Σ tituly + FX + ostatní = Δ NAV), IRR a graf
  výkonnosti (na Day skrytý). Pravý box = změna za 7 dní (bod nejblíže 168 h).
- Pozice sloučené přes brokery (jeden řádek na ticker, chip brokera; klik →
  rozpad per broker + historie obchodů), první sloupec přilepený.
- Data tečou přes zástupný text `__DATA__` — šablonu needitovat kvůli datům.

## Data a údržba

Zdroj pravdy je `data/portfolio.json` (+ `conversions.json`, `prices.json`).
Postup při každé změně dat, formát záznamů a pravidla zápisu obchodů jsou
v `CLAUDE.md` (ledger.py → update.py → commit). `scripts/ledger.py` přepočítá
historické CZK hodnoty metodou skutečných konverzí a selže při nesouladu
zůstatků (tolerance 2 jednotky / 0,2 %) nebo kusů. `scripts/backfill_nav.py`
je jednorázová rekonstrukce historie NAV (konce měsíců 2022–2025, denně 2026).

Zápis obchodů lze zadat i GitHub issue s `@claude …` (workflow
`claude.yml`, stejné předplatné jako news agent).

## Zprovoznění

1. Secrets (Settings → Secrets → Actions): `TWELVE_DATA_KEY` (zdarma,
   twelvedata.com), `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`).
2. Pages: Deploy from a branch → `main`, folder `/docs`.
3. Actions → „Aktualizace portfolia" → Run workflow.

## Omezení

- Free tier Twelve Data = zpožděná data; snapshoty 3× denně to nevadí.
- GitHub cron neumí časové zóny — v zimě běhy o hodinu dřív (8:20 / 19:20 /
  22:20 Prahy), US závěr 22:00 je stále pokrytý.
- Svátky v pracovních dnech ukáží denní změnu ~0 (kalendář burz se nevede).
- Repozitář je veřejný — čísla účtů a citlivé údaje do něj nepatří.
