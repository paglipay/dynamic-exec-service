def test_execute_sample_add_success(client) -> None:
    payload = {
        "module": "plugins.sample_module",
        "class": "SampleModule",
        "method": "add",
        "constructor_args": {"name": "demo", "data": "x"},
        "args": [1, 2],
    }

    response = client.post("/execute", json=payload)
    body = response.get_json()

    assert response.status_code == 200
    assert body == {"status": "success", "result": 3}


def test_execute_rejects_non_allowlisted_module(client) -> None:
    payload = {
        "module": "plugins.not_real_module",
        "class": "Nope",
        "method": "x",
        "constructor_args": {},
        "args": [],
    }

    response = client.post("/execute", json=payload)
    body = response.get_json()

    assert response.status_code == 400
    assert body["status"] == "error"
    assert "Module is not allowed" in body["message"]


def test_execute_file_system_plugin_create_directory_success(client, tmp_path) -> None:
    payload = {
        "module": "plugins.system_tools.file_system_plugin",
        "class": "FileSystemPlugin",
        "method": "create_directory",
        "constructor_args": {"base_dir": str(tmp_path)},
        "args": ["workspace/new-folder"],
    }

    response = client.post("/execute", json=payload)
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "success"
    assert body["result"]["directory"] == "workspace/new-folder"
    assert (tmp_path / "workspace" / "new-folder").is_dir()


def test_execute_file_system_plugin_rejects_traversal(client, tmp_path) -> None:
    payload = {
        "module": "plugins.system_tools.file_system_plugin",
        "class": "FileSystemPlugin",
        "method": "create_directory",
        "constructor_args": {"base_dir": str(tmp_path)},
        "args": ["../outside"],
    }

    response = client.post("/execute", json=payload)
    body = response.get_json()

    assert response.status_code == 400
    assert body["status"] == "error"
    assert "Invalid path" in body["message"]
