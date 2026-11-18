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
| Podanie od zákazníka | `POST /insertShipmentForDropoff` | `{"trackNumber":"…"}`; explicitné `cargonetAccepted=true` alebo `cdexAccepted=true` |
| Uložené zásielky | `GET /getStoredShipments` | query `trackNumber` (prázdny pre celý zoznam) |
| Vyhľadanie podľa PIN-u | `GET /getShipmentDetails` | query `smsKey`, `logInfo=false` |
| Kontrola existujúcej online úhrady | `GET /shipment/refreshCod` | query `trackNumber`; po kontrole nový PIN lookup poskytne aktuálne `cod` |
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

`getShipmentDetails` vracia jeden objekt alebo `null`; portál ho hľadá cez `smsKey`, nie cez telefónne číslo. Lookup číta `trackNumber`, `cod`, `recipientName`, `senderName` a `psRemark`. Adaptér nevracia PIN, telefón či e-mail príjemcu. PIN zostáva iba v krátkodobej serverovej relácii viazanej na konkrétne prihlásenie a lookup; neukladá sa do skladu ani auditu. Neplatná alebo chýbajúca suma `cod` pri výdajnom lookupe zastaví operáciu. Pred potvrdením sa vždy znovu overí identita zásielky a suma v SPS. Nastavenie `pickup_scan_required=false` predvolene umožní potvrdiť výdaj po PIN lookupe bez druhého skenu; pri zapnutí server vyžaduje celé naskenované číslo. Ak je sken odoslaný aj pri vypnutej požiadavke, nesprávne číslo vždy zablokuje výdaj. Dvojznaková koncovka nenahrádza overenie celého kódu.

`shipment/expired` je napriek názvu autoritatívny zoznam obrazovky **Výdaj kuriérovi**. Používa sa aj pre balíky podané zákazníkmi, ktoré má prevziať kuriér. Trieda klienta `Aue` načítava celý tento zoznam a odovzdáva vybrané zásielky cez rovnaké `PUT /shipment/return`; samostatný domyslený endpoint na odosielané balíky sa nepoužíva. Portál číta `status.code`, `statusDate`, `entryCourier`, `trackNumber` a `senderName`. **Stav 5555 nesmie ísť priamo do odovzdania**: pôvodný portál ho blokuje a ponúka osobitnú administrátorskú opravu. Adaptér preto vracia `returnEligible=false`; aplikácia nemá obchádzať túto podmienku. Pre ostatné položky tohto autoritatívneho zoznamu vracia `returnEligible=true`. Chýbajúca zásielka, aj keď má lokálny záznam alebo starý dátum expirácie, sa do zoznamu svojvoľne nepridáva.

Rozdelenie obrazovky používa explicitné metadáta udalosti: `droppoff=true` (presný názov v SPS, s dvomi p) označuje zákaznícke podanie; `shouldReturnToCourier=true` označuje vratku, ak nejde o podanie. Samotné `droppoff=false` nestačí na označenie vratky. Klientský editor udalostí `Que` tieto vlastnosti číta a upravuje cez `/events` a zobrazuje ich ako prijatie od zákazníka a vrátenie kuriérovi. Ak kompaktný objekt `status` v zásielke neposkytne všetky príznaky, adaptér môže doplniť metadáta z tohto katalógu podľa **presného `status.code`**, s päťminútovou vyrovnávacou pamäťou. Explicitné booleany priamo pri zásielke majú prednosť; nejednoznačné duplicitné kódy katalógu sa nepoužijú. Zamietnuté oprávnenie, výpadok alebo neúplné metadáta ponechajú typ `unknown`, bez obchádzania oprávnení a bez blokovania celého výdaja.

Zobrazenie ide v poradí **Podané zákazníkmi → Expirované a ďalšie vratky → Ďalšie zásielky podľa SPS**. Posledná skupina otvorene ponecháva neznámy typ; neodvodzuje ho z čísla stavu, názvu či dátumu. Zaradenie do skupiny nemení oprávnenie na odovzdanie. Každý zápis opäť načíta autoritatívny zoznam a overí aktuálnu oprávnenosť vrátane 5555, lokálnych nejasných výsledkov a už dokončených operácií.

Obsluha môže balíky vyberať ťuknutím, skenom celého kódu alebo hlasovou koncovkou, ktorá je jednoznačná medzi všetkými známymi fyzickými balíkmi. Výber všetkých preskočí blokované, už odovzdané a neoverené položky; limit jednej dávky je viditeľne 200 balíkov. Samotný výber, príprava vopred ani presun na policu nemení stav v SPS; to robí až explicitné potvrdenie odovzdania. Ak sa pri výdaji zákazníkovi použije fyzické overenie, porovnáva sa **celý** kód balíka, nie skrátená hlasová koncovka.

