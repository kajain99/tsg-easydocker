# TSG EasyDocker

TSG EasyDocker is a lightweight web utility that helps you install Docker applications using guided forms. It is built for home users, NAS users, and anyone who wants a simpler way to deploy apps without dealing with `docker compose` YAML or long `docker run` commands.

EasyDocker reads curated app recipes and presents them as easy web forms, with most values already filled in, so you can deploy apps with much less friction.

## From the User's Perspective

From a user's point of view, EasyDocker is simple:

1. open the app catalog
2. choose an app
3. click install
4. review a small form with most values already filled in, with help available for each field
5. fill in the few fields that matter
6. click deploy

That's it.

No writing Docker Compose files. No building `docker run` commands. EasyDocker handles the repetitive setup work so you can get an app running quickly and easily.

## What It Does

EasyDocker helps you:

- browse supported app recipes
- configure apps through forms
- generate Docker Compose automatically
- deploy apps directly
- view live deployment logs
- detect port conflicts before deployment
- detect possible duplicate installs
- reopen and redeploy saved configurations
- refresh recipes from the official EasyDocker recipe repository

## Built for UGREEN, Works Everywhere

I wrote EasyDocker primarily with UGREEN NAS users in mind, but it works anywhere Docker and Docker Compose are available, including:

- UGREEN NAS
- QNAP
- Synology
- Linux home servers
- other Docker-capable systems

## Where Your Configs and Data Go

EasyDocker requires a mounted base config directory. It stores generated app configurations, saved recipe data, and runtime recipe copies inside that location.

For example, if you run EasyDocker with:

```bash
-v /base_config:/base_config
```

then EasyDocker stores app-specific configuration inside that location. Each deployed app gets its own folder under the base config path, including its generated `docker-compose.yml` and related saved configuration.

For example, an Immich deployment would typically be stored under:

```text
/base_config/tsg_immich
```

This makes it easier to inspect, back up, and redeploy saved configurations later.

EasyDocker will not start unless the base config path is mounted and available inside the container.

For Linux and NAS deployments, EasyDocker writes each app into its own project folder under the mounted base config path. In practice that means a deployment such as `tsg_tailscale` keeps its generated Compose, metadata, recipe snapshot, and relative data folders under:

```text
/base_config/tsg_tailscale
```

So a relative bind like `./tailscale_data` is intended to resolve under that same project folder.

EasyDocker keeps that relative `./...` value in the recipe so recipes stay portable, but in the UI it also shows the resolved host path for transparency. In other words:

- `./something` is expanded into the current app's project folder under your mounted base config path
- an absolute path such as `/volume1/docker/appdata` is left exactly as written

For example, if your mounted host base config path is:

```text
/volume1/host_tsg
```

then a recipe value like:

```text
./tailscale_data
```

resolves to:

```text
/volume1/host_tsg/tsg_tailscale/tailscale_data
```

On Windows with Docker Desktop, bind-mount path resolution may not map back to normal host-visible Windows paths as cleanly as it does on Linux. EasyDocker is designed primarily for Linux and NAS-style Docker hosts, and that storage model is the one we recommend.

## Security Notes

EasyDocker is a Docker management utility, so it should be treated as a trusted local-network tool.

- EasyDocker requires a password to start
- you must set `EASYDOCKER_PASSWORD` before running it
- set `EASYDOCKER_SECRET_KEY` to a long random value for stable and secure sessions
- it is meant for use on a trusted local network
- do not expose it directly to the public internet
- recipe refresh pulls only from the official EasyDocker recipe repository

## Docker Image

EasyDocker is available on Docker Hub:

[kajain99/easydocker](https://hub.docker.com/r/kajain99/easydocker)

Pull the latest image with:

```bash
docker pull kajain99/easydocker:latest
```

## Quick Start

You can run EasyDocker using either `docker run` or `docker compose`.

### Docker Run

Run EasyDocker with Docker:

```bash
docker run -d \
  -p 5000:5000 \
  -e EASYDOCKER_PASSWORD=your-strong-password \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /base_config:/base_config \
  --name easydocker \
  kajain99/easydocker:latest
```

### Docker Compose

Create a `docker-compose.yml` file like this:

```yaml
services:
  easydocker:
    image: kajain99/easydocker:latest
    container_name: easydocker
    ports:
      - "5000:5000"
    environment:
      EASYDOCKER_PASSWORD: your-strong-password
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /base_config:/base_config
    restart: unless-stopped
```

Then start it with:

```bash
docker compose up -d
```

After that, open:

```text
http://YOUR-SERVER-IP:5000
```

Default username:

```text
admin
```

Password:

```text
the value you set in EASYDOCKER_PASSWORD
```

## Optional Environment Variables

You can also set:

```text
EASYDOCKER_USERNAME
EASYDOCKER_PASSWORD
EASYDOCKER_HOST
EASYDOCKER_PORT
EASYDOCKER_BASE_CONFIG
EASYDOCKER_HOST_BASE_CONFIG
EASYDOCKER_SECRET_KEY
```

`EASYDOCKER_BASE_CONFIG` is optional only if you mount your persistent folder at `/base_config`, which is the default expected path. If you want to use a different in-container path, set `EASYDOCKER_BASE_CONFIG` to that mounted path.

`EASYDOCKER_HOST_BASE_CONFIG` is optional and normally does not need to be set. EasyDocker tries to detect the host-side source path of the required `/base_config` bind automatically through the Docker socket so it can expand relative bind mounts into real host paths. Set this only if you want to override that detected host base path manually.

Typical example:

```bash
docker run -d \
  -p 5000:5000 \
  -e EASYDOCKER_USERNAME=admin \
  -e EASYDOCKER_PASSWORD=your-strong-password \
  -e EASYDOCKER_SECRET_KEY=your-long-random-secret \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /base_config:/base_config \
  --name easydocker \
  kajain99/easydocker:latest
```

`EASYDOCKER_SECRET_KEY` is strongly recommended. It is used by Flask to securely sign session data such as login sessions and CSRF-related session values. Set it to a long random value so sessions remain stable across container restarts and your deployment stays more secure.

## How It Works

EasyDocker uses a recipe-based model. Each supported app has a recipe that defines things like:

- Docker images
- environment variables
- ports
- volume mappings
- configurable fields
- defaults

When you choose an app in the UI, EasyDocker builds a form from that recipe, generates the Compose file, stores it in the base config folder, and can deploy it immediately.

## Recipe Source

EasyDocker refreshes recipes from the official EasyDocker recipes repository:

[EasyDocker Recipes](https://github.com/kajain99/tsg-easydocker-recipes)

This keeps application releases separate from recipe updates.

## Project Scope

EasyDocker is meant to be:

- simple
- practical
- approachable
- useful for real home-server deployments

It is not trying to be:

- a full orchestration platform
- a multi-user enterprise system
- a public-facing deployment service

Operations like start, stop, restart, and delete are not handled inside EasyDocker. Those are expected to be managed from your NAS container manager or Docker environment.

## License

This project is licensed under the MIT License.
