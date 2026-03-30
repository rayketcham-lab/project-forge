# Project Forge

Autonomous IT project think-tank engine. Uses Claude to generate novel project ideas across security, market gaps, vulnerability research, automation, and more -- then scaffolds them into real GitHub repos.

## Features

- **Idea Generation**: Claude-powered divergent thinking engine that generates project concepts hourly
- **Feasibility Scoring**: Each idea scored on market timing, competition, MVP complexity
- **Auto-Scaffolding**: High-scoring ideas automatically get a GitHub repo with CI, tests, and issues
- **Web Dashboard**: Monitor ideas, approve/reject, trigger scaffolding (port 55443)
- **Autonomous Operation**: Runs on cron, generates ideas without human intervention

## Quick Start

```bash
# Install
pip install -e ".[dev,test]"

# Run tests
pytest tests/ -v

# Start dashboard
python -m uvicorn project_forge.web.app:app --host 0.0.0.0 --port 55443

# Generate one idea
python -m project_forge.cron.runner
```

## Categories

| Category | Focus |
|----------|-------|
| security-tool | Tools that fill gaps in the security toolchain |
| market-gap | Products/services missing from the market |
| vulnerability-research | Novel vulnerability discovery and analysis |
| automation | Workflow and process automation |
| devops-tooling | Developer experience and infrastructure |
| privacy | Privacy-preserving technologies |
| compliance | Regulatory compliance automation |
| observability | Monitoring, logging, and tracing |

## License

MIT
