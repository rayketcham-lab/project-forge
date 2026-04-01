// === Tab Switching ===

// Delegated click handler — replaces all onclick= attributes in templates
document.addEventListener('DOMContentLoaded', function() {
    document.addEventListener('click', function(e) {
        // Tab switching: any [data-tab] inside a .tab-bar
        var tabBtn = e.target.closest('.tab-bar [data-tab]');
        if (tabBtn) {
            e.preventDefault();
            switchTab(tabBtn.getAttribute('data-tab'));
            return;
        }

        // Stat card tab links: [data-tab] outside .tab-bar (e.g. super-ideas stat card)
        var statTab = e.target.closest('a[data-tab]');
        if (statTab) {
            e.preventDefault();
            switchTab(statTab.getAttribute('data-tab'));
            return;
        }

        // Idea detail and thinktank action buttons
        var actionBtn = e.target.closest('[data-action]');
        if (actionBtn) {
            var action = actionBtn.getAttribute('data-action');
            var ideaId = actionBtn.getAttribute('data-idea-id');
            if (action === 'approve-idea') {
                approveIdea(ideaId);
            } else if (action === 'reject-idea') {
                rejectIdea(ideaId);
            } else if (action === 'scaffold-idea') {
                scaffoldIdea(ideaId);
            } else if (action === 'compare-idea') {
                compareIdea(ideaId);
            } else if (action === 'promote-proposal') {
                promoteProposal(ideaId);
            } else if (action === 'reject-proposal') {
                rejectProposal(ideaId);
            }
        }
    });

    // URL submit button
    var urlBtn = document.getElementById('url-submit-btn');
    if (urlBtn) {
        urlBtn.addEventListener('click', submitUrl);
    }
});

function switchTab(tabName) {
    // Deactivate all tabs and panels
    document.querySelectorAll('.tab-btn').forEach(function(btn) {
        btn.classList.remove('active');
    });
    document.querySelectorAll('.tab-panel').forEach(function(panel) {
        panel.classList.remove('active');
    });
    // Activate selected tab and panel
    var btn = document.querySelector('[data-tab="' + tabName + '"]');
    var panel = document.getElementById('tab-' + tabName);
    if (btn) btn.classList.add('active');
    if (panel) panel.classList.add('active');
}

async function apiAction(url, method = 'POST') {
    try {
        const response = await fetch(url, { method });
        if (!response.ok) {
            const data = await response.json();
            alert(data.detail || 'Action failed');
            return null;
        }
        return await response.json();
    } catch (err) {
        alert('Request failed: ' + err.message);
        return null;
    }
}

async function approveIdea(id) {
    const result = await apiAction('/ideas/' + id + '/approve');
    if (result) location.reload();
}

async function rejectIdea(id) {
    const result = await apiAction('/ideas/' + id + '/reject');
    if (result) location.reload();
}

// === Think Tank Proposal Actions ===

async function promoteProposal(ideaId) {
    if (!confirm('Promote this proposal to a GitHub issue?')) return;
    try {
        var r = await fetch('/api/thinktank/' + ideaId + '/promote', { method: 'POST' });
        var data = await r.json();
        if (data.issue_url) {
            location.reload();
        } else {
            alert('Failed to promote: ' + (data.detail || 'Unknown error'));
        }
    } catch (err) {
        alert('Error: ' + err);
    }
}

async function rejectProposal(ideaId) {
    if (!confirm('Reject this proposal?')) return;
    try {
        var r = await fetch('/api/thinktank/' + ideaId + '/reject', { method: 'POST' });
        await r.json();
        location.reload();
    } catch (err) {
        alert('Error: ' + err);
    }
}

