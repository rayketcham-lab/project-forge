// Forge Mechanic review panel. Loaded from /mechanic.
// The operator's gate: approve (merge) or reject (close) autonomous PRs.
// CSP-compliant: no inline scripts, no innerHTML on server data.
(function () {
    'use strict';

    function headers() {
        return Object.assign(
            { 'Content-Type': 'application/json' },
            typeof getAuthHeaders === 'function' ? getAuthHeaders() : {}
        );
    }

    async function act(number, action, btn, verb) {
        var prev = btn.textContent;
        btn.disabled = true;
        btn.textContent = verb + '...';
        try {
            var resp = await fetch('/api/mechanic/prs/' + encodeURIComponent(number) + '/' + action, {
                method: 'POST',
                headers: headers(),
            });
            if (!resp.ok) {
                var err = await resp.json().catch(function () { return {}; });
                throw new Error(err.detail || ('HTTP ' + resp.status));
            }
            btn.textContent = '✓ ' + verb + 'd';
            var card = btn.closest('.moneybot-card');
            if (card) { card.style.opacity = '0.5'; }
            setTimeout(function () { window.location.reload(); }, 900);
        } catch (e) {
            btn.textContent = 'failed: ' + e.message;
            setTimeout(function () { btn.textContent = prev; btn.disabled = false; }, 3000);
        }
    }

    // === Run mechanic now (validation / on-demand) ===
    var runBtn = document.getElementById('mechanic-run-btn');
    var runStatus = document.getElementById('mechanic-run-status');
    if (runBtn && runStatus) {
        runBtn.addEventListener('click', async function () {
            if (!confirm('Run one mechanic cycle now? It implements the top Think Tank item on your subscription and opens a PR here for review.')) return;
            runBtn.disabled = true;
            runStatus.textContent = 'launching mechanic...';
            runStatus.className = 'churn-status churn-status-loading';
            try {
                var resp = await fetch('/api/mechanic/run', { method: 'POST', headers: headers() });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                runStatus.textContent = 'mechanic started — a PR will appear here when it finishes (may take a few minutes). Refresh to check.';
                runStatus.className = 'churn-status churn-status-success';
            } catch (e) {
                runStatus.textContent = 'failed: ' + e.message;
                runStatus.className = 'churn-status churn-status-error';
            } finally {
                runBtn.disabled = false;
            }
        });
    }

    document.querySelectorAll('.mechanic-approve').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var n = btn.getAttribute('data-pr-number');
            if (!n) return;
            if (!confirm('Squash-merge PR #' + n + ' to main? This ships the change.')) return;
            act(n, 'approve', btn, 'merge');
        });
    });

    document.querySelectorAll('.mechanic-reject').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var n = btn.getAttribute('data-pr-number');
            if (!n) return;
            if (!confirm('Reject and close PR #' + n + '?')) return;
            act(n, 'reject', btn, 'reject');
        });
    });
})();
