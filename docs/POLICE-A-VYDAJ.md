# Police a výdaj: praktický postup

Hlavná navigácia má v jednom riadku štyri operácie: **Príjem od kuriéra**, **Výdaj kuriérovi**, **Príjem od zákazníka** a **Výdaj zákazníkovi**. **Uložiť / nájsť**, **Miesta** a **Nastavenia** sú v hornej ponuke **Viac**.

## 1. Priprav miesta

V **Viac → Miesta** vytvor regál, počet políc a prípadne počet miest na každej polici. Nula miest znamená jednu nerozdelenú policu. Predvolené názvy sú napríklad `A1`, `A2` alebo `A1-1`; používateľ ich môže upraviť. Jedno miesto môže obsahovať viac balíkov. Na nadrozmerné zásielky, pult alebo vratky môžeš vytvoriť samostatné pomenované miesto.

Každé miesto dostane stály čiarový kód `LOC-…`. Premenovanie nezneplatní starý čiarový kód; nové štítky treba vytlačiť iba kvôli viditeľnému názvu. Tlač používa Code 128 s veľkým názvom. V tlačovom dialógu možno vybrať tlačiareň alebo uloženie do PDF. Najskôr vytlač a skenerom over jeden štítok v skutočnej veľkosti.

Obsadené miesto nemožno vymazať. Najskôr jeho balíky presuň. Premenovanie regálu zachová vlastné názvy miest aj ich kódy.

## 2. Prijímaj a ukladaj podľa prevádzky

Príjem má jeden dvojstĺpcový zoznam čísel zásielok. Ťuknutím kartu vyberieš alebo odoberieš; podsvietenie označuje výber a tlačidlo **Prijať** ho potvrdí. Výber zachováva pozíciu stránky aj pri obnove údajov zo servera. K dispozícii sú aj výber všetkých a vyčistenie výberu. Prijaté či neoverené operácie majú vlastný stav a nemožno ich zrušiť obyčajným odobratím karty.

Náhľad kamery sa zobrazí až po stlačení **Skenovať** a posunie zoznam nižšie. Po vypnutí sa skryje. Tlačidlo **Hlasom** zostáva dostupné aj bez kamery. Voľbu **Ukladať do políc počas príjmu** zapni v **Nastaveniach**; ide o spoločnú preferenciu prevádzky.

Riadok na ručné zadanie čísla je predvolene vypnutý. Zapína sa v **Viac → Nastavenia**; rovnaké pole potom možno použiť aj s USB/Bluetooth čítačkou.

**Spolu s príjmom:** po balíku skenuj jeho miesto. Umiestnenie sa uchová aj vtedy, keď potvrdenie príjmu do SPS zlyhá. Miestny pohyb nepredstiera úspešné prijatie dopravcom.

**Samostatne po príjme:** otvor ukladanie/presun, naskenuj balík a potom cieľové miesto. Uvidíš aj jeho pôvodnú polohu. Tento postup nie je naviazaný na konkrétnu dávku ani deň; používa sa aj na preloženie už uloženého balíka.

**Zisti obsah:** sken miesta bez aktívneho balíka otvorí jeho obsah. Zoznam bez umiestnenia pomáha nájsť balíky, ktoré ešte treba uložiť. Neznámy balík možno zaevidovať iba s jasným stavom neovereného príjmu.

Veľké posledné dva znaky sú pomôcka k označeniu fixkou, ktoré už používate. Dva balíky môžu mať rovnaký koniec, preto sa identita vždy kontroluje celým číslom. Umiestnenie zúži hľadanie na niekoľko balíkov; fotografia je ďalšia voliteľná pomôcka.

## 3. Vyber kuriéra

V hornej lište otvor **Kuriéri**. Uložených kuriérov vidíš priamo v zozname; po kliknutí možno zobraziť/skryť uložené heslo, zmeniť ho alebo vymazať miestny záznam kuriéra. Zobrazenie sa vyžiada iba pre konkrétneho kuriéra a heslo sa vyčistí pri zatvorení okna či zmene výberu.

Vedľa potvrdenia je viditeľná predvoľba. Používa sa posledný úspešne použitý kuriér aj z predchádzajúceho dňa. Bez histórie sa navrhne prvý zo SPS. Ak je navrhnutý iný než prvý, zobrazí sa upozornenie, ktoré možno vypnúť. Ak posledný použitý kuriér v zozname chýba, treba vybrať iného.

Zmena výberu ani uloženie hesla ešte nemení posledného použitého kuriéra. Táto preferencia sa uloží po skutočnom úspešnom príjme. Dávka si drží svoj výber aj vtedy, keď iná obsluha medzitým prijíma na inom telefóne.

## 4. Vydaj zákazníkovi

1. Zadaj **Verifikačný kód** z e-mailu alebo správy SPS a vyhľadaj balík. Kód zostane vo vstupnom poli.
2. Jedna karta ukazuje miesto, veľké posledné dva znaky, celý kód, odosielateľa, zaznamenaný čas príjmu, príjemcu, prípadnú fotografiu a dobierku. Posledná synchronizácia zo SPS sa nezobrazuje ako čas príjmu.
3. Pri nezaplatenej dobierke je celá karta svetlooranžová. Pri platbe hotovosťou zaškrtni **Hotovosť prijatá**. Keď zákazník zaplatí cez mobil, použi **Obnoviť platbu zo SPS**. Až potvrdená nulová dobierka zo SPS odstráni oranžové zvýraznenie a požiadavku na hotovosť. Zlyhané obnovenie ponechá pôvodnú sumu a pozastaví potvrdenie do úspešnej obnovy.
4. Potvrď výdaj. Server znova overí kód aj sumu a až po úspechu v SPS uvoľní polohu. Obnovenie platby samo balík nevydáva.

