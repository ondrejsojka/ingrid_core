# Nápovědy ke křížovkám — pracovní dokument

Status: návrh po první designové iteraci. Není to hotová specifikace ani prompt —
je to zápis toho, na čem jsme se shodli, co jsme zamítli a proč, aby se dalo
navázat, aniž bychom znovu procházeli slepé uličky.

Testovací půda: **Brněnský Metropolitan**, číslo červenec–srpen 2026.
Metropolitan je MVP, ne cíl. Všechno, co je tu specifické pro radniční magazín,
má být oddělitelné od obecného mechanismu.

---

## 1. Východisko

### Co dělá Metropolitan dnes

Napříč všemi čísly ročníku 2026 funguje křížovka jedním způsobem: **tajenka je
odpověď na otázku o něčem, co je v tomhle čísle.** Zápis do prvních tříd (1/2026),
Urban centrum (4/2026), kampaň u Hrocha (6/2026), festival Na prknech, dlažbě
i trávě (7–8/2026). Zbytek legendy je generická výplň.

Z toho plyne dvojí:

- Redakce **už dnes akceptuje kontextově vázaný obsah** — nemusíme je přesvědčovat
  o principu, jen o rozsahu.
- Kontext dnes nese **jeden slot ze sedmdesáti**. Tam je celý prostor pro zlepšení.

### Co ukázala data z generátoru

V nejčerstvějším fillu (`local/trials/fill_canonical_bias.txt`, 78 hesel) je
**jediné** slovo z tematického slovníku: `lískovec`. Ostatní theme dicty
(`metro_context_balanced` = 276 hesel, `metro_context_reader` = 108) netrefily nic.
`bm_2026-7-8.dict` dá osm zásahů, ale to jsou `aniz`, `lepsi`, `nej`, `film` —
frekvenční plevel, ne téma.

**Kanál „kontext přes odpověď" dnes prakticky neexistuje.** Celou zátěž nese
legenda. Proto není rozpočet znaků v krabičce kosmetika, ale hlavní úzké hrdlo
celého nápadu.

Zároveň: dostat do mřížky víc topical slov je drahé a samo o sobě těžké. Nelze
s tím počítat jako s hlavním řešením. Viz § 6, kde je z toho aspoň částečné
východisko.

---

## 2. Formát: švédská se číslovaným přesahem

Americká je bohatší na legendy, švédská je zažraná pod kůží českému luštiteli.
Shodli jsme se na hybridu, ale rozhodl to nakonec **layout, ne tradice**:

Křížovka v Metropolitanu sdílí stránku 30 s textovým blokem a inzercí. Americká
mřížka plus oddělený seznam legend potřebuje zhruba dvojnásobek plochy. Švédská
si legendy nese uvnitř svého obrysu; marginálie s deseti čísly je přirážka pár
centimetrů. Formát tedy vybrala sazba.

### Tři délkové tiery

| tier | kde je | rozpočet | podíl |
|---|---|---|---|
| **BOX** | legenda v buňce mřížky | medián 14 znaků, strop ~34 | většina |
| **WIDE** | dvoubuňkový blok | ~70 znaků | pár hesel |
| **NUM** | číslo v buňce, text v marginálii | bez limitu | 6–10 na mřížku |

Strop BOX je odhad (buňka ~7 mm, písmo 5–6 b. kondenzované → 3 řádky × 11–13
znaků). **Musí se přeměřit na reálné sazbě** — viz otevřené otázky.

Klíčové: 34 je strop, **ne cíl**. Vejde-li se legenda pod 12 znaků, je to lepší
legenda, ne lenivější.

### Pravidlo pro NUM

Číslovaná nápověda **musí být nestlačitelná**. Jde-li napsat do 34 znaků, nesmí
dostat číslo. Jinak je číslo slib, který se nesplní, a luštitel si marginálii
příště nepřečte.

### Požadavek na mřížku