## Príjem podania od zákazníka

Táto operácia bola znovu overená **18. 11. 2026** v aktuálne odkazovanom oficiálnom balíku uvedenom vyššie. Trieda `cye` používa `POST /insertShipmentForDropoff` s jediným JSON poľom `trackNumber`. Oprávnenie sa číta z `parcelshop.supportdropoff` v prihlásení. `DROPOFF_DISABLED` a `INVALID_TRACKNUMBER_LENGTH` sú explicitné zamietnutia; úspech klient posudzuje podľa `cargonetAccepted` alebo `cdexAccepted`. Adaptér vyžaduje skutočný boolean `true`; prázdna 2xx odpoveď, chýbajúce príznaky, reťazec `"true"`, rozporné údaje či timeout nemôžu vytvoriť potvrdený miestny príjem. Explicitné záporné príznaky sú zamietnutie, nejasný výsledok vyžaduje kontrolu v SPS. Heslo kuriéra sa pri zákazníckom podaní neposiela.

Vlastný endpoint je `POST /api/dropoff/confirm` s `number`, `operation_id` a voliteľným `scanned` (predvolene `false`). Samotný sken vytvorí iba miestnu kartu; SPS volanie nastane až po potvrdení. Pre nové podanie aplikácia žiada 6–100 bezpečných ASCII znakov ako ochranu pred zadaním krátkej koncovky. **Toto nie je overený formát SPS**: verejný klient nepublikuje minimálnu dĺžku ani prefix, správnosť skutočného štítku rozhoduje SPS. Číslo sa neskladá z koncovky a nestráca úvodné nuly.

Perzistentný denník používa operáciu `dropoff`; jej nejasný výsledok je `dropoff_uncertain`. Opakovaný identifikátor ani nový pokus pri pending/uncertain zápise neposielajú ďalšie SPS podanie. Pri novom zápise musí byť lokálny stav atomicky `unconfirmed`; uložený balík nemožno preklasifikovať ďalším príjmom. Úspešný replay vráti výsledok bez nového zápisu a bez nového pending záznamu. Nejasný výsledok sa rieši cez existujúce `operations/resolve` s `kind:"dropoff"`, explicitným overením v SPS a auditnou poznámkou; bežiacu požiadavku nemožno manuálne rozhodnúť. Samotný neskorší skladový import nepreukazuje výsledok konkrétneho podania.

Potvrdený príjem ukladá `carrier_data.handoverKind="outgoing"` a DTO `dropoff_confirmed=true`, odvodené z potvrdeného denníka. Pri neskoršom SPS zozname s neúplnými metadátami možno tento dôkaz použiť na rozdelenie skupín. Do výdaja kuriérovi však naďalej vstupujú **iba balíky v aktuálnom autoritatívnom zozname SPS**; lokálny príjem nevyrába oprávnenie na odovzdanie. Stav 5555 zostáva blokovaný. Umiestnenie sa pri príjme ani pri nejasnom výsledku nemaže.

Miestna karta poskytuje `received_at` a `scanned_at`, obe nullable. Prvý údaj vychádza zo skutočne potvrdeného miestneho príjmu; druhý z explicitného skenu (`scanned:true` pri registrácii alebo podaní). Bežná registrácia ťuknutím, skladový import, `created_at`, synchronizácia expirácie ani neskoršie manuálne overenie nevyrábajú čas fyzického prijatia. Importované podanie môže mať známu smerovosť bez toho, aby malo `dropoff_confirmed=true` alebo známy čas miestneho príjmu.

## Dobierka a doklady

Pôvodný portál rozlišuje potvrdenú platbu v hotovosti (`codConfirmed=true`) a platbu uskutočnenú cez terminál (`paidWithPaymentTerminal=true`). Obidve možnosti sú vzájomne výlučné. Terminál sa zobrazuje iba vtedy, keď prihlásený účet obsahuje `parcelshop.paymentTerminal=true`; adaptér túto podmienku kontroluje aj pred odoslaním. Výdajný lookup vracia odvodené `paymentTerminalAllowed`.

Označenie platby cez terminál **nespúšťa platobnú transakciu**. Potvrdzuje už uskutočnenú platbu. Portál má aj samostatné volanie `GET /shipment/callCardPayment?trackNumber=…` a nastavenie `GET /options/cardPaymentAllowed` pre platobnú požiadavku; táto funkcia nie je súčasťou adaptéra (`payment_request=false`).

