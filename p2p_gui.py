import socket
import threading
import os
import json
import struct
import time
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QPushButton,
    QLabel, QProgressBar, QMessageBox, QTextEdit
)
from PySide6.QtCore import Qt, QThread, Signal

# --- CONFIGURATION ---
CONFIG_FILE = "config.json"
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 9999
HOSTS_FILE = os.path.join(os.path.dirname(__file__), "p2p_hosts.txt")

def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

config = load_config()
shared_dir = config["shared_dir"]

# --- P2P BACKEND ---
def list_files():
    try:
        return os.listdir(shared_dir)
    except FileNotFoundError:
        return []

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def resolve_host(host):
    # Résolution via p2p_hosts.txt
    if os.path.exists(HOSTS_FILE):
        with open(HOSTS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == host:
                    return parts[0]
    # Fallback DNS
    try:
        return socket.gethostbyname(host)
    except Exception:
        return host

class PeerServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.host = config["host"]
        self.port = config["port"]

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((self.host, self.port))
        s.listen(5)
        while True:
            conn, addr = s.accept()
            threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    def handle_client(self, conn, addr):
        try:
            request = conn.recv(1024).decode()
            if request.startswith("GET_FILE "):
                filename = request.split(" ", 1)[1].strip()
                filepath = os.path.join(shared_dir, filename)
                if os.path.exists(filepath):
                    conn.send(b"OK\n")
                    with open(filepath, "rb") as f:
                        while True:
                            chunk = f.read(4096)
                            if not chunk:
                                break
                            conn.sendall(chunk)
                else:
                    conn.send(b"ERROR: File not found\n")
            elif request.startswith("LIST_FILES"):
                files = list_files()
                response = "\n".join(files) + "\n"
                conn.sendall(response.encode())
            else:
                conn.send(b"ERROR: Invalid command\n")
        except Exception as e:
            print(f"[!] Erreur avec {addr}: {e}")
        finally:
            conn.close()

class MulticastResponder(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.group = MULTICAST_GROUP
        self.port = MULTICAST_PORT

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.group, self.port))
        except OSError:
            sock.bind(('', self.port))
        group_bin = socket.inet_aton(self.group)
        mreq = group_bin + socket.inet_aton('0.0.0.0')
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        while True:
            data, addr = sock.recvfrom(1024)
            if data.decode() == "DISCOVER_P2P":
                hostname = socket.gethostname()
                ip = get_local_ip()
                sock.sendto(f"{hostname}|{ip}".encode(), addr)