U dlouhých a tematických hesel chtít **shluk dvou sousedních bloků** místo
osamělého `#`, aby vznikla dvoubuňková legenda. To je omezení na vzorec mřížky,
ne na fill — v hledání to nestojí nic, a je to rozdíl mezi 34 a 70 znaky přesně
tam, kde to jediné stojí za to.

---

## 3. Základní princip

> **Kontext faktem pro pár hesel, kontext hlasem pro všechna ostatní.**

Fakt je drahý na znaky. Hlas je zadarmo.

Šest až deset hesel unese konkrétní fakt z čísla (číslované nebo dvoubuňkové).
Zbytek dostane lokálnost tím, **jak** je napsaný: brněnský registr, druhá osoba,
idiom, sucho, drobná ironie. `puls` → „tep štatlu". `tři` → „nových zastávek
šaliny?". Nula znaků navíc, a přesto to nemohlo vzniknout jinde.

Brněnský registr, který je bezpečný napříč generacemi: **šalina, štatl, Svoboďák,
Zelňák, Moravák, v čudu, šlus**. Tvrdý hantec (love, kanci, borec) je pro
radniční magazín riziko — zatím ne.

---

## 4. Rodiny legend

| rodina | mechanika | příklad |
|---|---|---|
| **tázací** | tázací zájmeno vynutí pád, číslo i rod | „čímpak slyšíš?" → UŠIMA · „koho hledáš v úzkých?" → SPOJENCE · „čeho jsou Tuřany jedno?" → LETIŠŤ |
| **novotvar** | průhledná složenina, která věc popíše a není to ono | „spolusocha" → SOUSOŠÍ · „ucho na vodu" → DŽBÁN · „budka na počasí" → METEOSTANICE · „papírová nápověda" → TAHÁK |
| **citát** | zkrácená hláška, idiom, ustálená vazba | „div se!" → SVĚTE · „a je to v čudu" → FUČ · „Čí só?" → BRNA |
| **číslo z čísla** | statistika z tohohle vydání | „44992785" → IČO · „115 dní ražby" → TUNEL · „245 let" → OSVĚTLENÍ |
| **dezorientace** | doslovnost, která ukazuje jinam | „svítí, nehřeje" → DISKO · „fotka po dietě" → OŘEZ |
| **definice** | věcný popis, nejkratší možný | „Kainův bratr" → ÁBEL |

### Tázací rodina ruší značky pádů

Toto byl jeden z nejlepších posunů v celé debatě. Místo `UŠIMA (7. p.)` prostě
**„čímpak slyšíš?"**. Značka pádu je administrativní šum, který ubíjí rytmus;
tázací zájmeno dělá totéž, je kratší a je to hra, ne poznámka pod čarou.

Praktický důsledek: **ohnutý tvar přestává být vadou slova.** To mění i skórování
slovníku — viz § 7.

Zásoba: kdo / co / koho / čeho / komu / čemu / kým / čím / kde / kam / odkud / kolik.

---

## 5. Komprese

Rodové slovo je skoro vždycky redundantní — pád, délka a křížení už řeknou,
o jaký druh věci jde.

| pravidlo | před | po |
|---|---|---|
| škrtni rodové slovo | ulice od Zvonařky k Cejlu (25) | **od Zvonařky k Cejlu** (19) |
| škrtni rodové slovo | papoušek, co vás přežije (24) | **přežije majitele** (16) |
| vztažná věta → asyndeton | koule, co svítí a nehřeje (25) | **svítí, nehřeje** (14) |
| u 1. pádu vynech tázací sloveso | co dělá Riviéra? (16) | **vábí v srpnu** (12) |
| dvojtečka místo věty | koho nafoukli na Kraví hoře? (28) | **12 metrů: koho?** (15) |
| pomlčka místo „a" | bylo to, a už není (18) | **bylo – není** (11) |
| je-li faktem číslo, je legendou číslo | osm číslic z tiráže (19) | **44992785** (8) |

