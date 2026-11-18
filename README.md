# SPS: príjem, police a výdaj balíkov

Mobilná aplikácia pre výdajné miesto SPS. Prijíma zásielky, eviduje ich fyzické miesta, nájde balík podľa PIN-u zákazníka a rozlišuje **Výdaj zákazníkovi** od **Výdaja kuriérovi**. Kuriérovi možno odovzdať podania od zákazníkov aj expirované zásielky. Uložené heslo patrí konkrétnemu kuriérovi; výber vidíš a môžeš ho zmeniť pred potvrdením.

**[Police, fotografie, výdaj, vratky a inventúra — návod →](docs/POLICE-A-VYDAJ.md)**

## Použitie

1. Prihlás sa heslom aplikácie a nechaj načítať zásielky zo SPS.
2. Vyber balíky ťuknutím na čísla v dvojstĺpcovej mriežke, cez **Vybrať všetky**, hlasom alebo skenerom. Opätovné ťuknutie zruší výber. Náhľad kamery je predvolene skrytý: rozbalí sa až tlačidlom **Skenovať**, posunie zoznam nižšie a zostáva zapnutý medzi balíkmi.
3. Nový balík: jedno vyššie pípnutie. Už naskenovaný: dve vyššie pípnutia. Kód mimo zoznamu: tri nižšie pípnutia. Rovnaký sken nepridá druhý riadok.
4. Pred potvrdením skontroluj zobrazeného kuriéra. Aplikácia navrhne posledného úspešne použitého aj z predchádzajúceho dňa; prvého v zozname SPS použije iba pri prvom použití. Výber možno ľahko zmeniť.
5. Ak heslo chýba alebo prijatie zlyhá, zobrazí sa výber kuriéra a heslo. Po úspešnom prijatí sa zadané heslo automaticky uloží.
6. Aplikácia prijme balíky postupne a zobrazí výsledok. Pri chybe sa zastaví; po oprave sa posielajú iba zostávajúce balíky. Ak sa stratí odpoveď SPS, výsledok sa označí ako neoverený a aplikácia ho neposiela naslepo znovu.

Príjem má jeden spoločný zoznam zásielok bez mien odosielateľov a tlačidiel plus. Podsvietené karty sú vybrané na potvrdenie; prijaté, odosielané a neoverené operácie majú vlastný stav. Voľba **Ukladať do políc počas príjmu** je v **Nastaveniach** a platí aj pre ďalšie mobily prevádzky.

Kódy možno zadať aj ručne alebo načítať USB/Bluetooth čítačkou do vstupného poľa. Čísla s úvodnými nulami zostávajú nezmenené. Párovanie prechádza celý zoznam SPS, aj keď sa na obrazovke zobrazuje iba časť.

## Hlasové zadávanie po slovensky

Tlačidlo **Hlasom** je dostupné pri príjme, ukladaní a inventúre, výdaji kuriérovi aj overovaní balíka pri výdaji zákazníkovi. Pri konečnom overení balíka pre zákazníka treba vysloviť celé číslo; krátky koniec nestačí. Ostatné režimy rozlišujú balíky podľa jednoznačného konca čísla v aktuálnom zozname.

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

Tlačidlo **Stop hlas** režim vypne. Vypne sa aj pri zmene pracovného režimu, zapnutí kamery, obnovení zoznamu, otvorení kuriérov, potvrdení dávky, odhlásení alebo prepnutí aplikácie do pozadia. Hlas pracuje s balíkmi aktuálneho režimu; sám nepotvrdzuje príjem ani odovzdanie do SPS. Staré rozpoznávanie po zmene režimu nemôže vybrať balík v novom režime.

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

`sps.env` sa načíta doslovne: hodnoty nepíš do úvodzoviek. Súbor nepatrí do GitHubu ani do Docker image. Existujúci Docker volume `courier_passwords` obsahuje aj SQLite databázu, fotografie a doklady v `/app/data`; jeho názov sa nemení, aby zostali zachované uložené heslá. Bežné zoznamy API vracajú iba mená a informáciu o uloženom hesle. Samotné heslo kuriéra sa načíta až po výslovnej požiadavke prihlásenej obsluhy na jeho zobrazenie; tento POST vyžaduje ochranu Origin/CSRF a odpoveď sa necachuje. Heslá sa neukladajú do miestnej zálohy prehliadača ani do GitHubu. Demo a jednotlivé SPS účty majú oddelené súbory. Pri lokálnom vývoji možno cestu zmeniť cez `DATA_DIR` (predvolene `data`). Zálohuj celý dátový adresár.

## Uložené heslá kuriérov

Tlačidlo **Kuriéri** v hornej lište ukazuje všetkých uložených kuriérov priamo v zozname. Kliknutím vyberieš kuriéra a môžeš výslovne zobraziť/skryť jeho uložené heslo, zmeniť ho alebo odstrániť uloženého kuriéra. Odstránenie sa týka miestnej evidencie, nie zoznamu dopravcu. Noví kuriéri sa načítajú zo SPS podľa dostupných zásielok; už uložených možno upravovať aj bez čakajúcich balíkov. Zobrazené heslo sa vyčistí pri zmene kuriéra, zatvorení okna aj odhlásení. Uloženie hesla neprijíma žiadne zásielky; SPS ho overí až pri skutočnom príjme alebo odovzdaní.

