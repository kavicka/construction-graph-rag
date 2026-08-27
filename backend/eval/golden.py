"""Golden sada nad deterministickým vzorkovým korpusom (eval/corpus.py).

Po nasadení vlastného korpusu nahraď otázky vlastnými — harness ostáva.
expected_docs: ktorý dokument MUSÍ byť medzi zdrojmi (hit@k)
expected_substrings: čo musí odpoveď obsahovať (presné čísla = fidelity)
relational: relačná otázka — meria prínos grafu (očakáva zdroje z viacerých dokumentov)
"""

from dataclasses import dataclass, field


@dataclass
class GoldenQuestion:
    id: str
    question: str
    expected_docs: list[str]  # filenames
    expected_substrings: list[str] = field(default_factory=list)
    relational: bool = False


GOLDEN: list[GoldenQuestion] = [
    GoldenQuestion(
        id="q1-faktual-sk",
        question="Aký prietok mala vzorka A-02?",
        expected_docs=["meranie-sk.pdf"],
        expected_substrings=["13.1"],
    ),
    GoldenQuestion(
        id="q2-tabulka-max",
        question="Aká bola teplota vzorky B-01 podľa tabuľky meraní?",
        expected_docs=["meranie-sk.pdf"],
        expected_substrings=["20.9"],
    ),
    GoldenQuestion(
        id="q3-faktual-en",
        question="How often must the coolant flow sensor be calibrated?",
        expected_docs=["calibration-en.pdf"],
        expected_substrings=["30"],
    ),
    GoldenQuestion(
        id="q4-en-tabulka",
        question="What is the maximum permissible deviation for the FL-200 sensor?",
        expected_docs=["calibration-en.pdf"],
        expected_substrings=["0.5"],
    ),
    GoldenQuestion(
        id="q5-cross-sk",
        question="Ktorý snímač sa používa na meranie prietoku chladiaceho média?",
        expected_docs=["meranie-sk.pdf", "calibration-en.pdf"],
    ),
    GoldenQuestion(
        id="q6-relacna",
        question="Aký je súvis medzi meraním prietoku a kalibráciou snímačov naprieč dokumentmi?",
        expected_docs=["meranie-sk.pdf", "calibration-en.pdf"],
        relational=True,
    ),
    GoldenQuestion(
        id="q7-relacna-en",
        question="What is the relation between the flow measurements and sensor calibration?",
        expected_docs=["meranie-sk.pdf", "calibration-en.pdf"],
        relational=True,
    ),
    GoldenQuestion(
        id="q8-nesuvisiaca",
        question="Aké odporúčania platia pre skladovanie vzoriek?",
        expected_docs=["skladovanie-sk.pdf"],
        expected_substrings=["4"],
    ),
    GoldenQuestion(
        id="q9-cislo-fts",
        question="V ktorom dokumente sa spomína hodnota 101.3 kPa?",
        expected_docs=["meranie-sk.pdf"],
    ),
    GoldenQuestion(
        id="q10-negativna",
        question="Aká je cena zariadenia XYZ-9000?",
        expected_docs=[],  # nie je v korpuse — odpoveď musí priznať absenciu
        expected_substrings=[],
    ),
]
