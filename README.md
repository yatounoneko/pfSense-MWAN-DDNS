# pfSense-MWAN-DynDNS: High-Availability Multi-WAN Dynamic DNS for incoming round-robin

This project provides a robust, high-availability Dynamic DNS (DynDNS) solution for pfSense firewalls with multiple WAN connections (Multi-WAN). It intelligently manages a single DNS hostname with multiple IP addresses (A/AAAA Round-Robin records) and automatically removes IPs from the record when their corresponding gateway goes down.

This solution is designed to be fully integrated with pfSense's gateway monitoring and is completely upgrade-safe.

## Features

* **Multi-WAN Round-Robin**: Manages a single hostname that points to the public IPs of all active WAN interfaces.
* **High-Availability**: Automatically and instantly removes the IP of a failed gateway from the DNS record, directing traffic only to healthy connections.
* **Intelligent Gateway Monitoring**: Uses the exact same advanced monitoring thresholds (latency, packet loss) that you configure in the pfSense GUI to determine gateway health.
* **Event-Driven Updates**: A watcher daemon provides a near real-time trigger, ensuring DNS updates happen within seconds of a gateway status change.
* **Visual Status Feedback**: Intentionally uses a quirk in the pfSense DynDNS dashboard widget to color-code cached IPs: **green** for healthy/online and **red** for unhealthy/offline.
* **Fully Upgrade-Safe**: Does not modify any core pfSense system files, ensuring your configuration survives system updates.
* **Multiple DNS Providers**: Supports both **PowerDNS** (via its REST API) and **Cloudflare** (via API Token) out of the box via separate updater scripts.
* **Cloudflare Proxy Toggle**: When using Cloudflare, independently control the orange-cloud proxy mode per deployment via the `CF_PROXIED` variable in `cf_dyndns.py`.
* **Portable Architecture**: Platform-specific code is abstracted into a class, making it significantly easier to port the solution to other systems like OPNsense or OpenWrt.

## How It Works

This solution consists of two Python scripts that work together:

1.  **`gateway_watcher.py` (The Trigger)**: This is a lightweight daemon that runs continuously in the background. It polls the status of the pfSense gateway monitoring sockets every few seconds. By reading your configured thresholds for latency and packet loss, it determines the true health of each gateway. When it detects a change in any gateway's status (e.g., from `online` to `down`), it instantly executes the configured updater script.
2.  **`pdns_dyndns.py` or `cf_dyndns.py` (The Updater)**: This is the main script that does the heavy lifting. When executed, it:
    * Gets the list of all public IPv4 and IPv6 addresses on your WAN interfaces.
    * Checks the health of each corresponding gateway using the same logic as the watcher.
    * Constructs a list of "healthy" IPs.
    * Updates a single DNS record on your DNS provider with the list of healthy IPs via the API.
    * Updates the pfSense DynDNS cache files. It cleverly writes IPs for healthy gateways without a newline (making them appear **green** in the widget) and writes IPs for unhealthy gateways *with* a newline (making them appear **red**).

## Prerequisites

* A pfSense firewall with multiple WAN connections configured.
* A PowerDNS server with its API enabled **or** a Cloudflare account with an API Token that has `Zone:DNS:Edit` permission.
* SSH access or console access to your pfSense firewall.
* Python 3.11 or newer installed on pfSense (`pkg install python3.11`).

## Installation & Configuration

Follow these steps to set up the entire system.

### Step 1: Place the Scripts

Copy the scripts you need to `/root/` on your pfSense firewall:

* **PowerDNS**: `gateway_watcher.py` and `pdns_dyndns.py`
* **Cloudflare**: `gateway_watcher.py` and `cf_dyndns.py`

### Step 2: Edit the Configuration Variables

All configuration lives directly inside each updater script. Open the script and edit the variables at the bottom of the file (inside the `if __name__ == "__main__":` block).

#### PowerDNS (`pdns_dyndns.py`)

