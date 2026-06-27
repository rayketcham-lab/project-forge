// Sniper page interactions. Loaded from /sniper.
// CSP-compliant: no inline scripts, no innerHTML on LLM data.
(function() {
    'use strict';

    var root = document.getElementById('sniper-root');
    var categoryFilter = root ? (root.dataset.categoryFilter || '') : '';

    var btn = document.getElementById('snipe-churn-btn');
    var statusEl = document.getElementById('snipe-churn-status');

    function renderSuccess(idea) {
        statusEl.textContent = '';
        var check = document.createElement('span');
        check.textContent = '🎯 ';
        statusEl.appendChild(check);
        var strong = document.createElement('strong');
        strong.textContent = idea.name;
        statusEl.appendChild(strong);
        if (idea.target_incumbent) {
            var vs = document.createElement('span');
            vs.textContent = ' vs. ' + idea.target_incumbent;
            statusEl.appendChild(vs);
        }
        var meta = document.createElement('span');
        var snipe = (typeof idea.snipe_score === 'number' ? idea.snipe_score : 0).toFixed(2);
        meta.textContent = ' — snipe ' + snipe + ' · ';
        statusEl.appendChild(meta);
        var code = document.createElement('code');
        code.textContent = idea.artifact_type || 'snipe';
        statusEl.appendChild(code);
        statusEl.className = 'churn-status churn-status-success';
    }

    if (btn && statusEl) {
        btn.addEventListener('click', async function() {
            btn.disabled = true;
            btn.classList.add('is-loading');
            statusEl.textContent = 'picking an incumbent + pulling live signal...';
            statusEl.className = 'churn-status churn-status-loading';
            try {
                var headers = Object.assign(
                    {'Content-Type': 'application/json'},
                    (typeof getAuthHeaders === 'function' ? getAuthHeaders() : {})
                );
                var resp = await fetch('/api/churn', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({ lab: 'snipe', category: categoryFilter })
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var data = await resp.json();
                if (data.idea) {
                    renderSuccess(data.idea);
                    setTimeout(function() { window.location.reload(); }, 1800);
                } else {
                    statusEl.textContent = data.message || 'no snipe produced (backend returned empty)';
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
})();
