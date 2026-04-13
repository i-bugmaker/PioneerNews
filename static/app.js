const API_URL = '/api/news';
const REFRESH_INTERVAL = 3000;
const MAX_VISIBLE_NEWS = 30;

let autoRefreshTimer = null;
let previousNewsKeys = new Set();
let isRefreshing = false;

document.addEventListener('DOMContentLoaded', function() {
    loadNews(true);
    startAutoRefresh();
});

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => loadNews(false), REFRESH_INTERVAL);
}

async function loadNews(showLoading = true) {
    if (isRefreshing) return;
    isRefreshing = true;

    const loadingEl = document.getElementById('loading');
    const containerEl = document.getElementById('news-container');
    const errorEl = document.getElementById('error-message');
    const updateTimeEl = document.getElementById('update-time');

    if (showLoading) {
        loadingEl.classList.add('active');
        containerEl.style.display = 'none';
        errorEl.style.display = 'none';
    }

    try {
        const response = await fetch(API_URL);
        const result = await response.json();

        if (result.success) {
            const newsKeys = new Set(result.data.map(n => `${n.title.slice(0, 30)}|${n.source}`));
            const hasNewNews = previousNewsKeys.size > 0 &&
                               result.data.some(n => !previousNewsKeys.has(`${n.title.slice(0, 30)}|${n.source}`));

            renderNews(result.data.slice(0, MAX_VISIBLE_NEWS), hasNewNews);
            previousNewsKeys = newsKeys;

            updateTimeEl.textContent = `更新时间：${result.update_time}`;

            errorEl.style.display = 'none';
            containerEl.style.display = 'grid';
        } else {
            handleError(result.message || '获取新闻失败');
        }
    } catch (error) {
        console.error('加载新闻失败:', error);
        handleError('网络错误');
    } finally {
        if (showLoading) loadingEl.classList.remove('active');
        isRefreshing = false;
    }
}

function handleError(message) {
    const errorEl = document.getElementById('error-message');
    const containerEl = document.getElementById('news-container');
    errorEl.style.display = 'block';
    errorEl.querySelector('p').textContent = `⚠️ ${message}`;
    containerEl.style.display = 'none';
}

function renderNews(newsList, hasNewNews = false) {
    const containerEl = document.getElementById('news-container');

    if (!newsList || newsList.length === 0) {
        containerEl.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">暂无新闻</p>';
        return;
    }

    const existingCards = containerEl.children;
    const existingCount = existingCards.length;
    const newCount = newsList.length;

    for (let i = 0; i < newCount; i++) {
        const news = newsList[i];
        const key = `${news.title.slice(0, 30)}|${news.source}`;
        const isNew = hasNewNews && !previousNewsKeys.has(key);

        if (i < existingCount) {
            const card = existingCards[i];
            if (card.dataset.newsKey === key) continue;
            updateCard(card, news, isNew, i);
        } else {
            containerEl.appendChild(createNewsCard(news, isNew, i));
        }
    }

    while (containerEl.children.length > newCount) {
        containerEl.removeChild(containerEl.lastChild);
    }
}

function createNewsCard(news, isNew, index) {
    const card = document.createElement('div');
    card.className = `news-card ${isNew ? 'news-new' : ''}`;
    card.dataset.newsKey = `${news.title.slice(0, 30)}|${news.source}`;
    card.style.animationDelay = `${index * 0.03}s`;
    card.onclick = () => window.open(news.url || '#', '_blank');
    card.innerHTML = buildCardHTML(news, isNew);
    return card;
}

function updateCard(card, news, isNew, index) {
    card.className = `news-card ${isNew ? 'news-new' : ''}`;
    card.dataset.newsKey = `${news.title.slice(0, 30)}|${news.source}`;
    card.style.animationDelay = `${index * 0.03}s`;
    card.onclick = () => window.open(news.url || '#', '_blank');
    card.innerHTML = buildCardHTML(news, isNew);
}

function buildCardHTML(news, isNew) {
    const time = formatTime(news.publish_time);
    const source = news.source || '未知来源';
    const intro = news.intro || '暂无摘要';
    const badge = isNew ? '<span class="new-badge">新增 </span>' : '';

    return `
        <h3>📰 ${badge}${escapeHtml(news.title)}</h3>
        <div class="meta">
            <span class="source-tag">${escapeHtml(source)}</span>
            <span>🕐 ${time}</span>
        </div>
        <p class="intro">${escapeHtml(intro)}</p>
    `;
}

function formatTime(timeStr) {
    if (!timeStr) return '--';
    try {
        const date = new Date(timeStr);
        if (isNaN(date.getTime())) return timeStr;
        return date.toLocaleString('zh-CN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
        });
    } catch (e) {
        return timeStr;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

window.addEventListener('visibilitychange', function() {
    if (document.hidden) {
        if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
    } else {
        startAutoRefresh();
        loadNews(false);
    }
});
