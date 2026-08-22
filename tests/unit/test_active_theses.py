from investing_agent.services.active_theses import ThesisEvidenceError


def test_evidence_error_is_explicit() -> None:
    assert str(ThesisEvidenceError("financial_result_not_pit_valid_at_as_of")) == "financial_result_not_pit_valid_at_as_of"


def test_thesis_version_contract_is_fixed() -> None:
    from investing_agent.services.active_theses import THESIS_MODEL_VERSION
    assert THESIS_MODEL_VERSION == "active-thesis-v1"
