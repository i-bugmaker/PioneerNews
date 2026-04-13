const API_URL = '/api/news';
const REFRESH_INTERVAL = 3000;
const MAX_VISIBLE_NEWS = 30;

let autoRefreshTimer = null;
let allNews = [];
let isRefreshing = false;
let clockTimer = null;
let hasLoaded = false;

function formatBeijingTime() {
    const now = new Date();
    const utc = now.getTime() + now.getTimezoneOffset() * 60000;
    const bj = new Date(utc + 8 * 3600000);
    const pad = n => String(n).padStart(2, '0');
    const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
    const weekday = weekdays[bj.getDay()];
    return `${bj.getFullYear()}-${pad(bj.getMonth()+1)}-${pad(bj.getDate())} ${weekday} ${pad(bj.getHours())}:${pad(bj.getMinutes())}:${pad(bj.getSeconds())}`;
}

function startClock() {
    if (clockTimer) clearInterval(clockTimer);
    const el = document.getElementById('current-time');
    el.textContent = formatBeijingTime();
    clockTimer = setInterval(() => { el.textContent = formatBeijingTime(); }, 1000);
}

document.addEventListener('DOMContentLoaded', function() {
    startClock();
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

    if (showLoading) {
        loadingEl.classList.add('active');
        containerEl.style.display = 'none';
        errorEl.style.display = 'none';
    }

    try {
        const response = await fetch(API_URL);
        const result = await response.json();

        if (result.success && result.data) {
            const existingKeys = new Set(allNews.map(n => `${n.title.slice(0, 30)}|${n.source}`));
            let hasNew = false;

            for (const news of result.data) {
                const key = `${news.title.slice(0, 30)}|${news.source}`;
                if (!existingKeys.has(key)) {
                    allNews.unshift(news); // 新新闻放头部
                    existingKeys.add(key);
                    hasNew = true;
                }
            }

            allNews.sort((a, b) => b.publish_time.localeCompare(a.publish_time));
            if (allNews.length > MAX_VISIBLE_NEWS) {
                allNews = allNews.slice(0, MAX_VISIBLE_NEWS);
            }

            if (!hasLoaded) {
                // 首次加载：全量渲染
                renderAllNews(allNews);
                hasLoaded = true;
            } else if (hasNew) {
                // 有新数据：重新渲染（避免 DOM 位置错乱）
                renderAllNews(allNews);
            }

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

function renderAllNews(newsList) {
    const containerEl = document.getElementById('news-container');

    if (!newsList || newsList.length === 0) {
        containerEl.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">暂无新闻</p>';
        return;
    }

    // 使用 DocumentFragment 批量操作，减少重排
    const fragment = document.createDocumentFragment();
    for (let i = 0; i < newsList.length; i++) {
        fragment.appendChild(createNewsCard(newsList[i], i));
    }

    // 一次性替换，避免多次 DOM 操作
    containerEl.innerHTML = '';
    containerEl.appendChild(fragment);
}

function createNewsCard(news, index) {
    const card = document.createElement('div');
    card.className = 'news-card';
    card.dataset.newsKey = `${news.title.slice(0, 30)}|${news.source}`;
    card.onclick = () => window.open(news.url || '#', '_blank');
    card.innerHTML = buildCardHTML(news);
    return card;
}

function buildCardHTML(news) {
    const time = formatTime(news.publish_time);
    const source = news.source || '未知来源';
    const intro = news.intro || '暂无摘要';

    return `
        <h3>📰 ${escapeHtml(news.title)}</h3>
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
