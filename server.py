#!/usr/bin/env python3
"""Small LAN server for the Favori Foto queue demo."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import hashlib
import secrets
import socket
import sys
import threading
import time
from http.cookies import SimpleCookie

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "app_data.json"
LOCK = threading.Lock()
MANAGER_SESSIONS = {}
SESSION_TTL = 12 * 60 * 60


def read_data():
    try:
        return json.loads(DATA_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {"options": None, "nextGuest": 21, "checkedIn": ["Mia Lê"], "attendance": [], "guestLinks": {}, "customers": []}


def write_data(data):
    temp = DATA_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(DATA_FILE)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def reply(self, status, body, content_type="application/json; charset=utf-8", extra_headers=None):
        payload = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request too large")
        return json.loads(self.rfile.read(length) or b"{}")

    def manager_session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        token = cookie.get("favori_manager")
        session = MANAGER_SESSIONS.get(token.value) if token else None
        if not session or session["expires"] < time.time():
            if token:
                MANAGER_SESSIONS.pop(token.value, None)
            return None
        session["expires"] = time.time() + SESSION_TTL
        return session

    def require_manager(self):
        session = self.manager_session()
        if not session:
            self.reply(401, {"error": "Cần đăng nhập quản lý"})
        return session

    def validate_staff_state(self, old, incoming):
        previous = old.get("options")
        updated = incoming.get("options")
        if previous is None:
            return True
        if not isinstance(updated, list) or len(previous) != len(updated):
            return False
        old_rounds = {}
        new_rounds = {}
        old_config = [(o.get("room"), o.get("label")) for o in previous]
        new_config = [(o.get("room"), o.get("label")) for o in updated]
        if old_config != new_config:
            return False
        for oi, option in enumerate(previous):
            for start, item in option.get("slots", {}).items():
                old_rounds[item.get("number")] = (oi, start, item)
        for oi, option in enumerate(updated):
            for start, item in option.get("slots", {}).items():
                number = item.get("number")
                if number in new_rounds:
                    return False
                new_rounds[number] = (oi, start, item)
        if not set(old_rounds).issubset(new_rounds):
            return False
        for number, (oi, start, old_round) in old_rounds.items():
            new_oi, new_start, new_round = new_rounds[number]
            if oi != new_oi or old_round.get("name") != new_round.get("name"):
                return False
            old_status, new_status = old_round.get("status"), new_round.get("status")
            if old_status == new_status:
                if old_status == "on air" and old_round.get("startedAt") != new_round.get("startedAt"):
                    return False
            elif (old_status, new_status) not in (("waiting", "on air"), ("on air", "done"), ("on air", "remind"), ("remind", "done")):
                return False
            if start != new_start and (old_status != "waiting" or new_status != "waiting"):
                return False
        for number, (oi, start, new_round) in new_rounds.items():
            previous = old_rounds.get(number)
            if previous and previous[1] == start:
                continue
            if previous is None and new_round.get("status") != "waiting":
                return False
            room = updated[oi].get("room")
            start_minute = sum(int(v) * m for v, m in zip(start.split(":"), (60, 1)))
            for other_oi, other_option in enumerate(updated):
                if other_option.get("room") != room:
                    continue
                for other_start, other_round in other_option.get("slots", {}).items():
                    if other_round is new_round:
                        continue
                    other_minute = sum(int(v) * m for v, m in zip(other_start.split(":"), (60, 1)))
                    if abs(start_minute - other_minute) < 10:
                        return False
        return True

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/api/state":
            with LOCK:
                data = read_data()
                return self.reply(200, {key: data.get(key) for key in ("options", "nextGuest", "checkedIn", "activeGuest")})
        if path.path == "/api/manager/status":
            data = read_data()
            return self.reply(200, {"configured": bool(data.get("manager")), "authenticated": bool(self.manager_session())})
        if path.path == "/api/manager/storage":
            if not self.require_manager():
                return
            with LOCK:
                data = read_data()
                customers = list(data.get("customers", []))
                known = {item.get("token") for item in customers}
                for token, guest in data.get("guestLinks", {}).items():
                    if token in known:
                        continue
                    profile = guest.get("profile", {})
                    customers.append({
                        "token": token,
                        "number": guest.get("number"),
                        "name": profile.get("name") or guest.get("name") or "Khách",
                        "firstVisit": profile.get("firstVisit"),
                        "source": profile.get("source"),
                        "social": profile.get("social"),
                        "rating": guest.get("rating"),
                        "feedback": guest.get("feedback", ""),
                    })
                return self.reply(200, {"customers": customers, "attendance": data.get("attendance", []), "checkedIn": data.get("checkedIn", [])})
        if path.path.startswith("/api/guest/"):
            token = path.path.rsplit("/", 1)[-1]
            with LOCK:
                data = read_data()
                guest = data.get("guestLinks", {}).get(token)
                if guest and isinstance(data.get("options"), list):
                    for option in data["options"]:
                        for start, round_data in option.get("slots", {}).items():
                            if round_data.get("number") == guest.get("number"):
                                guest = {**guest, "room": option["room"], "label": option["label"], "time": start}
                                break
            response = {"guest": guest, "options": data.get("options")} if guest else None
            return self.reply(200, response) if response else self.reply(404, {"error": "Không tìm thấy lượt khách này"})
        if path.path in ("/", "/guest"):
            try:
                return self.reply(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            except OSError:
                return self.reply(500, "Không tìm thấy index.html", "text/plain; charset=utf-8")
        if path.path == "/health":
            return self.reply(200, {"ok": True})
        return self.reply(404, {"error": "Không tìm thấy trang"})

    def do_POST(self):
        try:
            incoming = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return self.reply(400, {"error": "Dữ liệu không hợp lệ"})
        if self.path == "/api/state":
            if not isinstance(incoming, dict) or not isinstance(incoming.get("options"), list):
                return self.reply(400, {"error": "Trạng thái không hợp lệ"})
            with LOCK:
                data = read_data()
                if not self.manager_session() and not self.validate_staff_state(data, incoming):
                    return self.reply(403, {"error": "Nhân viên không thể ghi đè lịch hoặc thay đổi cấu hình phòng"})
                data.update({key: incoming[key] for key in ("options", "nextGuest", "checkedIn", "activeGuest") if key in incoming})
                write_data(data)
            return self.reply(200, {"ok": True})
        if self.path == "/api/attendance":
            name, action = incoming.get("name"), incoming.get("action")
            if not isinstance(name, str) or not name.strip() or action not in ("in", "out"):
                return self.reply(400, {"error": "Thông tin chấm công không hợp lệ"})
            with LOCK:
                data = read_data()
                data.setdefault("attendance", []).append({"name": name.strip(), "action": action, "at": incoming.get("at", "")})
                data["attendance"] = data["attendance"][-1000:]
                write_data(data)
            return self.reply(200, {"ok": True})
        if self.path in ("/api/manager/setup", "/api/manager/login"):
            password = incoming.get("password", "")
            if not isinstance(password, str) or len(password) < 8:
                return self.reply(400, {"error": "Mật khẩu cần có ít nhất 8 ký tự"})
            with LOCK:
                data = read_data()
                manager = data.get("manager")
                if self.path.endswith("/setup"):
                    if manager:
                        return self.reply(409, {"error": "Quản lý đã được thiết lập"})
                    salt = secrets.token_bytes(16)
                    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000)
                    data["manager"] = {"salt": salt.hex(), "passwordHash": password_hash.hex()}
                    write_data(data)
                else:
                    if not manager:
                        return self.reply(409, {"error": "Hãy tạo mật khẩu quản lý trước"})
                    salt = bytes.fromhex(manager["salt"])
                    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240_000).hex()
                    if not secrets.compare_digest(check, manager["passwordHash"]):
                        return self.reply(401, {"error": "Mật khẩu không đúng"})
            token = secrets.token_urlsafe(32)
            MANAGER_SESSIONS[token] = {"role": "manager", "expires": time.time() + SESSION_TTL}
            return self.reply(200, {"ok": True, "role": "manager"}, extra_headers={"Set-Cookie": f"favori_manager={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_TTL}"})
        if self.path == "/api/manager/logout":
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie.get("favori_manager")
            if token:
                MANAGER_SESSIONS.pop(token.value, None)
            return self.reply(200, {"ok": True}, extra_headers={"Set-Cookie": "favori_manager=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})
        if self.path == "/api/manager/rooms":
            if not self.require_manager():
                return
            incoming_options = incoming.get("options")
            if not isinstance(incoming_options, list) or not incoming_options or len(incoming_options) > 16:
                return self.reply(400, {"error": "Cấu hình phòng không hợp lệ"})
            with LOCK:
                data = read_data()
                old_options = data.get("options") or []
                result = []
                kept_sources = set()
                for item in incoming_options:
                    room, label, source_index = item.get("room"), item.get("label"), item.get("sourceIndex")
                    if not isinstance(room, str) or not room.strip() or not isinstance(label, str) or not label.strip():
                        return self.reply(400, {"error": "Tên phòng và lựa chọn không được để trống"})
                    previous = old_options[source_index] if isinstance(source_index, int) and 0 <= source_index < len(old_options) else None
                    if previous is not None:
                        kept_sources.add(source_index)
                    result.append({"room": room.strip(), "label": label.strip(), "slots": previous.get("slots", {}) if previous else {}})
                for source_index, previous in enumerate(old_options):
                    if source_index not in kept_sources and previous.get("slots"):
                        return self.reply(409, {"error": "Không thể xóa lựa chọn đang có lượt đã đặt"})
                config = [(o["room"].casefold(), o["label"].casefold()) for o in result]
                if len(set(config)) != len(config):
                    return self.reply(400, {"error": "Tên lựa chọn bị trùng"})
                data["options"] = result
                write_data(data)
            return self.reply(200, {"ok": True, "options": result})
        if self.path == "/api/guest-link":
            required = ("number", "room", "label", "time")
            if any(key not in incoming for key in required):
                return self.reply(400, {"error": "Thiếu thông tin lượt khách"})
            token = secrets.token_urlsafe(18)
            guest = {key: incoming.get(key) for key in ("number", "name", "room", "label", "time")}
            with LOCK:
                data = read_data()
                data.setdefault("guestLinks", {})[token] = guest
                write_data(data)
            host = self.headers.get("Host", "localhost:8000")
            return self.reply(201, {"token": token, "url": f"http://{host}/guest?token={token}"})
        if self.path.startswith("/api/guest/"):
            token, _, action = self.path[len("/api/guest/"):].partition("/")
            with LOCK:
                data = read_data()
                guest = data.get("guestLinks", {}).get(token)
                if not guest:
                    return self.reply(404, {"error": "Không tìm thấy lượt khách này"})
                if action == "rating":
                    rating = incoming.get("rating")
                    if rating not in ("sad", "neutral", "happy"):
                        return self.reply(400, {"error": "Đánh giá không hợp lệ"})
                    guest["rating"] = rating
                elif action == "profile":
                    profile = incoming.get("profile")
                    if not isinstance(profile, dict):
                        return self.reply(400, {"error": "Thông tin khách không hợp lệ"})
                    guest["profile"] = {key: profile.get(key) for key in ("name", "firstVisit", "source", "social")}
                elif action == "feedback":
                    feedback = incoming.get("feedback")
                    if not isinstance(feedback, str) or len(feedback) > 1000:
                        return self.reply(400, {"error": "Góp ý không hợp lệ"})
                    guest["feedback"] = feedback.strip()
                    guest["feedbackSubmitted"] = True
                else:
                    return self.reply(404, {"error": "Không tìm thấy API này"})
                customers = data.setdefault("customers", [])
                customer = next((item for item in customers if item.get("token") == token), None)
                if customer is None:
                    customer = {"token": token, "number": guest.get("number")}
                    customers.append(customer)
                customer.update({
                    "name": guest.get("profile", {}).get("name") or guest.get("name") or "Khách",
                    "firstVisit": guest.get("profile", {}).get("firstVisit"),
                    "source": guest.get("profile", {}).get("source"),
                    "social": guest.get("profile", {}).get("social"),
                    "rating": guest.get("rating"),
                    "feedback": guest.get("feedback", ""),
                    "feedbackSubmitted": guest.get("feedbackSubmitted", False),
                })
                write_data(data)
            return self.reply(200, {"ok": True})
        return self.reply(404, {"error": "Không tìm thấy API này"})


def lan_addresses():
    found = set()
    for target in ("8.8.8.8", "1.1.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80))
            address = sock.getsockname()[0]
            if not address.startswith("127."):
                found.add(address)
        except OSError:
            pass
        finally:
            sock.close()
    return sorted(found)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("Favori Foto is running:")
    print(f"  This computer: http://localhost:{port}")
    for address in lan_addresses():
        print(f"  Same Wi-Fi devices: http://{address}:{port}")
    print("Keep this window open while trying the app. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
