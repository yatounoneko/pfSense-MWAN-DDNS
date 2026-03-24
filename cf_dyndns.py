#!/usr/local/bin/python3.11

import subprocess
import re
import urllib.request
import urllib.error
import json
import os
from datetime import datetime
import argparse
import xml.etree.ElementTree as ET
import glob

# === Platform Abstraction ===
# To port this script to another platform (like OPNsense or OpenWrt),
# you would create a new class that inherits from BasePlatform and
# implement all of its methods with OS-specific logic.

class BasePlatform:
    """Abstract base class defining the interface for platform-specific functions."""
    def get_public_ipv4_addresses(self, physical_interfaces):
        raise NotImplementedError
    def get_public_ipv6_addresses(self, physical_interfaces):
        raise NotImplementedError
    def get_gateway_monitoring_thresholds(self):
        raise NotImplementedError
    def get_gateway_statuses(self, thresholds):
        raise NotImplementedError
    def get_gateway_interface_map(self):
        raise NotImplementedError
    def get_physical_to_logical_interface_map(self):
        raise NotImplementedError
    def get_ip_to_physical_interface_map(self):
        raise NotImplementedError
    def get_dyndns_ids(self):
        raise NotImplementedError
    def update_cache_files(self, healthy_ipv4, unhealthy_ipv4, healthy_ipv6, unhealthy_ipv6, mappings):
        raise NotImplementedError

