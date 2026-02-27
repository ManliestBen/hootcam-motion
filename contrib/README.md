# Hootcam Motion – systemd service

## Run on boot (NUC or Linux server)

1. Complete the main [README](../README.md) setup (venv + `pip install -r requirements.txt`, configure `stream_url` per camera).

2. Copy the unit file:
   ```bash
   sudo cp contrib/hootcam-motion.service /etc/systemd/system/
   ```

3. Edit the unit to match your install:
   ```bash
   sudo nano /etc/systemd/system/hootcam-motion.service
   ```
   - **WorkingDirectory**: path to your hootcam-motion clone (e.g. `/home/nucuser/hootcam-motion` or `/opt/hootcam-motion`).
   - **ExecStart**: use the full path to uvicorn from your venv. If the app is in `/home/nucuser/hootcam-motion` with a venv at `.venv`:
     ```ini
     WorkingDirectory=/home/nucuser/hootcam-motion
     ExecStart=/home/nucuser/hootcam-motion/.venv/bin/uvicorn hootcam_motion.main:app --host 0.0.0.0 --port 8080
     ```
   - **User/Group**: use a dedicated user (create with `sudo useradd -r -s /bin/false hootcam`) or your normal user. Ensure that user can read the install dir and write to the recording target dir.

4. Optional: set recording path via env file. Create `/etc/hootcam-motion.env`:
   ```bash
   echo 'HOOTCAM_TARGET_DIR=/mnt/storage/hootcam-motion' | sudo tee /etc/hootcam-motion.env
   ```
   Then in the unit file uncomment:
   ```ini
   EnvironmentFile=/etc/hootcam-motion.env
   ```

5. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable hootcam-motion
   sudo systemctl start hootcam-motion
   sudo systemctl status hootcam-motion
   ```

**Useful commands:** `sudo systemctl stop hootcam-motion`, `sudo systemctl restart hootcam-motion`, `sudo journalctl -u hootcam-motion -f`
