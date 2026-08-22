from app.services.analysis_service import (
    INPUT_REQUIRED_MESSAGE,
    AnalysisRegistry,
    AnalysisService,
)


def test_registry_does_not_start_a_default_video(monkeypatch):
    started_sources = []
    monkeypatch.setattr(
        AnalysisService,
        "start",
        lambda self, source=None: started_sources.append(source),
    )

    registry = AnalysisRegistry()
    service = registry.get(site_id=101)

    assert started_sources == []
    assert service.get_status()["stage"] == "stopped"
    assert service.get_status()["message"] == INPUT_REQUIRED_MESSAGE