Pri online úhrade zákazníkom oficiálny klient `jCe.updateCod()` opakuje PIN lookup s `logInfo=false`, preberá aktuálne `cod` a pri nule skryje výber hotovosti. V tejto vetve nepoužíva ďalší lokálny príznak úhrady ani `paidWithPaymentTerminal`. Tlačidlo obnovenia najprv volá `GET /shipment/refreshCod?trackNumber=…`; HTTP 204 portál interpretuje ako zatiaľ nezaplatené. Samotný HTTP úspech tejto kontroly **nie je dôkaz nulovej dobierky**. Autoritatívna zostávajúca suma pochádza až z nasledujúceho `getShipmentDetails.cod`.

Vlastné `POST /api/pickup/refresh` prijíma iba `lookup_id`; PIN zostáva na serveri. Po kontrole platby, novom lookupe tej istej zásielky a potvrdení jej uloženia v SPS vráti nový `parcel.cod`, `local`, `amount_changed`, `refreshed_at` a `scan_required`. Až úplný úspech nahradí referenčnú sumu v relácii. Výpadok, neplatná suma či zmena identity zachovajú pôvodnú sumu a nikdy neoznačia zásielku za zaplatenú. Finálny výdaj opäť číta SPS a zmenenú sumu blokuje až do explicitného obnovenia alebo nového PIN lookupu. Po autoritatívnom `cod=0` sa odosiela výdaj s oboma platobnými príznakmi `false`; nulová dobierka s potvrdením hotovosti alebo terminálu sa odmietne ako rozporná. Obnovenie nevydáva balík, nežiada novú platbu ani neopakuje predchádzajúci výdaj.

`manual_entry=false` je predvolené nastavenie zobrazenia ručného zadania v klientovi. `pickup_scan_required=false` je nastavenie overenia fyzickým kódom, ktoré server vynucuje pri každom potvrdení. Obe nastavenia sú spoločné pre prihlásené zariadenia účtu a pretrvajú reštart.

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

Demo režim nevykonáva žiadnu sieťovú požiadavku. Dvaja skúšobní kuriéri majú ID `1` a `2`, heslo `demo`. Desať zásielok `DEMO00000001` až `DEMO00000010` čaká na príjem; po prijatí je PIN posledných šesť číslic (`000001` až `000010`). `DEMOSTORED01` je už uložená s PIN-om `111111` a dobierkou 12,50 €. Samostatná `DEMOMOBILE01` s PIN-om `222222` začína dobierkou 18,90 €; iba jej explicitné obnovenie v demo adaptéri simuluje externú mobilnú úhradu a zmení sumu na nulu. Produkčný adaptér takúto simuláciu nemá. `DEMOOUTGOING01` a `DEMOOUTGOING02` sú už prijaté zákaznícke podania na odovzdanie kuriérovi; nemajú vymyslenú expiráciu. `DEMOOUTBLOCKED03` je podanie blokované stavom 5555. `DEMOEXPIRED01` je pripravená vratka s PIN-om `222223`; `DEMOBLOCKED01` demonštruje blokovanú vratku. Novú `DEMODROPOFF01` možno explicitne prijať od zákazníka; až potom sa objaví medzi podaniami na odovzdanie kuriérovi. Skúšobný PDF doklad je zreteľne označený DEMO. Údaje simulátora SPS sú v pamäti procesu.

## Overenie pred ostrou prevádzkou

Automatizované testy používajú mock alebo demo SPS. Prihlasovacie údaje nie sú súčasťou repozitára. Vlastník 22. 10. 2026 potvrdil funkčnosť prvej verzie príjmu na svojom serveri. Nové operácie sú overené proti klientskemu kódu a mockom, nie proti produkčným zápisom. Pred širším nasadením treba overiť výdaj, dobierku, doklad a vratku na konkrétnom účte pod dohľadom obsluhy. Dostupnosť ani stabilita interného API nie je garantovaná verejnou dokumentáciou SPS.

## Referencie

- [Oficiálny portál SPS](https://ps.sps-sro.sk/)
- [Preskúmaný klientsky balík SPS, UI 4.9.29](https://ps.sps-sro.sk/main.f1da756c6e8e064d.js)
- [ZXing Browser](https://github.com/zxing-js/browser)
- [FastAPI v Docker](https://fastapi.tiangolo.com/deployment/docker/)
- [Caddy handle](https://caddyserver.com/docs/caddyfile/directives/handle)
- [Prístup ku kamere a HTTPS](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
