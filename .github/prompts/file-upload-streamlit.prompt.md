---
description: "Implement file upload/download/list/delete UI in the Streamlit app that offloads media to the local Flask file storage server."
name: "File Upload — Streamlit Integration"
argument-hint: "Optional: describe the page or feature to build (e.g. 'video upload page')"
agent: "agent"
---

# File Upload Integration — Streamlit ↔ Flask `/files/*`

## Context

The local Flask server ([app.py](../../app.py)) exposes four dedicated file storage routes:

| Method | Route | Purpose |
|--------|-------|---------|
| `POST` | `/files/upload` | Upload a file via `multipart/form-data` |
| `GET` | `/files/download/<path>` | Download / stream a stored file |
| `GET` | `/files/list` | List stored files, optional `?folder=` |
| `DELETE` | `/files/delete/<path>` | Delete a stored file |

The Streamlit app that needs this feature lives at [deploy/heroku/streamlit_app.py](../../deploy/heroku/streamlit_app.py).  
It is deployed on **Heroku** and talks to the Flask server via the `API_BASE_URL` environment variable.  
The Flask server is **locally hosted** and exposed via a tunnel (e.g. ngrok).

### Required environment variables on Heroku

```
API_BASE_URL=https://<your-ngrok-or-tunnel-url>
FILE_UPLOAD_API_KEY=<same secret set on the Flask server>
```

Both variables are already read from the environment — do not hardcode them.

---

## Task

Implement the file upload/download/list/delete feature directly inside [deploy/heroku/streamlit_app.py](../../deploy/heroku/streamlit_app.py) (or create a new standalone Streamlit page if the user specifies one).

### Rules

1. **Read `API_BASE_URL` and `FILE_UPLOAD_API_KEY` from `os.getenv`** — never hardcode URLs or secrets.
2. **Use `st.file_uploader`** for uploads. Support both images (`jpg`, `jpeg`, `png`, `gif`, `webp`) and videos (`mp4`, `mov`, `avi`, `mkv`, `webm`).
3. **Pass `X-API-Key` header** on every request to the Flask server.
4. **Use `requests`** (already in the requirements) for all HTTP calls.
5. **Handle errors gracefully** — show `st.error(...)` for HTTP failures or missing config.
6. **Do not exceed 500 MB per upload** — warn the user before uploading if the file is larger.
7. **Optional `folder` field** — expose a `st.text_input` so the user can organise uploads into subdirectories (e.g. `videos/2026`). Validate client-side: only `[A-Za-z0-9_\-/]` allowed.
8. **Show upload progress** with `st.spinner`.
9. After a successful upload, display the returned `download_url` as a copyable `st.code(...)` block.
10. **List view** — add a section that calls `GET /files/list` and renders results in `st.dataframe` or `st.table`.

### Minimal upload snippet (reference implementation)

```python
import os
import requests
import streamlit as st

API_BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")
FILE_UPLOAD_API_KEY = os.getenv("FILE_UPLOAD_API_KEY", "")

def _headers() -> dict:
    return {"X-API-Key": FILE_UPLOAD_API_KEY} if FILE_UPLOAD_API_KEY else {}

uploaded = st.file_uploader("Upload a file", type=["jpg","jpeg","png","gif","webp","mp4","mov","avi","mkv","webm"])
folder = st.text_input("Subfolder (optional)", placeholder="videos/2026")

if uploaded and st.button("Upload"):
    if not API_BASE_URL:
        st.error("API_BASE_URL is not configured.")
    else:
        with st.spinner("Uploading…"):
            resp = requests.post(
                f"{API_BASE_URL}/files/upload",
                headers=_headers(),
                files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                data={"folder": folder.strip()} if folder.strip() else {},
                timeout=120,
            )
        if resp.ok:
            data = resp.json()
            st.success(f"Uploaded: {data['filename']} ({data['size_bytes']:,} bytes)")
            st.code(f"{API_BASE_URL}{data['download_url']}")
        else:
            st.error(f"Upload failed ({resp.status_code}): {resp.json().get('message', resp.text)}")
```

---

## Checklist for the agent

- [ ] Imports: `os`, `re`, `requests`, `streamlit as st`
- [ ] Read `API_BASE_URL` and `FILE_UPLOAD_API_KEY` from env
- [ ] `st.file_uploader` with allowed extension list
- [ ] Optional folder input with client-side regex validation
- [ ] Upload button → POST `/files/upload` with `X-API-Key` header
- [ ] Show success with filename, size, and download URL
- [ ] List section → GET `/files/list` → render as table
- [ ] Delete button per row → DELETE `/files/delete/<path>` → refresh list
- [ ] Graceful error handling for missing config and HTTP errors
