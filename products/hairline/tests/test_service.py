"""The HTTP surface: the routes a judge and a phone actually call."""

from __future__ import annotations

import json
import time

import cv2
import pytest
from fastapi.testclient import TestClient

from hairline.service import app, schedule_csv
from hairline.synth import CameraSpec, RenderOptions, render
from tests.conftest import CLOSE_CAMERA, make_scene


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as started:      # the context manager runs the startup gate
        yield started


def _wait(client: TestClient, job_id: str, timeout_s: float = 180.0) -> dict:
    """Poll until the analyzer finishes. It runs in a worker thread, so this sleeps."""
    deadline = time.time() + timeout_s
    job = client.get(f"/api/jobs/{job_id}").json()
    while job["status"] in {"queued", "running"} and time.time() < deadline:
        time.sleep(0.4)
        job = client.get(f"/api/jobs/{job_id}").json()
    return job


def _jpeg(image) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    assert ok
    return buf.tobytes()


class TestVersionAndConfig:
    def test_version_names_the_opencv_that_is_running(self, client):
        payload = client.get("/version").json()
        assert payload["opencv_version"].startswith("5."), (
            "the entry requires OpenCV 5 and /version is where a judge checks it"
        )
        assert payload["product"]["slug"] == "hairline"
        assert "custom_hal" in payload["opencv_build"]

    def test_healthz(self, client):
        assert client.get("/healthz").json()["status"] == "ok"

    def test_config_exposes_the_parameters_the_interface_renders(self, client):
        payload = client.get("/api/config").json()
        names = {p["name"] for p in payload["params"]}
        assert {"marker_length_mm", "estimator", "working_distance_mm"} <= names
        assert ".mp4" in payload["accepts"]

    def test_the_index_is_hairlines_own_and_not_the_shell(self, client):
        html = client.get("/").text
        assert "Crack survey" in html
        assert 'id="crack-table"' in html or 'id="result"' in html
        assert 'id="submit"' in html


class TestCalibrationGate:
    def test_the_gate_result_is_published(self, client):
        payload = client.get("/api/calibration").json()
        assert payload["ok"] is True, payload["failure"]
        assert payload["lines"]
        assert payload["tolerance_pct"] > 0

    def test_a_judge_can_see_which_lines_were_measured(self, client):
        payload = client.get("/api/calibration").json()
        measured = [line for line in payload["lines"] if line["measured_mm"] is not None]
        declined = [line for line in payload["lines"] if line["measured_mm"] is None]
        assert measured and declined, (
            "the card must show both halves: widths measured and widths declined"
        )


class TestCaptureAdvice:
    def test_a_square_close_view_is_usable(self, client):
        image, _ = render(make_scene(), CLOSE_CAMERA, RenderOptions())
        response = client.post(
            "/api/capture",
            files={"file": ("frame.jpg", _jpeg(image), "image/jpeg")},
            params={"target_width_mm": 0.6},
        )
        advice = response.json()
        assert response.status_code == 200
        assert advice["state"] in {"ready", "marginal"}
        assert advice["apparent_tilt_deg"] < 10
        assert advice["finest_measurable_mm"] > 0

    def test_no_marker_is_reported_before_anything_else(self, client):
        image, _ = render(make_scene(with_marker=False), CLOSE_CAMERA, RenderOptions())
        advice = client.post(
            "/api/capture", files={"file": ("frame.jpg", _jpeg(image), "image/jpeg")}
        ).json()
        assert advice["state"] == "not-ready"
        assert "marker" in advice["headline"].lower()

    def test_an_oblique_view_tells_the_operator_to_move(self, client):
        image, _ = render(
            make_scene(),
            CameraSpec(image_size=(1920, 1080), focal_px=1500.0,
                       distance_mm=300.0, yaw_deg=48.0),
            RenderOptions(),
        )
        advice = client.post(
            "/api/capture", files={"file": ("frame.jpg", _jpeg(image), "image/jpeg")}
        ).json()
        assert advice["state"] == "not-ready"
        assert "square" in advice["detail"].lower() or "square" in advice["headline"].lower()

    def test_an_empty_upload_is_a_bad_request_not_a_crash(self, client):
        response = client.post(
            "/api/capture", files={"file": ("frame.jpg", b"", "image/jpeg")}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "BAD_REQUEST"


class TestSamplesAndSheets:
    def test_the_bundled_samples_declare_their_true_widths(self, client):
        payload = client.get("/api/samples").json()
        assert payload["samples"], "the endpoint has to work from a cold start"
        for entry in payload["samples"]:
            assert entry["truth_mm"], "a sample without declared truth cannot be checked"
            assert entry["expect"]
        assert "synthetic" in payload["note"].lower()

    def test_a_sample_downloads(self, client):
        name = client.get("/api/samples").json()["samples"][0]["file"]
        response = client.get(f"/api/samples/{name}")
        assert response.status_code == 200
        assert len(response.content) > 1000

    def test_an_unknown_sample_is_a_clean_404(self, client):
        assert client.get("/api/samples/../../etc/passwd").status_code in (404, 400)

    @pytest.mark.parametrize("kind", ["marker", "calibration"])
    def test_the_printable_sheets_render(self, client, kind):
        response = client.get(f"/api/target/{kind}.png", params={"dpi": 150})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) > 5000


class TestTheWholeJob:
    def test_a_still_goes_in_and_a_schedule_comes_out(self, client, close_bench, tmp_path):
        path = tmp_path / "wall.png"
        cv2.imwrite(str(path), close_bench.image)
        response = client.post(
            "/api/jobs",
            files={"file": ("wall.png", path.read_bytes(), "image/png")},
            data={"params": json.dumps({"marker_length_mm": close_bench.scene.marker_mm})},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        job = _wait(client, job_id)
        assert job["status"] == "done", job.get("error")

        record = job["result"]
        assert record["results"], "a wall with four drawn cracks produced no rows"
        assert record["metrics"]["calibration"]["ok"] is True
        assert record["env"]["opencv_version"].startswith("5.")
        measured = [row for row in record["results"] if row["measurable"]]
        assert measured
        for row in measured:
            assert row["width_p95_mm"] > 0
            assert row["expanded_uncertainty_mm"] > 0

        csv_text = client.get(record["metrics"]["schedule_csv"]).text
        assert csv_text.startswith("crack_id,")
        assert len(csv_text.strip().splitlines()) == len(record["results"]) + 1

    def test_an_unsupported_file_type_is_rejected_before_opencv_sees_it(self, client):
        response = client.post(
            "/api/jobs",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            data={"params": "{}"},
        )
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA"

    def test_bad_parameters_are_rejected_with_a_reason(self, client, close_bench, tmp_path):
        path = tmp_path / "wall.png"
        cv2.imwrite(str(path), close_bench.image)
        response = client.post(
            "/api/jobs",
            files={"file": ("wall.png", path.read_bytes(), "image/png")},
            data={"params": json.dumps({"estimator": "wishful-thinking"})},
        )
        job = _wait(client, response.json()["job_id"])
        assert job["status"] == "failed"
        assert "estimator" in job["error"]["message"]