```python
PDNS_API_URL                = "https://pdns-api/api/v1"
PDNS_API_KEY                = "your_powerdns_api_key"
PDNS_SERVER_ID              = "localhost"
PDNS_ZONE                   = "example.com."
RECORD_NAME                 = "home.example.com."
TTL                         = 60
ALLOWED_PHYSICAL_INTERFACES = ["em0", "ixl2"]
STATE_FILE                  = "/var/db/pdns-dyndns.state.json"
```

* `PDNS_API_URL`: The base URL of your PowerDNS API.
* `PDNS_API_KEY`: Your PowerDNS API key.
* `PDNS_SERVER_ID`: The server ID for your PowerDNS instance (usually `localhost`).
* `PDNS_ZONE`: The DNS zone you are updating (e.g., `example.com.`). Note the trailing dot (FQDN format).
* `RECORD_NAME`: The full hostname to manage. PowerDNS requires a trailing dot (e.g., `home.example.com.`).

#### Cloudflare (`cf_dyndns.py`)

```python
CF_API_TOKEN                = "your_cloudflare_api_token"
CF_ZONE_ID                  = "your_cloudflare_zone_id"
CF_PROXIED                  = False   # Set to True to enable Cloudflare orange-cloud proxy
RECORD_NAME                 = "home.example.com"
TTL                         = 120     # Ignored when CF_PROXIED=True (Cloudflare enforces TTL=1)
ALLOWED_PHYSICAL_INTERFACES = ["em0", "ixl2"]
STATE_FILE                  = "/var/db/cf-dyndns.state.json"
```

