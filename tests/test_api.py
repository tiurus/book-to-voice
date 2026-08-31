from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor


def wait_for_completion(client, job_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job = response.json()
        if job["state"] in {"completed", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_health_and_voices(app_factory) -> None:
    with app_factory()[0] as client:
        health = client.get("/api/health")
        voices = client.get("/api/voices")

    assert health.status_code == 200
    assert health.json()["model_ready"] is True
    assert voices.json()["voices"] == ["aidar", "baya", "kseniya", "xenia", "eugene"]
    assert voices.json()["model"] == "v5_5_ru"


def test_validation_errors_are_readable(app_factory) -> None:
    with app_factory()[0] as client:
        empty = client.post("/api/speech", json={"text": "   "})
        too_long = client.post("/api/speech", json={"text": "я" * 5001})
        bad_voice = client.post("/api/speech", json={"text": "Привет", "voice": "unknown"})
        bad_ssml = client.post(
            "/api/speech", json={"text": "<speak><audio/></speak>", "ssml": True}
        )

    assert empty.status_code == 422
    assert "Введите текст" in str(empty.json())
    assert too_long.status_code == 422
    assert "5 000" in str(too_long.json())
    assert bad_voice.status_code == 422
    assert bad_ssml.status_code == 422
    assert "не поддерживается" in bad_ssml.json()["detail"]


def test_generation_download_and_delete(app_factory) -> None:
    with app_factory()[0] as client:
        queued = client.post(
            "/api/speech",
            json={"text": "В 2026 году ёж сказал: «Привет!»", "voice": "eugene"},
        )
        assert queued.status_code == 202
        job = wait_for_completion(client, queued.json()["job_id"])

        assert job["state"] == "completed"
        assert set(job["audio"]) == {"wav", "mp3"}
        wav = client.get(job["audio"]["wav"]["url"])
        mp3 = client.get(job["audio"]["mp3"]["download_url"])
        assert wav.status_code == 200
        assert wav.headers["content-type"].startswith("audio/wav")
        assert mp3.status_code == 200
        assert "attachment" in mp3.headers["content-disposition"]

        deleted = client.delete(f"/api/audio/{job['file_id']}")
        assert deleted.status_code == 204
        assert client.get(job["audio"]["wav"]["url"]).status_code == 404


def test_only_one_synthesis_runs_at_once(app_factory) -> None:
    client, synthesizer = app_factory(delay=0.08)
    with client:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda number: client.post("/api/speech", json={"text": f"Текст {number}"}),
                    range(2),
                )
            )
        jobs = [wait_for_completion(client, response.json()["job_id"]) for response in responses]

    assert all(job["state"] == "completed" for job in jobs)
    assert synthesizer.max_active == 1
