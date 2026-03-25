# Heroku Deployment: Flask API + Streamlit UI

This folder contains a practical two-app Heroku setup:

- Flask API app (dynamic exec service)
- Streamlit UI app (separate web dyno and URL)

## Why two Heroku apps

Heroku web dynos are designed for one primary web process per app. In production, run Flask and Streamlit as separate apps so each gets its own URL and lifecycle.

## Files in this folder

- Procfile.api: web process for Flask
- Procfile.streamlit: web process for Streamlit
- streamlit_app.py: Streamlit entrypoint example

## Example URLs

- Flask API: https://dynamic-exec-api-<suffix>.herokuapp.com
- Streamlit UI: https://dynamic-exec-ui-<suffix>.herokuapp.com

## One-time login

```bash
heroku login
```

## 1) Deploy Flask API app

```bash
heroku create dynamic-exec-api-<suffix>
copy deploy\heroku\Procfile.api Procfile
git add Procfile
git commit -m "Use Flask Procfile for Heroku API app"
git push heroku main
```

Set production flags/config vars:

```bash
heroku config:set FLASK_ENV=production
heroku config:set SIGNING_SECRET=<your-slack-signing-secret>
heroku config:set SLACK_BOT_TOKEN=<your-slack-bot-token>
heroku config:set OPENAI_API_KEY=<your-openai-key>
```

## 2) Deploy Streamlit UI app

Use a separate app for Streamlit:

```bash
heroku create dynamic-exec-ui-<suffix>
copy deploy\heroku\Procfile.streamlit Procfile
git add Procfile
git commit -m "Use Streamlit Procfile for Heroku UI app"
git push heroku main
```

Set UI config vars:

```bash
heroku config:set API_BASE_URL=https://dynamic-exec-api-<suffix>.herokuapp.com
heroku config:set STREAMLIT_PAGE_TITLE="Dynamic Exec Streamlit UI"
heroku config:set STREAMLIT_PAGE_DESCRIPTION="Simple submit form hosted on Heroku Streamlit app."
```

## 3) Enable GitHub Actions auto-deploy for Streamlit

This repository includes workflow:

- `.github/workflows/deploy-streamlit-heroku.yml`

The workflow deploys Streamlit app on push to `main` when any of these change:

- `deploy/heroku/streamlit_app.py`
- `deploy/heroku/Procfile.streamlit`
- `requirements.txt`
- `runtime.txt`

In GitHub repository settings, add these Actions secrets:

- `HEROKU_API_KEY`
- `HEROKU_EMAIL`
- `HEROKU_STREAMLIT_APP_NAME`

## 4) Trigger Streamlit updates from Flask API

Use plugin:

- `plugins.integrations.github_repo_sync_plugin`

Methods:

- `commit_streamlit_app`: commits Streamlit source file to GitHub
- `upsert_text_file`: generic text file commit within allowed path prefix

Typical flow:

1. Flask `/execute` calls `commit_streamlit_app` with updated Streamlit source.
2. GitHub push triggers workflow.
3. Workflow deploys Streamlit app to Heroku.
4. Updated Streamlit URL serves new UI.

Sample payloads are available under:

- `jsons/integrations/github/`

## Recommended workflow for one repo / two apps

Use two git remotes:

```bash
git remote add heroku-api <api-heroku-git-url>
git remote add heroku-ui <ui-heroku-git-url>
```

Then switch Procfile before each push:

- For API deploy: copy Procfile.api to Procfile and push to heroku-api
- For UI deploy: copy Procfile.streamlit to Procfile and push to heroku-ui

## Notes and constraints

- Heroku filesystem is ephemeral. Do not rely on generated local files for persistence.
- For production app definitions/data, use durable storage (Postgres, Redis, S3).
- Avoid Flask debug reloader in production.
- Keep Streamlit and Flask ports managed by Heroku via $PORT.
