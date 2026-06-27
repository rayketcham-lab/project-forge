// Foundry page interactions. Loaded from /foundry.
// CSP-compliant: no inline scripts; DOM built with createElement.
(function () {
    'use strict';

    var candidates = document.getElementById('foundry-candidates');
    var loadingEl = document.getElementById('foundry-plan-loading');
    var errorEl = document.getElementById('foundry-plan-error');
    var contentEl = document.getElementById('foundry-plan-content');
    var regenBtn = document.getElementById('foundry-regen-btn');

    function getHeaders() {
        var h = { 'Content-Type': 'application/json' };
        if (typeof getAuthHeaders === 'function') {
            Object.assign(h, getAuthHeaders());
        }
        return h;
    }

    function showLoading() {
        if (loadingEl) loadingEl.style.display = '';
        if (errorEl) { errorEl.style.display = 'none'; errorEl.textContent = ''; }
        if (contentEl) contentEl.style.opacity = '0.4';
    }

    function hideLoading() {
        if (loadingEl) loadingEl.style.display = 'none';
        if (contentEl) contentEl.style.opacity = '1';
    }

    function showError(msg) {
        hideLoading();
        if (errorEl) {
            errorEl.textContent = 'Error: ' + msg;
            errorEl.style.display = '';
        }
    }

    function renderPlan(plan, ideaId) {
        hideLoading();
        if (!contentEl) return;

        contentEl.textContent = '';

        // Header
        var header = document.createElement('div');
        header.className = 'foundry-plan-header';

        var h2 = document.createElement('h2');
        h2.className = 'foundry-repo-name';
        var code = document.createElement('code');
        code.textContent = plan.repo_name || '';
        h2.appendChild(code);
        header.appendChild(h2);

        var desc = document.createElement('p');
        desc.className = 'foundry-repo-desc';
        desc.textContent = plan.description || '';
        header.appendChild(desc);

        var meta = document.createElement('div');
        meta.className = 'foundry-meta';
        var langBadge = document.createElement('span');
        langBadge.className = 'foundry-lang-badge';
        langBadge.textContent = plan.language || '';
        meta.appendChild(langBadge);
        header.appendChild(meta);

        contentEl.appendChild(header);

        // File tree
        if (plan.file_tree && plan.file_tree.length) {
            var treeSection = document.createElement('div');
            treeSection.className = 'foundry-section';
            var treeH3 = document.createElement('h3');
            treeH3.textContent = 'File Tree';
            treeSection.appendChild(treeH3);
            var ul = document.createElement('ul');
            ul.className = 'foundry-file-tree';
            plan.file_tree.forEach(function (path) {
                var li = document.createElement('li');
                var c = document.createElement('code');
                c.textContent = path;
                li.appendChild(c);
                ul.appendChild(li);
            });
            treeSection.appendChild(ul);
            contentEl.appendChild(treeSection);
        }

        // Starter issues
        if (plan.starter_issues && plan.starter_issues.length) {
            var issSection = document.createElement('div');
            issSection.className = 'foundry-section';
            var issH3 = document.createElement('h3');
            issH3.textContent = 'Starter Issues';
            issSection.appendChild(issH3);
            var ol = document.createElement('ol');
            ol.className = 'foundry-issues';
            plan.starter_issues.forEach(function (issue) {
                var li = document.createElement('li');
                var strong = document.createElement('strong');
                strong.textContent = issue.title || '';
                li.appendChild(strong);
                if (issue.body) {
                    var p = document.createElement('p');
                    p.className = 'foundry-issue-body';
                    p.textContent = issue.body;
                    li.appendChild(p);
                }
                ol.appendChild(li);
            });
            issSection.appendChild(ol);
            contentEl.appendChild(issSection);
        }

        // First steps
        if (plan.first_steps && plan.first_steps.length) {
            var stepsSection = document.createElement('div');
            stepsSection.className = 'foundry-section';
            var stepsH3 = document.createElement('h3');
            stepsH3.textContent = 'First Steps';
            stepsSection.appendChild(stepsH3);
            var stepsOl = document.createElement('ol');
            stepsOl.className = 'foundry-steps';
            plan.first_steps.forEach(function (step) {
                var li = document.createElement('li');
                li.textContent = step;
                stepsOl.appendChild(li);
            });
            stepsSection.appendChild(stepsOl);
            contentEl.appendChild(stepsSection);
        }

        // README preview
        if (plan.readme_md) {
            var readmeSection = document.createElement('div');
            readmeSection.className = 'foundry-section foundry-readme';
            var readmeH3 = document.createElement('h3');
            readmeH3.textContent = 'README Preview';
            readmeSection.appendChild(readmeH3);
            var pre = document.createElement('pre');
            pre.className = 'foundry-readme-pre';
            var codeEl = document.createElement('code');
            codeEl.textContent = plan.readme_md;
            pre.appendChild(codeEl);
            readmeSection.appendChild(pre);
            contentEl.appendChild(readmeSection);
        }

        // Actions
        var actions = document.createElement('div');
        actions.className = 'foundry-actions';

        var detailLink = document.createElement('a');
        detailLink.href = '/ideas/' + ideaId;
        detailLink.className = 'btn btn-outline';
        detailLink.textContent = 'View Full Idea →';
        actions.appendChild(detailLink);

        var newRegenBtn = document.createElement('button');
        newRegenBtn.type = 'button';
        newRegenBtn.id = 'foundry-regen-btn';
        newRegenBtn.className = 'btn btn-outline';
        newRegenBtn.dataset.ideaId = ideaId;
        newRegenBtn.textContent = 'Regenerate Plan';
        newRegenBtn.addEventListener('click', function () { loadPlan(ideaId); });
        actions.appendChild(newRegenBtn);

        var createBtn = document.createElement('button');
        createBtn.type = 'button';
        createBtn.className = 'btn btn-primary';
        createBtn.textContent = '🏗 Create Repo';
        createBtn.addEventListener('click', function () { createRepo(ideaId, createBtn); });
        actions.appendChild(createBtn);

        contentEl.appendChild(actions);
    }

    async function createRepo(ideaId, btn) {
        if (!window.confirm('Create a REAL GitHub repo for this idea? Pushes a starter skeleton and files the issues above.')) return;
        var prev = btn.textContent;
        btn.disabled = true;
        btn.textContent = 'creating repo…';
        try {
            var resp = await fetch('/api/foundry/create/' + ideaId, {
                method: 'POST', headers: getHeaders(), body: JSON.stringify({}),
            });
            var data = await resp.json();
            if (!resp.ok) throw new Error(data.detail || ('HTTP ' + resp.status));
            btn.textContent = '✓ created (' + (data.issues_filed || 0) + ' issues)';
            if (data.repo_url) window.open(data.repo_url, '_blank', 'noopener');
        } catch (e) {
            btn.disabled = false;
            btn.textContent = prev;
            showError('Create failed: ' + e.message);
        }
    }

    async function loadPlan(ideaId) {
        showLoading();
        try {
            var resp = await fetch('/api/foundry/plan/' + ideaId, {
                method: 'POST',
                headers: getHeaders(),
                body: JSON.stringify({}),
            });
            if (!resp.ok) {
                var body = await resp.text();
                throw new Error('HTTP ' + resp.status + ': ' + body);
            }
            var data = await resp.json();
            if (data.plan) {
                renderPlan(data.plan, ideaId);
            } else {
                showError('No plan returned.');
            }
        } catch (e) {
            showError(e.message);
        }
    }

    // Wire up candidate card clicks.
    if (candidates) {
        candidates.addEventListener('click', function (e) {
            var card = e.target.closest('.foundry-card');
            if (!card) return;
            var ideaId = card.dataset.ideaId;
            if (!ideaId) return;

            // Update active state.
            candidates.querySelectorAll('.foundry-card').forEach(function (c) {
                c.classList.remove('foundry-card-active');
                c.setAttribute('aria-pressed', 'false');
            });
            card.classList.add('foundry-card-active');
            card.setAttribute('aria-pressed', 'true');

            loadPlan(ideaId);
        });

        // Keyboard support for card selection.
        candidates.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            var card = e.target.closest('.foundry-card');
            if (card) { e.preventDefault(); card.click(); }
        });
    }

    // Wire up the server-rendered regen button (first load).
    if (regenBtn) {
        regenBtn.addEventListener('click', function () {
            var ideaId = regenBtn.dataset.ideaId;
            if (ideaId) loadPlan(ideaId);
        });
    }
})();
