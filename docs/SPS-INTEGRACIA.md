# SPS integračné poznámky

Príjem bol preskúmaný 22. 10. 2026. Rozšírenie o sklad, vyhľadanie podľa PIN-u, výdaj, vratky a doklady bolo overené 17. 11. 2026 vo verejnom klientskom kóde portálu `https://ps.sps-sro.sk/`. **18. 11. 2026** bola znovu načítaná aktuálna úvodná stránka aj jej klientsky balík a overený spoločný výdaj zákazníckych podaní a vratiek kuriérovi. Stránka stále odkazovala na `https://ps.sps-sro.sk/main.f1da756c6e8e064d.js`, UI verzia 4.9.29; SHA-256 súboru je `cf17967dfc2cb82dad4152776241a8e7b9fb2ee56cb07da8124389f52014371d`. Pri overení sa nepoužilo prihlásenie ani nevykonal príjem, výdaj, platba alebo vrátenie reálnej zásielky. Ide o overenie kontraktu klienta; produkčné odpovede nových operácií ešte treba potvrdiť pri riadenom nasadení.

Základ API: `https://ps.sps-sro.sk/psappserver/rest`.

| Operácia | HTTP | Vstup |
| --- | --- | --- |
| Prihlásenie | `POST /login` | `username`, `password` |
| Kontrola prihlásenia v portáli | `GET /login` | session cookies |
| Zásielky na príjem | `GET /shipment/tour` | session cookies |
| Kuriéri pre výber | `POST /shipment/couriers` | `{"trackNumbers":["…"]}` |
| Prijatie balíka | `POST /shipment/accept` | `trackNumber`, `courierId`, `password` |
| Uložené zásielky | `GET /getStoredShipments` | query `trackNumber` (prázdny pre celý zoznam) |
| Vyhľadanie podľa PIN-u | `GET /getShipmentDetails` | query `smsKey`, `logInfo=false` |
| Výdaj zákazníkovi | `PUT /shipmentDelivery` | `smsKey`, `state:40`, `codConfirmed`, `paidWithPaymentTerminal` |
| Celý zoznam na výdaj kuriérovi (podania aj vratky) | `GET /shipment/expired` | session cookies |
| Odovzdanie kuriérovi | `PUT /shipment/return` | `trackNumber`, `courierId`, `password` |
| Katalóg typov udalostí, voliteľné doplnenie rozdelenia | `GET /events` | `code`, `droppoff`, `shouldReturnToCourier`; oprávnenia závisia od účtu |
| Hash na stiahnutie dokladu | `POST /hash` | `{"text":"číslo zásielky"}` → `{"hash":"…"}` |
| PDF doklad | `GET /getreceiptdocuments` | query `trackNumber`, `hash`, `updateReceiptNumber` |

Portál používa JSON a cookies (`withCredentials: true`). Príjem odosiela postupne po jednom balíku. Zoznam kuriérov obsahuje `id`, `code`, `name`. Zoznam zásielok obsahuje `trackNumber`, `senderName`, `tourDate`, `tourCourier` a ďalšie nepotrebné polia.

Podľa overenia obsluhy môže poradie kuriérov v SPS pomáhať s určením aktuálneho odovzdávajúceho kuriéra. Požiadavka aplikácie je však používať **posledného úspešne použitého kuriéra naprieč dňami** a prvého v zozname iba ako počiatočný návrh, keď história neexistuje. Poradie odpovede sa zachováva; rozdiel oproti prvému môže zobraziť vypínateľné upozornenie. `tourCourier` nenahrádza používateľov výber. Zapamätaný kuriér, ktorý z aktuálneho zoznamu zmizol, vyžaduje manuálny výber. Heslo patrí k stabilnému ID kuriéra a nové heslo z príjmu sa ukladá až po potvrdenom úspechu.

V kontrolovanom klientskom kóde sa HTTP 404 pri potvrdení interpretuje ako nesprávne heslo kuriéra. Aplikácia preto pri 404 žiada skontrolovať kuriéra a heslo. Neočakávané odpovede sa nezobrazujú ako úspešný príjem.

Adaptér `app/sps.py` je jediným miestom, ktoré komunikuje so SPS. Pri zmene portálu sa upraví tu. Prihlásenie a načítanie údajov sú oddelené od potvrdenia: žiadna zásielka sa neprijíma pri samotnom skenovaní, otvorení stránky ani načítaní kuriérov. Rovnako vyhľadanie podľa PIN-u nevydáva zásielku. Zmena fyzického umiestnenia je lokálna operácia a nemení stav v SPS.

Mobilný prehliadač volá iba vlastný server, nie priamo doménu SPS. Server drží vlastnú SPS reláciu v pamäti. Prenos existujúcich cookies z používateľovho prehliadača sa nevyužíva.

## Kontrakt skladu, PIN-u a vratiek

