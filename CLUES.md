# Nápovědy ke křížovkám — pracovní dokument

Status: návrh po druhé designové iteraci. Není to hotová specifikace ani prompt —
je to zápis toho, na čem jsme se shodli, co jsme zamítli a proč, aby se dalo
navázat, aniž bychom znovu procházeli slepé uličky. Druhá iterace přidala § 5
(tvar vs. úhel), § 9 (pásma a pravidla na mřížku), § 11 (jak to generovat),
§ 12 (zákazy) a přílohy D–E s celou sadou legend na jednu skutečnou mřížku.

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
s tím počítat jako s hlavním řešením. Viz § 7, kde je z toho aspoň částečné
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
slovníku — viz § 8.

Zásoba: kdo / co / koho / čeho / komu / čemu / kým / čím / kde / kam / odkud / kolik.

---

## 5. Tvar nese svěžest, úhel ji platí

Tohle je nejdůležitější věc, na kterou jsme přišli po § 4, a mění to celý postup
výroby.

### Co český luštitel čeká

Výchozí česká legenda je **nominální fráze**: hyperonym plus přívlastek.
„Malířská pracovna" → ATELIÉR. „Básnický obrat" → TROP. Osmdesát takových za
sebou je to, na co je luštitel zvyklý — a je to zvyk **syntaktický**, ne
obsahový. Proto:

> **Pocit svěžesti vzniká z porušení tvaru, ne z chytrosti obsahu.**

To je dobrá zpráva, protože tvar je zdarma. Úhel (sémantická odchylka, pointa,
dezorientace) se platí znaky, ověřováním a rizikem nefér legendy. Tvar se
neplatí ničím — často je dokonce kratší než nominální varianta, kterou nahrazuje.

### Katalog tvarů

| tvar | příklad | cena znaků |
|---|---|---|
| **nominální** (výchozí) | básnický obrat → TROP | — |
| **tázací** | čím objímáš? → PAŽEMI | 0 |
| **rozkaz / 2. osoba** | kufry, honem! → BAL | 0 |
| **asyndeton** (dvě položky) | v síti, v punčoše → OKA | −3 proti vztažné větě |
| **dvojtečka** | šváb i Moskvan: koho? → RUSA | 0 |
| **pomlčka** | ne aliance – hned potom → NATO | −2 proti „ale" |
| **výpustka** | …ubývá → VALEM | −6 |
| **holé číslo** | 44992785 → IČO | −10 |
| **negace** | ne samo od sebe → ORGANIZOVÁNO | 0 |
| **replika** | „…jednu rundu!" → EŠTĚ | 0, a nese navíc registr |

Nejvíc nevyužitá je **výpustka**. Za jeden interpunkční znak udělá z ustálené
vazby legendu: `…do vazby` → VZETÍ (9 znaků), `…zaplatíš` → DRAZE (10),
`tápeš v …` → TMĚ (10). Je to zároveň nejšvédštější věc v celém katalogu, protože
se to vejde do krabičky i s rezervou.

### Replika: hovorové slovo se nedefinuje, dává se do pusy

Tohle vylezlo z jedné vady. `eště` se nedá oklikat nominálně — každá věcná
legenda musí říct „ještě", tedy kořen odpovědi, a to je § 12.1. Napsal jsem, že
má vypadnout. Byl to špatný závěr: **výpustka plus mluvčí** to řeší úplně.

```
…jednu rundu!            EŠTĚ      (14)
…jedno, a platím         EŠTĚ      (17)
…ne, ale skoro           EŠTĚ      (15)
```

Rozdíl proti výpustce: výpustka vynechává slovo z **ustálené vazby**, replika ho
vynechává z **věty, kterou někdo říká**. To druhé přidává zdarma to, co § 3 chce
kupovat — registr, místo, tón. „…jednu rundu!" je hospoda, ne slovník.

Platí to na celou třídu slov, kterou § 8 rehabilitoval a která se nominálně
oklikat nedá:

```
ty …!                    TEDA
…, poslouchej            HELE
…, a co má být           JO
jen …                    NAOKO
vydat …                  VŠANC
```

Obecné pravidlo: **částice a hovorové varianty se cluují replikou, ne definicí.**
Definice takového slova buď prosákne kořen, nebo zní jako poznámka jazykovědce.

### Kotva první, pointa druhá

