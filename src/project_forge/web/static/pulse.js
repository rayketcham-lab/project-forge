// Pulse page interactions. Loaded from /pulse.
// CSP-compliant: no inline scripts, no innerHTML on untrusted data.
(function () {
    'use strict';

    var btn = document.getElementById('pulse-churn-btn');
    var statusEl = document.getElementById('pulse-churn-status');

    function renderSuccess(idea) {
        statusEl.textContent = '';

        var icon = document.createElement('span');
        icon.textContent = '⚡ ';
        statusEl.appendChild(icon);

        var strong = document.createElement('strong');
        strong.textContent = idea.name;
        statusEl.appendChild(strong);

        var meta = document.createElement('span');
        var feas = (typeof idea.feasibility_score === 'number'
            ? idea.feasibility_score : 0).toFixed(2);
        meta.textContent = ' — ' + (idea.category || '') + ' · feasibility ' + feas;
        statusEl.appendChild(meta);

        statusEl.className = 'churn-status churn-status-success';
    }

    if (btn && statusEl) {
        btn.addEventListener('click', async function () {
            btn.disabled = true;
            btn.classList.add('is-loading');
            statusEl.textContent = 'fetching live signals and generating…';
            statusEl.className = 'churn-status churn-status-loading';

            try {
                var headers = Object.assign(
                    { 'Content-Type': 'application/json' },
                    (typeof getAuthHeaders === 'function' ? getAuthHeaders() : {})
                );
                var resp = await fetch('/api/pulse/churn', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({})
                });
                if (!resp.ok) { throw new Error('HTTP ' + resp.status); }
                var data = await resp.json();
                if (data.idea) {
                    renderSuccess(data.idea);
                    setTimeout(function () { window.location.reload(); }, 1800);
                } else {
                    statusEl.textContent = data.message || 'no idea produced (backend returned empty)';
                    statusEl.className = 'churn-status churn-status-error';
                    btn.disabled = false;
                    btn.classList.remove('is-loading');
                }
            } catch (e) {
                statusEl.textContent = 'error: ' + e.message;
                statusEl.className = 'churn-status churn-status-error';
                btn.disabled = false;
                btn.classList.remove('is-loading');
            }
        });
    }
}());
