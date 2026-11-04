"""Golden set over the deterministic sample corpus (eval/corpus.py).

After deploying your own corpus, replace the questions with your own — the harness stays.
expected_docs: which document MUST be among the sources (hit@k)
expected_substrings: what the answer must contain (exact numbers = fidelity)
relational: relational question — measures the graph's gain (expects sources from several docs)
"""

from dataclasses import dataclass, field


@dataclass
class GoldenQuestion:
    id: str
    question: str
    expected_docs: list[str]  # filenames
    expected_substrings: list[str] = field(default_factory=list)
    forbidden_substrings: list[str] = field(default_factory=list)  # must not be in the answer
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
        expected_docs=[],  # not in the corpus — the answer must admit the absence
        expected_substrings=[],
    ),
    # --- filter/compose over the menu (records branch) ---
    GoldenQuestion(
        id="q11-filter-obedy",
        question="Ktoré obedy majú do 870 kcal?",
        expected_docs=["jedalnicek-sk.pdf"],
        expected_substrings=["829", "820"],
        forbidden_substrings=["900"],  # Stir-fry exceeds the limit — must not be offered
    ),
    GoldenQuestion(
        id="q12-filter-multi",
        question="Chcem jedlo do 820 kcal a nad 50 g bielkovín. Ktoré si môžem dať?",
        expected_docs=["jedalnicek-sk.pdf"],
        expected_substrings=["Těstoviny"],
        forbidden_substrings=["Stir-fry"],
    ),
    GoldenQuestion(
        id="q13-compose",
        question=(
            "Vyber mi obed a raňajky tak, aby som sa spolu zmestil do 1300 kcal "
            "a mal čo najviac bielkovín."
        ),
        expected_docs=["jedalnicek-sk.pdf"],
        expected_substrings=["rizoto", "kaše"],  # 829+447=1276, b=104 — the only optimum
    ),
    GoldenQuestion(
        id="q14-filter-nula",
        question="Ktoré obedy majú do 100 kcal?",
        expected_docs=["jedalnicek-sk.pdf"],
        expected_substrings=[],  # must admit zero, not make things up
        forbidden_substrings=["môžeš si dať Stir", "môžeš si dať Kuřecí"],
    ),
]
