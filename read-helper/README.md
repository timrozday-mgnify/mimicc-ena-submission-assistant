# MIMICC Reads Upload Helper

A tiny local companion app for the [MIMICC ENA Submission Assistant](../README.md).

When the Assistant is hosted on a shared server, your study and sample metadata
are submitted server-side — but **your sequencing reads must upload directly from
your machine to ENA**, never through the server. This helper is what performs
that upload. It runs `enasequence/webin-cli` locally; the Assistant's web page
(in your browser) detects it on `localhost`, hands it the manifest the server
built, and streams the upload log back.

The helper is a dumb local executor: it never connects to the hosted server.

## Run it

Requires Docker. From this directory:

```bash
cp .env.example .env          # optional; set MIMICC_APP_ORIGIN if the app is remote
docker compose up -d --build
```

Then open the **Reads** tab in the Assistant — it will show the helper as
detected. Leave the helper running for the duration of your uploads.

To stop it: `docker compose down`.

## How it works

- Binds to `127.0.0.1:9100` only (not reachable from the network).
- Mounts the host Docker socket (to spawn the webin-cli container) and the host
  filesystem (to read your local read files and write the manifest beside them).
- CORS allows `localhost` plus the origin set in `MIMICC_APP_ORIGIN`, so a page
  served from your hosted Assistant can drive the helper.

### API
- `GET /api/health` — liveness + whether Webin credentials are set (used for
  browser detection).
- `POST /api/credentials` / `DELETE /api/credentials` — set/clear Webin
  credentials (held in memory only, never persisted).
- `POST /api/scan {host_dir}` — list read groups in a local directory.
- `POST /api/submit {input_host_dir, manifest_filename, manifest_text, submit, test}`
  → `{job_id}`; then `GET /api/stream/{job_id}` (SSE) launches webin-cli and
  streams the log, ending with `{done, exit_code, log, experiment_accession,
  run_accession}`.

## Security

Like the upstream webin-cli-browser-assistant, the helper has **no auth** and
relies on loopback binding: any local process/page that can reach
`localhost:9100` can drive it. Webin credentials are kept in memory only. Run it
only while you are actively uploading.
