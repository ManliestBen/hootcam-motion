# Hootcam Motion

Runs on the **NUC** (or any machine with more CPU). Consumes **video streams** from the Pi (e.g. [**Hootcam Streamer**](https://github.com/ManliestBen/hootcam-streamer)) and handles:

- Motion detection
- Motion-triggered recording (pictures and movies)
- Snapshots, events, file browser
- REST API and MJPEG live view for the [**Hootcam UI**](https://github.com/ManliestBen/hootcam-ui)

No direct camera access—video is pulled from `stream_url` per camera. **stream_url** can be an RTSP URL or an MJPEG-over-HTTP URL (e.g. from Hootcam Streamer with Spyglass: `http://<pi-ip>:8080/stream` and `:8081/stream`).

Part of the **3-part Hootcam** setup:

- **Pi – [Hootcam Streamer](https://github.com/ManliestBen/hootcam-streamer)** – Publishes 2 MJPEG streams (cam0 on port 8080, cam1 on 8081). No motion or recording.
- **NUC (this app)** – Pulls those streams, runs motion detection, records to disk, serves API and MJPEG to the UI.
- **UI – [Hootcam UI](https://github.com/ManliestBen/hootcam-ui)** – Web interface; talks only to this app (the NUC).

## Requirements

- Python 3.10+
- OpenCV (for stream capture), FastAPI, and other deps in `requirements.txt`
- Network access to the Pi’s stream URLs (e.g. `http://192.168.1.10:8080/stream` and `http://192.168.1.10:8081/stream` for Hootcam Streamer with Spyglass)

## Setup

1. **Create a virtualenv and install dependencies** (recommended; keeps deps isolated from system Python):

   ```bash
   cd hootcam-motion
   python3 -m venv .venv
   source .venv/bin/activate   # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure each camera’s stream_url**

   Before or after first run, set the stream URL for each camera. The UI talks to this app, so use the UI’s **Cameras → Camera 0 (or 1) → Config** and set **Stream URL** to the Pi’s stream. For [Hootcam Streamer](https://github.com/ManliestBen/hootcam-streamer) (Spyglass), use MJPEG HTTP URLs:

   - Camera 0: `http://192.168.1.10:8080/stream`
   - Camera 1: `http://192.168.1.10:8081/stream`

   Replace `192.168.1.10` with your Pi’s IP. You can also set these via the API: `PATCH /cameras/0/config` and `PATCH /cameras/1/config` with `{ "stream_url": "http://..." }`.

3. **Run** (with venv activated)

   ```bash
   uvicorn hootcam_motion.main:app --host 0.0.0.0 --port 8080
   ```

   Use `--host 0.0.0.0` so the UI and other clients can reach the API from other machines.

4. **Point the Hootcam UI at this server**

   In the UI’s `.env`, set:

   ```env
   VITE_HOOTCAM_STREAMER_URL=http://<nuc-ip>:8080
   ```

   The UI will then use this app for all API calls and for the live MJPEG streams (which this app generates from the Pi’s stream frames).

## Configuration

- **Global config** – Target directory for recordings, log level, stream quality, stream max rate, etc. Use the UI **Config** page or `GET/PATCH /config`.
- **Per-camera config** – Motion threshold, event gap, pre/post capture, picture/movie output, filenames, and **stream_url** (e.g. MJPEG `http://<pi>:8080/stream` or RTSP). Use the UI **Cameras → Config** or `GET/PATCH /cameras/{id}/config`.
- **Storage** – Where recordings are saved. Use **Storage** in the UI or `GET/PATCH /storage`.

Config is stored in SQLite (and optionally in a JSON file). Default data directory is derived from `HOOTCAM_TARGET_DIR` or the current working directory.

**Timestamps** – All event and file timestamps (started_at, ended_at, file timestamps, and filenames) use **US Central time** (America/Chicago, CST/CDT).

## Motion detection and recording

### Pixel-count threshold

Motion is detected by **frame differencing**: each new frame is compared to a reference frame (grayscale). Pixels whose intensity change is above a **noise level** (default 32) are counted as “changed.” That count is the **changed-pixel count**.

- **Motion threshold** – Number of changed pixels required to declare motion. Default is **1500**. So if 1500 or more pixels change (above the noise level), the frame is considered “motion.”  
  - **Lower threshold** (e.g. 500) → more sensitive; small movements or lighting changes can trigger.  
  - **Higher threshold** (e.g. 3000) → less sensitive; only larger motion triggers.

- **Threshold maximum** – If set (non-zero), motion is only declared when the count is **between** threshold and threshold_maximum. Used to ignore huge changes (e.g. headlights or full-frame flash).

- **Minimum motion frames** – Motion must be seen for this many **consecutive** frames before an event starts (default 1). Reduces false triggers from single-frame glitches.

So: “motion” = (changed pixels ≥ threshold, and ≤ threshold_max if set) for at least **minimum_motion_frames** frames in a row.

### How long motion is recorded

Recording is grouped into **events**. One event = one segment of continuous (or nearly continuous) motion.

1. **Event start** – When motion is first detected, an event starts. Optionally, the last **pre_capture** frames (before motion) are included so you see the lead-in.

2. **While motion continues** – Every frame with motion is recorded. After each motion frame, **post_capture** extra frames are also recorded (so you get a short tail after motion stops).

3. **Event end** – When there has been **no motion** for **event_gap** seconds (default **60**), the event ends. The movie (and any pictures) are written to disk and the event is closed.

So **motion is recorded until there have been `event_gap` seconds with no motion**. For example, with default **event_gap = 60**:
- Motion at 0:00 → recording starts.
- Motion at 0:10, 0:20, 0:30 → still one event, keeps recording.
- Last motion at 0:35 → recording continues for **post_capture** frames, then the “no motion” timer starts.
- At **1:35** (60 s after last motion) the event ends and the movie is saved.

**Settings that affect length:**

| Setting        | Default | Effect |
|----------------|---------|--------|
| **Event gap**  | 60 s    | Seconds of **no motion** before the event (and thus the recording) ends. |
| **Post-capture** | 0 frames | Extra frames recorded **after** each motion frame. Increase (e.g. framerate × 5) for a longer tail after motion stops. |
| **Pre-capture**  | 0 frames | Frames from **before** motion to prepend to the event (rolling buffer). |
| **Movie max time** | 120 s (config) | Intended cap on movie length; see API/schema. In practice the event usually ends first due to **event_gap**. |

To get **shorter** clips: decrease **event_gap** (e.g. 15–30 s). To keep recording **longer** after motion stops: increase **post_capture** (e.g. 75 frames at 15 fps ≈ 5 s of tail).

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

- **Pi ([Hootcam Streamer](https://github.com/ManliestBen/hootcam-streamer)):** Runs two Spyglass instances; publishes MJPEG at `http://<pi>:8082/stream` and `http://<pi>:8083/stream`.
- **NUC (this app):** Opens each camera’s `stream_url` (MJPEG HTTP or RTSP), reads frames in a background thread, runs motion detection, updates `latest_jpeg` for the MJPEG stream, and drives recording (RecordingSession). Serves the same REST API as the legacy [Hootcam Server](https://github.com/ManliestBen/hootcam-server).
- **UI:** Points at the NUC only; never talks to the Pi directly.

## Reused from [hootcam-server](https://github.com/ManliestBen/hootcam-server)

- Motion detection, recording (RecordingSession, pictures/movies, scripts), database, auth, API routes, MJPEG streaming (from latest frame), config load/save.
- **Removed:** Pi camera code (Picamera2, DualCameraService), hardware resolution listing, camera restart. Replaced with a frame source that reads from `stream_url` (MJPEG HTTP or RTSP).

## Troubleshooting

- **"no frame from stream for too long; marking failed"** – The app is not receiving any frames from the camera’s `stream_url`. Check: (1) **Stream URL** is set in the UI (Cameras → Camera 0/1 → Config) to the Pi’s stream (e.g. `http://<pi-ip>:8082/stream` and `http://<pi-ip>:8083/stream`). (2) Hootcam Streamer is running on the Pi and both streams are up. (3) The NUC can reach the Pi (e.g. `curl http://<pi-ip>:8082/stream` from the NUC). (4) No firewall blocking the stream ports. The log line includes `stream_url=...` so you can confirm which URL is being used.

## API

Same API as [**Hootcam Server**](https://github.com/ManliestBen/hootcam-server): `/docs` for Swagger, `/redoc` for ReDoc. Key endpoints:

- `GET/PATCH /config` – Global config
- `GET/PATCH /storage` – Recording path
- `GET/PATCH /cameras/{id}/config` – Per-camera config (including `stream_url`)
- `GET /cameras/{id}/stream` – MJPEG live stream
- `GET /cameras/{id}/status` – Connected = receiving frames from stream_url (Pi)
- `POST /cameras/{id}/detection/start` and `.../pause`
- `GET /events`, `GET /files`, etc.

## See also

- [**Hootcam Streamer**](https://github.com/ManliestBen/hootcam-streamer) – Run on the Pi to publish the MJPEG streams this app consumes. Configure each camera’s `stream_url` to `http://<pi-ip>:8082/stream` and `http://<pi-ip>:8083/stream` (or whatever ports you use).
- [**Hootcam UI**](https://github.com/ManliestBen/hootcam-ui) – Point its `VITE_HOOTCAM_STREAMER_URL` at this app (NUC).
- [**Hootcam Server**](https://github.com/ManliestBen/hootcam-server) – Legacy all-in-one Pi backend (cameras + motion + API). This app (Hootcam Motion) is the split alternative that offloads work to the NUC.
