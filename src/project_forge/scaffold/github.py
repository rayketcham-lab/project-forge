"""GitHub integration via gh CLI -- supports personal and org repos."""

import json
import logging
import subprocess

from project_forge.config import settings

logger = logging.getLogger(__name__)


def _run_gh(args: list[str], cwd: str | None = None) -> str:
    """Run a gh CLI command and return stdout."""
    cmd = ["gh"] + args
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=60)
    if result.returncode != 0:
        logger.error("gh command failed: %s", result.stderr)
        raise RuntimeError(f"gh command failed: {result.stderr}")
    return result.stdout.strip()


def create_repo(
    name: str,
    description: str,
    public: bool = True,
    owner: str | None = None,
) -> str:
    """Create a GitHub repo under owner (personal or org) and return its URL."""
    owner = owner or settings.github_owner
    args = ["repo", "create", f"{owner}/{name}", f"--description={description}"]
    args.append("--public" if public else "--private")
    url = _run_gh(args)
    logger.info("Created repo: %s", url)
    return url


def create_issue(
    repo: str,
    title: str,
    body: str,
    labels: list[str] | None = None,
) -> str:
    """Create a GitHub issue and return its URL."""
    args = ["issue", "create", "-R", repo, "--title", title, "--body", body]
    if labels:
        args.extend(["--label", ",".join(labels)])
    url = _run_gh(args)
    logger.info("Created issue: %s", url)
    return url


def create_label(repo: str, name: str, color: str, description: str = "") -> None:
    """Create a label on a GitHub repo."""
    args = ["label", "create", name, "-R", repo, "--color", color]
    if description:
        args.extend(["--description", description])
    try:
        _run_gh(args)
    except RuntimeError:
        logger.warning("Label %s may already exist on %s", name, repo)


def list_org_repos(org: str | None = None) -> list[dict]:
    """List repos in a GitHub org. Returns list of {name, description, visibility}."""
    org = org or settings.github_org
    output = _run_gh(["repo", "list", org, "--limit", "100", "--no-archived"])
    if not output.strip():
        return []
    repos = []
    for line in output.strip().split("\n"):
        parts = line.split("\t")
        if len(parts) >= 3:
            repos.append(
                {
                    "name": parts[0],
                    "description": parts[1],
                    "visibility": parts[2],
                }
            )
    return repos


def get_repo_details(owner: str, repo: str) -> dict:
    """Fetch repo metadata and README content."""
    api_output = _run_gh(["api", f"repos/{owner}/{repo}"])
    raw = json.loads(api_output)
    details = {
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
        "topics": raw.get("topics", []),
        "language": raw.get("language"),
    }

    # Fetch README (non-fatal)
    try:
        readme = _run_gh(["api", f"repos/{owner}/{repo}/readme", "--jq", ".content"])
        details["readme"] = readme
    except RuntimeError:
        details["readme"] = ""

    return details


def push_initial_commit(project_dir: str, remote_url: str) -> None:
    """Initialize git, commit all files, and push to remote."""
    cmds = [
        ["git", "init"],
        ["git", "branch", "-M", "main"],
        ["git", "add", "-A"],
        [
            "git",
            "commit",
            "-m",
            "Initial scaffold from Project Forge\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        ],
        ["git", "remote", "add", "origin", remote_url],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_dir, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}: {result.stderr}")

    # Push with gh auth token
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
    token = result.stdout.strip()
    push_url = remote_url.replace("https://", f"https://x-access-token:{token}@")
    subprocess.run(
        ["git", "remote", "set-url", "origin", push_url],
        capture_output=True,
        text=True,
        cwd=project_dir,
        timeout=10,
    )
    result = subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        capture_output=True,
        text=True,
        cwd=project_dir,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Git push failed: {result.stderr}")
    # Clean up token from remote URL
    subprocess.run(
        ["git", "remote", "set-url", "origin", remote_url],
        capture_output=True,
        text=True,
        cwd=project_dir,
        timeout=10,
    )
    logger.info("Pushed initial commit to %s", remote_url)
