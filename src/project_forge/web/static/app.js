// === Auth Token ===
// Read Bearer token from server-injected <meta> tag for POST requests
function getAuthHeaders() {
    var meta = document.querySelector('meta[name="api-token"]');
    if (meta && meta.content) {
        return { 'Authorization': 'Bearer ' + meta.content };
    }
    return {};
}

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
            } else if (action === 'challenge-idea') {
                toggleChallengeInput();
            }
        }
    });

    // URL submit button
    var urlBtn = document.getElementById('url-submit-btn');
    if (urlBtn) {
        urlBtn.addEventListener('click', submitUrl);
    }

    // Add as Issue static button
    var addStaticBtn = document.getElementById('add-to-project-static-btn');
    if (addStaticBtn) {
        addStaticBtn.addEventListener('click', function() {
            var ideaId = addStaticBtn.getAttribute('data-idea-id');
            var select = document.getElementById('compare-repo');
            if (select && select.value) {
                addToProject(ideaId, select.value);
            }
        });
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
        const response = await fetch(url, { method, headers: getAuthHeaders() });
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
        var r = await fetch('/api/thinktank/' + ideaId + '/promote', { method: 'POST', headers: getAuthHeaders() });
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
        var r = await fetch('/api/thinktank/' + ideaId + '/reject', { method: 'POST', headers: getAuthHeaders() });
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

// === Challenge ===

var _challengeType = 'freeform';

function toggleChallengeInput() {
    var input = document.getElementById('challenge-input');
    if (!input) return;
    var section = document.getElementById('challenge-section');
    if (section) section.scrollIntoView({ behavior: 'smooth' });
    input.style.display = input.style.display === 'none' ? '' : 'none';
    var textarea = document.getElementById('challenge-question');
    if (textarea && input.style.display !== 'none') textarea.focus();
}

(function() {
    // Challenge type chip selection
    var typeGrid = document.getElementById('challenge-type-grid');
    if (typeGrid) {
        typeGrid.addEventListener('click', function(e) {
            var chip = e.target.closest('.challenge-chip');
            if (!chip) return;
            typeGrid.querySelectorAll('.challenge-chip').forEach(function(c) { c.classList.remove('active'); });
            chip.classList.add('active');
            _challengeType = chip.getAttribute('data-type');
            // Update hint text
            var hints = {
                feasibility: 'Can this actually be built with the proposed stack and timeline?',
                market: 'Is there real demand? Who pays? What\'s the competition?',
                security: 'What are the attack surfaces, compliance gaps, or trust issues?',
                scope: 'Is the MVP too big or too small? What to cut or add?',
                differentiation: 'What makes this different from what already exists?',
                kill: 'Make the strongest case for abandoning this idea.',
                freeform: 'Ask anything about this idea.',
            };
            var hint = document.getElementById('challenge-hint');
            if (hint) hint.textContent = hints[_challengeType] || '';
        });
    }

    var submitBtn = document.getElementById('challenge-submit-btn');
    if (!submitBtn) return;

    submitBtn.addEventListener('click', async function() {
        var textarea = document.getElementById('challenge-question');
        var question = textarea ? textarea.value.trim() : '';
        if (!question) { alert('Please enter a question or concern.'); return; }

        var focusEl = document.getElementById('challenge-focus');
        var toneEl = document.getElementById('challenge-tone');

        var payload = {
            question: question,
            challenge_type: _challengeType,
            focus_area: focusEl ? focusEl.value : 'all',
            tone: toneEl ? toneEl.value : 'skeptical',
        };

        var ideaId = submitBtn.getAttribute('data-idea-id');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Thinking...';

        try {
            var resp = await fetch('/api/ideas/' + ideaId + '/challenge', {
                method: 'POST',
                headers: Object.assign({ 'Content-Type': 'application/json' }, getAuthHeaders()),
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                var err = await resp.json();
                throw new Error(err.detail || 'Challenge failed');
            }
            var data = await resp.json();

            // Remove "no challenges" message
            var noMsg = document.getElementById('no-challenges-msg');
            if (noMsg) noMsg.remove();

            // Append the new challenge to the thread
            var thread = document.getElementById('challenge-thread');
            var entry = document.createElement('div');
            entry.className = 'challenge-entry challenge-entry-new';

            // Header row with type badge, verdict, confidence
            var header = document.createElement('div');
            header.className = 'challenge-entry-header';

            var typeBadge = document.createElement('span');
            typeBadge.className = 'challenge-type-badge challenge-type-' + (data.challenge_type || 'freeform');
            typeBadge.textContent = data.challenge_type || 'freeform';
            header.appendChild(typeBadge);

            if (data.verdict && data.verdict !== 'no_change') {
                var verdict = document.createElement('span');
                verdict.className = 'challenge-verdict challenge-verdict-' + data.verdict;
                verdict.textContent = data.verdict;
                header.appendChild(verdict);
            }

            if (data.confidence) {
                var conf = document.createElement('span');
                conf.className = 'challenge-confidence';
                conf.textContent = Math.round(data.confidence * 100) + '% confidence';
                header.appendChild(conf);
            }

            entry.appendChild(header);

            var qDiv = document.createElement('div');
            qDiv.className = 'challenge-question';
            var qStrong = document.createElement('strong');
            qStrong.textContent = 'Q: ';
            qDiv.appendChild(qStrong);
            qDiv.appendChild(document.createTextNode(data.question));
            entry.appendChild(qDiv);

            var rDiv = document.createElement('div');
            rDiv.className = 'challenge-response';
            rDiv.textContent = data.response;
            entry.appendChild(rDiv);

            if (data.changes && data.changes.length > 0) {
                var changesDiv = document.createElement('div');
                changesDiv.className = 'challenge-changes';
                data.changes.forEach(function(change) {
                    var span = document.createElement('span');
                    if (change.action === 'removed') {
                        span.className = 'change-removed';
                    } else if (change.action === 'added') {
                        span.className = 'change-added';
                    } else {
                        span.className = 'change-modified';
                    }
                    span.textContent = change.field + ': ' + change.text;
                    changesDiv.appendChild(span);
                });
                entry.appendChild(changesDiv);
            }

            thread.appendChild(entry);
            textarea.value = '';
        } catch (err) {
            alert('Error: ' + err.message);
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Submit Challenge';
        }
    });
})();

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
            headers: Object.assign({ 'Content-Type': 'application/json' }, getAuthHeaders()),
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
            var addBtn = document.getElementById('add-to-project-static-btn');
            if (addBtn) addBtn.disabled = !select.value;
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
        var resp = await fetch(url, { method: 'POST', headers: getAuthHeaders() });
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

        // Show "Add as Issue" button for enhance/duplicate verdicts
        if (data.verdict === 'enhance' || data.verdict === 'duplicate') {
            var addBtn = document.createElement('button');
            addBtn.className = 'btn btn-primary';
            addBtn.style.marginTop = '1rem';
            addBtn.textContent = 'Add as Issue to ' + data.repo_name;
            addBtn.id = 'add-to-project-btn';
            addBtn.addEventListener('click', function() {
                addToProject(id, select.value);
            });
            verdictDiv.appendChild(addBtn);
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

async function addToProject(ideaId, repoName) {
    var addBtn = document.getElementById('add-to-project-btn');
    if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = 'Creating issue...';
    }
    try {
        var url = '/api/ideas/' + ideaId + '/add-to-project?repo=' + encodeURIComponent(repoName);
        var resp = await fetch(url, { method: 'POST', headers: getAuthHeaders() });
        if (!resp.ok) {
            var err = await resp.json();
            throw new Error(err.detail || 'Failed to add issue');
        }
        var data = await resp.json();
        if (addBtn) {
            var link = document.createElement('a');
            link.href = data.issue_url;
            link.target = '_blank';
            link.className = 'btn btn-primary';
            link.style.marginTop = '1rem';
            link.textContent = 'View Issue on GitHub';
            addBtn.parentNode.replaceChild(link, addBtn);
        }
    } catch (err) {
        if (addBtn) {
            addBtn.disabled = false;
            addBtn.textContent = 'Add as Issue (retry)';
        }
        alert('Failed: ' + err.message);
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

// === Issue Reporter ===

(function() {
    var fab = document.getElementById('issue-reporter-fab');
    var modal = document.getElementById('issue-reporter-modal');
    var closeBtn = document.getElementById('issue-modal-close');
    var nextBtn = document.getElementById('issue-next-btn');
    var backBtn = document.getElementById('issue-back-btn');
    var typeList = document.getElementById('issue-type-list');
    var step1 = document.getElementById('issue-step-1');
    var step2 = document.getElementById('issue-step-2');
    var stepResult = document.getElementById('issue-step-result');

    if (!fab || !modal) return;

    var currentStep = 1;
    var selectedType = null;
    var issueTypes = [];
    var colorMap = { red: '#ef4444', amber: '#f59e0b', blue: '#6366f1', green: '#22c55e', gray: '#6b7280' };

    // Load issue types
    fetch('/api/issues/types').then(function(r) { return r.json(); }).then(function(types) {
        issueTypes = types;
        while (typeList.firstChild) typeList.removeChild(typeList.firstChild);
        types.forEach(function(t) {
            var btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'issue-type-btn';
            btn.setAttribute('data-type-id', t.id);

            var dot = document.createElement('span');
            dot.className = 'issue-type-dot';
            dot.style.background = colorMap[t.color] || '#6b7280';
            btn.appendChild(dot);

            var textDiv = document.createElement('div');
            var labelSpan = document.createElement('div');
            labelSpan.className = 'issue-type-label';
            labelSpan.textContent = t.label;
            textDiv.appendChild(labelSpan);
            var descSpan = document.createElement('div');
            descSpan.className = 'issue-type-desc';
            descSpan.textContent = t.description;
            textDiv.appendChild(descSpan);
            btn.appendChild(textDiv);

            btn.addEventListener('click', function() {
                typeList.querySelectorAll('.issue-type-btn').forEach(function(b) { b.classList.remove('selected'); });
                btn.classList.add('selected');
                selectedType = t.id;
                nextBtn.disabled = false;
                nextBtn.textContent = 'Next';
            });

            typeList.appendChild(btn);
        });
    });

    function showStep(step) {
        currentStep = step;
        step1.style.display = step === 1 ? '' : 'none';
        step2.style.display = step === 2 ? '' : 'none';
        stepResult.style.display = step === 3 ? '' : 'none';
        backBtn.style.display = step > 1 && step < 3 ? '' : 'none';
        if (step === 1) {
            nextBtn.textContent = selectedType ? 'Next' : 'Select a type';
            nextBtn.disabled = !selectedType;
        } else if (step === 2) {
            nextBtn.textContent = 'Submit';
            nextBtn.disabled = false;
        } else {
            nextBtn.style.display = 'none';
            backBtn.style.display = 'none';
        }
    }

    function resetModal() {
        selectedType = null;
        currentStep = 1;
        var desc = document.getElementById('issue-description');
        var sev = document.getElementById('issue-severity');
        var exp = document.getElementById('issue-expected');
        if (desc) desc.value = '';
        if (sev) sev.value = 'medium';
        if (exp) exp.value = '';
        typeList.querySelectorAll('.issue-type-btn').forEach(function(b) { b.classList.remove('selected'); });
        nextBtn.style.display = '';
        showStep(1);
    }

    fab.addEventListener('click', function() {
        resetModal();
        modal.style.display = '';
    });

    closeBtn.addEventListener('click', function() {
        modal.style.display = 'none';
    });

    modal.addEventListener('click', function(e) {
        if (e.target === modal) modal.style.display = 'none';
    });

    backBtn.addEventListener('click', function() {
        if (currentStep === 2) showStep(1);
    });

    nextBtn.addEventListener('click', async function() {
        if (currentStep === 1 && selectedType) {
            showStep(2);
        } else if (currentStep === 2) {
            var desc = document.getElementById('issue-description').value.trim();
            if (desc.length < 5) { alert('Please enter at least 5 characters.'); return; }

            nextBtn.disabled = true;
            nextBtn.textContent = 'Submitting...';

            var payload = {
                issue_type: selectedType,
                description: desc,
                page_url: window.location.pathname,
                severity: document.getElementById('issue-severity').value,
            };
            var exp = document.getElementById('issue-expected').value.trim();
            if (exp) payload.expected_behavior = exp;

            try {
                var resp = await fetch('/api/issues/report', {
                    method: 'POST',
                    headers: Object.assign({ 'Content-Type': 'application/json' }, getAuthHeaders()),
                    body: JSON.stringify(payload),
                });
                var data = await resp.json();
                var resultDiv = document.getElementById('issue-result-content');
                while (resultDiv.firstChild) resultDiv.removeChild(resultDiv.firstChild);

                if (resp.ok && data.success) {
                    var h = document.createElement('h4');
                    h.className = 'issue-result-success';
                    h.textContent = 'Issue created!';
                    resultDiv.appendChild(h);
                    var link = document.createElement('a');
                    link.href = data.issue_url;
                    link.target = '_blank';
                    link.textContent = 'View on GitHub';
                    link.className = 'btn btn-primary btn-sm';
                    link.style.marginTop = '0.75rem';
                    link.style.display = 'inline-block';
                    resultDiv.appendChild(link);
                } else {
                    var msg = document.createElement('p');
                    msg.className = 'issue-result-error';
                    msg.textContent = data.error || data.detail || 'Failed to create issue';
                    resultDiv.appendChild(msg);
                }
                showStep(3);
            } catch (err) {
                alert('Request failed: ' + err.message);
                nextBtn.disabled = false;
                nextBtn.textContent = 'Submit';
            }
        }
    });
})();
