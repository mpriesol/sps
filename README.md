# SPS: mobilný príjem balíkov

Samostatná malá webová aplikácia pre príjem zásielok od kuriéra SPS. Otvoríš ju na mobile, zapneš kameru, naskenuješ balíky a na konci prijmeš celú dávku. Známe heslo kuriéra sa použije automaticky.

## Použitie

1. Prihlás sa heslom aplikácie a nechaj načítať zásielky zo SPS.
2. Stlač **Zapnúť skenovanie** a povoľ fotoaparát. Kamera zostáva zapnutá medzi balíkmi.
3. Nový balík: jedno vyššie pípnutie. Už naskenovaný: dve vyššie pípnutia. Kód mimo zoznamu: tri nižšie pípnutia. Rovnaký sken nepridá druhý riadok.
4. Stlač **Prijať balíky**. Aplikácia použije prvého kuriéra presne v poradí zo SPS a jeho uložené heslo.
5. Ak heslo chýba alebo prijatie zlyhá, zobrazí sa výber kuriéra a heslo. Po úspešnom prijatí sa zadané heslo automaticky uloží.
6. Aplikácia prijme balíky postupne a zobrazí výsledok. Pri chybe sa zastaví; po oprave sa posielajú iba zostávajúce balíky.

Kódy možno zadať aj ručne, načítať USB/Bluetooth čítačkou do vstupného poľa alebo vybrať zo zoznamu. Čísla s úvodnými nulami zostávajú nezmenené. Párovanie prechádza celý zoznam SPS, aj keď sa na obrazovke zobrazuje iba časť.

## Hlasové zadávanie po slovensky

Stlač **Hlasom**, povoľ mikrofón a povedz **posledné dva znaky kódu**, každý zvlášť. Mobil môže zostať položený na poličke: netreba držať tlačidlo ani po každom balíku klikať. Hlasový režim zostáva zapnutý pre ďalšie balíky; medzi kódmi urob krátku pauzu.

| Povieš | Aplikácia hľadá koniec |
| --- | --- |
| „pé tri“ | `P3` |
| „osem sedem“ | `87` |
| „nula sedem“ | `07` |
| „á bé“ | `AB` |

Jediná zhoda pridá celý balík do dávky a pípne. Opakovanie rovnakého balíka dvojpípne. Neznámy alebo nerozpoznaný koniec dá tri nižšie pípnutia. Pri viacerých zhodách sa nič nepridá a aplikácia si vypýta posledné tri znaky. Povedz znovu **celý dlhší koniec**, napríklad „em pé tri“ pre `MP3`. Ak je aj ten nejednoznačný, vypýta si ďalší znak. Po úspechu znova stačia dva znaky. Do porovnania sa počítajú aj už naskenované balíky, aby sa pri nejednoznačnosti nevybral automaticky iný balík. Aplikácia porovnáva všetky vyslovené znaky; dlhší koniec nikdy neskráti na dva.

Režim ukazuje počutý text aj jeho prevod na znaky. Hlasové rozpoznávanie nastavuje slovenčinu `sk-SK` cez `SpeechRecognition` / `webkitSpeechRecognition`. Vyžaduje podporovaný mobilný prehliadač, HTTPS a povolenie mikrofónu. Prepis môže využívať online službu poskytovateľa prehliadača; aplikácia nenahráva zvuk na svoj server ani nevyžaduje vlastný API kľúč. Ak rozpoznávanie nie je dostupné alebo prehliadač nepodporuje slovenčinu, zobrazí vysvetlenie a môžeš ďalej používať kameru či ručné zadanie.

Pri viacerých zhodách zaznie aj hlasová požiadavka na dlhší koniec; pri nenájdenom balíku alebo nejasnom prepise zaznie krátke vysvetlenie. Počas vlastnej hlasovej výzvy aplikácia prestane počúvať, aby nezachytila svoj reproduktor, a potom počúvanie sama obnoví. Dostupnosť a výslovnosť hlasových výziev závisí od hlasov v telefóne. V podporovanom prehliadači sa počas hlasového režimu udržuje zapnutý displej. Ak telefón udržanie displeja odmietne, napríklad pri šetrení batérie, treba predĺžiť čas zhasnutia v nastavení telefónu. Dosah mikrofónu treba overiť podľa vzdialenosti a hluku v predajni; nie je potrebné držať telefón v ruke.

Tlačidlo **Stop hlas** režim vypne. Vypne sa aj pri zapnutí kamery, obnovení zoznamu, otvorení kuriérov, potvrdení dávky, odhlásení alebo prepnutí aplikácie do pozadia. Hlas pridáva balíky iba do dávky; prijatie do SPS naďalej spúšťa tlačidlo **Prijať**.

Prepis z reálneho mikrofónu treba vyskúšať na konkrétnom mobile. Jednotkové testy overujú prevod slovenských názvov, úvodné nuly, jednoznačnosť aj životný cyklus rozpoznávania; nenahrádzajú skúšku presnosti reči v predajni.

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

`sps.env` sa načíta doslovne: hodnoty nepíš do úvodzoviek. Súbor nepatrí do GitHubu ani do Docker image. Heslá kuriérov sa ukladajú na serveri do Docker volume `courier_passwords` (súbory v `/app/data`, prístup `0600`). Sú uložené v čitateľnom tvare pre aplikáciu, nie v prehliadači ani v GitHube. API vracia iba mená a informáciu o uloženom hesle, nikdy samotné heslo. Demo a jednotlivé SPS účty majú oddelené súbory. Pri lokálnom vývoji možno cestu zmeniť cez `DATA_DIR` (predvolene `data`).

## Uložené heslá kuriérov

Tlačidlo **Kuriéri** v hornej lište umožňuje priradiť známe heslá dopredu, zmeniť ich a odstrániť. Noví kuriéri sa načítajú zo SPS podľa dostupných zásielok; už uložených možno upravovať aj bez čakajúcich balíkov. Uloženie hesla v tomto okne neprijíma žiadne zásielky. Heslo sa overí SPS až pri skutočnom príjme.

Pri automatickom príjme sa skúša iba prvý kuriér zo SPS. Aplikácia neprechádza ďalších kuriérov a neháda heslá. Po odmietnutí zobrazí formulár; opravené heslo si zapamätá po úspechu. Uloženie platí aj pre ostatné mobily a prežije reštart či aktualizáciu kontajnera.

## Skenovanie na mobile

Pod hornou lištou je priamo skener. Obraz vyplní celé okno stredovým výrezom bez deformácie; dekóduje sa rovnaký výrez, ktorý vidíš. Správa sa zobrazuje hneď pod obrazom a **Obnoviť** je pri zozname balíkov.

Zvuk zaznie pri každom rozpoznaní, aj pri opakovane držanom kóde (interval približne 0,9 sekundy). Nerozpoznaný obraz bez dekódovaného kódu nepípa. Hlasitosť signálu je zvýšená; výsledná hlasitosť závisí aj od nastavenia médií a reproduktora mobilu.

## Demo

Pri `SPS_DEMO=true` aplikácia vôbec nekomunikuje so SPS. Obsahuje desať balíkov `DEMO00000001` až `DEMO00000010`, tlačidlo na pridanie skúšobného balíka a kuriéra s heslom **demo**. Na skúšku kamery otvor alebo vytlač [ukážkové čiarové kódy](docs/demo-barcodes.svg) a nasmeruj na ne mobil. Po reštarte servera sa demo balíky obnovia.

## Rozsah

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