Komprese má být **povinný poslední průchod** nad každou legendou, ne věc vkusu.
Po jednom takovém průchodu klesl můj průměr z 19 na 14 znaků — to je rozdíl mezi
třemi a dvěma řádky v krabičce.

---

## 6. Číslo z čísla

Nejlepší rodina, protože je zároveň nejkratší, nejlokálnější a nejlevnější na
výrobu.

### Banka se extrahuje deterministicky

Dva regexy nad textem vydání (číslo + jednotka, číslo + podstatné jméno) vytáhly
z čísla 7–8/2026 **110 použitelných číselných faktů**. Žádný model v tom není.
Šum je předvídatelný (zalomení sloupce rozbije `3,5 km` na `5 km`) a spraví ho
de-hyphenace a desetinná čárka v regexu.

Výstupní tvar: `{value, unit, subject, page}`.

### A tohle je ta netriviální část

Banka negeneruje jen legendy. Generuje **seznam slov, která se vyplatí do mřížky
protlačit**, protože každé z nich přichází s hotovou dvanáctiznakovou nápovědou:
`tunel`, `lampa`, `jízda`, `byt`, `dron`, `nit`, `noc`, `les`. Všechno krátká,
ohebná slova, která se do mřížky cpou nesrovnatelně líp než `funkcionalismus`.

Tím se to potkává s tvojí námitkou, že generátor legend **nelze volat při
generování mřížky**. Nevolá se. Volá se regex, jednou za vydání, a výsledek se
propíše do skóre:

```
CSV pipeline:  has_number_fact (bool) · number_fact (str) · fact_page (int)
.dict:         score += bonus, když has_number_fact
```

Fill dostane číslo ve sloupci skóre, ne model ve smyčce.

---

## 7. Háčky místo „cluability"

První návrh měl skalární cluability skóre a penalizoval nekanonické tvary
a hovorové částice. **Byla to chyba a je systémová.**

Švédská odměňuje přesně ta slova, která běžný quality wordlist vyhazuje.
Idiomatická částice se do krabičky vejde i s pointou; pětislabičné verbální
substantivum se tam nevejde ani bez ní. `FUČ` je krásné slovo: „bylo – není" (11),
„a je to v čudu" (14), „konec, šlus" (11).

Slovo je použitelné, má-li aspoň jeden **háček**:

| háček | co to je | příklady |
|---|---|---|
| **IDIOM** | žije v ustálené vazbě → legenda = vazba bez toho slova | fuč, vale, všanc, naoko, ach, ehm |
| **OBRAZ** | máš to hned před očima | džbán, sova, obočí, disko, ešus, ohař |
| **ČÍSLO** | má v tomhle čísle číslo | tunel, lampa, jízda, byt, nit |
| **MÍSTO** | brněnská kotva | lískovec, Křenová, Slatina, Zelňák |
| **TVAR** | ohnutý tvar, který vytáhne tázací zájmeno | ušima, spojence, lístků, letišť |

Nula háčků → ven, bez ohledu na frekvenci. Reálný odpad je užší, než to vypadalo:
vlastní jména bez kotvy (`seman`, `ledec`, `kadani`) a cizí úlomky (`kite`, `ele`,
`komp`).

Rehabilitováno oproti prvnímu návrhu: `fuč`, `vale`, `nána`, `všanc`, `naoko`,
`šák`, `jílek`, `ešus`.

---

## 8. Míra: polovina smí být nudná

Nemusí být každý den posvícení. Tohle není ústupek, je to konstrukční prvek:

- Věcná legenda je **lešení**, na kterém teprve vynikne ta vtipná. Křížovka, kde
  je každá legenda hra, je vyčerpávající a zpomaluje luštění na obtěžující tempo.
- Krátká definice je **férový vstup** do křížení. Když je heslo obklopené samými
  hříčkami, luštitel nemá kde začít.
- Výrobně: model musí být brilantní třicetkrát, ne osmdesátkrát, a redaktor musí
  ověřit třicet faktů, ne osmdesát. To je rozdíl mezi udržitelným procesem
  a jednorázovým kouskem.

