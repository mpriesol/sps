# Police a výdaj: praktický postup

## 1. Priprav miesta

V nastaveniach vytvor regál, počet políc a prípadne počet miest na každej polici. Nula miest znamená jednu nerozdelenú policu. Predvolené názvy sú napríklad `A1`, `A2` alebo `A1-1`; používateľ ich môže upraviť. Jedno miesto môže obsahovať viac balíkov. Na nadrozmerné zásielky, pult alebo vratky môžeš vytvoriť samostatné pomenované miesto.

Každé miesto dostane stály čiarový kód `LOC-…`. Premenovanie nezneplatní starý čiarový kód; nové štítky treba vytlačiť iba kvôli viditeľnému názvu. Tlač používa Code 128 s veľkým názvom. V tlačovom dialógu možno vybrať tlačiareň alebo uloženie do PDF. Najskôr vytlač a skenerom over jeden štítok v skutočnej veľkosti.

Obsadené miesto nemožno vymazať. Najskôr jeho balíky presuň. Premenovanie regálu zachová vlastné názvy miest aj ich kódy.

## 2. Prijímaj a ukladaj podľa prevádzky

**Spolu s príjmom:** po balíku skenuj jeho miesto. Umiestnenie sa uchová aj vtedy, keď potvrdenie príjmu do SPS zlyhá. Miestny pohyb nepredstiera úspešné prijatie dopravcom.

**Samostatne po príjme:** otvor ukladanie/presun, naskenuj balík a potom cieľové miesto. Uvidíš aj jeho pôvodnú polohu. Tento postup nie je naviazaný na konkrétnu dávku ani deň; používa sa aj na preloženie už uloženého balíka.

**Zisti obsah:** sken miesta bez aktívneho balíka otvorí jeho obsah. Zoznam bez umiestnenia pomáha nájsť balíky, ktoré ešte treba uložiť. Neznámy balík možno zaevidovať iba s jasným stavom neovereného príjmu.

Veľké posledné dva znaky sú pomôcka k označeniu fixkou, ktoré už používate. Dva balíky môžu mať rovnaký koniec, preto sa identita vždy kontroluje celým číslom. Umiestnenie zúži hľadanie na niekoľko balíkov; fotografia je ďalšia voliteľná pomôcka.

## 3. Vyber kuriéra

Vedľa potvrdenia je viditeľná predvoľba. Používa sa posledný úspešne použitý kuriér aj z predchádzajúceho dňa. Bez histórie sa navrhne prvý zo SPS. Ak je navrhnutý iný než prvý, zobrazí sa upozornenie, ktoré možno vypnúť. Ak posledný použitý kuriér v zozname chýba, treba vybrať iného.

Zmena výberu ani uloženie hesla ešte nemení posledného použitého kuriéra. Táto preferencia sa uloží po skutočnom úspešnom príjme. Dávka si drží svoj výber aj vtedy, keď iná obsluha medzitým prijíma na inom telefóne.

## 4. Vydaj zákazníkovi

1. Zadaj zákaznícky PIN. Toto je vyhľadanie, nie potvrdenie výdaja.
2. Pozri miesto, veľké posledné dva znaky, prípadnú fotografiu a dobierku.
3. Vyber balík a naskenuj jeho celé číslo. Nesprávny balík výdaj nepovolí ani pri rovnakom dvojznakovom konci.
4. Ak má dobierku, potvrď skutočne prijatú hotovosť alebo platbu na termináli povolenom SPS účtom. Tlačidlo samo nezaúčtuje platbu kartou.
5. Potvrď výdaj. Server znova overí PIN aj sumu a až po úspechu v SPS uvoľní polohu. História zostáva.

Pri dobierke sa načíta PDF doklad SPS. Chyba pri jeho načítaní nemení úspešný výdaj; obnovuje sa iba doklad, nie odovzdanie zásielky. PIN sa neukladá do databázy, histórie ani miestnej zálohy prehliadača.

## 5. Vráť balíky kuriérovi

Vratky sa načítavajú zo SPS vrátane aktuálnej oprávnenosti na vrátenie a expirácie. Dátum sa neodhaduje podľa pevného počtu dní. Pred odovzdaním sa platnosť ešte raz overí, aby sa nevrátil balík s medzičasom predĺženým uložením.

Bežný postup je priamo pri príchode kuriéra: nájdi a skenuj niekoľko balíkov, vyber kuriéra a potvrď odovzdanie. Netreba ich najskôr presúvať na zvláštne miesto.

Iné prevádzky si môžu v nastaveniach zapnúť prípravu vratiek vopred. Označenie pripravených zásielok nemení stav v SPS; konečné odovzdanie je samostatný krok. Ak sa balíky aj fyzicky presunú, použije sa bežný presun na miesto vratiek.

## 6. Presuň policu alebo sprav inventúru

Pri hromadnom presune zvoľ zdroj, cieľ a všetky alebo vybrané balíky. Pred potvrdením vidíš súpis. Zmena je spoločná transakcia: ak medzitým iný telefón presunul jeden z balíkov, celý presun sa odmietne a treba obnoviť údaje. História zostane pri každom balíku.

Inventúra porovná obsah miesta s tým, čo naskenuješ. Ukáže nájdené, chýbajúce a neočakávané balíky. Nič sama nevymaže, nevydá ani nepresunie. Opravy robíš obvyklým presunom. Najnovší miestny presun možno vrátiť, pokiaľ ho neprekonala ďalšia zmena.

## 7. Fotografie, výpadky a dáta

Fotografie sú predvolene vypnuté. Po zapnutí možno snímku konkrétneho balíka pridať, preskočiť alebo nahradiť. Aplikácia ju automaticky zmenší ešte v telefóne: najdlhšia strana má najviac 640 pixelov a JPEG najviac 40 KiB. Kvalita začína na 70 %; pri náročnom zábere sa podľa potreby zníži kvalita alebo rozmery. Malé snímky sa nezväčšujú a natočenie fotografie sa zachová. Server nezávisle overí rovnaké limity a odstráni metadáta. Nič netreba nastavovať pri každom fotení. Neúspešné nahratie nevracia už prijatý balík ani jeho polohu späť. Fotografie a doklady sú prístupné iba po prihlásení.

Rozpracovaná dávka sa jednoducho zálohuje v prehliadači pre daný SPS účet, bez hesiel a PIN-ov. Nie je to offline systém: spoločné police a SPS operácie potrebujú sieť. Pri obnovení stránky sa úspešné operácie neposielajú znovu.

Pri strate odpovede od SPS sa operácia označí ako neoverená. Obnovenie zoznamu môže pozitívne potvrdiť príjem. Zmiznutie zo zoznamu však samo osebe nepotvrdzuje výdaj ani vratku. V takom prípade over výsledok v portáli SPS a až potom zadaj potvrdený výsledok s poznámkou; nový zápis do SPS sa týmto overením neposiela.

Údaje sú v existujúcom serverovom volume `/app/data`, oddelené podľa SPS účtu. Zálohuj celý volume vrátane databázy, fotografií, dokladov a hesiel. Zatiaľ sa automaticky nemaže história ani fotografie; pri predaji ďalším prevádzkam treba dohodnúť pravidlá uchovávania a zálohovania. Prihlásenia používajú spoločné heslo prevádzky a história označenie relácie obsluhy; individuálne používateľské účty a predplatné ešte nie sú súčasťou aplikácie.
