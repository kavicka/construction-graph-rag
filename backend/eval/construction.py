"""Eval corpus v2 — a construction company, 12 linked documents + questions with gold pages.

Why synthetic: deterministic, shareable (no client data) and with exactly
known answers and pages. To keep it from being trivial:
- two projects with the same document structure and similar numbers (distractors),
- the same entities across documents (a subcontractor in the contract, the list and the complaints),
- SK and EN documents and questions (cross-lingual retrieval),
- tables, multi-page documents, questions with no answer in the corpus.

Each item in `pages` is one PDF page, so `gold` (file, page) is correct by construction.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from eval.pdfgen import Page, render_pages

Kind = Literal["factual", "table", "cross_doc", "multi_hop", "unanswerable"]


@dataclass
class EvalQuestion:
    id: str
    question: str
    kind: Kind
    gold: list[tuple[str, int]]  # (file, page) — relevant for retrieval
    expected: list[str] = field(default_factory=list)  # everything must be in the answer
    forbidden: list[str] = field(default_factory=list)
    lang: Literal["sk", "en"] = "sk"

    @property
    def gold_docs(self) -> set[str]:
        return {d for d, _ in self.gold}


@dataclass
class CorpusDoc:
    filename: str
    title: str
    pages: list[Page]


ZMLUVA_AGATOVA = CorpusDoc(
    "zmluva-agatova.pdf",
    "Zmluva o dielo č. ZoD-2026/014 — Bytový dom Agátová",
    [
        [
            ("h", "Článok I. Zmluvné strany"),
            (
                "p",
                "Objednávateľ: Agátová Development s.r.o., Agátová 22, 841 01 Bratislava, "
                "IČO 52314871. Zhotoviteľ: Stavby Kráľ s.r.o., Hlavná 12, 010 01 Žilina, "
                "IČO 46789123.",
            ),
            ("h", "Článok II. Predmet diela"),
            (
                "p",
                "Predmetom diela je výstavba bytového domu Agátová v Bratislave-Dúbravke so "
                "62 bytovými jednotkami, podzemnou garážou pre 70 áut a spevnenými plochami "
                "podľa projektovej dokumentácie pre stavebné povolenie.",
            ),
            ("h", "Článok III. Cena diela"),
            (
                "p",
                "Cena diela je dohodnutá ako pevná vo výške 4 860 000 EUR bez DPH. "
                "Cena zahŕňa všetky náklady zhotoviteľa vrátane zariadenia staveniska.",
            ),
        ],
        [
            ("h", "Článok IV. Čas plnenia"),
            (
                "p",
                "Zhotoviteľ začne práce 3. 3. 2026 a dielo dokončí a odovzdá najneskôr "
                "30. 11. 2027.",
            ),
            ("h", "Článok V. Zmluvná pokuta"),
            (
                "p",
                "Pri omeškaní s odovzdaním diela zaplatí zhotoviteľ zmluvnú pokutu vo výške "
                "0,05 % z ceny diela za každý deň omeškania, najviac však 10 % z ceny diela.",
            ),
            ("h", "Článok VI. Zádržné a záruka"),
            (
                "p",
                "Objednávateľ je oprávnený zadržať 5 % z každej faktúry ako zádržné, ktoré "
                "uvoľní po odstránení všetkých vád z preberacieho konania. Záručná doba na "
                "stavebnú časť je 60 mesiacov, na technologické zariadenia 24 mesiacov.",
            ),
        ],
        [
            ("h", "Článok VII. Subdodávatelia"),
            (
                "p",
                "Zhotoviteľ použije na vybrané časti diela týchto subdodávateľov: strešný "
                "plášť — Strechy Tatra s.r.o.; výplne otvorov — Okná Vadovce a.s.; "
                "elektroinštalácie — Elektro Lux s.r.o.",
            ),
            ("h", "Článok VIII. Zástupcovia strán"),
            (
                "p",
                "Stavbyvedúcim zhotoviteľa je Ing. Peter Hronec. Technický dozor "
                "objednávateľa vykonáva Ing. Lucia Šimková.",
            ),
            ("p", "V Bratislave dňa 20. 2. 2026."),
        ],
    ],
)

ROZPOCET_AGATOVA = CorpusDoc(
    "rozpocet-agatova.pdf",
    "Rozpočet stavby — Bytový dom Agátová",
    [
        [
            ("p", "Položkový rozpočet k zmluve o dielo ZoD-2026/014. Ceny sú bez DPH."),
            (
                "table",
                [
                    ["Č.", "Stavebný objekt", "Cena (EUR)"],
                    ["1", "Zemné práce", "312 400"],
                    ["2", "Základy", "488 900"],
                    ["3", "Hrubá stavba", "1 742 300"],
                    ["4", "Strecha", "386 500"],
                    ["5", "Výplne otvorov", "418 200"],
                ],
            ),
        ],
        [
            (
                "table",
                [
                    ["Č.", "Stavebný objekt", "Cena (EUR)"],
                    ["6", "Elektroinštalácie", "297 800"],
                    ["7", "ZTI a VZT", "351 600"],
                    ["8", "Dokončovacie práce", "862 300"],
                ],
            ),
            (
                "p",
                "Celková cena diela je 4 860 000 EUR bez DPH. Rezerva na nepredvídané práce "
                "je 3 % z ceny diela, t. j. 145 800 EUR, a čerpá sa len so súhlasom "
                "objednávateľa.",
            ),
        ],
    ],
)

DENNIK_AGATOVA = CorpusDoc(
    "stavebny-dennik-agatova.pdf",
    "Stavebný denník — Bytový dom Agátová (výpis)",
    [
        [
            ("h", "12. 5. 2026"),
            (
                "p",
                "Betonáž základovej dosky. Uložených 64 m³ betónu triedy C30/37. "
                "Na stavbe 14 pracovníkov, počasie polooblačno, 18 °C.",
            ),
            ("h", "13. 5. 2026"),
            ("p", "Kontrola výstuže stien 1. PP statikom Ing. Jurajom Bielikom — bez výhrad."),
        ],
        [
            ("h", "2. 6. – 6. 6. 2026"),
            (
                "p",
                "Prerušenie prác na 5 dní pre trvalý dážď. Betonáž stropu nad 2. NP "
                "presunutá na 15. 6. 2026.",
            ),
            ("h", "15. 6. 2026"),
            (
                "p",
                "Betonáž stropu nad 2. NP, uložených 48 m³ betónu C30/37. Odobraté skúšobné "
                "kocky K-03 a K-04.",
            ),
        ],
        [
            ("h", "21. 8. 2026"),
            (
                "p",
                "Pri obhliadke zistená trhlina v stene 1. NP v osi B-3, dĺžka cca 1,2 m. "
                "Vec oznámená statikovi.",
            ),
            ("h", "24. 8. 2026"),
            (
                "p",
                "Statik Ing. Bielik posúdil trhlinu ako zmrašťovaciu, bez vplyvu na "
                "statiku. Oprava injektážou epoxidovou živicou do 10. 9. 2026.",
            ),
        ],
    ],
)

KONTROLA_AGATOVA = CorpusDoc(
    "protokol-kontroly-kvality-agatova.pdf",
    "Protokol o kontrole kvality — Bytový dom Agátová",
    [
        [
            (
                "p",
                "Skúšky pevnosti betónu v tlaku na kockách 150 mm po 28 dňoch. "
                "Požiadavka pre triedu C30/37 je minimálne 37,0 MPa.",
            ),
            (
                "table",
                [
                    ["Vzorka", "Konštrukcia", "Pevnosť (MPa)", "Výsledok"],
                    ["K-01", "Základová doska", "41.2", "vyhovuje"],
                    ["K-02", "Steny 1. PP", "39.8", "vyhovuje"],
                    ["K-03", "Strop nad 2. NP", "36.4", "nevyhovuje"],
                    ["K-04", "Strop nad 2. NP", "40.5", "vyhovuje"],
                    ["K-05", "Steny 1. NP", "38.9", "vyhovuje"],
                ],
            ),
        ],
        [
            ("h", "Zistené vady"),
            (
                "table",
                [
                    ["Kód", "Popis vady", "Opatrenie", "Termín"],
                    ["D-01", "Trhlina v stene 1. NP, os B-3", "Injektáž epoxidom", "10. 9. 2026"],
                    [
                        "D-02",
                        "Chýbajúca hydroizolácia pri výťahovej šachte",
                        "Doplnenie",
                        "30. 9. 2026",
                    ],
                ],
            ),
            ("h", "Záver"),
            (
                "p",
                "Pre vzorku K-03 sa nariaďuje odber jadrového vývrtu zo stropu nad 2. NP a "
                "jeho skúška do 15. 9. 2026. Do vyhodnotenia sa strop nesmie zaťažiť "
                "skladovaním materiálu.",
            ),
        ],
    ],
)

ZMLUVA_SENEC = CorpusDoc(
    "zmluva-senec.pdf",
    "Zmluva o dielo č. ZoD-2026/021 — Logistická hala Senec",
    [
        [
            ("h", "Článok I. Zmluvné strany a predmet"),
            (
                "p",
                "Objednávateľ: Senec Logistics Park a.s., IČO 51908234. Zhotoviteľ: Stavby "
                "Kráľ s.r.o. Predmetom diela je logistická hala s plochou 18 400 m², "
                "administratívnou vstavbou a 9 nakladacími rampami.",
            ),
            ("h", "Článok II. Cena a čas plnenia"),
            ("p", "Cena diela je 7 240 000 EUR bez DPH. Termín dokončenia diela je 15. 6. 2027."),
        ],
        [
            ("h", "Článok III. Sankcie, zádržné, záruka"),
            (
                "p",
                "Zmluvná pokuta za omeškanie je 0,03 % z ceny diela za každý deň. Zádržné "
                "je 3 % z každej faktúry. Záručná doba je 36 mesiacov.",
            ),
            ("h", "Článok IV. Subdodávatelia a zástupcovia"),
            (
                "p",
                "Oceľovú konštrukciu dodá Oceľ Krupina s.r.o., strešný plášť a opláštenie "
                "Strechy Tatra s.r.o. Stavbyvedúcou zhotoviteľa je Ing. Mária Kováčová.",
            ),
        ],
    ],
)

ROZPOCET_SENEC = CorpusDoc(
    "rozpocet-senec.pdf",
    "Rozpočet stavby — Logistická hala Senec",
    [
        [
            ("p", "Rozpočet k zmluve ZoD-2026/021, ceny bez DPH."),
            (
                "table",
                [
                    ["Č.", "Stavebný objekt", "Cena (EUR)"],
                    ["1", "Zemné práce", "540 000"],
                    ["2", "Oceľová konštrukcia", "2 310 000"],
                    ["3", "Opláštenie a strecha", "1 480 000"],
                    ["4", "Priemyselné podlahy", "1 120 000"],
                    ["5", "Technológie", "1 050 000"],
                    ["6", "Spevnené plochy", "740 000"],
                ],
            ),
            ("p", "Spolu 7 240 000 EUR bez DPH."),
        ],
    ],
)

SMERNICA_BOZP = CorpusDoc(
    "smernica-bozp.pdf",
    "Smernica BOZP č. 3/2026 — Stavby Kráľ s.r.o.",
    [
        [
            ("h", "1. Práca vo výškach"),
            (
                "p",
                "Pri práci vo výške nad 1,5 m nad terénom musí byť zaistená kolektívna "
                "ochrana (zábradlie, siete). Ak to nie je možné, pracovník použije osobný "
                "zachytávací systém.",
            ),
            ("h", "2. Osobné ochranné pracovné prostriedky"),
            (
                "p",
                "Ochranná prilba a reflexná vesta sú na stavenisku povinné pre všetky osoby "
                "vrátane návštev. Školenie BOZP absolvuje každý zamestnanec raz za 12 "
                "mesiacov.",
            ),
        ],
        [
            ("h", "3. Lešenia"),
            (
                "p",
                "Lešenie skontroluje poverená osoba pred začiatkom každej zmeny a dôkladne "
                "najmenej raz za 7 dní. Výsledok sa zapíše do stavebného denníka.",
            ),
            ("h", "4. Pracovné úrazy"),
            (
                "p",
                "Každý pracovný úraz sa hlási bezpečnostnému technikovi do 24 hodín. "
                "Bezpečnostným technikom spoločnosti je Ján Mikula.",
            ),
        ],
    ],
)

SUBDODAVATELIA = CorpusDoc(
    "zoznam-subdodavatelov.pdf",
    "Zoznam schválených subdodávateľov 2026",
    [
        [
            (
                "p",
                "Hodnotenie je priemer interného auditu kvality a dodržania termínov "
                "(stupnica 1 až 5).",
            ),
            (
                "table",
                [
                    ["Subdodávateľ", "IČO", "Činnosť", "Hodnotenie", "Projekty 2026"],
                    [
                        "Strechy Tatra s.r.o.",
                        "36123456",
                        "strechy, opláštenie",
                        "4.6",
                        "Agátová, Senec",
                    ],
                    ["Okná Vadovce a.s.", "31445566", "výplne otvorov", "4.2", "Agátová"],
                    ["Elektro Lux s.r.o.", "47112233", "elektroinštalácie", "3.8", "Agátová"],
                    ["Oceľ Krupina s.r.o.", "36998877", "oceľové konštrukcie", "4.9", "Senec"],
                    ["Izolácie Kysuce s.r.o.", "50223344", "zatepľovanie", "4.4", "ZŠ Hrnčiarska"],
                ],
            ),
        ],
    ],
)

TECH_SPRAVA_ZS = CorpusDoc(
    "technicka-sprava-zs-hrnciarska.pdf",
    "Technická správa — Rekonštrukcia ZŠ Hrnčiarska, Žilina",
    [
        [
            ("h", "Súčasný stav"),
            (
                "p",
                "Budova školy bola postavená v roku 1974. Celková podlahová plocha "
                "vykurovaných priestorov je 4 820 m². Budova je v energetickej triede E s "
                "potrebou tepla 182 kWh/m² za rok.",
            ),
            ("p", "Pôvodné drevené okná sú netesné, strecha nie je zateplená."),
        ],
        [
            ("h", "Navrhované opatrenia"),
            (
                "p",
                "Zateplenie fasády minerálnou vlnou hrúbky 160 mm, zateplenie strechy "
                "hrúbkou 300 mm, nové okná so súčiniteľom Uw = 0,9 W/m²K a dve tepelné "
                "čerpadlá vzduch-voda s výkonom 2 × 45 kW.",
            ),
            (
                "p",
                "Cieľom je energetická trieda A0 s potrebou tepla 38 kWh/m² za rok. "
                "Zatepľovacie práce vykoná Izolácie Kysuce s.r.o.",
            ),
        ],
    ],
)

PONUKA_ZS = CorpusDoc(
    "ponuka-zs-hrnciarska.pdf",
    "Cenová ponuka — Rekonštrukcia ZŠ Hrnčiarska",
    [
        [
            (
                "p",
                "Na základe výzvy mesta Žilina predkladáme ponuku na rekonštrukciu "
                "ZŠ Hrnčiarska podľa technickej správy.",
            ),
            (
                "p",
                "Cena: 1 395 000 EUR bez DPH. Platnosť ponuky je 90 dní od predloženia. "
                "Doba realizácie je 7 mesiacov, hlavné práce prebehnú počas letných "
                "prázdnin 2027. Záruka na dielo je 60 mesiacov.",
            ),
            ("p", "Kontaktná osoba: Ing. Peter Hronec."),
        ],
    ],
)

WARRANTY_EN = CorpusDoc(
    "warranty-claims-2026-en.pdf",
    "Warranty claims log 2026",
    [
        [
            ("p", "Warranty claims on completed projects. Costs are in EUR excluding VAT."),
            (
                "table",
                [
                    ["Claim", "Project", "Issue", "Responsible", "Status", "Cost"],
                    [
                        "WC-101",
                        "Ruzinov residential",
                        "Leaking roof terrace",
                        "Strechy Tatra",
                        "closed",
                        "12 800",
                    ],
                    ["WC-102", "Galanta hall", "Floor cracks", "own crew", "open", "n/a"],
                    [
                        "WC-103",
                        "Ruzinov residential",
                        "Window fittings",
                        "Okna Vadovce",
                        "closed",
                        "2 150",
                    ],
                    [
                        "WC-104",
                        "Galanta hall",
                        "Roof drainage failure",
                        "Strechy Tatra",
                        "open",
                        "8 400",
                    ],
                ],
            ),
            ("p", "Open claims must be resolved within 30 days of the report date."),
        ],
    ],
)

EQUIPMENT_EN = CorpusDoc(
    "equipment-inspection-en.pdf",
    "Heavy equipment inspection register",
    [
        [
            ("h", "Tower crane TC-03"),
            (
                "p",
                "Liebherr 81 K tower crane, inventory number TC-03, deployed at the Agatova "
                "residential site. Mandatory inspection every 3 months. Last inspection on "
                "4 August 2026, next inspection due on 4 November 2026.",
            ),
            ("p", "Maximum load at the jib tip is 1.6 t."),
        ],
        [
            ("h", "Concrete pump CP-07"),
            (
                "p",
                "Truck-mounted concrete pump CP-07 with a 36 m boom. Operators need a valid "
                "certificate, which is valid for 5 years. Daily pre-start checks are "
                "recorded by the operator.",
            ),
        ],
    ],
)

CORPUS: list[CorpusDoc] = [
    ZMLUVA_AGATOVA,
    ROZPOCET_AGATOVA,
    DENNIK_AGATOVA,
    KONTROLA_AGATOVA,
    ZMLUVA_SENEC,
    ROZPOCET_SENEC,
    SMERNICA_BOZP,
    SUBDODAVATELIA,
    TECH_SPRAVA_ZS,
    PONUKA_ZS,
    WARRANTY_EN,
    EQUIPMENT_EN,
]

ZA, RA, DA, KA = (
    d.filename for d in (ZMLUVA_AGATOVA, ROZPOCET_AGATOVA, DENNIK_AGATOVA, KONTROLA_AGATOVA)
)
ZS, RS, BOZP, SUB = (
    d.filename for d in (ZMLUVA_SENEC, ROZPOCET_SENEC, SMERNICA_BOZP, SUBDODAVATELIA)
)
TS, PZ, WC, EQ = (d.filename for d in (TECH_SPRAVA_ZS, PONUKA_ZS, WARRANTY_EN, EQUIPMENT_EN))


def _q(
    id_: str,
    question: str,
    kind: Kind,
    gold: list[tuple[str, int]],
    expected: list[str],
    *,
    forbidden: list[str] | None = None,
    lang: Literal["sk", "en"] = "sk",
) -> EvalQuestion:
    return EvalQuestion(id_, question, kind, gold, expected, forbidden or [], lang)


QUESTIONS: list[EvalQuestion] = [
    # --- contract Agátová ----------------------------------------------------------------
    _q(
        "za-cena",
        "Aká je cena diela pri bytovom dome Agátová?",
        "factual",
        [(ZA, 1)],
        ["4 860 000"],
        forbidden=["7 240 000"],
    ),
    _q(
        "za-objednavatel",
        "Kto je objednávateľom stavby bytového domu Agátová?",
        "factual",
        [(ZA, 1)],
        ["Agátová Development"],
    ),
    _q(
        "za-byty",
        "Koľko bytových jednotiek bude mať bytový dom Agátová?",
        "factual",
        [(ZA, 1)],
        ["62"],
    ),
    _q("za-garaz", "Pre koľko áut je podzemná garáž v dome Agátová?", "factual", [(ZA, 1)], ["70"]),
    _q(
        "za-ico-zhotovitel",
        "Aké IČO má zhotoviteľ Stavby Kráľ v zmluve Agátová?",
        "factual",
        [(ZA, 1)],
        ["46789123"],
    ),
    _q(
        "za-zaciatok",
        "Kedy sa začínajú práce na bytovom dome Agátová?",
        "factual",
        [(ZA, 2)],
        ["3. 3. 2026"],
    ),
    _q(
        "za-termin",
        "Do kedy musí byť dokončený bytový dom Agátová?",
        "factual",
        [(ZA, 2)],
        ["30. 11. 2027"],
        forbidden=["15. 6. 2027"],
    ),
    _q(
        "za-pokuta",
        "Aká je zmluvná pokuta za omeškanie pri projekte Agátová?",
        "factual",
        [(ZA, 2)],
        ["0,05"],
        forbidden=["0,03"],
    ),
    _q(
        "za-pokuta-max",
        "Aká je maximálna výška zmluvnej pokuty v zmluve Agátová?",
        "factual",
        [(ZA, 2)],
        ["10"],
    ),
    _q(
        "za-zadrzne",
        "Koľko percent zádržného si môže objednávateľ Agátová zadržať?",
        "factual",
        [(ZA, 2)],
        ["5"],
    ),
    _q(
        "za-zaruka-stavba",
        "Aká je záručná doba na stavebnú časť bytového domu Agátová?",
        "factual",
        [(ZA, 2)],
        ["60"],
    ),
    _q(
        "za-zaruka-tech",
        "Aká záruka platí na technologické zariadenia v projekte Agátová?",
        "factual",
        [(ZA, 2)],
        ["24"],
    ),
    _q(
        "za-strecha-sub",
        "Ktorý subdodávateľ robí strechu na bytovom dome Agátová?",
        "factual",
        [(ZA, 3)],
        ["Strechy Tatra"],
    ),
    _q("za-okna-sub", "Kto dodáva okná pre Agátovú?", "factual", [(ZA, 3)], ["Okná Vadovce"]),
    _q(
        "za-stavbyveduci",
        "Kto je stavbyvedúcim na stavbe Agátová?",
        "factual",
        [(ZA, 3)],
        ["Hronec"],
        forbidden=["Kováčová"],
    ),
    _q(
        "za-dozor",
        "Kto vykonáva technický dozor objednávateľa pri Agátovej?",
        "factual",
        [(ZA, 3)],
        ["Šimková"],
    ),
    _q(
        "za-podpis",
        "Kedy bola podpísaná zmluva o dielo ZoD-2026/014?",
        "factual",
        [(ZA, 3)],
        ["20. 2. 2026"],
    ),
    _q(
        "za-en-price",
        "What is the contract price for the Agatova residential building?",
        "factual",
        [(ZA, 1)],
        ["4 860 000"],
        lang="en",
    ),
    _q(
        "za-en-deadline",
        "What is the completion deadline of the Agatova project?",
        "factual",
        [(ZA, 2)],
        ["30. 11. 2027"],
        lang="en",
    ),
    _q(
        "za-en-warranty",
        "How long is the warranty on the building works at Agatova?",
        "factual",
        [(ZA, 2)],
        ["60"],
        lang="en",
    ),
    # --- budget Agátová --------------------------------------------------------------
    _q(
        "ra-zemne",
        "Koľko stoja zemné práce v rozpočte Agátová?",
        "table",
        [(RA, 1)],
        ["312 400"],
        forbidden=["540 000"],
    ),
    _q(
        "ra-hruba",
        "Aká je cena hrubej stavby bytového domu Agátová?",
        "table",
        [(RA, 1)],
        ["1 742 300"],
    ),
    _q("ra-strecha", "Koľko stojí strecha v rozpočte Agátovej?", "table", [(RA, 1)], ["386 500"]),
    _q(
        "ra-okna",
        "Akú cenu majú výplne otvorov v rozpočte Agátová?",
        "table",
        [(RA, 1)],
        ["418 200"],
    ),
    _q("ra-zaklady", "Koľko stoja základy bytového domu Agátová?", "table", [(RA, 1)], ["488 900"]),
    _q(
        "ra-elektro",
        "Aká je rozpočtová cena elektroinštalácií v projekte Agátová?",
        "table",
        [(RA, 2)],
        ["297 800"],
    ),
    _q("ra-zti", "Koľko stoja ZTI a VZT na Agátovej?", "table", [(RA, 2)], ["351 600"]),
    _q(
        "ra-dokoncovacie",
        "Aká je cena dokončovacích prác pri Agátovej?",
        "table",
        [(RA, 2)],
        ["862 300"],
    ),
    _q(
        "ra-rezerva",
        "Aká je rezerva na nepredvídané práce v rozpočte Agátová?",
        "factual",
        [(RA, 2)],
        ["145 800"],
    ),
    _q(
        "ra-rezerva-pct",
        "Koľko percent tvorí rezerva v rozpočte bytového domu Agátová?",
        "factual",
        [(RA, 2)],
        ["3"],
    ),
    _q(
        "ra-najdrahsia",
        "Ktorá položka je najdrahšia v rozpočte Agátová?",
        "table",
        [(RA, 1)],
        ["Hrubá stavba"],
    ),
    _q(
        "ra-en-roof",
        "How much does the roof cost in the Agatova budget?",
        "table",
        [(RA, 1)],
        ["386 500"],
        lang="en",
    ),
    # --- construction diary -----------------------------------------------------------------
    _q(
        "da-doska",
        "Kedy sa betónovala základová doska na Agátovej?",
        "factual",
        [(DA, 1)],
        ["12. 5. 2026"],
    ),
    _q(
        "da-doska-objem",
        "Koľko m³ betónu sa uložilo pri betonáži základovej dosky?",
        "factual",
        [(DA, 1)],
        ["64"],
    ),
    _q(
        "da-pracovnici",
        "Koľko pracovníkov bolo na stavbe pri betonáži základovej dosky?",
        "factual",
        [(DA, 1)],
        ["14"],
    ),
    _q("da-statik", "Kto kontroloval výstuž stien 1. PP?", "factual", [(DA, 1)], ["Bielik"]),
    _q(
        "da-dazd",
        "Na koľko dní sa prerušili práce na Agátovej kvôli dažďu?",
        "factual",
        [(DA, 2)],
        ["5"],
    ),
    _q(
        "da-strop-datum",
        "Kedy sa betónoval strop nad 2. NP?",
        "factual",
        [(DA, 2)],
        ["15. 6. 2026"],
    ),
    _q(
        "da-strop-objem",
        "Koľko betónu sa uložilo do stropu nad 2. NP?",
        "factual",
        [(DA, 2)],
        ["48"],
    ),
    _q(
        "da-trhlina-datum",
        "Kedy bola zistená trhlina v stene 1. NP?",
        "factual",
        [(DA, 3)],
        ["21. 8. 2026"],
    ),
    _q(
        "da-trhlina-dlzka",
        "Aká dlhá je trhlina v stene 1. NP v osi B-3?",
        "factual",
        [(DA, 3)],
        ["1,2"],
    ),
    _q(
        "da-trhlina-typ",
        "Ako statik posúdil trhlinu v stene 1. NP?",
        "factual",
        [(DA, 3)],
        ["zmrašťovac"],
    ),
    _q(
        "da-en-rain",
        "Why was work at the Agatova site interrupted in June 2026?",
        "factual",
        [(DA, 2)],
        [],
        lang="en",
    ),
    # --- quality control ----------------------------------------------------------------
    _q(
        "ka-poziadavka",
        "Akú minimálnu pevnosť musí mať betón C30/37 na kockách?",
        "factual",
        [(KA, 1)],
        ["37"],
    ),
    _q("ka-k01", "Akú pevnosť mala vzorka K-01?", "table", [(KA, 1)], ["41.2"]),
    _q(
        "ka-k03",
        "Akú pevnosť v tlaku nameral protokol pre vzorku K-03?",
        "table",
        [(KA, 1)],
        ["36.4"],
    ),
    _q("ka-k05", "Aká bola pevnosť vzorky K-05 zo stien 1. NP?", "table", [(KA, 1)], ["38.9"]),
    _q(
        "ka-nevyhovuje",
        "Ktorá vzorka betónu nevyhovela skúške pevnosti?",
        "table",
        [(KA, 1)],
        ["K-03"],
    ),
    _q(
        "ka-d02",
        "Akú vadu označuje kód D-02 v protokole kontroly kvality?",
        "table",
        [(KA, 2)],
        ["hydroizol"],
    ),
    _q(
        "ka-d02-termin",
        "Do kedy sa musí odstrániť chýbajúca hydroizolácia pri šachte?",
        "table",
        [(KA, 2)],
        ["30. 9. 2026"],
    ),
    _q(
        "ka-vyvrt",
        "Do kedy sa musí vyskúšať jadrový vývrt pre vzorku K-03?",
        "factual",
        [(KA, 2)],
        ["15. 9. 2026"],
    ),
    _q(
        "ka-zakaz",
        "Čo je zakázané do vyhodnotenia vývrtu zo stropu nad 2. NP?",
        "factual",
        [(KA, 2)],
        ["zaťaž"],
    ),
    _q(
        "ka-en-k03",
        "Which concrete sample failed the compressive strength test?",
        "table",
        [(KA, 1)],
        ["K-03"],
        lang="en",
    ),
    # --- Senec ---------------------------------------------------------------------------
    _q(
        "zs-cena",
        "Aká je cena diela logistickej haly Senec?",
        "factual",
        [(ZS, 1)],
        ["7 240 000"],
        forbidden=["4 860 000"],
    ),
    _q("zs-plocha", "Akú plochu má logistická hala v Senci?", "factual", [(ZS, 1)], ["18 400"]),
    _q("zs-rampy", "Koľko nakladacích rámp bude mať hala Senec?", "factual", [(ZS, 1)], ["9"]),
    _q(
        "zs-termin",
        "Aký je termín dokončenia logistickej haly Senec?",
        "factual",
        [(ZS, 1)],
        ["15. 6. 2027"],
        forbidden=["30. 11. 2027"],
    ),
    _q(
        "zs-objednavatel-ico",
        "Aké IČO má objednávateľ Senec Logistics Park?",
        "factual",
        [(ZS, 1)],
        ["51908234"],
    ),
    _q(
        "zs-pokuta",
        "Aká je zmluvná pokuta pri hale Senec?",
        "factual",
        [(ZS, 2)],
        ["0,03"],
        forbidden=["0,05"],
    ),
    _q("zs-zaruka", "Akú záručnú dobu má logistická hala Senec?", "factual", [(ZS, 2)], ["36"]),
    _q("zs-zadrzne", "Koľko je zádržné pri zmluve na halu Senec?", "factual", [(ZS, 2)], ["3"]),
    _q(
        "zs-ocel-sub",
        "Kto dodá oceľovú konštrukciu haly Senec?",
        "factual",
        [(ZS, 2)],
        ["Oceľ Krupina"],
    ),
    _q(
        "zs-stavbyveduca",
        "Kto je stavbyvedúci na hale v Senci?",
        "factual",
        [(ZS, 2)],
        ["Kováčová"],
        forbidden=["Hronec"],
    ),
    _q(
        "rs-ocel",
        "Koľko stojí oceľová konštrukcia v rozpočte haly Senec?",
        "table",
        [(RS, 1)],
        ["2 310 000"],
    ),
    _q(
        "rs-podlahy", "Aká je cena priemyselných podláh v Senci?", "table", [(RS, 1)], ["1 120 000"]
    ),
    _q(
        "rs-zemne",
        "Koľko stoja zemné práce pri hale Senec?",
        "table",
        [(RS, 1)],
        ["540 000"],
        forbidden=["312 400"],
    ),
    _q(
        "rs-technologie",
        "Koľko je v rozpočte Senec vyčlenené na technológie?",
        "table",
        [(RS, 1)],
        ["1 050 000"],
    ),
    _q(
        "zs-en-price",
        "What is the price of the Senec logistics hall contract?",
        "factual",
        [(ZS, 1)],
        ["7 240 000"],
        lang="en",
    ),
    # --- occupational safety (BOZP) ----------------------------------------------------------
    _q(
        "bozp-vyska",
        "Od akej výšky treba pri práci zaistiť kolektívnu ochranu?",
        "factual",
        [(BOZP, 1)],
        ["1,5"],
    ),
    _q("bozp-prilba", "Musia mať návštevy na stavenisku prilbu?", "factual", [(BOZP, 1)], []),
    _q("bozp-skolenie", "Ako často sa opakuje školenie BOZP?", "factual", [(BOZP, 1)], ["12"]),
    _q(
        "bozp-lesenie",
        "Ako často sa musí dôkladne kontrolovať lešenie?",
        "factual",
        [(BOZP, 2)],
        ["7"],
    ),
    _q("bozp-uraz", "Do kedy treba nahlásiť pracovný úraz?", "factual", [(BOZP, 2)], ["24"]),
    _q(
        "bozp-technik",
        "Kto je bezpečnostným technikom spoločnosti?",
        "factual",
        [(BOZP, 2)],
        ["Mikula"],
    ),
    _q(
        "bozp-en-training",
        "How often must employees attend safety training?",
        "factual",
        [(BOZP, 1)],
        ["12"],
        lang="en",
    ),
    # --- subcontractors ------------------------------------------------------------------
    _q("sub-tatra-hodnotenie", "Aké hodnotenie má Strechy Tatra?", "table", [(SUB, 1)], ["4.6"]),
    _q(
        "sub-krupina-hodnotenie",
        "Ktorý subdodávateľ má najvyššie hodnotenie?",
        "table",
        [(SUB, 1)],
        ["Oceľ Krupina"],
    ),
    _q("sub-elektro-ico", "Aké IČO má Elektro Lux?", "table", [(SUB, 1)], ["47112233"]),
    _q(
        "sub-kysuce",
        "Na ktorom projekte pracuje Izolácie Kysuce?",
        "table",
        [(SUB, 1)],
        ["Hrnčiarska"],
    ),
    _q(
        "sub-najhorsi",
        "Ktorý subdodávateľ má najnižšie hodnotenie?",
        "table",
        [(SUB, 1)],
        ["Elektro Lux"],
    ),
    # --- ZŠ Hrnčiarska -------------------------------------------------------------------
    _q("ts-rok", "Kedy bola postavená ZŠ Hrnčiarska?", "factual", [(TS, 1)], ["1974"]),
    _q("ts-plocha", "Aká je vykurovaná plocha ZŠ Hrnčiarska?", "factual", [(TS, 1)], ["4 820"]),
    _q(
        "ts-trieda-dnes",
        "V akej energetickej triede je dnes ZŠ Hrnčiarska?",
        "factual",
        [(TS, 1)],
        ["E"],
    ),
    _q(
        "ts-potreba-dnes",
        "Aká je súčasná potreba tepla školy na Hrnčiarskej?",
        "factual",
        [(TS, 1)],
        ["182"],
    ),
    _q(
        "ts-izolacia",
        "Akou hrúbkou minerálnej vlny sa zateplí fasáda školy?",
        "factual",
        [(TS, 2)],
        ["160"],
    ),
    _q(
        "ts-strecha",
        "Akou hrúbkou sa zateplí strecha ZŠ Hrnčiarska?",
        "factual",
        [(TS, 2)],
        ["300"],
    ),
    _q("ts-cerpadla", "Aký výkon majú tepelné čerpadlá pre školu?", "factual", [(TS, 2)], ["45"]),
    _q(
        "ts-ciel",
        "Akú energetickú triedu má dosiahnuť škola po rekonštrukcii?",
        "factual",
        [(TS, 2)],
        ["A0"],
    ),
    _q(
        "ts-potreba-ciel",
        "Aká má byť potreba tepla školy po rekonštrukcii?",
        "factual",
        [(TS, 2)],
        ["38"],
    ),
    _q(
        "pz-cena",
        "Aká je ponuková cena rekonštrukcie ZŠ Hrnčiarska?",
        "factual",
        [(PZ, 1)],
        ["1 395 000"],
    ),
    _q(
        "pz-platnost",
        "Ako dlho platí cenová ponuka na školu Hrnčiarska?",
        "factual",
        [(PZ, 1)],
        ["90"],
    ),
    _q(
        "pz-doba",
        "Aká je doba realizácie rekonštrukcie ZŠ Hrnčiarska?",
        "factual",
        [(PZ, 1)],
        ["7"],
    ),
    _q(
        "ts-en-class",
        "What energy class should the Hrnciarska school reach?",
        "factual",
        [(TS, 2)],
        ["A0"],
        lang="en",
    ),
    # --- EN documents --------------------------------------------------------------------
    _q(
        "wc-101-cost",
        "What did warranty claim WC-101 cost?",
        "table",
        [(WC, 1)],
        ["12 800"],
        lang="en",
    ),
    _q(
        "wc-open",
        "Which warranty claims are still open?",
        "table",
        [(WC, 1)],
        ["WC-102", "WC-104"],
        lang="en",
    ),
    _q(
        "wc-deadline",
        "How quickly must open warranty claims be resolved?",
        "factual",
        [(WC, 1)],
        ["30"],
        lang="en",
    ),
    _q(
        "wc-sk-okna",
        "Koľko stála reklamácia kovania okien v Ružinove?",
        "table",
        [(WC, 1)],
        ["2 150"],
    ),
    _q(
        "eq-crane-next",
        "When is the next inspection of tower crane TC-03 due?",
        "factual",
        [(EQ, 1)],
        ["4 November 2026|November 4, 2026"],
        lang="en",
    ),
    _q(
        "eq-crane-interval",
        "How often must the tower crane be inspected?",
        "factual",
        [(EQ, 1)],
        ["3"],
        lang="en",
    ),
    _q(
        "eq-crane-load",
        "What is the maximum load at the jib tip of TC-03?",
        "factual",
        [(EQ, 1)],
        ["1.6"],
        lang="en",
    ),
    _q(
        "eq-pump-boom",
        "How long is the boom of concrete pump CP-07?",
        "factual",
        [(EQ, 2)],
        ["36"],
        lang="en",
    ),
    _q(
        "eq-cert",
        "How long is a concrete pump operator certificate valid?",
        "factual",
        [(EQ, 2)],
        ["5"],
        lang="en",
    ),
    _q(
        "eq-sk-zeriav",
        "Kedy bola posledná prehliadka žeriavu TC-03?",
        "factual",
        [(EQ, 1)],
        ["4 August 2026|4. augusta 2026|4. 8. 2026"],
    ),
    # --- across documents --------------------------------------------------------------
    _q(
        "x-pokuty",
        "Porovnaj zmluvné pokuty za omeškanie pri projektoch Agátová a Senec.",
        "cross_doc",
        [(ZA, 2), (ZS, 2)],
        ["0,05", "0,03"],
    ),
    _q(
        "x-zaruky",
        "Aké sú záručné doby v zmluvách Agátová a Senec?",
        "cross_doc",
        [(ZA, 2), (ZS, 2)],
        ["60", "36"],
    ),
    _q(
        "x-stavbyveduci",
        "Kto sú stavbyvedúci na projektoch Agátová a Senec?",
        "cross_doc",
        [(ZA, 3), (ZS, 2)],
        ["Hronec", "Kováčová"],
    ),
    _q(
        "x-tatra-projekty",
        "Na ktorých stavbách robí Strechy Tatra strechu?",
        "cross_doc",
        [(ZA, 3), (ZS, 2), (SUB, 1)],
        ["Agátová", "Senec"],
    ),
    _q(
        "x-zemne-porovnanie",
        "Porovnaj cenu zemných prác v rozpočtoch Agátová a Senec.",
        "cross_doc",
        [(RA, 1), (RS, 1)],
        ["312 400", "540 000"],
    ),
    _q(
        "x-trhlina",
        "Ako súvisí trhlina zo stavebného denníka s protokolom kontroly kvality?",
        "cross_doc",
        [(DA, 3), (KA, 2)],
        ["D-01"],
    ),
    _q(
        "x-k03-strop",
        "Kedy sa betónoval strop, z ktorého pochádza nevyhovujúca vzorka K-03?",
        "multi_hop",
        [(KA, 1), (DA, 2)],
        ["15. 6. 2026"],
    ),
    _q(
        "x-hronec-ponuka",
        "Na ktorých dokumentoch figuruje Ing. Peter Hronec a v akej úlohe?",
        "cross_doc",
        [(ZA, 3), (PZ, 1)],
        ["stavbyved", "Kontakt"],
    ),
    _q(
        "x-strecha-hodnotenie",
        "Aké hodnotenie má subdodávateľ strechy bytového domu Agátová?",
        "multi_hop",
        [(ZA, 3), (SUB, 1)],
        ["4.6"],
    ),
    _q(
        "x-tatra-reklamacie",
        "Koľko reklamácií má na starosti subdodávateľ strechy haly Senec a koľko stáli?",
        "multi_hop",
        [(ZS, 2), (WC, 1)],
        ["WC-101", "WC-104"],
    ),
    _q(
        "x-kysuce-hrubka",
        "Akú hrúbku izolácie na fasáde použije firma s hodnotením 4.4?",
        "multi_hop",
        [(SUB, 1), (TS, 2)],
        ["160"],
    ),
    _q(
        "x-zeriav-stavba",
        "Na ktorej stavbe je nasadený žeriav TC-03 a kto je tam stavbyvedúci?",
        "multi_hop",
        [(EQ, 1), (ZA, 3)],
        ["Hronec"],
    ),
    _q(
        "x-energia-uspora",
        "O koľko kWh/m² za rok klesne potreba tepla školy Hrnčiarska?",
        "multi_hop",
        [(TS, 1), (TS, 2)],
        ["144"],
    ),
    _q(
        "x-en-roof-sub-rating",
        "What rating does the roofing subcontractor of the Senec hall have?",
        "multi_hop",
        [(ZS, 2), (SUB, 1)],
        ["4.6"],
        lang="en",
    ),
    _q(
        "x-en-penalties",
        "Compare the delay penalties of the Agatova and Senec contracts.",
        "cross_doc",
        [(ZA, 2), (ZS, 2)],
        ["0,05", "0,03"],
        lang="en",
    ),
    # --- no answer in the corpus ----------------------------------------------------------
    _q("na-senec-zaciatok", "Kedy sa začnú práce na hale Senec?", "unanswerable", [], []),
    _q("na-cena-zeriav", "Koľko stál žeriav TC-03?", "unanswerable", [], []),
    _q("na-agatova-vytah", "Aký výťah sa osadí v bytovom dome Agátová?", "unanswerable", [], []),
    _q("na-k06", "Akú pevnosť mala vzorka K-06?", "unanswerable", [], []),
    _q("na-skola-riaditel", "Kto je riaditeľom ZŠ Hrnčiarska?", "unanswerable", [], []),
    _q("na-dph", "Aká je cena bytového domu Agátová s DPH?", "unanswerable", [], []),
    _q("na-en-ceo", "Who is the CEO of Stavby Kral?", "unanswerable", [], [], lang="en"),
    _q(
        "na-en-wc105",
        "What is the status of warranty claim WC-105?",
        "unanswerable",
        [],
        [],
        lang="en",
    ),
    _q(
        "na-bozp-alkohol",
        "Aké sú pravidlá pre kontrolu alkoholu na stavenisku?",
        "unanswerable",
        [],
        [],
    ),
    _q("na-tatra-ico-dic", "Aké DIČ má Strechy Tatra?", "unanswerable", [], []),
]


def generate_construction_corpus(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for doc in CORPUS:
        path = out_dir / doc.filename
        render_pages(path, doc.title, doc.pages)
        paths.append(path)
    return paths