Druhá věc z téhle iterace, a je to oprava mé chyby. `kyselá i protitanková`
→ MINA nefunguje, přestože obojí je správné: „mina" jako výraz tváře je
slovníkově v pořádku (z fr. *mine*, „udělal kyselou minu"), ale luštitel má
v hlavě nejdřív **výbušninu**, a legenda mu jako první podá ten druhý význam.
Výsledek je „cože?", ne „aha" — přesně to, co § 12.2 zakazuje.

Oprava je jen pořadí: **`protitanková i kyselá`**. Známý význam kotví, neznámý
dopadne jako pointa.

> V asyndetonu, dvojtečce i pomlčce jde první ta část, kterou luštitel zná.
> Obrácené pořadí není odvážnější, je jen nefér.

Kdyby i tak vadil (redakce je první test), fallback je pásmo O bez dvojznačnosti:
`leží tiše a čeká` → MINA.

### Tři pásma, ne dvě

Původní dělení „vtipná vs. nudná" bylo hrubé. Uprostřed leží pásmo, které je
**svěží a přitom stejně snadné jako slovník** — a to je největší páka, kterou
máme:

| pásmo | co to je | příklad | obtížnost |
|---|---|---|---|
| **S — slovník** | hyperonym + přívlastek | básnický obrat → TROP | základní |
| **O — obraz** | konkrétní věc místo hyperonyma | těsto pod utěrkou → KYNE · tři tóny naráz → AKORD · kniha jako harmonika → LEPORELO | **stejná jako S** |
| **H — hra** | dezorientace, idiom, novotvar, pointa | koho si vezmeš? → ADVOKÁTA · vrtí psem → OCAS | vyšší |

Pásmo O je zadarmo v obojím: v znacích i v obtížnosti. „Tři tóny naráz" není
o nic těžší než „souzvuk tónů", je stejně dlouhé a luštitel u něj poprvé za
osmdesát legend něco vidí. **Když se má někde zvýšit podíl svěžího, zvyšuje se
O, ne H.** H je to, co se ověřuje a co může být nefér; O není ani jedno.

### Nejvýš jedna odchylka na legendu

Legenda si vezme buď netradiční tvar, nebo sémantický úhel. Obojí naráz je
exhibice: vyjde delší, méně fér a pointa se v tom ztratí. `komu nic nevysvětlíš`
má tázací tvar a nulový úhel; `vrtí psem` má nominální tvar a plný úhel. Obě
fungují. Jejich hybrid by nefungoval.

---

## 6. Komprese

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

## 7. Číslo z čísla

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

## 8. Háčky místo „cluability"

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

## 9. Míra: polovina smí být nudná

Nemusí být každý den posvícení. Tohle není ústupek, je to konstrukční prvek:

- Věcná legenda je **lešení**, na kterém teprve vynikne ta vtipná. Křížovka, kde
  je každá legenda hra, je vyčerpávající a zpomaluje luštění na obtěžující tempo.
- Krátká definice je **férový vstup** do křížení. Když je heslo obklopené samými
  hříčkami, luštitel nemá kde začít.
- Výrobně: model musí být brilantní třicetkrát, ne osmdesátkrát, a redaktor musí
  ověřit třicet faktů, ne osmdesát. To je rozdíl mezi udržitelným procesem
  a jednorázovým kouskem.

### Cílová skladba pásem

| pásmo | cíl | dosaženo na `no_marked_n33` (69 hesel) |
|---|---|---|
| S — slovník | 45–50 % | 34 (49 %) |
| O — obraz | 15–25 % | 12 (17 %) |
| H — hra | 30–35 % | 23 (33 %) |

Z toho 6–10 hesel v pásmu H nese fakt z čísla (NUM v marginálii nebo dvoubuňková
legenda), zbytek H je hlas — lokálnost tónem, ne faktem.

### Dvě pravidla, která platí na mřížku, ne na legendu

Rozprostření je důležitější než poměr. Obojí se dá zkontrolovat strojově z grafu
křížení, a obojí jsem si na `no_marked_n33` musel opravit — okem to není vidět.

**1. Férové křížení.** Žádné heslo nesmí mít *všechna* svá křížení v pásmu H.
Každé heslo potřebuje aspoň jeden snadný vstup zvenčí. (Na n33: 0 porušení.)

**2. Rozptyl tvarů.** Heslo smí mít nejvýš **jedno** křížení se stejným
netradičním tvarem. Svěžest se opakováním spotřebovává: třetí tázací legenda
v jednom rohu už není hra, ale tik. První verze mé sady n33 měla **17 křížících
se párů se stejným tvarem** (samé tázací) a nevšiml jsem si toho, dokud jsem to
nespočítal. Po přepsání devíti legend: 0.

Ani jedno z těch pravidel nelze splnit při generování legendy po jedné. Obojí
je důvod pro architekturu v § 11.

---

## 10. Co jsme zamítli a proč

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

## 11. Generování: nejprve menu, potom přiřazení

Legenda se **negeneruje po jedné**. Model, který dostane jedno heslo a vrátí
jednu legendu, nemůže splnit § 9 — nevidí sousedy, nevidí poměr pásem, nevidí,
že už použil čtyři tázací za sebou. Rozdělení práce:

**Krok 1 — menu (lokální, paralelní, model).** Pro každé heslo: háčky (§ 8)
určí, které tvary (§ 5) jsou přípustné, a model vyrobí 5–8 kandidátů. Každý
kandidát nese tagy:

```
{answer, clue, shape, band, len, fact?: {value, page}}
```

Kandidát bez tagů se nepoužívá. O sousedech krok 1 nic neví a nemá vědět.

**Krok 2 — přiřazení (globální, deterministické, bez modelu).** Nad grafem
křížení se vybere jeden kandidát na heslo tak, aby se držely cíle pásem a platila
obě pravidla z § 9. Hladový průchod od nejvíc omezených hesel stačí; je to malá
úloha (~74 hesel, ~5 kandidátů). Když se změní fill, krok 2 se pustí znovu
a model se nevolá.

**Krok 3 — kontrolor (nutný, ne volitelný).** Osmdesát řádek kódu nad
`(fill, sada legend)`, které spočítají:

```
medián a maximum délky            (cíl: medián ≤ 15, max ≤ 34)
kořen odpovědi v legendě          (musí být 0)
poměr pásem S/O/H                 (§ 9)
heslo bez snadného křížení        (musí být 0)
shodný tvar na křížení            (≤ 1 na heslo)
fakt bez čísla stránky            (musí být 0)
```

Tenhle kontrolor je první věc, kterou má cenu napsat. Na mé ruční sadě našel
17 kolizí tvarů a dva prosáklé kořeny (`vlevo` u LEVOBOKU, `ještě` u EŠTĚ),
což je přesně to, co při psaní legenda po legendě nikdo neuvidí. Prosáklý kořen
neznamená „heslo ven": znamená „změň tvar" — u EŠTĚ to vyřešila replika (§ 5).

**Krok 4 — zpětná vazba do fillu.** Heslo, pro které neexistuje čestná legenda,
není problém legendy, ale **vada fillu**. Jde do slovníku jako penalizace, ne do
krabičky jako výmluva. Na `no_marked_n33` je to 5 hesel ze 74 (7 %): `kamp`,
`kaleta`, `japan`, `šavelová` (neověřitelné vlastní jméno) a `velel` — to
poslední je horší, protože se ve stejné mřížce potkává s `velet`. Shodný kořen
dvakrát v jedné mřížce je tvrdá vada, kterou má hlídat dupe index, ne legendář.

---

## 12. Zakázané tahy

Tvrdá pravidla. Porušení je vada, ne otázka vkusu.

1. **Legenda neobsahuje kořen odpovědi.** Ani v jiném tvaru, ani v jiném pádu.
   Kontroluje se strojově. Porušení znamená **změň tvar**, ne „heslo ven" —
   replika a výpustka umí obejít i případy, kde nominální legenda prosáknout musí.
2. **Pointu, kterou je třeba vysvětlit, zahoď.** Test: po odhalení odpovědi musí
   legenda číst jako *evidentně správná*. „Aha", ne „cože". Když to potřebuje
   výklad, je to hádanka, ne legenda. Nejčastější příčina není špatná pointa, ale
   **špatné pořadí** — kotva musí jít první (§ 5).
3. **Žádná legenda o křížovce.** Odkazy na mřížku, na počet písmen, na sousední
   heslo. Švédská na to nemá místo a luštitel na to není zvyklý.
4. **Neověřitelné vlastní jméno se neobchází vtipem.** Nevymýšlet fakt. Heslo
   jde zpátky do fillu (§ 11, krok 4).
5. **Fakt bez čísla stránky se nepoužije.** Provenience je součást legendy,
   nikoli komentář k ní.
6. **Nejvýš jedna odchylka na legendu** (§ 5) a **nejvýš jedno stejné tvarové
   křížení** (§ 9).

---

## 13. Otevřené otázky

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
local/trials/no_marked_n33_fill.txt                    nejčerstvější fill, 74 hesel
local/trials/no_marked_n32_fill.txt                    předchozí fill
local/trials/no_vocative_fill.txt                      fill bez vokativů
local/trials/fill_canonical_bias.txt                   starší fill, 78 hesel
local/trials/czech_15x15_score40_fill.txt              hustá mřížka, brněnské ulice
local/trials/metro_context_*.dict, metro_theme_*.dict  tematické slovníky
local/trials/metro_context_*.csv                       anotační pipeline (sem patří
                                                       has_number_fact a háčky)
```

---

## Příloha D — celá sada pro `no_marked_n33` (74 hesel)

Kompletní sada, ne výběr — poměry a pravidla z § 9 mají smysl jen na celé mřížce.
Medián délky **15 znaků**, maximum 23, nula prosáklých kořenů, nula hesel bez
snadného křížení, nula tvarových kolizí na křížení. Bez čísla vydání v ruce jsou
všechny legendy soběstačné; místa, kam patří fakt se stránkou, jsou v pásmu H.

### S — věcné lešení (34)

```
folkový festival          PORTA         nominální
co dostane prase?         KRMI          tázací
nad nominál               ÁŽIO          nominální
z horní sněmovny          LORD          nominální
měna z Pretorie           RAND          nominální
hudba na etapy            SUITA         nominální
rozkazovat                VELET         nominální
aniž si kdo všiml         NEPOZOROVANĚ  nominální
nosník napříč             TRAVERZ       nominální
odpuzující                REPELENTNÍ    nominální
u Sázavy je jich plno     OSAD          nominální
čeho se chopíš?           OTĚŽÍ         tázací
bylina s toulcem          ÁRON          nominální
co tlačíš?                KÁRU          tázací
šest ve výtahu: čeho?     OSOB          dvojtečka
nevolník                  OTROK         nominální
více než dost             AŽAŽ          nominální
čtvrt pinty               GILL          nominální
vystihnout …              PODSTATU      výpustka
měsíc ve verších          LUNA          nominální
objetí: čím?              PAŽEMI        dvojtečka
zaznělo                   OZVALO        nominální
být slyšet                ZNÍT          nominální
dělej, moravsky           ROB           rozkaz
tápeš v …                 TMĚ           výpustka
básnický obrat            TROP          nominální
má směr i délku           VEKTOR        nominální
spojka po „lepší"         NEŽ           nominální
šlo dolů                  KLESLO        nominální
přibývá                   ROSTE         nominální
tlačenice s křikem        MELA          nominální
pán v Kataru              EMÍR          nominální
otvírat a zavírat oči     MRKAT         nominální
fasáda je jich plná       OKEN          nominální
```

### O — obraz místo hyperonyma (12)

```
tři tóny naráz            AKORD         nominální
s otevřenou pusou         UŽASLE        nominální
zkouška prsty             OMAK          nominální
co drží kravatu?          LÍMEC         tázací
těsto pod utěrkou         KYNE          nominální
co suší kabina?           DRESY         tázací
kniha jako harmonika      LEPORELO      nominální
v síti, v punčoše         OKA           asyndeton
první metry šaliny        ROZJEZD       nominální
štít, který hlodá         RAZÍCÍ        nominální
hlas bez obsahu           ŘVANÍ         nominální
klid z pohlednice         IDYLA         nominální
```

### H — hra (23)

```
kde v Brně kvákají?       ŽABOVŘESKY    tázací
ze židle i do zbraně      POVSTAL       asyndeton
co nemá růst              TUMOR         tázací
ne samo od sebe           ORGANIZOVÁNO  negace
…do vazby                 VZETÍ         výpustka
maže se kolem huby        MED           výpustka
komu nic nevysvětlíš      OSLU          tázací
psí kosmonautka           LAJKA         novotvar
ani bílo, ani černo       ŠEDO          negace
kufry, honem!             BAL           rozkaz
…ubývá                    VALEM         výpustka
vlak nečekal              ODJELO        nominální
…zaplatíš                 DRAZE         výpustka
jak je v parku?           ZELENO        tázací
nejste bez pojmu          VÍTE          negace
šváb i Moskvan: koho?     RUSA          dvojtečka
protitanková i kyselá     MINA          asyndeton
co dělá slepá ulička?     NEVEDE        tázací
koho si vezmeš?           ADVOKÁTA      tázací
dnes na čem, zítra pod?   VOZE          tázací
první adresa              EDEN          nominální
ne aliance – hned potom   NATO          pomlčka
kde je pán?               DOMĚ          tázací
```

### Vady fillu — bez legendy, zpátky do slovníku (5)

```
kamp        není české slovo; „kemp" ano, „kamp" ne
kaleta      archaická peněženka / příjmení — neověřitelné
japan       anglický úlomek
šavelová    příjmení bez kotvy, nedohledatelné
velel       shodný kořen s VELET ve stejné mřížce → vada dupe indexu
```

Hraniční, ale unesené: `gill` (cizí jednotka, ale „čtvrt pinty" je čestná
legenda), `ažaž`, `áron`, `trop`, `rob` — všechno crosswordese, které projde
jednou za mřížku, ne třikrát.

---

## Příloha E — výběr z `no_marked_n32` a `no_vocative`

Ne celé sady — jen hesla, na kterých se dá kalibrovat tón. Pásmo v druhém sloupci.

### `no_marked_n32`

```
z něj vyrostl dolar       TOLAR         H  (etymologie je nejlevnější pointa)
jizva pro všechny         PUPEK         H
mezi koněm a vozem        OJE           O
kde má loď srdce?         LEVOBOKU      H
čeho je v hádce nejmíň?   SEBEKONTROLY  H
napětí po Italovi         VOLT          S
kam teče ze střechy?      OKAP          O
sliby v množném čísle     POLITIKOVÉ    H
za oponou jich je plno    KULIS         S
první, co se probudí      BIOS          O
pokusný mazlík            MORČE         S
co škrábe zjara           EKZÉMY        O
mrkl tam                  JUKL          S
…pálení slivovice         PĚSTITELSTVÍ  H
čím to skončilo?          ZKÁZOU        S
dovolená s vozem          AUTOKEMP      O
bylina z Bible            YZOP          S
marné výzvy               APELY         O
přejít na křestní         TYKAT         H
…jednu rundu!             EŠTĚ          H
```

`eště` bylo v první verzi odepsané jako vada — nominální legenda pro něj musí
vyslovit „ještě", tedy kořen odpovědi (§ 12.1). Řeší to replika: `…jednu rundu!`
(14 znaků, § 5). Zůstává v mřížce, a je to jedna z nejlepších legend v sadě.

### `no_vocative`

```
vrtí psem                 OCAS          H  (nejlepší z celé trojice)
z pánve i z tabule        SMAŽ          H
sladce, ne 1609 m         MILE          H
pták i ovoce              KIVI          H
jihočeské, a svítí        TEMELÍNSKÉ    H
zvedla ruce               KAPITULOVALA  H
„kdo je tam?" s obrázkem  VIDEOTELEFON  H
ruka pod hrazdou          DOPOMOC       O
orgán, nebo sraz          SLEZINU       H
hora s hvězdárnou: kde?   KLETI         S
jak se prosí?             VKLEČE        H
pod zemí i pod plachtou   KRYTY         H
kostka o hraně 10 cm      LITR          S
playlist po staru         MIXY          O
kde blikají automaty      HERNY         S
co dělá réva?             PNE           S
obilí i šachy             POLE          H
…po vědění                HLAD          H
stroje na ráno            KÁVOVARY      O
brněnská čtvrť            ŽIDENICE      S
má je jako talíře         OKATÁ         O
co už není nedělitelné    ATOM          H
brát to jak?              CITEM         S
přítok Volhy              KAMA          S
```

`ŽIDENICE` je schválně jen „brněnská čtvrť": tohle je přesně to místo, kam patří
fakt se stránkou z čísla. Bez čísla v ruce by legenda musela lhát, a to je § 12.4.