async function scaffoldIdea(id) {
    const ownerEl = document.getElementById('scaffold-owner');
    const visEl = document.getElementById('scaffold-visibility');
    const btn = document.getElementById('scaffold-btn');

    const owner = ownerEl ? ownerEl.value : 'rayketcham-lab';
    const visibility = visEl ? visEl.value : 'public';

    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Creating repo...';
    }

    const url = '/ideas/' + id + '/scaffold?owner=' + encodeURIComponent(owner) + '&visibility=' + encodeURIComponent(visibility);
    const result = await apiAction(url);

    if (result && result.repo_url) {
        if (btn) btn.textContent = 'Created!';
        setTimeout(() => location.reload(), 1000);
    } else if (btn) {
        btn.disabled = false;
        btn.textContent = 'Create on GitHub';
    }
}

// === URL Ingestion ===

async function submitUrl() {
    var input = document.getElementById('url-input');
    var catSelect = document.getElementById('url-category');
    var btn = document.getElementById('url-submit-btn');
    var resultDiv = document.getElementById('url-result');

    if (!input || !input.value.trim()) {
        alert('Please enter a URL');
        return;
    }

    var body = { url: input.value.trim() };
    if (catSelect && catSelect.value) {
        body.category = catSelect.value;
    }

    btn.disabled = true;
    btn.textContent = 'Generating...';
    resultDiv.style.display = 'none';

    try {
        var resp = await fetch('/api/ideas/from-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || 'Failed to generate idea');
        }

        var idea = await resp.json();

        // Show success result
        while (resultDiv.firstChild) resultDiv.removeChild(resultDiv.firstChild);

        var successDiv = document.createElement('div');
        successDiv.className = 'url-result-success';

        var heading = document.createElement('h3');
        heading.textContent = idea.name;
        successDiv.appendChild(heading);

        var tagline = document.createElement('p');
        tagline.textContent = idea.tagline;
        successDiv.appendChild(tagline);

        var meta = document.createElement('div');
        meta.className = 'url-result-meta';

        var scoreBadge = document.createElement('span');
        scoreBadge.className = 'score-pill';
        scoreBadge.textContent = Math.round(idea.feasibility_score * 100) + '%';
        meta.appendChild(scoreBadge);

        var catBadge = document.createElement('span');
        catBadge.className = 'badge';
        catBadge.textContent = idea.category;
        meta.appendChild(catBadge);

        successDiv.appendChild(meta);

        var viewLink = document.createElement('a');
        viewLink.href = '/ideas/' + idea.id;
        viewLink.className = 'btn btn-primary btn-sm';
        viewLink.textContent = 'View Idea';
        successDiv.appendChild(viewLink);

        resultDiv.appendChild(successDiv);
        resultDiv.style.display = 'block';
        input.value = '';
    } catch (err) {
        while (resultDiv.firstChild) resultDiv.removeChild(resultDiv.firstChild);
        var errP = document.createElement('p');
        errP.style.color = '#e74c3c';
        errP.textContent = 'Error: ' + err.message;
        resultDiv.appendChild(errP);
        resultDiv.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate Idea';
    }
}

// === Compare to GitHub Project ===

async function loadRepos() {
    const select = document.getElementById('compare-repo');
    if (!select) return;
    try {
        const resp = await fetch('/api/repos');
        if (!resp.ok) throw new Error('Failed to load repos');
        const data = await resp.json();
        // Clear and rebuild select options safely
        while (select.firstChild) select.removeChild(select.firstChild);
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = '-- Select a repo --';
        select.appendChild(placeholder);
        for (const repo of data.repos) {
            const opt = document.createElement('option');
            opt.value = repo.name;
            opt.textContent = repo.name + (repo.description ? ' - ' + repo.description : '');
            select.appendChild(opt);
        }
        select.addEventListener('change', function() {
            var btn = document.getElementById('compare-btn');
            if (btn) btn.disabled = !select.value;
        });
    } catch (err) {
        while (select.firstChild) select.removeChild(select.firstChild);
        var errOpt = document.createElement('option');
        errOpt.value = '';
        errOpt.textContent = 'Failed to load repos';
        select.appendChild(errOpt);
    }
}

