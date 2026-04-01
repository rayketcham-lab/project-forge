"""Project scaffolding: renders templates into a project directory."""

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from project_forge.models import Idea, ScaffoldSpec

TEMPLATES_DIR = Path(__file__).parent / "templates"


def sanitize_repo_name(name: str) -> str:
    """Sanitize a string into a valid GitHub repo name.

    Replaces all characters outside [a-z0-9-] with dashes, collapses runs of
    dashes, then strips leading/trailing dashes.  If the result is empty (e.g.
    the input was all special characters or the empty string) a fallback of
    ``"project"`` is returned so that ``gh repo create`` never receives a blank
    name.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.lower().strip())
    sanitized = re.sub(r"-+", "-", sanitized).strip("-")
    sanitized = sanitized[:100]
    return sanitized if sanitized else "project"


def build_scaffold_spec(idea: Idea) -> ScaffoldSpec:
    """Create a ScaffoldSpec from an Idea, inferring language from tech stack."""
    lang = "python"  # default
    for tech in idea.tech_stack:
        t = tech.lower()
        if t in ("node", "nodejs", "javascript", "typescript", "express", "react", "next.js"):
            lang = "node"
            break
        if t in ("rust", "cargo", "tokio"):
            lang = "rust"
            break
        if t in ("go", "golang", "gin", "echo"):
            lang = "go"
            break

    framework = None
    for tech in idea.tech_stack:
        t = tech.lower()
        if t in ("fastapi", "flask", "django", "express", "gin", "actix", "rocket"):
            framework = t
            break

    issues = [
        {"title": f"Set up {lang} project structure", "body": f"Initialize the {lang} project with basic scaffolding."},
        {"title": "Implement core functionality", "body": f"Build the core feature: {idea.mvp_scope}"},
        {"title": "Add tests", "body": "Write initial test suite covering core functionality."},
        {"title": "Add CI/CD pipeline", "body": "Set up GitHub Actions for lint + test."},
    ]

    return ScaffoldSpec(
        idea_id=idea.id,
        repo_name=sanitize_repo_name(idea.name),
        language=lang,
        framework=framework,
        initial_issues=issues,
    )


def render_scaffold(spec: ScaffoldSpec, idea: Idea, output_dir: Path, owner: str | None = None) -> Path:
    """Render scaffold templates to output_dir. Returns the project root."""
    from project_forge.config import settings

    owner = owner or settings.github_owner
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=False)
    project_dir = output_dir / spec.repo_name
    project_dir.mkdir(parents=True, exist_ok=True)

    context = {
        "name": idea.name,
        "tagline": idea.tagline,
        "description": idea.description,
        "market_analysis": idea.market_analysis,
        "mvp_scope": idea.mvp_scope,
        "tech_stack": idea.tech_stack,
        "repo_name": spec.repo_name,
        "repo_url": f"https://github.com/{owner}/{spec.repo_name}",
        "language": spec.language,
        "framework": spec.framework,
    }

    # README
    readme_tmpl = env.get_template("shared/README.md.j2")
    (project_dir / "README.md").write_text(readme_tmpl.render(**context))

    # CI
    ci_dir = project_dir / ".github" / "workflows"
    ci_dir.mkdir(parents=True, exist_ok=True)
    ci_tmpl = env.get_template("shared/ci.yml.j2")
    (ci_dir / "ci.yml").write_text(ci_tmpl.render(**context))

    # Language-specific files
    if spec.language == "python":
        pyproject_tmpl = env.get_template("python/pyproject.toml.j2")
        (project_dir / "pyproject.toml").write_text(pyproject_tmpl.render(**context))
        (project_dir / "src").mkdir(exist_ok=True)
        pkg_dir = project_dir / "src" / spec.repo_name.replace("-", "_")
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "__init__.py").write_text(f'"""{idea.name}."""\n')
        (project_dir / "tests").mkdir(exist_ok=True)
        (project_dir / "tests" / "__init__.py").write_text("")

    elif spec.language == "node":
        pkg_tmpl = env.get_template("node/package.json.j2")
        (project_dir / "package.json").write_text(pkg_tmpl.render(**context))
        (project_dir / "src").mkdir(exist_ok=True)
        (project_dir / "src" / "index.js").write_text(f'// {idea.name}\nconsole.log("Hello from {idea.name}");\n')
        (project_dir / "tests").mkdir(exist_ok=True)

    # .gitignore
    gitignore_lines = ["__pycache__/", "*.pyc", "node_modules/", ".env", "dist/", "build/", "*.egg-info/"]
    (project_dir / ".gitignore").write_text("\n".join(gitignore_lines) + "\n")

    return project_dir
