import socket
import threading
import os
import json
import struct
import time

# Fichier de configuration et paramètres du multicast
CONFIG_FILE = "config.json"
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 9999
HOSTS_FILE = os.path.join(os.path.dirname(__file__), "p2p_hosts.txt")

# Charger la configuration (dossier partagé, port, etc.)
def load_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

config = load_config()
shared_dir = config["shared_dir"]

# Résoudre un nom d'hôte en IP (pour supporter les noms d'ordinateur)
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

# Lister les fichiers du dossier partagé local
def list_files():
    try:
        return os.listdir(shared_dir)
    except FileNotFoundError:
        return []

# Obtenir l'adresse IP locale de la machine
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

# Serveur pair-à-pair : répond aux requêtes des autres pairs
class PeerServer(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.host = config["host"]
        self.port = config["port"]

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((self.host, self.port))
        s.listen(5)
        print(f"[+] Serveur P2P en écoute sur {self.host}:{self.port}")
        while True:
            conn, addr = s.accept()
            threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()

    # Gérer une connexion entrante (liste ou téléchargement de fichier)
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

# Thread pour répondre aux requêtes de découverte multicast
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

# Découverte des pairs sur le réseau local via multicast
def send_discovery(timeout=2):
    discovered = []
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
            if hostname not in discovered:
                discovered.append(hostname)
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
    return discovered

# Récupérer la liste des fichiers d'un pair distant
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
        print(f"[!] Erreur récupération liste distante: {e}")
        return []

# Télécharger un fichier depuis un pair distant
def download_file(host, port, filename):
    s = None
    try:
        ip = resolve_host(host)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((ip, int(port)))
        s.sendall(f"GET_FILE {filename}".encode())
        response = b""
        # Lire la réponse d'en-tête ("OK\n" ou "ERROR...")
        while not response.endswith(b"\n"):
            chunk = s.recv(1)
            if not chunk:
                break
            response += chunk
        if response.startswith(b"OK"):
            local_path = os.path.join(shared_dir, filename)
            print(f"Téléchargement de '{filename}' en cours...")
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
                    # Affichage de la progression et vitesse
                    now = time.time()
                    if now - last_time > 0.5:  # Mise à jour toutes les 0.5s
                        speed = (total_bytes - last_bytes) / (now - last_time) / 1024  # Ko/s
                        print(f"\rReçu: {total_bytes/1024:.1f} Ko | Vitesse: {speed:.1f} Ko/s", end="")
                        last_time = now
                        last_bytes = total_bytes
                # Affichage final
                elapsed = time.time() - start_time
                avg_speed = (total_bytes / 1024) / elapsed if elapsed > 0 else 0
                print(f"\rReçu: {total_bytes/1024:.1f} Ko | Vitesse moyenne: {avg_speed:.1f} Ko/s")
            print(f"[OK] Fichier '{filename}' téléchargé avec succès.")
        else:
            print("[ERREUR]", response.decode(errors="ignore").strip())
    except Exception as e:
        print(f"[ERREUR] Téléchargement: {e}")
    finally:
        if s:
            s.close()

# Menu principal CLI
def main_cli():
    os.makedirs(shared_dir, exist_ok=True)
    PeerServer().start()
    MulticastResponder().start()

    print("=== P2P File Share CLI ===")
    while True:
        print("\n1. Découvrir les pairs")
        print("2. Lister mes fichiers partagés")
        print("3. Lister les fichiers d'un pair")
        print("4. Télécharger un fichier depuis un pair")
        print("5. Quitter")
        choice = input("Choix: ").strip()
        if choice == "1":
            # Découverte des pairs sur le réseau
            peers = send_discovery()
            if not peers:
                print("Aucun pair trouvé.")
            else:
                print("Pairs trouvés:")
                for i, host in enumerate(peers):
                    print(f"  {i+1}. {host}")
        elif choice == "2":
            # Afficher les fichiers locaux partagés
            files = list_files()
            print("Fichiers partagés localement:")
            for f in files:
                print("  -", f)
        elif choice == "3":
            # Lister les fichiers d'un pair distant
            peers = send_discovery()
            if not peers:
                print("Aucun pair trouvé.")
                continue
            for i, host in enumerate(peers):
                print(f"  {i+1}. {host}")
            idx = input("Numéro du pair: ").strip()
            try:
                idx = int(idx) - 1
                host = peers[idx]
            except:
                print("Sélection invalide.")
                continue
            files = get_remote_files(host, config["port"])
            print(f"Fichiers partagés par {host}:")
            for f in files:
                print("  -", f)
        elif choice == "4":
            # Télécharger un fichier depuis un pair distant
            peers = send_discovery()
            if not peers:
                print("Aucun pair trouvé.")
                continue
            for i, host in enumerate(peers):
                print(f"  {i+1}. {host}")
            idx = input("Numéro du pair: ").strip()
            try:
                idx = int(idx) - 1
                host = peers[idx]
            except:
                print("Sélection invalide.")
                continue
            files = get_remote_files(host, config["port"])
            if not files:
                print("Aucun fichier à télécharger.")
                continue
            for i, f in enumerate(files):
                print(f"  {i+1}. {f}")
            fidx = input("Numéro du fichier à télécharger: ").strip()
            try:
                fidx = int(fidx) - 1
                filename = files[fidx]
            except:
                print("Sélection invalide.")
                continue
            download_file(host, config["port"], filename)
        elif choice == "5":
            # Quitter le programme
            print("Bye!")
            break
        else:
            print("Choix invalide.")

if __name__ == "__main__":
    main_cli()