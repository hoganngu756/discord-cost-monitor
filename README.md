# Kubernetes Resource Efficiency & Cost Monitor
Kubernetes Resource Efficiency & Cost Monitor is an automated Discord bot. It's developed to identify over-provisioned workloads inside a local Kubernetes cluster and surface resource-wasting configurations to a designated Discord channel.

## Install

### Standard install
The easiest way to get started is to clone the repository and set up a Python virtual environment.

First, grab it from GitHub:
```bash
git clone https://github.com/hoganngu756/discord-cost-monitor.git
cd discord-cost-monitor
```

### Setup dependencies
The bot requires Python 3.12+ and uses a virtual environment to manage its dependencies.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configuration
The bot reads all of its configuration from a `.env` file in the project root. Copy the example template and fill in your values:

```bash
cp .env.example .env
```

You will need the following credentials:
- **Discord Bot Token**: Create a bot at the [Discord Developer Portal](https://discord.com/developers/applications), go to the **Bot** tab, and copy the token.
- **Discord Guild ID**: Enable Developer Mode in Discord settings, then right-click your server and select **Copy Server ID**.
- **Report Channel ID**: Right-click the text channel where you want weekly reports posted and select **Copy Channel ID**.

Open `.env` and replace the placeholder values:
```env
DISCORD_TOKEN=your-bot-token-here
DISCORD_GUILD_ID=123456789012345678
REPORT_CHANNEL_ID=123456789012345678
```

All other settings have sensible defaults and are optional:

| Variable | Default | Description |
|---|---|---|
| `PROMETHEUS_URL` | `http://prometheus-server.monitoring.svc:9090` | Prometheus server URL |
| `OPENCOST_URL` | `http://opencost.opencost.svc:9003` | OpenCost API URL |
| `LOOKBACK_WINDOW` | `24h` | PromQL lookback window (e.g. `1h`, `24h`, `7d`) |
| `WEEKLY_REPORT_DAY` | `monday` | Day for the automated weekly report |
| `WEEKLY_REPORT_HOUR` | `9` | Hour (0-23 UTC) for the weekly report |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Getting started

### Prerequisites
The bot needs a running Kubernetes cluster with the following services:
- [Prometheus](https://prometheus.io/) with `kube-state-metrics` and `cAdvisor` for resource metrics.
- [OpenCost](https://www.opencost.io/) for financial cost mapping (optional — the bot falls back to a local pricing profile if OpenCost is unavailable).

### Running locally
The general workflow requires port-forwarding the cluster services and then starting the bot.

To port-forward Prometheus and OpenCost, open two separate terminals and run:
```bash
# Terminal 1 — Prometheus
kubectl port-forward svc/prometheus-server -n monitoring 9090:80

# Terminal 2 — OpenCost
kubectl port-forward svc/opencost -n opencost 9003:9003
```

Update your `.env` to point at the local ports:
```env
PROMETHEUS_URL=http://localhost:9090
OPENCOST_URL=http://localhost:9003
```

To start the bot, open a third terminal and run:
```bash
source .venv/bin/activate
python -m bot.main
```

### Using the bot
Once the bot is running and connected to your Discord server, you can interact with it using slash commands:

1. Open your Discord server.
2. Type `/audit-namespace` and select a namespace from the autocomplete list. The bot will scan the namespace, identify over-provisioned workloads, and display Requested vs. Actual usage with right-sized recommendations.
3. Type `/cluster-summary` to get a high-level overview of cluster-wide efficiency and total estimated daily financial waste.
4. The bot also posts an automated weekly report of the top 3 most inefficient workloads to the configured report channel.

## Deploying to Kubernetes
For production use, the bot is packaged as a container and deployed directly into the cluster.

### Build the Docker image
```bash
docker build -f deploy/Dockerfile -t kube-cost-bot:latest .
```

### Apply the Kubernetes manifests
```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/serviceaccount.yaml
kubectl apply -f deploy/k8s/clusterrole.yaml
kubectl apply -f deploy/k8s/clusterrolebinding.yaml
```

### Create the secrets
```bash
kubectl create secret generic cost-bot-secrets \
  --namespace=kube-cost-bot \
  --from-literal=DISCORD_TOKEN=<your-token> \
  --from-literal=DISCORD_GUILD_ID=<your-guild-id> \
  --from-literal=REPORT_CHANNEL_ID=<your-channel-id>
```

### Deploy the bot
```bash
kubectl apply -f deploy/k8s/deployment.yaml
```

### Verify
```bash
kubectl get pods -n kube-cost-bot
kubectl logs -f deployment/kube-cost-bot -n kube-cost-bot
```

## Testing
```bash
pip install -e ".[dev]"
pytest
```

## Project structure
```
├── bot/
│   ├── main.py                  # Bot entrypoint
│   ├── cogs/
│   │   ├── audit.py             # /audit-namespace command
│   │   ├── summary.py           # /cluster-summary command
│   │   └── scheduler.py         # Weekly automated report
│   └── embeds/
│       └── formatters.py        # Discord embed builders
├── metrics/
│   ├── prometheus_client.py     # PromQL query wrapper
│   ├── opencost_client.py       # OpenCost REST API client
│   └── analyzer.py              # Resource analysis & recommendations
├── config/
│   ├── settings.py              # Pydantic configuration
│   └── pricing/
│       └── default.json         # Mock CPU/RAM cost profile
├── deploy/
│   ├── Dockerfile               # Multi-stage Alpine image
│   └── k8s/                     # Kubernetes manifests
├── tests/                       # Unit tests
├── pyproject.toml
├── requirements.txt
└── .env.example
```
