from pathlib import Path

from aurora.regulation import RegulationIndex


def test_search_returns_relevant_article_without_unrelated_chapters() -> None:
    index = RegulationIndex(Path(__file__).resolve().parents[1] / "dados" / "regulamento.md")
    results = index.search("Até que horas a piscina funciona aos domingos?", limit=1)
    assert len(results) == 1
    assert "Art. 22" in results[0]
    assert "20h" in results[0]
    assert "Animais de estimação" not in results[0]

