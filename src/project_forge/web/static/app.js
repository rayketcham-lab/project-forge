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

// Auto-refresh stats on dashboard
if (window.location.pathname === '/') {
    setInterval(async () => {
        try {
            const resp = await fetch('/api/stats');
            if (resp.ok) {
                const stats = await resp.json();
                const numbers = document.querySelectorAll('.stat-number');
                if (numbers.length >= 4) {
                    numbers[0].textContent = stats.total_ideas;
                    numbers[1].textContent = stats.ideas_by_status.approved || 0;
                    numbers[2].textContent = stats.ideas_by_status.scaffolded || 0;
                    numbers[3].textContent = stats.avg_feasibility_score;
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
