# Ionpath on RHEL 9 with Podman and a separate NGINX VM

Traffic: browser → NGINX HTTPS → application VM private address:8501 → container.
Use an x86-64 application VM. Start with 2 vCPU and 4 GB RAM and measure actual
usage. The container uses Debian userspace on the RHEL kernel; nothing installs
Python dependencies or the HF binary onto the RHEL host.

## Example addresses — replace before deploying

- Application VM hostname: `ionpath`
- Application VM private IP: `192.168.1.50`
- NGINX VM private IP: `192.168.1.10`
- Public hostname: `ionpath.example.com` (replace `example.com` with your domain)

These are examples, not detected network settings. Replace every occurrence of
both IP addresses in this guide and the configuration files before installation.
The VM hostname `ionpath` alone does not create a public DNS name.

## 1. Prepare the application VM

Run as your normal login user, using sudo only where shown:

```bash
sudo dnf install -y podman git
podman --version
uname -m
git clone https://github.com/nousernamesavailabel/ionpath.git
cd ionpath
```

If this checkout is already on the VM, use it instead of cloning. The deployment
files must be committed and pushed before a fresh clone can contain them.
Quadlet requires Podman 4.6 or later; update RHEL's supported container-tools
packages if needed. Keep using the SAME normal user for builds and systemctl
--user commands. No registry account or image upload is necessary.

## 2. Build

From the repository directory:

```bash
podman build --format docker -t localhost/ionpath:latest -f Containerfile .
```

The first build downloads a Python base image, installs the NEC solver (building it if needed) and
installs the checksum-verified official ITU model (about 137 MB). Allow several
minutes and internet access. Local settings, archives and secrets are excluded
from the build. Only application sources and the model installer are copied.

## 3. Start automatically with rootless Podman

Edit deploy/ionpath.container: replace the example 192.168.1.50 with this VM's actual private
IPv4 address. Do not use the NGINX VM's address here.

```bash
mkdir -p ~/.config/containers/systemd
cp deploy/ionpath.container ~/.config/containers/systemd/
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user start ionpath.service
systemctl --user status ionpath.service
podman logs ionpath
```

Quadlet generates the service; do not run systemctl enable on the generated
unit. WantedBy plus lingering starts it at boot. The named volume is created
on first start and initialized from the image's model cache. :U assigns its
ownership to the container user; Podman manages SELinux labels for named
volumes. Avoid mounting an empty host directory over /app/.cache, which would
hide the installed HF model. Never disable SELinux to make a mount work.

## 4. Allow only the proxy through the firewall

On the application VM, identify the active interface zone:

```bash
sudo firewall-cmd --get-active-zones
```

Replace YOUR_ZONE and the example NGINX address 192.168.1.10 below. This example assumes IPv4. Do not
open 8501 globally or forward it from your router. Check the zone for existing
broad rules that might already allow it.

```bash
sudo firewall-cmd --permanent --zone=YOUR_ZONE --add-rich-rule='rule family="ipv4" source address="192.168.1.10/32" port port="8501" protocol="tcp" accept'
sudo firewall-cmd --reload
```

On the NGINX VM, verify that the backend responds:

```bash
curl --fail http://192.168.1.50:8501/_stcore/health
```

Expect `ok`. Also verify that another machine cannot reach port 8501.

## 5. Configure NGINX and DNS

Point your chosen hostname, for example ionpath.example.com, at the public entry
point for your existing NGINX service. Configure its HTTPS certificate using
your normal procedure. Paste deploy/nginx-location.conf into that hostname's
existing HTTPS server block, replacing 192.168.1.50. It is a location
snippet, not a complete standalone NGINX configuration. Use a dedicated hostname
at / rather than a URL subdirectory. Preserve Streamlit's XSRF/CORS defaults.

On the NGINX VM, validate before reloading:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

If SELinux on the NGINX VM denies outbound proxy connections, inspect its AVC
logs. On RHEL, enabling the standard proxy networking boolean may be needed:

```bash
sudo setsebool -P httpd_can_network_connect 1
```

## 6. Verify before sharing

Open your HTTPS hostname in a browser. Move the map pins and run Analyze
propagation. Confirm the HF model works; a health check alone does not test the
model. Open a separate private browser session and confirm changing station or
antenna defaults in one session does not change the other. Check NGINX logs for
successful WebSocket upgrades (101); disconnects often indicate missing Upgrade
headers. Upstream NOAA/GIRO/WSPR failures are displayed independently.

Public mode is enabled by default in the image. Station/antenna defaults and
validation history belong to the current Streamlit session in temporary storage.
They do not survive a new session or restart. Local desktop behavior is unchanged
without IONPATH_PUBLIC=1. Provider/model caches are shared, but user settings and
validation history are not. Do not override public mode on a public instance.

The FT8 archive is optional and NOT included in the image. WSPR and the HF model
work without it. To install an already downloaded May 12 archive into the volume:

```bash
podman exec ionpath mkdir -p /app/.cache/madrigal
podman cp /path/to/rsd2024-05-12.01.hdf5 ionpath:/app/.cache/madrigal/
```

Allow at least another 3.13 GB disk space. No bulk archive downloads are needed.

## Updates and rollback

```bash
git pull --ff-only
podman tag localhost/ionpath:latest localhost/ionpath:previous
podman build --format docker -t localhost/ionpath:latest -f Containerfile .
systemctl --user restart ionpath.service
```

Repeat the browser/model checks. If necessary, restore the previous image:

```bash
podman tag localhost/ionpath:previous localhost/ionpath:latest
systemctl --user restart ionpath.service
```

Restarting disconnects current visitors. Persistent cache volumes survive image
replacement; if a future update changes the bundled HF model, migrate its cache
explicitly (the existing volume is not reseeded automatically). Keep backups of
any optional archive. Do not delete the volume as a routine update step.

References: [RHEL containers and Quadlet](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/epub/building_running_and_managing_containers/index),
[NGINX WebSocket proxying](https://nginx.org/en/docs/http/websocket.html).

## Beta release checks

The image installs the exact Python versions in `requirements.lock` and pins its
base image digest. `requirements.txt` remains the human-maintained list of direct
dependencies; resolve updates deliberately, commit the updated lock, rebuild and
run the full suite. OS packages still come from Debian repositories, so this is
not a byte-for-byte reproducible OS build.

The supplied service caps the app at 2 CPU cores, 3 GiB memory and 256 tasks.
These are initial beta limits, not measured capacity guarantees. Monitor
`podman stats ionpath` during representative multi-user use and adjust the service
limits for the VM. Leave memory available for the OS and reverse proxy services.

Run all tests in a disposable container built from the release image (the image
excludes tests). From the repository, stage the current sources and tests:

```bash
tar -cf /tmp/ionpath-beta-source.tar -- *.py tests
podman run --rm -i --user 0 --entrypoint sh localhost/ionpath:latest \
  -c 'tar -xf - -C /app && IONPATH_PUBLIC=0 python -m unittest discover -s tests -v' \
  < /tmp/ionpath-beta-source.tar
```

The concurrency check starts two overlapping Streamlit AppTest processes with
different paths and physical antennas, exercises the actual NEC and P.533 solvers,
and checks private settings and validation databases. Its `BETA_CONCURRENCY` output
reports wall time and process/child peak RSS. This is a controlled solver/session
check, not an end-to-end HTTP load test or measurement of remote provider latency.
Still perform the two-browser HTTPS/WebSocket checks above on the deployed VM.
