# Nasadenie na server

Cieľová adresa: **https://hub.biketrek.sk/sps/**. Aplikácia beží samostatne, Hubu pribudne iba smerovanie `/sps`. SQLite databázu, heslá kuriérov, fotografie a načítané doklady uchováva v existujúcom trvalom Docker volume `courier_passwords`.

## 1. Stiahni aplikáciu

Prihlás sa cez SSH na server, na ktorom beží Hub:

```bash
docker compose version
cd /opt
git clone https://github.com/mpriesol/sps.git
cd /opt/sps
cp .env.example sps.env
chmod 600 sps.env
nano sps.env
```

Je potrebný Docker Compose **2.30+** kvôli doslovnému načítaniu hesiel (`env_file.format: raw`). V `sps.env` najprv nastav:

```dotenv
PUBLIC_URL=https://hub.biketrek.sk/sps
APP_PASSWORD=SEM_DLHE_HESLO_PRE_OBSLUHU
SESSION_HOURS=12
SPS_DEMO=true
SPS_USERNAME=
SPS_PASSWORD=
```

Ukážkové heslo nahraď vlastným heslom s aspoň 16 znakmi. Náhodné si môžeš vytvoriť príkazom `openssl rand -hex 16`. Hodnoty v súbore píš **bez úvodzoviek**. Heslo aplikácie je iné ako heslo SPS účtu a iné ako heslo kuriéra.

## 2. Spusti aplikáciu

Ak Caddy Hubu beží v Docker kontajneri, aplikácie prepoj spoločnou sieťou:

```bash
docker network inspect sps-proxy >/dev/null 2>&1 || docker network create sps-proxy
cd /opt/sps
docker compose -f compose.yaml -f compose.hub.yaml up -d --build
docker compose -f compose.yaml -f compose.hub.yaml ps
curl --fail http://127.0.0.1:8081/healthz
```

Posledný príkaz má vrátiť `{"status":"ok"}`. Počas prvého zostavenia sa stiahnu závislosti.

## 3. Pripoj existujúci Caddy do siete

V priečinku Hubu si over názov služby:

```bash
cd /opt/inventory-hub
docker compose config --services
```

Nasledujúci príklad predpokladá službu `caddy` a existujúcu sieť `default`. V súbore `/opt/inventory-hub/compose.override.yaml` **doplň**:

```yaml
services:
  caddy:
    networks:
      default: {}
      sps_proxy: {}

networks:
  sps_proxy:
    external: true
    name: sps-proxy
```

Ak tento súbor už existuje, zlúč nové položky s jeho obsahom. Zachovaj všetky siete, ku ktorým je Caddy už pripojený. Ak používaš inak pomenovaný override explicitne cez `-f`, pridaj nastavenie tam a pri ďalších príkazoch používaj rovnaké `-f` argumenty. Kópia úryvku je v [deploy/hub-compose.override.yaml](../deploy/hub-compose.override.yaml).

## 4. Pridaj `/sps` do Caddyfile

Otvor existujúci Caddyfile Hubu. Jeho cestu zistíš v `volumes` služby `caddy` pomocou `docker compose config`.

Do **existujúceho bloku `hub.biketrek.sk`** pridaj presmerovanie a nový `handle`. Súčasné smerovanie Hubu nechaj ako všeobecný `handle`. Príklad pri pôvodnom upstream `api:8000`:

```caddyfile
hub.biketrek.sk {
    redir /sps /sps/ 308

    handle /sps/* {
        request_body {
            max_size 3MB
        }
        reverse_proxy sps-receiver:8080
    }

    handle {
        reverse_proxy api:8000
    }
}
```

`api:8000` je príklad: zachovaj svoj súčasný upstream aj ostatné nastavenia Hubu. Nevytváraj druhý blok pre rovnakú doménu. Použi **`handle`**, nie `handle_path`, pretože aplikácia očakáva zachovanú cestu `/sps`.

Over konfiguráciu a aplikuj novú sieť aj Caddyfile:

```bash
cd /opt/inventory-hub
docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d --no-deps caddy
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Tieto príkazy predpokladajú štandardnú cestu Caddyfile v kontajneri. Ak ju máš inú, použi cestu zo svojej konfigurácie. Pripojenie novej siete môže Caddy krátko reštartovať.

## 5. Vyskúšaj demo na mobile

Otvor **https://hub.biketrek.sk/sps/** a prihlás sa hodnotou `APP_PASSWORD`.

- Zobrazí sa označenie **DEMO** a desať skúšobných balíkov.
- Skús **Pridať skúšobný balík**, prípadne zadaj `DEMO00000001` ručne.
- Pre fotoaparát otvor [demo-barcodes.svg](demo-barcodes.svg) na počítači alebo ho vytlač a skenuj mobilom.
- Stlač **Prijať balíky**, vyber skúšobného kuriéra a zadaj heslo **demo**.

Skontroluj aj bežné otvorenie hlavnej stránky Hubu. Demo nikdy neodosiela požiadavky do SPS.

## 6. Zapni skutočný príjem

```bash
cd /opt/sps
nano sps.env
```

Zmeň:

```dotenv
SPS_DEMO=false
SPS_USERNAME=PRIHLASOVACIE_MENO_VYDAJNEHO_MIESTA
SPS_PASSWORD=HESLO_VYDAJNEHO_MIESTA
```

Údaje patria účtu portálu `https://ps.sps-sro.sk/`. Heslo kuriéra sa sem nepíše.