async function compareIdea(id) {
    var select = document.getElementById('compare-repo');
    var btn = document.getElementById('compare-btn');
    var resultsDiv = document.getElementById('compare-results');
    var verdictDiv = document.getElementById('compare-verdict');

    if (!select || !select.value) return;

    btn.disabled = true;
    btn.textContent = 'Comparing...';

    try {
        var url = '/api/ideas/' + id + '/compare?repo=' + encodeURIComponent(select.value);
        var resp = await fetch(url, { method: 'POST' });
        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || 'Compare failed');
        }
        var data = await resp.json();

        var scorePercent = Math.round(data.overlap_score * 100);
        var verdictColors = { duplicate: '#e74c3c', enhance: '#f39c12', new: '#27ae60' };
        var verdictLabels = { duplicate: 'Likely Duplicate', enhance: 'Could Enhance', new: 'New Idea' };
        var color = verdictColors[data.verdict] || '#666';
        var label = verdictLabels[data.verdict] || data.verdict;

        // Build result display using safe DOM methods
        while (verdictDiv.firstChild) verdictDiv.removeChild(verdictDiv.firstChild);

        var headerRow = document.createElement('div');
        headerRow.style.cssText = 'display:flex;align-items:center;gap:1rem;margin-bottom:0.75rem';

        var scoreSpan = document.createElement('span');
        scoreSpan.style.cssText = 'font-size:2rem;font-weight:700;color:' + color;
        scoreSpan.textContent = scorePercent + '%';
        headerRow.appendChild(scoreSpan);

        var labelSpan = document.createElement('span');
        labelSpan.style.cssText = 'font-size:1.2rem;padding:0.25rem 0.75rem;border-radius:4px;color:#fff;font-weight:600;background:' + color;
        labelSpan.textContent = label;
        headerRow.appendChild(labelSpan);

        verdictDiv.appendChild(headerRow);

        var reasonP = document.createElement('p');
        reasonP.style.margin = '0.5rem 0';
        reasonP.textContent = data.reason;
        verdictDiv.appendChild(reasonP);

        if (data.matching_keywords && data.matching_keywords.length > 0) {
            var kwP = document.createElement('p');
            kwP.style.marginTop = '0.5rem';
            var strong = document.createElement('strong');
            strong.textContent = 'Matching keywords: ';
            kwP.appendChild(strong);
            kwP.appendChild(document.createTextNode(data.matching_keywords.join(', ')));
            verdictDiv.appendChild(kwP);
        }

        resultsDiv.style.display = 'block';
    } catch (err) {
        while (verdictDiv.firstChild) verdictDiv.removeChild(verdictDiv.firstChild);
        var errP = document.createElement('p');
        errP.style.color = '#e74c3c';
        errP.textContent = 'Error: ' + err.message;
        verdictDiv.appendChild(errP);
        resultsDiv.style.display = 'block';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Compare';
    }
}

// Load repos dropdown on idea detail pages
if (document.getElementById('compare-repo')) {
    loadRepos();
}

// Auto-refresh stats on dashboard
if (window.location.pathname === '/') {
    setInterval(async () => {
        try {
            const resp = await fetch('/api/stats');
            if (resp.ok) {
                const stats = await resp.json();
                const numbers = document.querySelectorAll('.stat-number');
                if (numbers.length >= 5) {
                    numbers[0].textContent = stats.total_ideas;
                    numbers[1].textContent = stats.ideas_by_status.approved || 0;
                    numbers[2].textContent = stats.ideas_by_status.scaffolded || 0;
                    numbers[3].textContent = stats.super_ideas;
                    numbers[4].textContent = stats.avg_feasibility_score;
                }
            }
        } catch (e) { /* ignore */ }
    }, 30000);
}

// Smooth scroll for anchor links
document.querySelectorAll('a[href^="#"]').forEach(a => {
    a.addEventListener('click', e => {
        e.preventDefault();
        document.querySelector(a.getAttribute('href'))?.scrollIntoView({ behavior: 'smooth' });
    });
});
