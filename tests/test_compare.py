"""Tests for Compare Idea to GitHub Project feature (issue #8)."""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.scaffold.github import get_repo_details, list_org_repos
from project_forge.web.app import app, db

# === GitHub CLI: list_org_repos ===


class TestListOrgRepos:
    @patch("project_forge.scaffold.github.subprocess.run")
    def test_list_org_repos_returns_list(self, mock_run):
        """gh repo list returns owner/name — list_org_repos must strip the prefix."""
        mock_run.return_value.returncode = 0
        # Real gh output uses owner/name format in the first column
        mock_run.return_value.stdout = (
            "rayketcham-lab/pki-ca-engine\tPKI Certificate Authority Engine\tpublic\n"
            "rayketcham-lab/project-forge\tAutonomous project think-tank\tpublic\n"
            "rayketcham-lab/honeypot\tSSH honeypot\tprivate\n"
        )
        mock_run.return_value.stderr = ""

        repos = list_org_repos("rayketcham-lab")
        assert len(repos) == 3
        # Names must be short (repo only), not owner/repo
        assert repos[0]["name"] == "pki-ca-engine"
        assert repos[0]["description"] == "PKI Certificate Authority Engine"
        assert repos[0]["visibility"] == "public"
        assert repos[1]["name"] == "project-forge"
        assert repos[2]["name"] == "honeypot"

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_list_org_repos_empty(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = ""

        repos = list_org_repos("empty-org")
        assert repos == []

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_list_org_repos_uses_default_org(self, mock_run):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "rayketcham-lab/repo1\tDesc\tpublic\n"
        mock_run.return_value.stderr = ""

        repos = list_org_repos()
        call_args = mock_run.call_args[0][0]
        assert "rayketcham-lab" in " ".join(call_args)
        # Should strip org prefix
        assert repos[0]["name"] == "repo1"

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_list_org_repos_failure(self, mock_run):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        mock_run.return_value.stderr = "organization not found"

        with pytest.raises(RuntimeError, match="organization not found"):
            list_org_repos("bad-org")


# === GitHub CLI: get_repo_details ===


class TestGetRepoDetails:
    @patch("project_forge.scaffold.github.subprocess.run")
    def test_get_repo_details(self, mock_run):
        """Should return repo name, description, topics, and README."""
        import json

        api_response = json.dumps(
            {
                "name": "pki-ca-engine",
                "description": "A PKI certificate authority engine with ACME support",
                "topics": ["pki", "cryptography", "acme", "x509"],
                "language": "Rust",
            }
        )
        readme_response = "# PKI CA Engine\n\nA certificate authority for issuing and managing X.509 certificates."

        # First call: gh api repos/owner/repo
        # Second call: gh api repos/owner/repo/readme (decoded)
        mock_run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": api_response, "stderr": ""})(),
            type("Result", (), {"returncode": 0, "stdout": readme_response, "stderr": ""})(),
        ]

        details = get_repo_details("rayketcham-lab", "pki-ca-engine")
        assert details["name"] == "pki-ca-engine"
        assert "PKI" in details["description"] or "pki" in details["description"].lower()
        assert "pki" in details["topics"]
        assert details["language"] == "Rust"
        assert "certificate" in details["readme"].lower()

    @patch("project_forge.scaffold.github.subprocess.run")
    def test_get_repo_details_no_readme(self, mock_run):
        """Should handle repos with no README gracefully."""
        import json

        api_response = json.dumps(
            {
                "name": "new-repo",
                "description": "Just created",
                "topics": [],
                "language": None,
            }
        )
        mock_run.side_effect = [
            type("Result", (), {"returncode": 0, "stdout": api_response, "stderr": ""})(),
            type("Result", (), {"returncode": 1, "stdout": "", "stderr": "Not Found"})(),
        ]

        details = get_repo_details("rayketcham-lab", "new-repo")
        assert details["name"] == "new-repo"
        assert details["readme"] == ""


# === Comparison logic ===


