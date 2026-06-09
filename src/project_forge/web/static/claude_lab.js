// Claude Lab interactions: Churn a Frontier Idea button.
// CSP-clean (no inline scripts, no innerHTML on LLM output).
(function() {
    'use strict';

    var root = document.getElementById('claude-lab-root');
    var categoryFilter = root ? (root.dataset.categoryFilter || '') : '';

    var btn = document.getElementById('lab-churn-btn');
    var statusEl = document.getElementById('lab-churn-status');
    if (!btn || !statusEl) return;

    function renderSuccess(idea) {
        statusEl.textContent = '';
        var spark = document.createElement('span');
        spark.textContent = '⚡ ';
        statusEl.appendChild(spark);
        var strong = document.createElement('strong');
        strong.textContent = idea.name;
        statusEl.appendChild(strong);
        var ambition = (typeof idea.ambition_score === 'number') ? idea.ambition_score : null;
        var meta = document.createElement('span');
        var ambitionText = (ambition === null) ? '' : (' — ambition ' + ambition.toFixed(2));
        meta.textContent = ambitionText;
        statusEl.appendChild(meta);
        // Artifact type badge — what shape did Churn produce
        if (idea.artifact_type) {
            var sep1 = document.createElement('span');
            sep1.textContent = ' · ';
            statusEl.appendChild(sep1);
            var artifact = document.createElement('code');
            artifact.textContent = idea.artifact_type;
            statusEl.appendChild(artifact);
        }
        var sep2 = document.createElement('span');
        sep2.textContent = ' · mode ';
        statusEl.appendChild(sep2);
        var modeCode = document.createElement('code');
        modeCode.textContent = idea.generation_mode || 'template';
        statusEl.appendChild(modeCode);
        statusEl.className = 'churn-status churn-status-success';
    }

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
                body: JSON.stringify({ lab: 'claude', category: categoryFilter })
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
})();
