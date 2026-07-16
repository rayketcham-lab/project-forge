// Crypto / Web3 board interactions. Loaded from /crypto.
// Mirrors money_bots.js but churns with lab='crypto' so generation +
// fundability scoring stay scoped to the crypto categories.
// CSP-compliant: no inline scripts, no innerHTML on LLM data.
(function() {
    'use strict';

    var rootEl = document.getElementById('crypto-root');
    var categoryFilter = rootEl ? (rootEl.dataset.categoryFilter || '') : '';

    // === Churn Now button ===
    var btn = document.getElementById('churn-btn');
    var statusEl = document.getElementById('churn-status');

    function renderSuccess(idea) {
        statusEl.textContent = '';
        var check = document.createElement('span');
        check.textContent = '✓ ';
        statusEl.appendChild(check);
        var strong = document.createElement('strong');
        strong.textContent = idea.name;
        statusEl.appendChild(strong);
        var meta = document.createElement('span');
        var fund = (typeof idea.fundability_score === 'number' ? idea.fundability_score : 0).toFixed(2);
        meta.textContent = ' — fund ' + fund + ' · mode ';
        statusEl.appendChild(meta);
        var code = document.createElement('code');
        code.textContent = idea.generation_mode || 'template';
        statusEl.appendChild(code);
        statusEl.className = 'churn-status churn-status-success';
    }

    if (btn && statusEl) {
        btn.addEventListener('click', async function() {
            btn.disabled = true;
            btn.classList.add('is-loading');
            statusEl.textContent = 'asking the engine...';
            statusEl.className = 'churn-status churn-status-loading';
            try {
                var headers = Object.assign(
                    {'Content-Type': 'application/json'},
                    (typeof getAuthHeaders === 'function' ? getAuthHeaders() : {})
                );
                var resp = await fetch('/api/churn', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({ lab: 'crypto', category: categoryFilter })
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                if (data.idea) {
                    renderSuccess(data.idea);
                    setTimeout(function() { window.location.reload(); }, 1500);
                } else {
                    statusEl.textContent = data.message || 'no idea produced (backend returned empty)';
                    statusEl.className = 'churn-status churn-status-error';
                }
            } catch (e) {
                statusEl.textContent = 'failed: ' + e.message;
                statusEl.className = 'churn-status churn-status-error';
            } finally {
                btn.disabled = false;
                btn.classList.remove('is-loading');
            }
        });
    }

    // === Per-card Promote → GitHub issue (human-initiated) ===
    document.querySelectorAll('.promote-btn').forEach(function(promoteBtn) {
        promoteBtn.addEventListener('click', async function(ev) {
            ev.preventDefault();
            ev.stopPropagation();
            var ideaId = promoteBtn.getAttribute('data-idea-id');
            if (!ideaId) return;
            if (!confirm('File a GitHub issue with the full MVP spec for this idea?')) return;
            promoteBtn.disabled = true;
            var prevLabel = promoteBtn.textContent;
            promoteBtn.textContent = 'promoting...';
            try {
                var headers = Object.assign(
                    {'Content-Type': 'application/json'},
                    (typeof getAuthHeaders === 'function' ? getAuthHeaders() : {})
                );
                var resp = await fetch('/api/promote/' + encodeURIComponent(ideaId), {
                    method: 'POST', headers: headers,
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                if (data.issue_url) {
                    promoteBtn.textContent = '✓ promoted — reloading';
                    setTimeout(function() { window.location.reload(); }, 800);
                } else {
                    promoteBtn.textContent = 'no issue created';
                    promoteBtn.disabled = false;
                }
            } catch (e) {
                promoteBtn.textContent = 'failed: ' + e.message;
                setTimeout(function() {
                    promoteBtn.textContent = prevLabel;
                    promoteBtn.disabled = false;
                }, 2500);
            }
        });
    });
})();
