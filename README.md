# Favori Foto queue app

This prototype uses only Python's standard library. There are no packages to install.

## Run it from VS Code

1. Open this project folder in VS Code.
2. Choose **Terminal → Run Task… → Favori Foto: Start LAN app**.
3. Keep the task terminal running. It prints the address for this computer and the address to use on the same Wi-Fi.
4. Open the printed address on this computer. On the staff iPad or a guest phone, use the **Same Wi-Fi devices** address and scan the guest QR.
5. To stop the app, focus its terminal and press **Control+C**.

The shared records are stored in `app_data.json` in this folder. That file is ignored by Git because it contains customer and feedback data.

## Current access model

The app is a local network prototype. Anyone who can reach the server on the Wi-Fi can open the staff interface; there is no staff sign-in yet. Use a trusted private Wi-Fi network and do not forward port 8000 or expose this server to the public internet. Before using it for real customer records, add staff authentication and backups.