`getStoredShipments` vracia pole objektov. Portál číta `trackNumber`, `senderName`, `recipientName`, `expiredDate`, `status.code/name/description`, `currentParcelshop` a ďalšie polia. Adaptér prepúšťa iba údaje potrebné pre aplikáciu a stav normalizuje na `statusCode` a `statusLabel`. Expirácia pochádza zo SPS; nepočíta sa napevno z dátumu prijatia. Chýbajúca dobierka v skladovom zozname je `null`, nie automaticky nula.

`getShipmentDetails` vracia jeden objekt alebo `null`; portál ho hľadá cez `smsKey`, nie cez telefónne číslo. Lookup číta `trackNumber`, `cod`, `recipientName`, `senderName` a `psRemark`. Adaptér neukladá ani nevracia PIN, telefón či e-mail príjemcu. Neplatná alebo chýbajúca suma `cod` pri výdajnom lookupe zastaví operáciu. Celé číslo zásielky sa pred potvrdením musí porovnať s vyhľadanou aj fyzicky naskenovanou zásielkou. Dvojznakový koniec čísla slúži iba ako vizuálna pomôcka.

`shipment/expired` je napriek názvu autoritatívny zoznam obrazovky **Výdaj kuriérovi**. Používa sa aj pre balíky podané zákazníkmi, ktoré má prevziať kuriér. Trieda klienta `Aue` načítava celý tento zoznam a odovzdáva vybrané zásielky cez rovnaké `PUT /shipment/return`; samostatný domyslený endpoint na odosielané balíky sa nepoužíva. Portál číta `status.code`, `statusDate`, `entryCourier`, `trackNumber` a `senderName`. **Stav 5555 nesmie ísť priamo do odovzdania**: pôvodný portál ho blokuje a ponúka osobitnú administrátorskú opravu. Adaptér preto vracia `returnEligible=false`; aplikácia nemá obchádzať túto podmienku. Pre ostatné položky tohto autoritatívneho zoznamu vracia `returnEligible=true`. Chýbajúca zásielka, aj keď má lokálny záznam alebo starý dátum expirácie, sa do zoznamu svojvoľne nepridáva.

Rozdelenie obrazovky používa explicitné metadáta udalosti: `droppoff=true` (presný názov v SPS, s dvomi p) označuje zákaznícke podanie; `shouldReturnToCourier=true` označuje vratku, ak nejde o podanie. Samotné `droppoff=false` nestačí na označenie vratky. Klientský editor udalostí `Que` tieto vlastnosti číta a upravuje cez `/events` a zobrazuje ich ako prijatie od zákazníka a vrátenie kuriérovi. Ak kompaktný objekt `status` v zásielke neposkytne všetky príznaky, adaptér môže doplniť metadáta z tohto katalógu podľa **presného `status.code`**, s päťminútovou vyrovnávacou pamäťou. Explicitné booleany priamo pri zásielke majú prednosť; nejednoznačné duplicitné kódy katalógu sa nepoužijú. Zamietnuté oprávnenie, výpadok alebo neúplné metadáta ponechajú typ `unknown`, bez obchádzania oprávnení a bez blokovania celého výdaja.

Zobrazenie ide v poradí **Podané zákazníkmi → Expirované a ďalšie vratky → Ďalšie zásielky podľa SPS**. Posledná skupina otvorene ponecháva neznámy typ; neodvodzuje ho z čísla stavu, názvu či dátumu. Zaradenie do skupiny nemení oprávnenie na odovzdanie. Každý zápis opäť načíta autoritatívny zoznam a overí aktuálnu oprávnenosť vrátane 5555, lokálnych nejasných výsledkov a už dokončených operácií.

Obsluha môže balíky vyberať ťuknutím, skenom celého kódu alebo hlasovou koncovkou, ktorá je jednoznačná medzi všetkými známymi fyzickými balíkmi. Výber všetkých preskočí blokované, už odovzdané a neoverené položky; limit jednej dávky je viditeľne 200 balíkov. Samotný výber, príprava vopred ani presun na policu nemení stav v SPS; to robí až explicitné potvrdenie odovzdania. Pri výdaji zákazníkovi sa aj naďalej overuje **celý** kód balíka, nie skrátená hlasová koncovka.

Prijatie nového podania od zákazníka je iná operácia: trieda `cye` v portáli používa `POST /insertShipmentForDropoff` s `trackNumber`, vyžaduje `parcelshop.supportdropoff` a úspech posudzuje podľa `cargonetAccepted` alebo `cdexAccepted`. Tento nový príjem nie je súčasťou zmeny výdaja kuriérovi. Aplikácia pracuje so zásielkami, ktoré už do zoznamu zaradilo SPS; nepredstiera registráciu zatiaľ neprijatého podania.

## Dobierka a doklady

