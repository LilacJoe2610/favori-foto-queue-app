# Favori Foto queue app

A queue management prototype for a photo studio. Staff can manage rooms and 10-minute rounds, create guest QR links, and record guest ratings and feedback. Managers can edit room options and review demo records.

## Try it locally

Run `python3 server.py` and open `http://localhost:8000`. To let phones on the same Wi-Fi try it, use the LAN address printed by the server.

## Deploy a public demo on Render

This repository includes a Render Blueprint in `render.yaml`. In Render, create a Blueprint Instance from this repository and deploy the `favori-foto-demo` web service. The demo runs with `DEMO_MODE=true`, stores data in temporary storage, and may reset when the service restarts or spins down. The public page displays a reminder to use fake data only.

The demo is intentionally for evaluation only: it does not provide staff sign-in, and visitors can change the shared demo queue. Do not use it for real customer or staff records. Render's free web services can sleep when idle, so the first visit may take about a minute to wake up.

## Local data

Outside demo mode, records are stored in `app_data.json`. This file is ignored by Git and must never be committed.
