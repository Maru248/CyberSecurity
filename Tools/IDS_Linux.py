import socket
import struct
import time
import argparse
import sys
import threading
import signal
import logging
from collections import defaultdict
from colorama import init, Fore, Style

# Initialize colorama
init()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='ids.log'
)

class SimpleIDS:
    def __init__(self, threshold=50, ban_time=300, interface=None, verbose=False):
        self.threshold = threshold
        self.ban_time = ban_time
        self.interface = interface
        self.verbose = verbose
        self.request_counts = defaultdict(int)
        self.banned_ips = {}
        self.last_reset = time.time()
        self.packet_count = 0
        self.lock = threading.Lock()
        self.running = True

        # Register graceful shutdown
        signal.signal(signal.SIGINT, self.stop)

    def stop(self, signum=None, frame=None):
        print(f"\n{Fore.YELLOW}[INFO] Gracefully shutting down...{Style.RESET_ALL}")
        self.running = False

    def setup_socket(self):
        try:
            conn = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
            if self.interface:
                conn.bind((self.interface, 0))
            return conn
        except PermissionError:
            print(f"{Fore.RED}[ERROR] Permission denied. Run with sudo/admin privileges.{Style.RESET_ALL}")
            sys.exit(1)
        except Exception as e:
            print(f"{Fore.RED}[ERROR] Failed to create socket: {e}{Style.RESET_ALL}")
            sys.exit(1)

    def ethernet_frame(self, data):
        dest_mac, src_mac, eth_proto = struct.unpack('! 6s 6s H', data[:14])
        return self.get_mac_addr(dest_mac), self.get_mac_addr(src_mac), socket.htons(eth_proto), data[14:]

    def get_mac_addr(self, bytes_addr):
        return ':'.join(map('{:02x}'.format, bytes_addr)).upper()

    def ipv4_packet(self, data):
        version_header_length = data[0]
        header_length = (version_header_length & 15) * 4
        ttl, proto, src, target = struct.unpack('! 8x B B 2x 4s 4s', data[:20])
        return header_length, ttl, proto, self.ipv4(src), self.ipv4(target), data[header_length:]

    def ipv4(self, addr):
        return '.'.join(map(str, addr))

    def get_protocol_name(self, proto):
        return {6: 'TCP', 17: 'UDP', 1: 'ICMP'}.get(proto, f'Unknown ({proto})')

    def monitor_traffic(self):
        conn = self.setup_socket()
        print(f"{Fore.CYAN}Starting IDS monitoring...{Style.RESET_ALL}")
        print(f"Interface: {self.interface or 'All'}")
        print(f"Threshold: {self.threshold} requests/minute")
        print(f"Ban time: {self.ban_time//60} minutes")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        logging.info(f"IDS started - Threshold: {self.threshold}, Ban time: {self.ban_time}s")

        try:
            while self.running:
                raw_data, _ = conn.recvfrom(65536)
                with self.lock:
                    self.packet_count += 1

                src_mac, dest_mac, eth_proto, data = self.ethernet_frame(raw_data)

                if eth_proto == 8:  # IPv4
                    header_length, ttl, proto, src_ip, target_ip, _ = self.ipv4_packet(data)
                    current_time = time.time()

                    # Filter only TCP/UDP/ICMP
                    if proto not in (1, 6, 17):
                        continue

                    if self.verbose:
                        print(f"{Fore.LIGHTBLACK_EX}[DEBUG] Packet from {src_ip} via {self.get_protocol_name(proto)}{Style.RESET_ALL}")

                    # Reset per minute
                    if current_time - self.last_reset > 60:
                        with self.lock:
                            self.request_counts.clear()
                            self.last_reset = current_time
                            print(f"{Fore.YELLOW}[INFO] Reset request counts (Packets: {self.packet_count:,}){Style.RESET_ALL}")

                    # Handle ban/unban
                    if src_ip in self.banned_ips:
                        if current_time - self.banned_ips[src_ip] < self.ban_time:
                            continue
                        else:
                            with self.lock:
                                del self.banned_ips[src_ip]
                                print(f"{Fore.GREEN}[+] Unbanned {src_ip}{Style.RESET_ALL}")
                                logging.info(f"Unbanned IP: {src_ip}")

                    # Count and check threshold
                    with self.lock:
                        self.request_counts[src_ip] += 1

                    if self.request_counts[src_ip] > self.threshold:
                        with self.lock:
                            if src_ip not in self.banned_ips:
                                print(f"{Fore.RED}[!] Possible DoS from {src_ip} - {self.request_counts[src_ip]} reqs/min{Style.RESET_ALL}")
                                print(f"Protocol: {self.get_protocol_name(proto)}")
                                print(f"Source MAC: {src_mac}")
                                self.banned_ips[src_ip] = current_time
                                print(f"{Fore.YELLOW}[+] Banned {src_ip} for {self.ban_time//60} minutes{Style.RESET_ALL}")
                                logging.warning(f"DoS detected from {src_ip} - Banned for {self.ban_time}s")

        finally:
            conn.close()
            print(f"{Fore.YELLOW}Stopped IDS. Packets captured: {self.packet_count:,}{Style.RESET_ALL}")
            logging.info(f"IDS stopped - Total packets: {self.packet_count}")

def main():
    parser = argparse.ArgumentParser(description="Simple Intrusion Detection System")
    parser.add_argument("--threshold", type=int, default=50, help="Max requests per minute per IP (default: 50)")
    parser.add_argument("--ban-time", type=int, default=300, help="Ban duration in seconds (default: 300)")
    parser.add_argument("--interface", help="Network interface to monitor (e.g., eth0)")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose packet logging")
    args = parser.parse_args()

    try:
        ids = SimpleIDS(threshold=args.threshold, ban_time=args.ban_time, interface=args.interface, verbose=args.verbose)
        ids.monitor_traffic()
    except Exception as e:
        print(f"{Fore.RED}[ERROR] Unexpected error: {e}{Style.RESET_ALL}")
        sys.exit(1)

if __name__ == "__main__":
    main()