Pôvodný portál rozlišuje potvrdenú platbu v hotovosti (`codConfirmed=true`) a platbu uskutočnenú cez terminál (`paidWithPaymentTerminal=true`). Obidve možnosti sú vzájomne výlučné. Terminál sa zobrazuje iba vtedy, keď prihlásený účet obsahuje `parcelshop.paymentTerminal=true`; adaptér túto podmienku kontroluje aj pred odoslaním. Výdajný lookup vracia odvodené `paymentTerminalAllowed`.

Označenie platby cez terminál **nespúšťa platobnú transakciu**. Potvrdzuje už uskutočnenú platbu. Portál má aj samostatné volanie `GET /shipment/callCardPayment?trackNumber=…` a nastavenie `GET /options/cardPaymentAllowed` pre platobnú požiadavku; táto funkcia nie je súčasťou adaptéra (`payment_request=false`).

Známe chyby pri výdaji:

| HTTP | Význam v portáli |
| --- | --- |
| 461 | Zásielka nie je uložená na tomto výdajnom mieste |
| 462 | Dobierka nebola potvrdená |
| 463 | Dobierka je už zaplatená; treba obnoviť údaje |
| 464 | Nepodarilo sa overiť platbu |
| 465 | Súbežná zmena stavu zásielky |

Po úspešnom výdaji s dobierkou portál vyžiada hash a stiahne PDF cez `getreceiptdocuments` s `updateReceiptNumber=true`. Administrátorská dotlač používa `false`. Metóda `receipt(number, update_number=True)` overuje PDF signatúru a vracia bajty dokumentu. Pri dotlači alebo obnove po nejasnom výsledku treba poslať `update_number=False`, aby sa slepo neopakovala operácia číslovania dokladu. Zlyhanie stiahnutia dokladu **neruší úspešný výdaj** a nikdy nesmie vyvolať nové odoslanie výdaja. Automatické posielanie e-mailov zákazníkom sa nepoužíva.

## Chyby a opakovanie

Žiadna zmena zásielky sa neopakuje automaticky. `OperationUncertainError` označuje prerušené spojenie po pokuse o odoslanie, nejasnú odpoveď, serverovú chybu alebo HTML namiesto potvrdenia. Server mohol operáciu dokončiť, preto nasleduje overenie stavu a manuálne vyriešenie, nie slepé opakovanie. Samotná neprítomnosť zásielky v zozname nestačí na dokázanie úspechu. Chyby prihlásenia pred odoslaním a známe zamietnutia sa od nejasného výsledku rozlišujú.

Frontend zobrazuje výsledok jednotlivých balíkov a po chybe zastaví dávku. Už potvrdené úspešné položky sa nesmú odoslať druhýkrát; umiestnenie sa zachová nezávisle od výsledku príjmu. Jednoduchá obnova rozpracovaných čísel v prehliadači neukladá heslá ani výdajné PIN-y.

## Demo kontrakt

Demo režim nevykonáva žiadnu sieťovú požiadavku. Dvaja skúšobní kuriéri majú ID `1` a `2`, heslo `demo`. Desať zásielok `DEMO00000001` až `DEMO00000010` čaká na príjem; po prijatí je PIN posledných šesť číslic (`000001` až `000010`). `DEMOSTORED01` je už uložená s PIN-om `111111` a dobierkou 12,50 €. `DEMOOUTGOING01` a `DEMOOUTGOING02` sú už prijaté zákaznícke podania na odovzdanie kuriérovi; nemajú vymyslenú expiráciu. `DEMOOUTBLOCKED03` je podanie blokované stavom 5555. `DEMOEXPIRED01` je pripravená vratka; `DEMOBLOCKED01` demonštruje blokovanú vratku. Skúšobný PDF doklad je zreteľne označený DEMO. Údaje simulátora SPS sú v pamäti procesu.

## Overenie pred ostrou prevádzkou

Automatizované testy používajú mock alebo demo SPS. Prihlasovacie údaje nie sú súčasťou repozitára. Vlastník 22. 10. 2026 potvrdil funkčnosť prvej verzie príjmu na svojom serveri. Nové operácie sú overené proti klientskemu kódu a mockom, nie proti produkčným zápisom. Pred širším nasadením treba overiť výdaj, dobierku, doklad a vratku na konkrétnom účte pod dohľadom obsluhy. Dostupnosť ani stabilita interného API nie je garantovaná verejnou dokumentáciou SPS.

## Referencie

- [Oficiálny portál SPS](https://ps.sps-sro.sk/)
- [Preskúmaný klientsky balík SPS, UI 4.9.29](https://ps.sps-sro.sk/main.f1da756c6e8e064d.js)
- [ZXing Browser](https://github.com/zxing-js/browser)
- [FastAPI v Docker](https://fastapi.tiangolo.com/deployment/docker/)
- [Caddy handle](https://caddyserver.com/docs/caddyfile/directives/handle)
- [Prístup ku kamere a HTTPS](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