Cílová skladba na mřížku o ~78 heslech:

| typ | počet | poznámka |
|---|---|---|
| NUM — rozšířená nápověda v marginálii | 6–10 | nestlačitelné fakty z čísla |
| číslo z čísla / místní v boxu | ~12 | krátké, tvrdě lokální |
| hlas — idiom, novotvar, dezorientace | ~15 | lokálnost tónem, ne faktem |
| věcná definice | ~40 | lešení |

Rozprostření je důležitější než poměr: luštitel by měl narazit na něco potměšilého
zhruba každých pět hesel, ne mít půlku mřížky zábavnou a půlku mrtvou. Dvě sousední
legendy nemají být ze stejné rodiny.

---

## 9. Co jsme zamítli a proč

**Obrázková legenda (fotka v buňce).** Zamítnuto. Formát je moc malý na to, aby
z fotky šlo něco poznat, a implikuje to hodně těžko automatizovatelné práce
(výběr výřezu, ověření rozpoznatelnosti, sazba).

**Piktogramy / emoji jako tag článku.** Zamítnuto. Špatný registr pro radniční
magazín a další ruční práce pro sazbu.

**Náhrada za obojí:** na konci legendy **tečka a číslo strany** — `„po 40 letech
kývl · 19"`. Tři až pět znaků. Provenience se stejně musí ukládat kvůli ověření
faktu, takže sazba nedělá nic navíc. Luštitel dostane signál „tohle je z tohohle
čísla" a vydavatel dostane šipku do článku, což je vlastně důvod, proč by o
kontextovou křížovku měl stát.

**Značky pádů `(4. p.)` v legendě.** Zamítnuto ve prospěch tázací rodiny.

**Volání generátoru legend během hledání fillu.** Zamítnuto — je už tak dost těžké
dostat do mřížky dost tematických slov. Vše, co má ovlivnit fill, musí být
předpočítaný sloupec ve slovníku.

**Americká mřížka.** Zamítnuto kvůli ploše na stránce, ne kvůli kvalitě.

---

## 10. Otevřené otázky

1. **Reálný rozpočet znaků v BOX.** Cílíme na medián 14 při stropu 34, ale strop
   je odhad. Je-li reálný strop 24, vypadnou nejlepší dvoubuňkové legendy
   a marginálie se stane povinnou, ne volitelnou.
2. **Smíme tlačit na vzorec mřížky?** Dvoubuňkové legendy u dlouhých hesel jsou
   pro generátor levné, pro nás zásadní.