```bash
docker compose -f compose.yaml -f compose.hub.yaml up -d --force-recreate
```

Obnov stránku a znovu sa prihlás. Demo označenie zmizne a aplikácia načíta skutočné zásielky. Pri prvom doručení otestuj jeden balík s kuriérom a skontroluj jeho prijatie v SPS.

## Aktualizácia

Pred aktualizáciou zálohuj celý existujúci volume. Názov volume sa nemení, uložené heslá sa zachovajú a databáza sa vytvorí automaticky vedľa nich. `sps.env` netreba meniť. Fotografia sa ešte v telefóne automaticky zmenší na najviac 640 px a 40 KiB; celá požiadavka má približne do 55 KB. **Pre fotografie z aplikácie preto netreba zvyšovať pôvodný limit Caddy `64KB`.** Ukážkový limit `3MB` umožňuje aj väčšie hromadné operácie a vstupy cez API; ak ho meníš, najprv over konfiguráciu a až potom ju načítaj. Backend samostatne obmedzuje bežné požiadavky na 64 KB, hromadné operácie na 500 KB a vstup fotografie na 2,5 MB vrátane obalu; uložený JPEG má vždy najviac 40 KiB.


```bash
cd /opt/sps
git pull --ff-only
docker compose -f compose.yaml -f compose.hub.yaml up -d --build
```

Po aktualizácii obnov stránku na mobile a prihlás sa znova. V hornej lište otvor **Kuriéri** a môžeš dopredu uložiť známe heslá; prípadne sa zapamätajú pri prvom úspešnom príjme.

Hlasové zadávanie zapneš tlačidlom **Hlasom** vedľa skenovania. Povoľ mikrofón a povedz dva posledné znaky, napríklad „nula sedem“ v deme. Pri viacerých zhodách si aplikácia vypýta posledné tri znaky. Nie je potrebná ďalšia konfigurácia ani API kľúč. Ak prehliadač rozpoznávanie nepodporuje, aplikácia to oznámi; kameru a ručné zadanie možno používať ďalej.

Súbor `sps.env` zostáva na serveri. Dáta zostávajú vo volume (zvyčajne `sps_courier_passwords`); pri aktualizácii nepoužívaj `down -v`, ktoré by vymazalo heslá, police aj históriu. Volume zahrň do serverovej zálohy ako citlivé údaje. Súbory nie sú šifrované. Pri jednoduchej súborovej zálohe zastav aplikáciu a skopíruj celý volume vrátane prípadných SQLite WAL súborov; alternatívou je SQLite backup API plus kópia fotografií a dokladov. Zastavenie aplikácie: `docker compose -f compose.yaml -f compose.hub.yaml down`. Neaktualizuj počas odosielania príjmu/výdaja do SPS. Rozpracovaný výber sa síce zálohuje v prehliadači, prerušená požiadavka na dopravcu však môže vyžadovať overenie výsledku.

Pred používaním nových výdajov over v deme celý postup uvedený v [návode na police a výdaj](POLICE-A-VYDAJ.md). Po nasadení over na konkrétnom telefóne sken vytlačeného miesta a fotografiu. Prvý ostrý príjem, výdaj s dobierkou a dokladom aj vratku skontroluj v portáli SPS; vývojové testy používajú len demo a simulované odpovede. Verejný klient SPS môže svoje interné API zmeniť.

## Ak Caddy beží priamo na serveri

Spusti iba `docker compose up -d --build`. Preskoč spoločnú Docker sieť aj úpravu Hub compose. V Caddyfile použi namiesto `sps-receiver:8080` adresu **`127.0.0.1:8081`**. Konfiguráciu over a načítaj spôsobom používaným pre svoju hostiteľskú službu Caddy.

## Rýchla diagnostika

```bash
cd /opt/sps
docker compose -f compose.yaml -f compose.hub.yaml ps
docker compose -f compose.yaml -f compose.hub.yaml logs --tail=50 sps
curl --fail http://127.0.0.1:8081/healthz
```

| Problém | Čo skontrolovať |
| --- | --- |
| Kontajner sa nespustí | `APP_PASSWORD` má aspoň 16 znakov; pri `SPS_DEMO=false` sú vyplnené SPS údaje |
| `502 Bad Gateway` | Caddy a SPS sú v sieti `sps-proxy`; upstream je `sps-receiver:8080` |
| `403` po prihlásení | `PUBLIC_URL` presne zodpovedá adrese otvorenej v mobile; potom reštartuj SPS |
| Čítačka sa nespustí | HTTPS a povolenie fotoaparátu pre túto stránku |
| Kód sa nenašiel | Obnov zoznam; načítaj správny kód zásielky, nie iný kód na etikete |
| SPS odmietne potvrdenie | Skontroluj vybraného kuriéra a jeho heslo |

Produkčný postup bol pripravený pre existujúci Hub s Caddy. Pred nasadením porovnaj uvedené názvy služieb a cesty so svojou aktuálnou konfiguráciou.