Skenovanie pri výdaji zákazníkovi je predvolene vypnuté; kamera, hlas a ručné načítanie balíka sú skryté. V **Viac → Nastavenia** možno zapnúť povinné overenie celého čísla skenom alebo hlasom. Vtedy nesprávne ani skrátené číslo výdaj nepovolí. Ručné zadávanie sa navyše riadi svojou samostatnou voľbou.

Pri dobierke sa načíta PDF doklad SPS. Chyba pri jeho načítaní nemení úspešný výdaj; obnovuje sa iba doklad, nie odovzdanie zásielky. PIN sa neukladá do databázy, histórie ani miestnej zálohy prehliadača.

## 5. Prijmi podanie od zákazníka

Otvor **Príjem od zákazníka**, načítaj celé číslo zásielky a potvrď jej prijatie. Kamera, hlas aj voliteľné ručné zadanie pripravia rovnaký balík. Úspech sa zobrazí až po potvrdení SPS; samotné načítanie čísla nevytvára potvrdený príjem. Zásielka sa potom môže zobraziť medzi podaniami na výdaj kuriérovi. Táto operácia prijíma pripravenú zásielku SPS, nevytvára prepravný štítok ani objednávku prepravy.

Odmietnutie SPS ponechá príjem nepotvrdený. Pri strate odpovede sa pokus neposiela naslepo znovu; výsledok treba overiť. Opakované potvrdenie už prijatého balíka nevytvorí ďalší zápis do SPS.

## 6. Odovzdaj kuriérovi podania aj vratky

**Výdaj kuriérovi** zobrazuje najprv podania prijaté od zákazníkov a pod nimi zásielky na vrátenie vrátane expirovaných. Rozdelenie pochádza zo stavových údajov SPS; keď typ nemožno spoľahlivo určiť, zásielka ostane v samostatnej skupine bez vymysleného označenia či expirácie. Aplikácia používa oficiálny zoznam na odovzdanie kuriérovi a pred potvrdením oprávnenosť znova overí. Blokované zásielky nemožno vybrať ani cez výber všetkých.

Bežný postup je priamo pri príchode kuriéra: vyber karty dotykom, naskenuj balíky, vyslov ich jednoznačné konce alebo použi výber všetkých. Opätovné ťuknutie zruší výber. Skontroluj kuriéra a potvrď skutočné odovzdanie; netreba balíky najskôr presúvať na zvláštne miesto. Podania možno predtým zaevidovať cez **Príjem od zákazníka**.

Iné prevádzky si môžu v nastaveniach zapnúť prípravu vratiek vopred. Označenie pripravených zásielok nemení stav v SPS; konečné odovzdanie vyžaduje nový výslovný výber a potvrdenie. Ak sa balíky aj fyzicky presunú, použije sa bežný presun na miesto vratiek.

## 7. Presuň policu alebo sprav inventúru

Pri hromadnom presune zvoľ zdroj, cieľ a všetky alebo vybrané balíky. Pred potvrdením vidíš súpis. Zmena je spoločná transakcia: ak medzitým iný telefón presunul jeden z balíkov, celý presun sa odmietne a treba obnoviť údaje. História zostane pri každom balíku.

Inventúra porovná obsah miesta s tým, čo naskenuješ. Ukáže nájdené, chýbajúce a neočakávané balíky. Nič sama nevymaže, nevydá ani nepresunie. Opravy robíš obvyklým presunom. Najnovší miestny presun možno vrátiť, pokiaľ ho neprekonala ďalšia zmena.

## 8. Fotografie, výpadky a dáta

Fotografie sú predvolene vypnuté. Po zapnutí možno snímku konkrétneho balíka pridať, preskočiť alebo nahradiť. Aplikácia ju automaticky zmenší ešte v telefóne: najdlhšia strana má najviac 640 pixelov a JPEG najviac 40 KiB. Kvalita začína na 70 %; pri náročnom zábere sa podľa potreby zníži kvalita alebo rozmery. Malé snímky sa nezväčšujú a natočenie fotografie sa zachová. Server nezávisle overí rovnaké limity a odstráni metadáta. Nič netreba nastavovať pri každom fotení. Neúspešné nahratie nevracia už prijatý balík ani jeho polohu späť. Fotografie a doklady sú prístupné iba po prihlásení.

Rozpracovaná dávka sa jednoducho zálohuje v prehliadači pre daný SPS účet, bez hesiel a PIN-ov. Nie je to offline systém: spoločné police a SPS operácie potrebujú sieť. Pri obnovení stránky sa úspešné operácie neposielajú znovu.

Pri strate odpovede od SPS sa operácia označí ako neoverená. Obnovenie zoznamu môže pozitívne potvrdiť príjem. Zmiznutie zo zoznamu však samo osebe nepotvrdzuje výdaj ani vratku. V takom prípade over výsledok v portáli SPS a až potom zadaj potvrdený výsledok s poznámkou; nový zápis do SPS sa týmto overením neposiela.

Údaje sú v existujúcom serverovom volume `/app/data`, oddelené podľa SPS účtu. Zálohuj celý volume vrátane databázy, fotografií, dokladov a hesiel. Zatiaľ sa automaticky nemaže história ani fotografie; pri predaji ďalším prevádzkam treba dohodnúť pravidlá uchovávania a zálohovania. Prihlásenia používajú spoločné heslo prevádzky a história označenie relácie obsluhy; individuálne používateľské účty a predplatné ešte nie sú súčasťou aplikácie.