Predvoľba sa mení až po úspešnom príjme, nie po otvorení zoznamu alebo uložení hesla. Ak sa líši od prvého kuriéra v aktuálnom poradí SPS, zobrazí sa malé upozornenie; možno ho vypnúť v nastaveniach. Ak uložený kuriér v zozname chýba, treba výslovne vybrať iného. Po odmietnutí možno opraviť heslo; aplikácia neprechádza ďalších kuriérov a neháda heslá. Uloženie platí aj pre ostatné mobily a prežije reštart či aktualizáciu kontajnera. Rozpracovaná dávka si drží vlastný zvolený výber.

## Skenovanie na mobile

Pod navigáciou sú tlačidlá **Skenovať** a **Hlasom**. Nezapnutá kamera nezaberá miesto. Zapnutie rozbalí menší náhľad v bežnom toku stránky a posunie obsah nižšie; vypnutie ho znova skryje. Obraz vyplní okno stredovým výrezom bez deformácie; dekóduje sa rovnaký výrez, ktorý vidíš. Hlasový režim používa vlastnú kompaktnú výzvu bez prázdneho kamerového obdĺžnika. **Obnoviť** je pri zozname balíkov.

Zvuk zaznie pri každom rozpoznaní, aj pri opakovane držanom kóde (interval približne 0,9 sekundy). Nerozpoznaný obraz bez dekódovaného kódu nepípa. Hlasitosť signálu je zvýšená; výsledná hlasitosť závisí aj od nastavenia médií a reproduktora mobilu.

## Demo

Pri `SPS_DEMO=true` aplikácia vôbec nekomunikuje so SPS. Obsahuje desať balíkov `DEMO00000001` až `DEMO00000010` na príjem a dvoch kuriérov s heslom **demo**. Po prijatí prvého balíka ho vyhľadáš PIN-om `000001`. Vopred uložený `DEMOSTORED01` má PIN `111111` a dobierku 12,50 €. V časti **Výdaj kuriérovi** sú podania `DEMOOUTGOING01` a `DEMOOUTGOING02`, pod nimi vratka `DEMOEXPIRED01`. Balíky `DEMOBLOCKED01` a `DEMOOUTBLOCKED03` ukazujú stav, ktorý SPS nedovoľuje odovzdať bez vyriešenia. Na skúšku kamery vytlač [ukážkové čiarové kódy](docs/demo-barcodes.svg).

Miestna databáza a police prežijú aj reštart dema, skúšobný dopravca však začína znova. Na úplne čistý test použi nový samostatný `DATA_DIR`; nemaž dáta ostrej prevádzky.

## Rozsah

- Kamera v mobilnom prehliadači cez ZXing, zadný fotoaparát, prisvietenie podľa podpory zariadenia.
- Načítanie zásielok, výber dávky, zoznam kuriérov, potvrdenie heslom a výsledok každého balíka.
- SQLite databáza, história pohybov, spoločná evidencia pre mobily a kontrola súbežných zmien. Jeden dátový adresár a jeden SPS účet na inštanciu.
- Potvrdené operácie sa neposielajú opakovane. Nejasný výsledok blokuje opakovanie; pozitívny záznam v zozname uložených zásielok vie potvrdiť príjem. Pri nejasnom výdaji alebo vratke treba výsledok overiť v SPS a zaznamenať ho s poznámkou.
- Jedna inštancia, jeden proces Uvicorn. Pri reštarte aplikácie sa obsluha prihlási znova; miestna záloha dávky je jednoduchá, bez samostatného systému pracovných relácií a offline synchronizácie.
- Skenovanie obrazu prebieha v mobile. Len výslovne pridaná fotografia sa odosiela na server. Potrebné je HTTPS a povolenie kamery.
- Zatiaľ vlastné heslo aplikácie a SPS účet jednej prevádzky; nejde o hotový viacnájomníkový fakturačný SaaS ani o integráciu s Google Sheets.

## Vývoj a testy

Backend: Python 3.12, FastAPI, HTTPX, SQLite, Pillow. Frontend: čistý JavaScript, Vite, ZXing, JsBarcode. Produkčný image zostaví frontend a podáva ho cez tú istú aplikáciu ako API. Žiadne externé CDN ani platené skenovacie služby.

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

Adaptér používa rovnaké interné rozhranie ako portál SPS, overené pre príjem, skladované zásielky, PIN, výdaj, vratky a doklady v jeho JavaScripte 17. 11. 2026. Nejde o verejné integračné API s garantovanou stabilitou. Pozri [technické poznámky](docs/SPS-INTEGRACIA.md).

Pred bežným používaním over na serveri prihlásenie, zoznam zásielok a jeden skutočný príjem s kuriérom. Funkčnosť na konkrétnom telefóne závisí aj od fotoaparátu a kvality etikety.
