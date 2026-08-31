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


def test_txt_file_queue_progress_and_download(app_factory) -> None:
    with app_factory()[0] as client:
        queued = client.post(
            "/api/file-jobs",
            files={"file": ("книга.txt", "Первая глава.\n\nВторая глава.".encode(), "text/plain")},
            data={"voice": "baya", "speed": "fast", "sample_rate": "24000"},
        )
        assert queued.status_code == 202
        job_id = queued.json()["job_id"]

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = client.get(f"/api/file-jobs/{job_id}").json()
            if job["state"] == "completed":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("file job did not finish")

        assert job["filename"] == "книга.txt"
        assert job["progress"] == 100
        assert job["processed_fragments"] == 3
        assert job["stage"] == "completed"
        assert client.get(job["audio"]["mp3"]["url"]).status_code == 200
        listing = client.get("/api/file-jobs").json()
        assert [item["job_id"] for item in listing] == [job_id]


def test_file_upload_validation_and_cp1251(app_factory) -> None:
    with app_factory()[0] as client:
        wrong_type = client.post(
            "/api/file-jobs",
            files={"file": ("book.pdf", b"content", "application/pdf")},
        )
        empty = client.post(
            "/api/file-jobs",
            files={"file": ("empty.txt", b"   ", "text/plain")},
        )
        cp1251 = client.post(
            "/api/file-jobs",
            files={"file": ("old.txt", "Текст с буквой ё.".encode("cp1251"), "text/plain")},
        )

    assert wrong_type.status_code == 422
    assert "только файлы .txt" in wrong_type.json()["detail"]
    assert empty.status_code == 422
    assert "не содержит текста" in empty.json()["detail"]
    assert cp1251.status_code == 202
