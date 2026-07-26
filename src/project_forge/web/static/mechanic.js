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