class TestCompareLogic:
    def test_high_overlap_detected(self):
        """Idea that closely matches an existing repo should score high overlap."""
        from project_forge.engine.compare import compare_idea_to_repo

        idea = Idea(
            name="PKI Certificate Issuance Engine",
            tagline="Automated X.509 cert issuance with ACME",
            description=(
                "Build a PKI certificate authority that handles X.509 certificate issuance with ACME protocol support."
            ),
            category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
            market_analysis="Growing demand for PKI automation.",
            feasibility_score=0.85,
            mvp_scope="ACME server + cert signing",
            tech_stack=["Rust", "OpenSSL", "ACME"],
        )
        repo_details = {
            "name": "pki-ca-engine",
            "description": "A PKI certificate authority engine with ACME support",
            "topics": ["pki", "cryptography", "acme", "x509"],
            "language": "Rust",
            "readme": (
                "# PKI CA Engine\nA certificate authority for issuing "
                "and managing X.509 certificates with ACME protocol."
            ),
        }

        result = compare_idea_to_repo(idea, repo_details)
        assert result["overlap_score"] >= 0.5
        assert result["verdict"] in ("enhance", "duplicate")
        assert "reason" in result
        assert len(result["reason"]) > 0

    def test_low_overlap_detected(self):
        """Idea unrelated to the repo should score low overlap."""
        from project_forge.engine.compare import compare_idea_to_repo

        idea = Idea(
            name="SSH Honeypot Analytics Dashboard",
            tagline="Visualize attacker patterns from honeypot logs",
            description=(
                "Build a real-time dashboard for analyzing SSH honeypot data, tracking attacker IPs and techniques."
            ),
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Security monitoring market growing.",
            feasibility_score=0.7,
            mvp_scope="Log parser + dashboard",
            tech_stack=["Python", "FastAPI", "D3.js"],
        )
        repo_details = {
            "name": "pki-ca-engine",
            "description": "A PKI certificate authority engine",
            "topics": ["pki", "cryptography", "x509"],
            "language": "Rust",
            "readme": "# PKI CA Engine\nCertificate authority for X.509 certs.",
        }

        result = compare_idea_to_repo(idea, repo_details)
        assert result["overlap_score"] < 0.5
        assert result["verdict"] == "new"

    def test_partial_overlap_suggests_enhance(self):
        """Idea that extends an existing repo should suggest enhancement."""
        from project_forge.engine.compare import compare_idea_to_repo

        idea = Idea(
            name="PKI Certificate Revocation Dashboard",
            tagline="CRL management UI for certificate authorities",
            description="Build a web dashboard for managing certificate revocation lists in PKI infrastructure.",
            category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
            market_analysis="CRL management is pain point.",
            feasibility_score=0.75,
            mvp_scope="CRL viewer + revoke UI",
            tech_stack=["Python", "FastAPI", "React"],
        )
        repo_details = {
            "name": "pki-ca-engine",
            "description": "A PKI certificate authority engine with ACME support",
            "topics": ["pki", "cryptography", "acme", "x509"],
            "language": "Rust",
            "readme": "# PKI CA Engine\nCertificate authority for X.509 cert issuance. No revocation support yet.",
        }

        result = compare_idea_to_repo(idea, repo_details)
        assert result["verdict"] in ("enhance", "new")
        assert 0.0 <= result["overlap_score"] <= 1.0

    def test_compare_returns_matching_keywords(self):
        """Result should include the overlapping keywords found."""
        from project_forge.engine.compare import compare_idea_to_repo

        idea = Idea(
            name="ACME Protocol Tester",
            tagline="Test ACME endpoints",
            description="Tool for testing ACME protocol compliance of certificate authorities.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Compliance testing needed.",
            feasibility_score=0.6,
            mvp_scope="ACME test suite",
            tech_stack=["Go", "ACME"],
        )
        repo_details = {
            "name": "pki-ca-engine",
            "description": "PKI CA with ACME support",
            "topics": ["acme", "pki", "x509"],
            "language": "Rust",
            "readme": "ACME protocol support for certificate issuance.",
        }

        result = compare_idea_to_repo(idea, repo_details)
        assert "matching_keywords" in result
        assert isinstance(result["matching_keywords"], list)
        assert "acme" in [kw.lower() for kw in result["matching_keywords"]]


# === API endpoint ===


@pytest_asyncio.fixture
async def client_with_idea(tmp_path):
    db.db_path = tmp_path / "test_compare.db"
    await db.connect()
    idea = Idea(
        id="dbf66d1bdc61",
        name="Test Compare Idea",
        tagline="Testing comparison",
        description="An idea about PKI certificate management and ACME protocol.",
        category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
        market_analysis="Good market.",
        feasibility_score=0.8,
        mvp_scope="Build it.",
        tech_stack=["Rust", "OpenSSL"],
    )
    await db.save_idea(idea)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, idea
    await db.close()