3. **Tvrdá, nebo měkká lokálnost?** Dosud psané klíče jsou řešitelné i bez
   přečtení čísla — fakt je bonus, ne podmínka. Tvrdá varianta („musíš mít
   přečteno") dá vyšší odměnu a vyhodí půlku luštitelů. Tenhle knob má na
   výsledný produkt největší dopad ze všech.
4. **Dávkování brněnského registru.** Šalina a Svoboďák ano; kde je hranice?
5. **Přenositelnost mimo Metropolitan.** Číselná banka a háčky jsou obecné.
   Brněnský registr a stránkové tagy nejsou. Až přijde druhý titul, oddělit.

---

## Příloha A — ukázkové legendy

Z gridů `fill_canonical_bias`, `fill_flattened_corpus`, `czech_15x15_score40_fill`.
Po kompresním průchodu. Slouží jako kalibrace tónu, ne jako hotová legenda.

### To nejlepší, co z debaty vylezlo

```
spolusocha                    SOUSOŠÍ
div se!                       SVĚTE
nových zastávek šaliny?       TŘI
svítí, nehřeje                DISKO
čímpak slyšíš?                UŠIMA
44992785                      IČO
bylo – není                   FUČ
od Zvonařky k Cejlu           KŘENOVÁ
Starý i Nový, obojí Brno      LÍSKOVEC
zvedne se dřív než hlas       OBOČÍ
po 40 letech kývl · 19        SVOLIL
šest zavěšených · 5           LÍSTKŮ
ucho na vodu                  DŽBÁN
papírová nápověda             TAHÁK
budka na počasí               METEOSTANICE
fotka po dietě                OŘEZ
12 metrů: koho?               OBRA
kým je pán hesel?             ADMINISTRÁTOREM
```

### Věcné lešení (a je to tak správně)

```
Kainův bratr                  ÁBEL      napospas              VŠANC
atom s nábojem                IONT      jen jako              NAOKO
pod sopránem                  ALT       Zátopek               EMIL
spojka před „by"              ANIŽ      ostrov teras          BALI
právě to děláš                ČTEŠ      miska trampa          EŠUS
tráva z trávníku              JÍLEK     husa, ale dvounohá    NÁNA
světlo ze severu              ATELIÉR   nesou otáčky          HŘÍDELE
slib z letáku                 LEPŠÍ     stát u Arábie         JEMEN
```

### Číslo z čísla, 7–8/2026

```
601 67                        PSČ         3,5 km, 10 stanovišť   LES
602 770 466                   SMS         400 dětí z nich        ŠKOL
12 metrů na Kraví hoře        OBR         90 Uhdemu              VĚK
ze 17. na 18. března 1939     NOC         místním za 115 dní     DÍK
co žere 96 km trubek?         KOROZI      212 000 ks             NÁKLAD
```

### NUM — rozšířené nápovědy

```
① NĚKOLIKANÁSOBNĚ — O kolik Gigalón překonal všechno, co brněnská hvězdárna
   kdy nafoukla. · 16
② ZADOKUMENTOVÁNÍ — Co čeká keramické střepy a pazourky z Kamech, než je
   překryjí koleje. · 21
③ KAPITALISMUS — Řád, ve kterém nový byt stojí 140 až 160 tisíc za metr,
   píše zastupitel Kment. · 22
```

---

## Příloha B — číselná banka 7–8/2026 (výběr)

```
44992785 · 601 67 · 602 770 466 · 212 000 ks nákladu
245 let osvětlení · 1781 první lampy · 1846 plyn · 30 lampářů
41 850 světelných míst · 526 slavnostních · 4 084 hodin ročně
115 dní ražby · 105 odstřelů · 320m tunel · 40 tisíc tun · 3 zastávky · 1,4 km
12 m Gigalón · 11,25 m výška · 7 % zploštění · 1 874 m nití · 218 kg · 78 h šití
196 701 jízd seniorbusu · 50 Kč · od 70 let · 6 vozů · 120 hovorů denně
28 tisíc obecních bytů · 33 bytů v Holáskách · 8 startovacích · 4 krizové
96 km parovodů → 67 km horkovodů · 336 t CO2 na km · 10 000 aut
400 dětí · 80 středoškoláků · 40 vysokoškoláků · 8 vynálezů MyMachine
90 let Uhdemu (28. 7.) · 28 let v důchodu · 1969 Nosorožec · 1939 synagoga
500 dronů · 46 inscenací · 7 Shakespearových her · 22 tisíc kandidátů ESA
1 600 diváků v aréně na Mendláku · 1928 konec plovárny · 4 766 diváků Zbrojovky
38 milionů na kulturu · 14,5 milionu Filharmonii · 20 miliard rozpočet
```

---

## Příloha C — zdroje dat

```
local/metropolitan/txt/Metropolitan_2026-7-8_web.txt   text vydání (1 671 řádků)
local/metropolitan/pdfs/                               archiv 2020–2026
local/trials/fill_canonical_bias.txt                   nejčerstvější fill, 78 hesel
local/trials/fill_flattened_corpus.txt                 alternativní fill
local/trials/czech_15x15_score40_fill.txt              hustá mřížka, brněnské ulice
local/trials/metro_context_*.dict, metro_theme_*.dict  tematické slovníky
local/trials/metro_context_*.csv                       anotační pipeline (sem patří
                                                       has_number_fact a háčky)
```