class PfSensePlatform(BasePlatform):
    """Implementation of platform-specific functions for pfSense."""

    def get_public_ipv4_addresses(self, physical_interfaces):
        result = subprocess.run(["/sbin/ifconfig"], stdout=subprocess.PIPE, text=True)
        output = result.stdout
        public_ips = []
        iface = None
        for line in output.splitlines():
            if line and not line.startswith("\t") and ":" in line:
                iface = line.split(":")[0]
            elif "inet " in line and iface:
                if physical_interfaces and iface not in physical_interfaces:
                    continue
                match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                if match:
                    ip = match.group(1)
                    octets = ip.split(".")
                    if ip.startswith("127.") or ip.startswith("169.254."): continue
                    if ip.startswith("10.") or ip.startswith("192.168."): continue
                    if int(octets[0]) == 172 and 16 <= int(octets[1]) <= 31: continue
                    public_ips.append(ip)
        return public_ips

    def get_public_ipv6_addresses(self, physical_interfaces):
        result = subprocess.run(["/sbin/ifconfig"], stdout=subprocess.PIPE, text=True)
        output = result.stdout
        public_ips = []
        iface = None
        for line in output.splitlines():
            if line and not line.startswith("\t") and ":" in line:
                iface = line.split(":")[0]
            elif "inet6 " in line and iface:
                if physical_interfaces and iface not in physical_interfaces:
                    continue
                match = re.search(r"inet6 ([a-f0-9:]+)", line)
                if match:
                    ip = match.group(1).split('%')[0]
                    if ip.startswith(("fe80", "fc", "fd")) or ip in ("::1", "::", "::10"): continue
                    public_ips.append(ip)
        return public_ips

    def get_gateway_monitoring_thresholds(self):
        thresholds = {}
        try:
            tree = ET.parse('/conf/config.xml')
            root = tree.getroot()
            gateways_config = root.find(".//gateways")
            defaults = {
                'latencyhigh': gateways_config.findtext('latencyhigh', '500'),
                'losshigh': gateways_config.findtext('losshigh', '20')
            }
            for gw_item in root.findall(".//gateways/gateway_item"):
                gw_name = gw_item.findtext("name")
                if gw_name:
                    thresholds[gw_name] = {
                        'latencyhigh': int(gw_item.findtext('latencyhigh', defaults['latencyhigh'])),
                        'losshigh': int(gw_item.findtext('losshigh', defaults['losshigh']))
                    }
        except Exception as e:
            print(f"❌ Could not parse gateway monitoring thresholds: {e}")
        return thresholds

    def get_gateway_statuses(self, thresholds):
        statuses = {}
        try:
            dpinger_sockets = glob.glob('/var/run/dpinger_*.sock')
            for socket_path in dpinger_sockets:
                basename = os.path.basename(socket_path)
                gateway_name = ""
                try:
                    name_part = basename.replace('dpinger_', '', 1)
                    gateway_name = name_part.split('~', 1)[0]
                except IndexError: continue
                status = 'down'
                try:
                    result = subprocess.run(['cat', socket_path], capture_output=True, text=True, timeout=2)
                    socket_output = result.stdout.strip()
                    parts = socket_output.split()
                    if len(parts) >= 4:
                        live_latency_us = int(parts[1])
                        live_loss_pct = int(parts[3])
                        gw_thresholds = thresholds.get(gateway_name, {})
                        latency_high_ms = gw_thresholds.get('latencyhigh', 500)
                        loss_high_pct = gw_thresholds.get('losshigh', 20)
                        if (live_latency_us / 1000) < latency_high_ms and live_loss_pct < loss_high_pct:
                            status = 'online'
                except Exception as e:
                    print(f"⚠️  Could not parse status for {gateway_name}, assuming down. Error: {e}")
                statuses[gateway_name] = status
        except Exception as e:
            print(f"❌ Could not retrieve gateway statuses from dpinger sockets: {e}")
        return statuses

    def get_gateway_interface_map(self):
        mapping = {}
        try:
            tree = ET.parse('/conf/config.xml')
            root = tree.getroot()
            for gw_item in root.findall(".//gateways/gateway_item"):
                gw_name = gw_item.findtext("name")
                interface = gw_item.findtext("interface")
                if gw_name and interface: mapping[gw_name] = interface
        except Exception as e:
            print(f"❌ Could not parse gateway to interface map: {e}")
        return mapping

    def get_physical_to_logical_interface_map(self):
        mapping = {}
        try:
            tree = ET.parse('/conf/config.xml')
            root = tree.getroot()
            for iface in root.findall(".//interfaces/*"):
                if iface.tag in ["lan", "wan"] or iface.tag.startswith("opt"):
                    pf_iface_name = iface.tag
                    physical_iface_name = iface.findtext("if")
                    if physical_iface_name: mapping[physical_iface_name] = pf_iface_name
        except Exception as e:
            print(f"❌ Could not parse physical to logical interface map: {e}")
        return mapping

    def get_ip_to_physical_interface_map(self):
        result = subprocess.run(["/sbin/ifconfig"], stdout=subprocess.PIPE, text=True)
        output = result.stdout
        ip_to_iface = {}
        iface = None
        for line in output.splitlines():
            if line and not line.startswith("\t") and ":" in line:
                iface = line.split(":")[0]
            elif iface:
                match4 = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", line)
                if match4: ip_to_iface[match4.group(1)] = iface
                match6 = re.search(r"inet6 ([a-f0-9:]+)", line)
                if match6: ip_to_iface[match6.group(1).split("%")[0]] = iface
        return ip_to_iface

    def get_dyndns_ids(self):
        mapping = {}
        try:
            tree = ET.parse('/conf/config.xml')
            root = tree.getroot()
            for dyndns_entry in root.findall(".//dyndnses/dyndns"):
                if dyndns_entry.findtext("type") == "custom":
                    interface = dyndns_entry.findtext("interface")
                    entry_id = dyndns_entry.findtext("id")
                    if interface and entry_id: mapping[interface] = entry_id
        except Exception as e:
            print(f"❌ Could not parse DynDNS IDs: {e}")
        return mapping

    def update_cache_files(self, healthy_ipv4, unhealthy_ipv4, healthy_ipv6, unhealthy_ipv6, mappings):
        print("Updating pfSense cache files to reflect gateway health...")
        ip_to_phys_if_map = mappings['ip_to_phys']
        phys_to_pf_if_map = mappings['phys_to_pf']
        dyndns_id_map = mappings['dyndns_ids']

        all_ips_to_process = { 'healthy': healthy_ipv4 + healthy_ipv6, 'unhealthy': list(unhealthy_ipv4) + list(unhealthy_ipv6) }
        for status, ip_list in all_ips_to_process.items():
            for ip in ip_list:
                physical_iface = ip_to_phys_if_map.get(ip)
                if not physical_iface: continue
                pf_iface = phys_to_pf_if_map.get(physical_iface)
                if not pf_iface: continue
                dyndns_id = dyndns_id_map.get(pf_iface)
                if dyndns_id is None: continue

                cache_path = f"/conf/dyndns_{pf_iface}custom''{dyndns_id}.cache"
                content_to_write = ip if status == 'healthy' else ip + "\n"
                try:
                    with open(cache_path, "w") as f: f.write(content_to_write)
                    print(f"    Wrote {cache_path} for IP {ip} with status '{status}'")
                except Exception as e:
                    print(f"    ❌ Error writing {cache_path}: {e}")