def send_discovery(timeout=2):
    discovered = set()
    known_hosts = {}
    # Charger les hosts connus
    if os.path.exists(HOSTS_FILE):
        with open(HOSTS_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    known_hosts[parts[1]] = parts[0]
    # Découverte multicast
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    ttl = struct.pack('b', 1)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    sock.sendto(b"DISCOVER_P2P", (MULTICAST_GROUP, MULTICAST_PORT))
    new_hosts = {}
    try:
        while True:
            data, addr = sock.recvfrom(1024)
            try:
                hostname, ip = data.decode().split("|")
            except Exception:
                continue
            discovered.add(hostname)
            if hostname not in known_hosts or known_hosts[hostname] != ip:
                new_hosts[hostname] = ip
    except socket.timeout:
        pass
    sock.close()
    # Mise à jour du fichier hosts si besoin
    if new_hosts:
        known_hosts.update(new_hosts)
        with open(HOSTS_FILE, "w") as f:
            for h, ip in known_hosts.items():
                f.write(f"{ip} {h}\n")
    return sorted(discovered)

def get_remote_files(host, port):
    try:
        ip = resolve_host(host)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((ip, int(port)))
        s.sendall(b"LIST_FILES")
        response = b""
        while True:
            part = s.recv(4096)
            if not part:
                break
            response += part
        s.close()
        files = response.decode(errors="ignore").strip().split("\n")
        return files if files != [''] else []
    except Exception as e:
        return []

def download_file(host, port, filename, progress_callback=None):
    s = None
    try:
        ip = resolve_host(host)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((ip, int(port)))
        s.sendall(f"GET_FILE {filename}".encode())
        response = b""
        while not response.endswith(b"\n"):
            chunk = s.recv(1)
            if not chunk:
                break
            response += chunk
        if response.startswith(b"OK"):
            local_path = os.path.join(shared_dir, filename)
            with open(local_path, "wb") as f:
                leftover = response[len(b"OK\n"):]
                total_bytes = 0
                if leftover:
                    f.write(leftover)
                    total_bytes += len(leftover)
                start_time = time.time()
                last_time = start_time
                last_bytes = 0
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    f.write(data)
                    total_bytes += len(data)
                    if progress_callback:
                        now = time.time()
                        speed = (total_bytes - last_bytes) / (now - last_time) / 1024 if now - last_time > 0 else 0
                        progress_callback(total_bytes, speed)
                        last_time = now
                        last_bytes = total_bytes
            return True, f"Fichier '{filename}' téléchargé avec succès."
        else:
            return False, response.decode(errors="ignore").strip()
    except Exception as e:
        return False, f"Erreur téléchargement: {e}"
    finally:
        if s:
            s.close()

# --- THREAD POUR LE TELECHARGEMENT AVEC SIGNALS ---
class DownloadThread(QThread):
    progress = Signal(int, float)  # bytes_received, speed
    finished = Signal(bool, str)   # success, message

    def __init__(self, host, port, filename):
        super().__init__()
        self.host = host
        self.port = port
        self.filename = filename

    def run(self):
        def cb(bytes_received, speed):
            self.progress.emit(bytes_received, speed)
        success, msg = download_file(self.host, self.port, self.filename, cb)
        self.finished.emit(success, msg)

# --- INTERFACE PySide6 ---
class P2PGuiQt(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("P2P File Share")
        self.setFixedSize(750, 420)

        hbox = QHBoxLayout(self)

        # Colonne pairs
        vbox_peers = QVBoxLayout()
        vbox_peers.addWidget(QLabel("Pairs disponibles :"))
        self.peers_list = QListWidget()
        vbox_peers.addWidget(self.peers_list)
        self.refresh_peers_btn = QPushButton("Découvrir pairs")
        vbox_peers.addWidget(self.refresh_peers_btn)
        hbox.addLayout(vbox_peers)

        # Colonne fichiers distants
        vbox_remote = QVBoxLayout()
        vbox_remote.addWidget(QLabel("Fichiers du pair sélectionné :"))
        self.remote_files_list = QListWidget()
        vbox_remote.addWidget(self.remote_files_list)
        self.refresh_files_btn = QPushButton("Rafraîchir fichiers")
        vbox_remote.addWidget(self.refresh_files_btn)
        self.download_btn = QPushButton("Télécharger")
        vbox_remote.addWidget(self.download_btn)
        hbox.addLayout(vbox_remote)

        # Colonne fichiers locaux
        vbox_local = QVBoxLayout()
        vbox_local.addWidget(QLabel("Fichiers partagés localement :"))
        self.local_files_list = QListWidget()
        vbox_local.addWidget(self.local_files_list)
        self.refresh_local_btn = QPushButton("Rafraîchir local")
        vbox_local.addWidget(self.refresh_local_btn)
        hbox.addLayout(vbox_local)

        # Barre de progression et log
        vbox_bottom = QVBoxLayout()
        self.progress = QProgressBar()
        self.progress.setMaximum(100)
        vbox_bottom.addWidget(self.progress)
        self.progress_label = QLabel("")
        vbox_bottom.addWidget(self.progress_label)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        vbox_bottom.addWidget(self.log_text)
        hbox.addLayout(vbox_bottom)

        # Connexions
        self.refresh_peers_btn.clicked.connect(self.refresh_peers)
        self.peers_list.currentRowChanged.connect(self.on_peer_select)
        self.refresh_files_btn.clicked.connect(self.refresh_remote_files)
        self.download_btn.clicked.connect(self.download_selected_file)
        self.refresh_local_btn.clicked.connect(self.refresh_local_files)

        # Init
        self.refresh_local_files()
        self.refresh_peers()

    def log(self, msg):
        self.log_text.append(msg)

    def refresh_peers(self):
        self.peers_list.clear()
        peers = send_discovery()
        for host in peers:
            self.peers_list.addItem(host)
        self.log(f"{len(peers)} pair(s) trouvé(s).")
        if peers:
            self.peers_list.setCurrentRow(0)
            self.on_peer_select(0)

    def get_selected_peer(self):
        row = self.peers_list.currentRow()
        if row < 0:
            return None, None
        host = self.peers_list.item(row).text()
        return host, config["port"]

    def refresh_remote_files(self):
        self.remote_files_list.clear()
        host, port = self.get_selected_peer()
        if not host or not port:
            self.log("Aucun pair sélectionné.")
            return
        files = get_remote_files(host, port)
        for f in files:
            self.remote_files_list.addItem(f)
        self.log(f"{len(files)} fichier(s) distant(s) listé(s).")

    def refresh_local_files(self):
        self.local_files_list.clear()
        files = list_files()
        for f in files:
            self.local_files_list.addItem(f)
        self.log(f"{len(files)} fichier(s) local(aux) listé(s).")

    def on_peer_select(self, row):
        self.refresh_remote_files()

    def download_selected_file(self):
        host, port = self.get_selected_peer()
        if not host or not port:
            QMessageBox.warning(self, "Attention", "Sélectionnez un pair.")
            return
        row = self.remote_files_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Attention", "Sélectionnez un fichier distant.")
            return
        filename = self.remote_files_list.item(row).text()
        self.progress.setValue(0)
        self.progress_label.setText("Téléchargement en cours...")
        self.download_btn.setEnabled(False)
        self.thread = DownloadThread(host, port, filename)
        self.thread.progress.connect(self.on_progress)
        self.thread.finished.connect(self.on_download_finished)
        self.thread.start()

    def on_progress(self, bytes_received, speed):
        self.progress.setValue(min(100, int(bytes_received / 1024)))
        self.progress_label.setText(f"{bytes_received/1024:.1f} Ko reçus | {speed:.1f} Ko/s")

    def on_download_finished(self, success, msg):
        self.progress_label.setText("")
        self.progress.setValue(0)
        self.download_btn.setEnabled(True)
        self.log(msg)
        if success:
            self.refresh_local_files()

if __name__ == "__main__":
    os.makedirs(shared_dir, exist_ok=True)
    PeerServer().start()
    MulticastResponder().start()
    app = QApplication([])
    window = P2PGuiQt()
    window.show()
    app.exec()