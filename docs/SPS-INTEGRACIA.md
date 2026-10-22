# SPS integračné poznámky

Zistenia z 22. 10. 2026. Preskúmaný portál `https://ps.sps-sro.sk/accept-shipment` a verejný balík `https://ps.sps-sro.sk/main.f1da756c6e8e064d.js`, UI verzia 4.9.29. Pri analýze bol overený zoznam a dialóg kuriéra; žiadna reálna zásielka nebola prijatá.

Základ API: `https://ps.sps-sro.sk/psappserver/rest`.

| Operácia | HTTP | Vstup |
| --- | --- | --- |
| Prihlásenie | `POST /login` | `username`, `password` |
| Kontrola prihlásenia v portáli | `GET /login` | session cookies |
| Zásielky na príjem | `GET /shipment/tour` | session cookies |
| Kuriéri pre výber | `POST /shipment/couriers` | `{"trackNumbers":["…"]}` |
| Prijatie balíka | `POST /shipment/accept` | `trackNumber`, `courierId`, `password` |

Portál používa JSON a cookies (`withCredentials: true`). Príjem odosiela postupne po jednom balíku. Zoznam kuriérov obsahuje `id`, `code`, `name`. Zoznam zásielok obsahuje `trackNumber`, `senderName`, `tourDate`, `tourCourier` a ďalšie nepotrebné polia.

Podľa overenia obsluhy SPS zaradí aktuálne odovzdávajúceho kuriéra po jeho skenovaní na prvé miesto. Aplikácia preto zachováva poradie odpovede a používa `couriers[0]`, nie `tourCourier` zo zásielok. Ak má tento kuriér uložené heslo, po stlačení Prijať začne príjem bez ďalšieho dialógu. Pri chýbajúcom alebo odmietnutom hesle zobrazí formulár. Nové heslo z príjmu uloží až po potvrdenom úspechu.

V kontrolovanom klientskom kóde sa HTTP 404 pri potvrdení interpretuje ako nesprávne heslo kuriéra. Aplikácia preto pri 404 žiada skontrolovať kuriéra a heslo. Neočakávané odpovede sa nezobrazujú ako úspešný príjem.

Adaptér `app/sps.py` je jediným miestom, ktoré komunikuje so SPS. Pri zmene portálu sa upraví tu. Prihlásenie a načítanie údajov sú oddelené od potvrdenia: žiadna zásielka sa neprijíma pri samotnom skenovaní, otvorení stránky ani načítaní kuriérov.

Mobilný prehliadač volá iba vlastný server, nie priamo doménu SPS. Server drží vlastnú SPS reláciu v pamäti. Prenos existujúcich cookies z používateľovho prehliadača sa nevyužíva.

## Zámerne jednoduchý rozsah

Podľa požiadavky vlastníka sa nerieši databáza príjmov, obnova rozpracovanej dávky po výpadku ani blokovanie opakovaného príjmu. Po chybe môže obsluha potvrdenie zopakovať. Frontend priebežne zobrazuje odpovede SPS a po prvej chybe dávku zastaví, aby sa rovnaké chybné heslo neposielalo ku všetkým balíkom.

## Overenie pred ostrou prevádzkou

Automatizované testy používajú mock alebo demo SPS. Prihlasovacie údaje nie sú súčasťou repozitára. Vlastník 22. 10. 2026 potvrdil funkčnosť prvej verzie na svojom serveri. Nové uloženie hesiel, hlasitosť a výrez kamery treba po aktualizácii skontrolovať aj na používanom mobile. Dostupnosť ani stabilita interného API nie je garantovaná verejnou dokumentáciou SPS.

## Referencie

- [ZXing Browser](https://github.com/zxing-js/browser)
- [FastAPI v Docker](https://fastapi.tiangolo.com/deployment/docker/)
- [Caddy handle](https://caddyserver.com/docs/caddyfile/directives/handle)
- [Prístup ku kamere a HTTPS](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