* `CF_API_TOKEN`: A Cloudflare API Token with the **Zone › DNS › Edit** permission for the target zone.
* `CF_ZONE_ID`: Your Cloudflare Zone ID (found on the zone's Overview page).
* `CF_PROXIED`: `True` to enable Cloudflare's orange-cloud proxy; `False` for DNS-only mode. When `True`, TTL is automatically set to 1 (Auto) as required by Cloudflare.
* `RECORD_NAME`: The full hostname to manage. Cloudflare does **not** require a trailing dot.

#### Common fields (both scripts)

* `ALLOWED_PHYSICAL_INTERFACES`: A list of the physical interface names for your WAN connections (e.g., `["em0", "ixl2"]`). You can find these names in pfSense under **Interfaces > Assignments**.
* `TTL`: DNS record TTL in seconds.
* `STATE_FILE`: Path to the state file used to detect IP changes between runs.

### Step 3: Configure pfSense DynDNS Service

This step configures pfSense to use your script. You must create one "Custom" DynDNS entry **for each of your WAN interfaces**.

1.  Navigate to **Services > Dynamic DNS**.
2.  Click **Add**.
3.  Configure the entry as follows:
    * **Service Type**: `Custom`
    * **Interface to monitor**: Select one of your WAN interfaces (e.g., `WAN`).
    * **Interface to send update from**: This should almost always be the same as the interface you are monitoring.
    * **Hostname**: Enter your full domain name (e.g., `home.yourdomain.org`).
    * **Update URL**: `/root/pdns_dyndns.py` (or `/root/cf_dyndns.py` for Cloudflare)
    * **Result Match**: `Update successful` (This is not strictly necessary as our script handles its own state, but it's good practice).
    * **Description**: A helpful description (e.g., `WAN1 DynDNS Trigger`).

    > **Note on IPv4-Only Setups**: If you do not have IPv6 connectivity or do not wish to manage AAAA records, you should add the `--ipv4only` flag to the Update URL. This prevents the script from trying to find and update non-existent IPv6 addresses.
    >
    > **Example `Update URL`**: `/root/pdns_dyndns.py --ipv4only`

4.  Click **Save**.
5.  **Repeat this process** for your second WAN interface (e.g., `WAN2`), making sure to select the correct interface in the dropdowns.

### Step 4: Install Required pfSense Packages

Navigate to **System > Package Manager > Available Packages** and install the following two packages:

1.  `shellcmd`: This allows us to run our watcher daemon safely on boot.
2.  `python3.11` (or your preferred Python 3 version): If not already installed.

### Step 5: Configure the Watcher Daemon

This final step makes the system event-driven and upgrade-safe.

1.  Navigate to **Services > Shellcmd**.
2.  Click **Add**.
3.  Configure the command:
    * **PowerDNS** (default): `/usr/local/bin/python3.11 /root/gateway_watcher.py &`
    * **Cloudflare**: `/usr/local/bin/python3.11 /root/gateway_watcher.py --updater /root/cf_dyndns.py &`
    * **Shellcmd Type**: `shellcmd`. This ensures it runs late in the boot process.
    * **Description**: `DynDNS Gateway Watcher Daemon`.
4.  Click **Save**.
5.  Reboot your pfSense firewall to start the watcher daemon, or run the command manually from the console to start it immediately.

The `--updater` flag tells the watcher which script to call when a gateway event is detected. It defaults to `/root/pdns_dyndns.py`, so existing PowerDNS users do not need to change anything.

## Usage & Verification

Once configured, the system is fully automatic. To verify it's working:

* **Check the logs**: The watcher script will log its activity. You can view it by running `tail -f /var/log/messages` and looking for entries from the watcher.
* **Test a failover**: You can simulate a gateway failure by unplugging a WAN connection or manually setting its monitor IP to an invalid address. Within seconds, you should see:
    1.  The watcher script detect the change and trigger the updater.
    2.  The updater script run and update the DNS provider.
    3.  The corresponding IP in the pfSense "Dynamic DNS Status" widget turn **red**.
* **Manual Execution**: You can test the main script at any time by running it from the console:
    ```shell
    # PowerDNS
    /usr/local/bin/python3.11 /root/pdns_dyndns.py --force-update

    # Cloudflare
    /usr/local/bin/python3.11 /root/cf_dyndns.py --force-update
    ```

## Script Usage

While the system is designed to be fully automatic, you can run either updater script manually from the command line for testing or debugging. Both scripts accept the same arguments.

### Command-Line Arguments

* `-h, --help`: Shows the help message and a list of all available arguments.
* `--dry-run`: Performs all checks, including gateway status and IP detection, but does not send any update to the DNS provider. This is useful for safely verifying the script's logic. Note: `--dry-run` output is always printed regardless of `--quiet`.
* `--ipv4only`: Forces the script to ignore all IPv6 addresses. It will only consider healthy IPv4 addresses for the DNS update.
* `--ipv6only`: Forces the script to ignore all IPv4 addresses. It will only consider healthy IPv6 addresses for the DNS update.
* `--force-update`: Bypasses the internal state check and forces the script to send a DNS update, even if no IP or gateway status changes have been detected. This is used by the watcher daemon to ensure an update happens after a gateway event.
* `--quiet`: Suppresses normal informational logs for a cleaner execution log. Note: `--dry-run` output is always printed regardless of this flag.
* `--reason REASON`: A text string used for logging purposes to indicate why the script was run. The watcher daemon uses this to specify that the trigger was a "Gateway-Event".

### `gateway_watcher.py` Arguments

* `--updater PATH`: Path to the updater script to call when a gateway change is detected. Defaults to `/root/pdns_dyndns.py`. Set to `/root/cf_dyndns.py` to use Cloudflare instead.

## Porting to Other Platforms (e.g., OPNsense, OpenWrt)

The scripts have been designed to make porting to other operating systems as easy as possible. All platform-specific code is contained within the `PfSensePlatform` class.

To adapt this solution for a new platform, you only need to:

1.  Create a new class (e.g., `OPNsensePlatform`) that inherits from `BasePlatform`.
2.  Implement all the methods defined in `BasePlatform` with logic specific to your target OS. For example, you would replace the code that reads `/conf/config.xml` with code that reads OPNsense's configuration, and replace the `dpinger` socket logic if OPNsense uses a different monitoring method.
3.  In the `if __name__ == "__main__":` block of both scripts, change the line `platform = PfSensePlatform()` to instantiate your new class (e.g., `platform = OPNsensePlatform()`).
4.  Adapt the installation method (e.g., use OPNsense's startup script mechanism instead of `shellcmd`).

The core DNS update and state management logic will work without modification.
