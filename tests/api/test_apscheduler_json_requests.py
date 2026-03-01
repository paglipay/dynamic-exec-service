from __future__ import annotations


def _execute(client, payload: dict) -> tuple[int, dict]:
    response = client.post("/execute", json=payload)
    body = response.get_json()
    return response.status_code, body


def test_apscheduler_health_from_json_request(client, load_json_request) -> None:
    payload = load_json_request("jsons/system_tools/apscheduler/apscheduler_health_request.json")

    status_code, body = _execute(client, payload)

    assert status_code == 200
    assert body["status"] == "success"
    assert body["result"]["status"] == "success"
    assert body["result"]["running"] is True


def test_apscheduler_add_list_remove_job_from_json_requests(client, load_json_request) -> None:
    add_payload = load_json_request("jsons/system_tools/apscheduler/apscheduler_add_interval_workflow_job_request.json")
    list_payload = load_json_request("jsons/system_tools/apscheduler/apscheduler_list_jobs_request.json")
    remove_payload = load_json_request("jsons/system_tools/apscheduler/apscheduler_remove_job_request.json")

    add_status, add_body = _execute(client, add_payload)
    assert add_status == 200
    assert add_body["status"] == "success"

    expected_job_id = add_payload["args"][0]["job_id"]

    list_status, list_body = _execute(client, list_payload)
    assert list_status == 200
    assert list_body["status"] == "success"

    jobs = list_body["result"]["jobs"]
    assert any(job["id"] == expected_job_id for job in jobs)

    remove_payload["args"] = [expected_job_id]
    remove_status, remove_body = _execute(client, remove_payload)
    assert remove_status == 200
    assert remove_body["status"] == "success"
