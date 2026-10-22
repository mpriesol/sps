# SPS: mobilný príjem balíkov

Samostatná malá webová aplikácia pre príjem zásielok od kuriéra SPS. Otvoríš ju na mobile, zapneš kameru, naskenuješ balíky a na konci kuriér zadá heslo raz pre celú dávku.

## Použitie

1. Prihlás sa heslom aplikácie a nechaj načítať zásielky zo SPS.
2. Stlač **Zapnúť skenovanie** a povoľ fotoaparát. Kamera zostáva zapnutá medzi balíkmi.
3. Rozpoznaný balík sa pridá do dávky, mobil pípne a podľa podpory zavibruje. Rovnaký sken nepridá druhý riadok.
4. Stlač **Prijať balíky**, vyber kuriéra a nechaj ho zadať heslo.
5. Aplikácia prijme balíky postupne a zobrazí výsledok. Pri chybe sa zastaví; po oprave možno potvrdiť zostávajúce balíky znova.

Kódy možno zadať aj ručne, načítať USB/Bluetooth čítačkou do vstupného poľa alebo vybrať zo zoznamu. Čísla s úvodnými nulami zostávajú nezmenené. Párovanie prechádza celý zoznam SPS, aj keď sa na obrazovke zobrazuje iba časť.

## Nasadenie

**[Návod na server a adresu hub.biketrek.sk/sps →](docs/NASADENIE.md)**

Potrebné: Docker s Compose **2.30 alebo novším**, HTTPS a prihlasovacie údaje výdajného miesta do SPS. Aplikácia má vlastné prístupové heslo; prihlasovanie Hubu zatiaľ nepoužíva.

```bash
git clone https://github.com/mpriesol/sps.git
cd sps
cp .env.example sps.env
chmod 600 sps.env
nano sps.env
docker compose up -d --build
```

Vyplň `APP_PASSWORD` (aspoň 16 znakov). Predvolene je zapnuté demo. Port `8081` je dostupný iba lokálne na serveri; mobil pristupuje cez HTTPS reverzný proxy. Pre Caddy bežiaci v Docker kontajneri použi postup v návode vrátane spoločnej siete.

## Konfigurácia

| Premenná | Význam |
| --- | --- |
| `PUBLIC_URL` | Presná verejná adresa, napr. `https://hub.biketrek.sk/sps` |
| `APP_PASSWORD` | Heslo, ktorým sa obsluha prihlási do tejto aplikácie |
| `SESSION_HOURS` | Platnosť prihlásenia, predvolene 12 hodín |
| `SPS_DEMO` | `true`: iba skúšobné dáta; `false`: skutočný SPS systém |
| `SPS_USERNAME` | Prihlasovacie meno účtu výdajného miesta |
| `SPS_PASSWORD` | Heslo účtu výdajného miesta |

`sps.env` sa načíta doslovne: hodnoty nepíš do úvodzoviek. Súbor nepatrí do GitHubu ani do Docker image. Heslo kuriéra sa zadáva až pri príjme a neukladá sa do súboru, databázy ani logov.

## Demo

Pri `SPS_DEMO=true` aplikácia vôbec nekomunikuje so SPS. Obsahuje desať balíkov `DEMO00000001` až `DEMO00000010`, tlačidlo na pridanie skúšobného balíka a kuriéra s heslom **demo**. Na skúšku kamery otvor alebo vytlač [ukážkové čiarové kódy](docs/demo-barcodes.svg) a nasmeruj na ne mobil. Po reštarte servera sa demo balíky obnovia.

## Rozsah prvej verzie

- Kamera v mobilnom prehliadači cez ZXing, zadný fotoaparát, prisvietenie podľa podpory zariadenia.
- Načítanie zásielok, výber dávky, zoznam kuriérov, potvrdenie heslom a výsledok každého balíka.
- Bez databázy, histórie, offline synchronizácie a osobitného riešenia výpadkov.
- Opakované odoslanie nie je serverom aplikácie blokované. Obsluha ho môže zopakovať.
- Jedna inštancia, jeden proces Uvicorn. Pri reštarte aplikácie sa obsluha prihlási znova; rozpracovaná dávka sa po obnovení stránky neuchováva.
- Fotoaparát sa spracúva v mobile, snímky sa na server neposielajú. Potrebné je HTTPS a povolenie kamery.

## Vývoj a testy

Backend: Python 3.12, FastAPI, HTTPX. Frontend: čistý JavaScript, Vite, ZXing. Produkčný image zostaví frontend a podáva ho cez tú istú aplikáciu ako API. Žiadne externé CDN ani platené skenovacie služby.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
.venv/bin/python -m pytest -q
```

Lokálny vývoj (samostatné testovacie heslo):

```bash
export PUBLIC_URL=http://localhost:8080
export APP_PASSWORD=local-development-password
export SPS_DEMO=true
.venv/bin/uvicorn app.main:create_app --factory --port 8080
```

Otvor `http://localhost:8080/`. Pri prístupe z mobilu cez LAN IP potrebuješ HTTPS, localhost výnimka platí iba na samotnom zariadení.

GitHub Actions spúšťa frontendové a backendové testy aj zostavenie Docker image. Testy nikdy neprijímajú reálne zásielky.

## SPS integrácia

Adaptér používa rovnaké interné rozhranie ako portál SPS, identifikované v jeho JavaScripte 22. 10. 2026. Nejde o potvrdené verejné integračné API s garantovanou stabilitou. Pozri [technické poznámky](docs/SPS-INTEGRACIA.md).

Pred bežným používaním over na serveri prihlásenie, zoznam zásielok a jeden skutočný príjem s kuriérom. Funkčnosť na konkrétnom telefóne závisí aj od fotoaparátu a kvality etikety.
