# Portfolio tracker — automatická aktualizace 2× denně (zdarma)

Statický tracker (docs/index.html) hostovaný na GitHub Pages, přegenerovaný
GitHub Actions každý den v 9:00 a 20:00 (Praha, letní čas). Ceny: Twelve Data
(free tier), kurzy: ČNB denní fixing. Vše v rámci bezplatných limitů.

## Zprovoznění (jednorázově, ~10 minut)

1. **Twelve Data klíč (zdarma):** registrace na https://twelvedata.com → API key.
   Free tier = 800 requestů/den; tracker spotřebuje ~50/den.
2. **GitHub repozitář:** založte nový (klidně private → Pages ale vyžaduje
   public, případně GitHub Pro), nahrajte obsah této složky.
3. **Secret:** Settings → Secrets and variables → Actions → New repository
   secret → jméno `TWELVE_DATA_KEY`, hodnota = váš klíč.
4. **Pages:** Settings → Pages → Source: "Deploy from a branch" →
   branch `main`, folder `/docs`.
5. **První běh:** záložka Actions → workflow "Aktualizace portfolia" →
   Run workflow. Po doběhnutí je tracker na
   `https://<užjméno>.github.io/<repo>/`.

## Co dělá každý běh

- stáhne kurzy ČNB (denní fixing, bez klíče),
- stáhne ceny všech tickerů z `data/prices.json` (při výpadku ponechá
  poslední známou cenu a napíše to do patičky trackeru),
- přepočte pozice, hotovost, NAV, zisk a IRR,
- připíše bod do `data/nav_history.json` → z něj se počítá Day/MTD/YTD
  (očištěno o vklady/výběry),
- vygeneruje `docs/index.html` a commitne.

## Údržba dat

- **Nový obchod / vklad:** doplňte do `data/portfolio.json`
  (positions/trades/flows/cash) — formát je zřejmý z existujících záznamů.
- **Zimní čas:** v `.github/workflows/update.yml` posuňte cron na
  `0 8` a `0 19` UTC (GitHub cron neumí časové zóny; reálný start může
  mít pár minut zpoždění, na 2× denní snapshot to nevadí).
- **Mapování symbolů** je v `data/prices.json`. Po prvním běhu zkontrolujte
  patičku trackeru: pokud je některý ticker v "bez aktualizace", upravte
  `q`/`exchange`. Předvyplněno: XETR (BOSS, P911, VOW3, NOV — NOV ověřit),
  LSE (CSPX; WIZZ s dělením 100 kvůli GBX), OMX (EVO). OTC ADR (BYDDY,
  DIDIY) nemusí být na free tieru dostupné — pak zůstane poslední cena.

## Omezení (poctivě)

- Free tier Twelve Data dodává zpožděná data (řádově hodiny) — pro
  2× denní snapshot bez významu, pro intradenní sledování nevhodné.
- Day/MTD/YTD se začnou plnit až s nasbíranou historií běhů.
- IRR se počítá numericky (konvence 365,25 dne) — může se o desetinu
  procentního bodu lišit od XIRR v Excelu.

---

# Automatický zápis obchodů (Claude Code)

Po nastavení stačí založit GitHub issue s textem typu
`@claude přidej obchod: 8.7.2026 nákup 50 ks PYPL @ 44,20 USD, IBKR, poplatek 1 USD`
— Claude upraví data, spustí ledger.py + update.py, zvaliduje a commitne.
Pravidla, kterými se řídí, jsou v `CLAUDE.md`.

## Nastavení (jednorázově)

1. Nainstalujte Claude Code lokálně (návod: https://code.claude.com/docs)
   a ve složce repozitáře spusťte `claude` → příkaz `/install-github-app`
   — průvodce nainstaluje GitHub aplikaci a založí secret s klíčem.
   (Alternativně ručně: Marketplace „Claude Code Action Official" +
   secret `ANTHROPIC_API_KEY` v Settings → Secrets → Actions.)
2. Workflow `.github/workflows/claude.yml` je už v repu — po instalaci
   aplikace funguje bez dalších úprav.
3. Otestujte: založte issue „@claude vypiš aktuální NAV z data/nav_history.json".

## Poznámky

- Spotřeba: jednotky až desítky Kč měsíčně v API kreditech dle počtu zásahů.
- ledger.py je jediný zdroj historických CZK přepočtů; při každé změně dat
  se spouští znovu, takže rozpad P/L zůstává konzistentní.
- Kontrola: každý zásah Claude commitne — diff vidíte v historii repa.
