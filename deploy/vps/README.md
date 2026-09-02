# VPS deployment notes

## Verified server baseline

- OS: CentOS 7, glibc 2.17, GCC 4.8.
- Active Nginx command: `/usr/bin/nginx -c /usr/local/nginx/conf-new/conf/nginx.conf`.
- Active include pattern: `/usr/local/nginx/conf-new/conf/conf.d/*/*.conf`.
- Deployment account: `AppsPros`, without sudo permission.
- Application address: `127.0.0.1:17861`.
- Production host name: `ccr.joysim.cn`.
- Application root: `/home/AppsPros/svr/agentic-writing-workbench`.

The application uses absolute `/api` and `/static` URLs. Deploy it on a dedicated
host name rather than under an existing site's path prefix.

## Installed isolated layout

```text
/home/AppsPros/svr/agentic-writing-workbench/
  current -> releases/20260831-1126
  releases/20260831-1126/
  shared/
    .env.shared          # mode 0600
    venv/                # Python 3.11
    python/              # uv-managed Python runtimes
    tools/uv
    cache/uv
    data/
    logs/
    novels/               # initial deployment backup/seed
```

CentOS 7 dependency pins live in `constraints-centos7.txt`. They avoid source
builds against the server's old GCC and do not change system packages.

The application expects project files to remain below its release root for path
policy and Web file routing. Active project data therefore lives under
`current/projects/writing/novels/`. When creating a new release, copy that
directory into the new release before switching `current`. `shared/novels/`
is retained as the initial deployment backup, not the active write location.

## Administrator activation procedure

The wildcard `*.joysim.cn` certificate covers `ccr.joysim.cn` through
2026-11-21. DNS still needs an A record pointing `ccr.joysim.cn` to the VPS
public address.

1. Copy the production Nginx file into a new isolated directory matching the active include:

   ```bash
   sudo mkdir -p /usr/local/nginx/conf-new/conf/conf.d/agentic-writing-workbench
   sudo cp ccr.joysim.cn.conf \
     /usr/local/nginx/conf-new/conf/conf.d/agentic-writing-workbench/ccr.joysim.cn.conf
   ```

3. Install the Supervisor example in the server's active Supervisor include directory.
4. Validate before changing runtime state:

   ```bash
   sudo /usr/bin/nginx -t -c /usr/local/nginx/conf-new/conf/nginx.conf
   sudo supervisorctl reread
   sudo supervisorctl update
   curl -fsS http://127.0.0.1:17861/api/writing/models
   ```

5. Only after the local health check succeeds, reload Nginx:

   ```bash
   sudo /usr/bin/nginx -s reload -c /usr/local/nginx/conf-new/conf/nginx.conf
   ```

## Current runtime

The application runs as one Uvicorn worker on `127.0.0.1:17861`.
Its PID is stored in `shared/app.pid`, with stdout/stderr under `shared/logs/`.
Supervisor manages it as `agentic-writing-workbench` under the `AppsPros` user
and restarts it after a crash or reboot. Nginx loads the isolated virtual host
from:

```text
/usr/local/nginx/conf-new/conf/conf.d/agentic-writing-workbench/ccr.joysim.cn.conf
```

HTTP redirects to HTTPS. Local and public-IP `--resolve` checks pass, including
the model-list and Qwen connectivity endpoints. Normal public name resolution
will work after the DNS A record is created.

Authentication state is stored in `shared/data/auth.db`. The initial `admin`
password is written once to `shared/admin-initial-password.txt` with mode 0600;
retrieve it over SSH, sign in, and reset it from `/admin`.