class TestCompareAPI:
    @pytest.mark.asyncio
    async def test_compare_with_repo_from_list(self, client_with_idea):
        """Full flow: list repos → pick one → compare. Must not 404."""
        client, idea = client_with_idea
        with (
            patch("project_forge.scaffold.github.subprocess.run") as mock_run,
            patch("project_forge.scaffold.github.get_repo_details") as mock_details,
        ):
            # Simulate list_org_repos returning realistic gh output
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "rayketcham-lab/pki-ca-engine\tPKI CA\tpublic\n"
            mock_run.return_value.stderr = ""

            resp = await client.get("/api/repos")
            assert resp.status_code == 200
            repo_name = resp.json()["repos"][0]["name"]

            # Now use that repo_name in compare — this is what the frontend does
            mock_details.return_value = {
                "name": "pki-ca-engine",
                "description": "PKI CA engine",
                "topics": ["pki"],
                "language": "Rust",
                "readme": "A cert authority.",
            }
            resp = await client.post(
                f"/api/ideas/{idea.id}/compare",
                params={"owner": "rayketcham-lab", "repo": repo_name},
            )
            # This must succeed — not 502 from a gh 404
            assert resp.status_code == 200
            data = resp.json()
            assert "overlap_score" in data

    @pytest.mark.asyncio
    async def test_compare_endpoint_exists(self, client_with_idea):
        """POST /api/ideas/{id}/compare should exist and accept repo param."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.get_repo_details") as mock_details:
            mock_details.return_value = {
                "name": "pki-ca-engine",
                "description": "PKI CA engine",
                "topics": ["pki"],
                "language": "Rust",
                "readme": "A cert authority.",
            }
            resp = await client.post(
                f"/api/ideas/{idea.id}/compare",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "overlap_score" in data
            assert "verdict" in data
            assert "reason" in data

    @pytest.mark.asyncio
    async def test_compare_idea_not_found(self, client_with_idea):
        client, _ = client_with_idea
        resp = await client.post(
            "/api/ideas/nonexistent/compare",
            params={"owner": "rayketcham-lab", "repo": "some-repo"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_repos_endpoint(self, client_with_idea):
        """GET /api/repos should return org repos for the dropdown."""
        client, _ = client_with_idea
        with patch("project_forge.scaffold.github.list_org_repos") as mock_list:
            mock_list.return_value = [
                {"name": "pki-ca-engine", "description": "PKI CA", "visibility": "public"},
                {"name": "project-forge", "description": "Forge", "visibility": "public"},
            ]
            resp = await client.get("/api/repos")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["repos"]) == 2
            assert data["repos"][0]["name"] == "pki-ca-engine"


# === Add Idea to Existing Project ===


class TestAddToProject:
    """Tests for adding an idea as a GitHub issue on an existing project."""

    @pytest.mark.asyncio
    async def test_add_to_project_endpoint_exists(self, client_with_idea):
        """POST /api/ideas/{id}/add-to-project should exist and accept repo param."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-ca-engine/issues/42"
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "issue_url" in data
            assert "github.com" in data["issue_url"]

    @pytest.mark.asyncio
    async def test_add_to_project_creates_issue_with_idea_details(self, client_with_idea):
        """The created issue should contain the idea's name, description, and tech stack."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-ca-engine/issues/42"
            await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            mock_create.assert_called_once()
            kw = mock_create.call_args.kwargs
            assert kw["repo"] == "rayketcham-lab/pki-ca-engine"
            assert idea.name in kw["title"]
            assert idea.description in kw["body"]
            assert "Rust" in kw["body"]  # from tech_stack

    @pytest.mark.asyncio
    async def test_add_to_project_returns_issue_url(self, client_with_idea):
        """Response should include the created issue URL."""
        client, idea = client_with_idea
        expected_url = "https://github.com/rayketcham-lab/pki-ca-engine/issues/99"
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = expected_url
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            data = resp.json()
            assert data["issue_url"] == expected_url

    @pytest.mark.asyncio
    async def test_add_to_project_idea_not_found(self, client_with_idea):
        """Should return 404 for nonexistent idea."""
        client, _ = client_with_idea
        resp = await client.post(
            "/api/ideas/nonexistent/add-to-project",
            params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_add_to_project_github_failure(self, client_with_idea):
        """Should return 502 if GitHub issue creation fails."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.side_effect = RuntimeError("gh: not found")
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_add_to_project_ensures_labels_exist(self, client_with_idea):
        """Labels must be created on the target repo before the issue is created."""
        client, idea = client_with_idea
        with (
            patch("project_forge.scaffold.github.create_issue") as mock_create,
            patch("project_forge.scaffold.github.create_label") as mock_label,
        ):
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-client/issues/7"
            await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-client"},
            )
            # create_label must be called for each label BEFORE create_issue
            assert mock_label.call_count == 2
            label_names = [c.kwargs["name"] for c in mock_label.call_args_list]
            assert "project-forge" in label_names
            assert idea.category.value in label_names
            # create_issue must still be called
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_to_project_label_failure_does_not_block_issue(self, client_with_idea):
        """If label creation fails (already exists), issue creation should proceed."""
        client, idea = client_with_idea
        with (
            patch("project_forge.scaffold.github.create_issue") as mock_create,
            patch("project_forge.scaffold.github.create_label") as mock_label,
        ):
            mock_label.side_effect = RuntimeError("label already exists")
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-client/issues/7"
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-client"},
            )
            assert resp.status_code == 200
            mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_to_project_body_has_external_origin_disclaimer(self, client_with_idea):
        """Issue body must warn that this came from an external AI idea generator."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-ca-engine/issues/42"
            await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            body = mock_create.call_args.kwargs["body"]
            # Must have a prominent warning/disclaimer section
            assert "External Idea" in body or "external" in body.lower()
            # Must tell maintainer to critically evaluate fitment
            assert "verify" in body.lower() or "evaluate" in body.lower()
            # Must mention it was AI-generated
            assert "auto" in body.lower() or "AI" in body or "generated" in body.lower()
            # Must ask whether this enhances the project
            assert "enhance" in body.lower() or "fit" in body.lower()

    @pytest.mark.asyncio
    async def test_add_to_project_sets_status_contributed(self, client_with_idea):
        """After adding to a project, idea status should become 'contributed'."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-client/issues/7"
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-client"},
            )
            assert resp.status_code == 200
        # Check status via DB
        updated = await db.get_idea(idea.id)
        assert updated.status == "contributed"

    @pytest.mark.asyncio
    async def test_add_to_project_stores_issue_url_on_idea(self, client_with_idea):
        """After adding, the idea should store the created issue URL."""
        client, idea = client_with_idea
        issue_url = "https://github.com/rayketcham-lab/pki-client/issues/7"
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = issue_url
            await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-client"},
            )
        updated = await db.get_idea(idea.id)
        assert updated.github_issue_url == issue_url

    @pytest.mark.asyncio
    async def test_add_to_project_stores_target_repo_on_idea(self, client_with_idea):
        """After adding, the idea should store the target repo URL."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-client/issues/7"
            await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-client"},
            )
        updated = await db.get_idea(idea.id)
        assert updated.project_repo_url == "https://github.com/rayketcham-lab/pki-client"

    @pytest.mark.asyncio
    async def test_add_to_project_response_includes_status(self, client_with_idea):
        """Response should confirm the new status."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-client/issues/7"
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-client"},
            )
            data = resp.json()
            assert data["status"] == "contributed"

    @pytest.mark.asyncio
    async def test_add_to_project_labels_include_forge(self, client_with_idea):
        """Issue should be labeled with 'project-forge' and the idea category."""
        client, idea = client_with_idea
        with patch("project_forge.scaffold.github.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/pki-ca-engine/issues/42"
            await client.post(
                f"/api/ideas/{idea.id}/add-to-project",
                params={"owner": "rayketcham-lab", "repo": "pki-ca-engine"},
            )
            call_args = mock_create.call_args
            # Labels passed as keyword arg or positional arg
            labels = call_args.kwargs.get("labels", [])
            assert "project-forge" in labels
            assert idea.category.value in labels


# === UI rendering ===


class TestCompareUI:
    @pytest.mark.asyncio
    async def test_idea_detail_has_compare_section(self, client_with_idea):
        """The idea detail page should render a compare-to-repo section."""
        client, idea = client_with_idea
        resp = await client.get(f"/ideas/{idea.id}")
        assert resp.status_code == 200
        html = resp.text
        # Should have a compare section with repo dropdown
        assert "compare-repo" in html or "Compare" in html
        assert "select" in html.lower()  # dropdown element

    @pytest.mark.asyncio
    async def test_idea_detail_has_compare_button(self, client_with_idea):
        """The idea detail page should have a Compare button."""
        client, idea = client_with_idea
        resp = await client.get(f"/ideas/{idea.id}")
        html = resp.text
        assert "compareIdea" in html or "compare-btn" in html

    @pytest.mark.asyncio
    async def test_compare_js_has_add_to_project_function(self, client_with_idea):
        """app.js should have an addToProject function for the enhance/duplicate flow."""
        client, _ = client_with_idea
        resp = await client.get("/static/app.js")
        assert resp.status_code == 200
        assert "addToProject" in resp.text

    @pytest.mark.asyncio
    async def test_idea_detail_has_add_as_issue_button(self, client_with_idea):
        """The idea detail page should have a static 'Add as Issue' button."""
        client, idea = client_with_idea
        resp = await client.get(f"/ideas/{idea.id}")
        html = resp.text
        assert "add-to-project-static-btn" in html
        assert "Add as Issue" in html
