import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.team_personas_service import get_all_key_personas, VERIFIED_TEAM_PERSONAS
from app.models.proposal import KeyPersona, ProposalDraft


@pytest.mark.asyncio
async def test_get_all_key_personas_returns_verified_members():
    personas = await get_all_key_personas()
    assert len(personas) >= len(VERIFIED_TEAM_PERSONAS)
    persona_names = [p["name"] for p in personas]
    assert "Sonja Anderson" in persona_names
    assert "Rachael Rice" in persona_names
    assert "Ella Lindau" in persona_names
    assert "Sarah Eichhorn" in persona_names


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