# === Cloudflare DynDNS Updater ===

class CloudflareDynDNSUpdater:
    def __init__(self, platform, config, args):
        self.platform = platform
        self.config = config
        self.args = args
        self._cf_base = "https://api.cloudflare.com/client/v4"
        self._headers = {
            "Authorization": f"Bearer {self.config['api_token']}",
            "Content-Type": "application/json",
        }

    def _cf_request(self, method, path, data=None):
        """Make a Cloudflare API request and return the parsed JSON response."""
        url = f"{self._cf_base}{path}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(url, data=body, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            print(f"❌ Cloudflare API HTTP {e.code} on {method} {path}: {error_body}")
            return None
        except Exception as e:
            print(f"❌ Cloudflare API exception on {method} {path}: {e}")
            return None

    def _list_records(self, record_type):
        """Return a dict mapping IP content -> record_id for the configured record name."""
        name = self.config['record_name']
        zone_id = self.config['zone_id']
        result = self._cf_request("GET", f"/zones/{zone_id}/dns_records?type={record_type}&name={name}&per_page=100")
        if result is None or not result.get("success"):
            print(f"❌ Failed to list {record_type} records for {name}")
            return {}
        return {r["content"]: r["id"] for r in result.get("result", [])}

    def _create_record(self, record_type, ip):
        """Create a new DNS record."""
        zone_id = self.config['zone_id']
        proxied = self.config.get('proxied', False)
        ttl = 1 if proxied else self.config.get('ttl', 60)
        payload = {
            "type": record_type,
            "name": self.config['record_name'],
            "content": ip,
            "ttl": ttl,
            "proxied": proxied,
        }
        result = self._cf_request("POST", f"/zones/{zone_id}/dns_records", payload)
        if result and result.get("success"):
            print(f"    ✅ Created {record_type} record: {ip}")
            return True
        print(f"    ❌ Failed to create {record_type} record: {ip}")
        return False

    def _update_record(self, record_type, record_id, ip):
        """Update an existing DNS record (e.g. to sync proxied/TTL settings)."""
        zone_id = self.config['zone_id']
        proxied = self.config.get('proxied', False)
        ttl = 1 if proxied else self.config.get('ttl', 60)
        payload = {
            "type": record_type,
            "name": self.config['record_name'],
            "content": ip,
            "ttl": ttl,
            "proxied": proxied,
        }
        result = self._cf_request("PUT", f"/zones/{zone_id}/dns_records/{record_id}", payload)
        if result and result.get("success"):
            print(f"    ✅ Updated {record_type} record: {ip}")
            return True
        print(f"    ❌ Failed to update {record_type} record: {ip}")
        return False

    def _delete_record(self, record_type, record_id, ip):
        """Delete a stale DNS record."""
        zone_id = self.config['zone_id']
        result = self._cf_request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
        if result and result.get("success"):
            print(f"    ✅ Deleted stale {record_type} record: {ip}")
            return True
        print(f"    ❌ Failed to delete {record_type} record: {ip}")
        return False

    def update_dns(self, healthy_ipv4, healthy_ipv6):
        """Reconcile Cloudflare DNS records with the current set of healthy IPs."""
        success = True
        for record_type, healthy_ips in [("A", healthy_ipv4), ("AAAA", healthy_ipv6)]:
            existing = self._list_records(record_type)
            existing_ips = set(existing.keys())
            desired_ips = set(healthy_ips)

            # Delete records no longer desired
            for ip in existing_ips - desired_ips:
                if not self._delete_record(record_type, existing[ip], ip):
                    success = False

            # Create new records for IPs not yet present
            for ip in desired_ips - existing_ips:
                if not self._create_record(record_type, ip):
                    success = False

            # Update records that already exist (syncs proxied/TTL settings)
            for ip in desired_ips & existing_ips:
                if not self._update_record(record_type, existing[ip], ip):
                    success = False

        return success

    def send_push_notification(self, subject, message):
        safe_message = message.replace('"', '\\"').replace("`", "'")
        php_code = f"""require_once("/etc/inc/notices.inc"); file_notice("dynupdate", "{safe_message}", "DynDNS", "", 1, false);"""
        try:
            subprocess.run(["/usr/local/bin/php", "-r", php_code], check=True)
        except Exception as e:
            print(f"Push notification could not be sent: {e}")

    def load_previous_state(self):
        if os.path.exists(self.config['state_file']):
            with open(self.config['state_file'], "r") as f: return json.load(f)
        return {}

    def save_state(self, ipv4, ipv6):
        timestamp = datetime.utcnow().isoformat()
        state = { "ipv4": {ip: timestamp for ip in ipv4}, "ipv6": {ip: timestamp for ip in ipv6} }
        with open(self.config['state_file'], "w") as f: json.dump(state, f)

    def run(self):
        print(f"--- Cloudflare DynDNS script started at {datetime.now().isoformat()} (Reason: {self.args.reason}) ---")

        # 1. Get all system mappings and configs from the platform
        thresholds = self.platform.get_gateway_monitoring_thresholds()
        gateway_statuses = self.platform.get_gateway_statuses(thresholds)
        gateway_to_if_map = self.platform.get_gateway_interface_map()
        phys_to_pf_if_map = self.platform.get_physical_to_logical_interface_map()
        ip_to_phys_if_map = self.platform.get_ip_to_physical_interface_map()
        if_to_gateway_map = {v: k for k, v in gateway_to_if_map.items()}
        dyndns_id_map = self.platform.get_dyndns_ids()

        print(f"Gateway Thresholds: {thresholds}")
        print(f"Gateway Statuses: {gateway_statuses}")

        # 2. Get all public IPs from all interfaces
        all_ipv4 = self.platform.get_public_ipv4_addresses(self.config['allowed_physical_interfaces'])
        all_ipv6 = self.platform.get_public_ipv6_addresses(self.config['allowed_physical_interfaces'])

        # 3. Filter IPs based on intelligent gateway status
        healthy_ipv4, healthy_ipv6 = [], []
        for ip in all_ipv4:
            phys_if = ip_to_phys_if_map.get(ip)
            pf_if = phys_to_pf_if_map.get(phys_if)
            gw_name = if_to_gateway_map.get(pf_if)
            if gateway_statuses.get(gw_name) == 'online': healthy_ipv4.append(ip)
        for ip in all_ipv6:
            phys_if = ip_to_phys_if_map.get(ip)
            pf_if = phys_to_pf_if_map.get(phys_if)
            gw_name = if_to_gateway_map.get(pf_if)
            if gateway_statuses.get(gw_name) == 'online': healthy_ipv6.append(ip)

        unhealthy_ipv4 = set(all_ipv4) - set(healthy_ipv4)
        unhealthy_ipv6 = set(all_ipv6) - set(healthy_ipv6)

        if self.args.ipv4only: healthy_ipv6, unhealthy_ipv6 = [], set()
        if self.args.ipv6only: healthy_ipv4, unhealthy_ipv4 = [], set()

        print(f"Healthy IPs selected for update: IPv4={healthy_ipv4}, IPv6={healthy_ipv6}")
        if unhealthy_ipv4 or unhealthy_ipv6:
            print(f"Unhealthy IPs to be marked in cache: IPv4={list(unhealthy_ipv4)}, IPv6={list(unhealthy_ipv6)}")

        # 4. Check if an update is needed and execute
        previous_state = self.load_previous_state()
        ipv4_changed = set(previous_state.get("ipv4", {}).keys()) != set(healthy_ipv4)
        ipv6_changed = set(previous_state.get("ipv6", {}).keys()) != set(healthy_ipv6)

        if self.args.force_update or ipv4_changed or ipv6_changed:
            if not self.args.force_update: print("Change detected, performing DNS update...")
            else: print(f"Forcing DNS update (Reason: {self.args.reason})...")

            if self.args.dry_run:
                print("[DRY RUN] Would update Cloudflare DNS records.")
                print(f"[DRY RUN]   A records  -> {healthy_ipv4}")
                print(f"[DRY RUN]   AAAA records -> {healthy_ipv6}")
            elif self.update_dns(healthy_ipv4, healthy_ipv6):
                self.save_state(healthy_ipv4, healthy_ipv6)
                mappings = {'ip_to_phys': ip_to_phys_if_map, 'phys_to_pf': phys_to_pf_if_map, 'dyndns_ids': dyndns_id_map}
                self.platform.update_cache_files(healthy_ipv4, unhealthy_ipv4, healthy_ipv6, unhealthy_ipv6, mappings)
                print("✅ Cloudflare DNS update and cache files successful.")
                proxied_label = " (proxied)" if self.config.get('proxied') else ""
                msg = f"Cloudflare DynDNS for {self.config['record_name']}{proxied_label} updated.\nHealthy IPs:\nIPv4: {healthy_ipv4}\nIPv6: {healthy_ipv6}"
                self.send_push_notification("Cloudflare DynDNS Gateway Update", msg)
            else:
                print("❌ Cloudflare DNS update failed.")
        else:
            print("No changes detected. Nothing to do.")

        print("--- Cloudflare DynDNS script finished ---")


if __name__ == "__main__":
    # === Configuration ===
    config = {
        'api_token': 'your_cloudflare_api_token_here',
        'zone_id': 'your_cloudflare_zone_id_here',
        'record_name': 'home.example.org',
        'proxied': False,        # Set True to enable Cloudflare proxy (forces TTL=1)
        'ttl': 60,               # Ignored when proxied=True (Cloudflare uses TTL=1 for proxied)
        'state_file': '/var/db/cf-dyndns.state.json',
        'allowed_physical_interfaces': ['em0', 'ixl2'],
    }

    # === Argument Parsing ===
    parser = argparse.ArgumentParser(description="Cloudflare Dynamic DNS updater for pfSense Multi-WAN")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done, but do not update")
    parser.add_argument("--ipv4only", action="store_true", help="Only use IPv4")
    parser.add_argument("--ipv6only", action="store_true", help="Only use IPv6")
    parser.add_argument("--force-update", action="store_true", help="Always run DNS update, even without detected IP change")
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--reason", type=str, default="Scheduled", help="Reason for the run (e.g., Gateway-Event)")
    args = parser.parse_args()

    # === Execution ===
    platform = PfSensePlatform()
    updater = CloudflareDynDNSUpdater(platform, config, args)
    updater.run()
