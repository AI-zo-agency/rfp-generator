import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.team_personas_service import get_all_key_personas
from app.models.proposal import KeyPersona, ProposalDraft


def test_roster_is_not_a_hardcoded_name_tuple():
    import app.services.team_personas_service as personas

    assert not hasattr(personas, "VERIFIED_TEAM_PERSONAS")


@pytest.mark.asyncio
async def test_get_all_key_personas_returns_kb_rows():
    personas = await get_all_key_personas()
    assert isinstance(personas, list)
    for person in personas:
        assert person.get("name")
        assert "id" in person


def test_key_persona_model_validation():
    p = KeyPersona(
        id="sonja-anderson",
        name="Sonja Anderson",
        title="Founder & ECD",
        hasResume=True,
        sourceFile="04_Bio_SonjaAnderson.pdf"
    )
    assert p.id == "sonja-anderson"
    assert p.has_resume is True
    assert p.source_file == "04_Bio_SonjaAnderson.pdf"


def test_knowledge_base_key_personas_endpoint():
    client = TestClient(app)
    response = client.get("/api/v1/knowledge-base/key-personas")
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "personas" in data
    assert data["total"] > 0
