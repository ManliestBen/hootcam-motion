# Hootcam Motion

Runs on the **NUC** (or any machine with more CPU). Consumes **RTSP streams** from the Pi (e.g. [**Hootcam Streamer**](https://github.com/ManliestBen/hootcam-streamer)) and handles:

- Motion detection
- Motion-triggered recording (pictures and movies)
- Snapshots, events, file browser
- REST API and MJPEG live view for the [**Hootcam UI**](https://github.com/ManliestBen/hootcam-ui)

No direct camera access—video is pulled from `stream_url` (RTSP) per camera.

Part of the **3-part Hootcam** setup:

- **Pi – [Hootcam Streamer](https://github.com/ManliestBen/hootcam-streamer)** – Publishes 2 RTSP streams (cam0, cam1). No motion or recording.
- **NUC (this app)** – Pulls those streams, runs motion detection, records to disk, serves API and MJPEG to the UI.
- **UI – [Hootcam UI](https://github.com/ManliestBen/hootcam-ui)** – Web interface; talks only to this app (the NUC).

## Requirements

- Python 3.10+
- OpenCV (for RTSP capture), FastAPI, and other deps in `requirements.txt`
- Network access to the Pi’s RTSP URLs (e.g. `rtsp://192.168.1.10:8554/cam0`)

## Setup

1. **Install dependencies**

   ```bash
   cd hootcam-motion
   pip install -r requirements.txt
   ```

2. **Configure each camera’s stream_url**

   Before or after first run, set the RTSP URL for each camera. The UI talks to this app, so use the UI’s **Cameras → Camera 0 (or 1) → Config** and set **Stream URL** to the Pi’s stream, e.g.:

   - Camera 0: `rtsp://192.168.1.10:8554/cam0`
   - Camera 1: `rtsp://192.168.1.10:8554/cam1`

   Replace `192.168.1.10` with your Pi’s IP. You can also set these via the API: `PATCH /cameras/0/config` and `PATCH /cameras/1/config` with `{ "stream_url": "rtsp://..." }`.

3. **Run**

   ```bash
   uvicorn hootcam_motion.main:app --host 0.0.0.0 --port 8080
   ```

   Use `--host 0.0.0.0` so the UI and other clients can reach the API from other machines.

4. **Point the Hootcam UI at this server**

   In the UI’s `.env`, set:

   ```env
   VITE_HOOTCAM_STREAMER_URL=http://<nuc-ip>:8080
   ```

   The UI will then use this app for all API calls and for the live MJPEG streams (which this app generates from the RTSP frames).

## Configuration

- **Global config** – Target directory for recordings, log level, stream quality, stream max rate, etc. Use the UI **Config** page or `GET/PATCH /config`.
- **Per-camera config** – Motion threshold, event gap, pre/post capture, picture/movie output, filenames, and **stream_url** (RTSP). Use the UI **Cameras → Config** or `GET/PATCH /cameras/{id}/config`.
- **Storage** – Where recordings are saved. Use **Storage** in the UI or `GET/PATCH /storage`.

Config is stored in SQLite (and optionally in a JSON file). Default data directory is derived from `HOOTCAM_TARGET_DIR` or the current working directory.

## Running as a service (start on boot)

To run Hootcam Motion automatically on the NUC (or any Linux server) after reboot:

1. **Copy the systemd unit file** (from the repo root):
   ```bash
   sudo cp contrib/hootcam-motion.service /etc/systemd/system/
   ```

2. **Edit the unit file** to match your install:
   ```bash
   sudo nano /etc/systemd/system/hootcam-motion.service
   ```
   - **WorkingDirectory**: path to your hootcam-motion clone (e.g. `/home/nucuser/hootcam-motion` or `/opt/hootcam-motion`).
   - **ExecStart**: full path to uvicorn from your venv. Example if the app is in `/home/nucuser/hootcam-motion`:
     ```ini
     WorkingDirectory=/home/nucuser/hootcam-motion
     ExecStart=/home/nucuser/hootcam-motion/.venv/bin/uvicorn hootcam_motion.main:app --host 0.0.0.0 --port 8080
     ```
   - **User/Group**: use a dedicated user (e.g. create with `sudo useradd -r -s /bin/false hootcam`) or your normal user. Ensure that user can read the install dir and write to the recording target dir.

3. **Optional – recordings on a specific path:** Create an env file and uncomment it in the unit:
   ```bash
   echo 'HOOTCAM_TARGET_DIR=/mnt/storage/hootcam-motion' | sudo tee /etc/hootcam-motion.env
   ```
   In the unit file, uncomment: `EnvironmentFile=/etc/hootcam-motion.env`

4. **Enable and start the service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hootcam-motion
   sudo systemctl start hootcam-motion
   sudo systemctl status hootcam-motion
   ```

**Useful commands:** `sudo systemctl stop hootcam-motion`, `sudo systemctl restart hootcam-motion`, `sudo journalctl -u hootcam-motion -f`

See **contrib/README.md** for more detail.

## Architecture

- **Pi ([Hootcam Streamer](https://github.com/ManliestBen/hootcam-streamer)):** Runs only the streamer; publishes `rtsp://<pi>:8554/cam0` and `cam1`.
- **NUC (this app):** Opens each camera’s `stream_url` (RTSP), reads frames in a background thread, runs motion detection, updates `latest_jpeg` for the MJPEG stream, and drives recording (RecordingSession). Serves the same REST API as the legacy [Hootcam Server](https://github.com/ManliestBen/hootcam-server).
- **UI:** Points at the NUC only; never talks to the Pi directly.

## Reused from [hootcam-server](https://github.com/ManliestBen/hootcam-server)

- Motion detection, recording (RecordingSession, pictures/movies, scripts), database, auth, API routes, MJPEG streaming (from latest frame), config load/save.
- **Removed:** Pi camera code (Picamera2, DualCameraService), hardware resolution listing, camera restart. Replaced with RTSP frame source and `stream_url` config.

## API

Same API as [**Hootcam Server**](https://github.com/ManliestBen/hootcam-server): `/docs` for Swagger, `/redoc` for ReDoc. Key endpoints:

- `GET/PATCH /config` – Global config
- `GET/PATCH /storage` – Recording path
- `GET/PATCH /cameras/{id}/config` – Per-camera config (including `stream_url`)
- `GET /cameras/{id}/stream` – MJPEG live stream
- `GET /cameras/{id}/status` – Connected = receiving frames from RTSP
- `POST /cameras/{id}/detection/start` and `.../pause`
- `GET /events`, `GET /files`, etc.

## See also

- [**Hootcam Streamer**](https://github.com/ManliestBen/hootcam-streamer) – Run on the Pi to publish the RTSP streams this app consumes.
- [**Hootcam UI**](https://github.com/ManliestBen/hootcam-ui) – Point its `VITE_HOOTCAM_STREAMER_URL` at this app (NUC).
- [**Hootcam Server**](https://github.com/ManliestBen/hootcam-server) – Legacy all-in-one Pi backend (cameras + motion + API). This app (Hootcam Motion) is the split alternative that offloads work to the NUC.
