const API_URL = '/api/news';
const REFRESH_INTERVAL = 3000;
const SOURCE_COLORS = {"新浪财经":"#E63B2E","财联社":"#DC2626","同花顺":"#F59E0B","东方财富":"#FF6600"};

let autoRefreshTimer = null;
let currentPage = 1;
let pageSize = 10;
let totalNews = 0;
let isRefreshing = false;
let clockTimer = null;
let hasLoaded = false;

function formatBeijingTime() {
    const now = new Date();
    const utc = now.getTime() + now.getTimezoneOffset() * 60000;
    const bj = new Date(utc + 8 * 3600000);
    const pad = n => String(n).padStart(2, '0');
    const weekdays = ['星期日','星期一','星期二','星期三','星期四','星期五','星期六'];
    return `${bj.getFullYear()}-${pad(bj.getMonth()+1)}-${pad(bj.getDate())} ${weekdays[bj.getDay()]} ${pad(bj.getHours())}:${pad(bj.getMinutes())}:${pad(bj.getSeconds())}`;
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

    document.getElementById('page-size').addEventListener('change', function() {
        pageSize = parseInt(this.value);
        currentPage = 1;
        loadNews(false);
    });
    document.getElementById('prev-page').addEventListener('click', function() {
        if (currentPage > 1) { currentPage--; loadNews(false); }
    });
    document.getElementById('next-page').addEventListener('click', function() {
        if (currentPage * pageSize < totalNews) { currentPage++; loadNews(false); }
    });
});

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => loadNews(false), REFRESH_INTERVAL);
}

async function loadNews(showLoading = true) {
    if (isRefreshing) return;
    isRefreshing = true;

    const containerEl = document.getElementById('news-container');

    if (showLoading) {
        containerEl.style.display = 'none';
        document.getElementById('loading').classList.add('active');
        document.getElementById('error-message').style.display = 'none';
    }

    try {
        const response = await fetch(`${API_URL}?page=${currentPage}&page_size=${pageSize}`);
        const result = await response.json();

        if (result.success) {
            totalNews = result.total;
            const newHashes = result.new_hashes || [];

            if (currentPage === 1 && (newHashes.length > 0 || !hasLoaded)) {
                renderNews(result.data, newHashes);
            }
            updatePagination();
            hasLoaded = true;

            document.getElementById('error-message').style.display = 'none';
            containerEl.style.display = 'grid';
        } else {
            handleError(result.message || '获取新闻失败');
        }
    } catch (error) {
        console.error('加载新闻失败:', error);
        handleError('网络错误');
    } finally {
        if (showLoading) document.getElementById('loading').classList.remove('active');
        isRefreshing = false;
    }
}

function handleError(msg) {
    const errorEl = document.getElementById('error-message');
    const containerEl = document.getElementById('news-container');
    errorEl.style.display = 'block';
    errorEl.querySelector('p').textContent = `⚠️ ${msg}`;
    containerEl.style.display = 'none';
}

function updatePagination() {
    const totalPages = Math.max(1, Math.ceil(totalNews / pageSize));
    document.getElementById('page-info').textContent = `第 ${currentPage}/${totalPages} 页，共 ${totalNews} 条`;
    document.getElementById('prev-page').disabled = currentPage <= 1;
    document.getElementById('next-page').disabled = currentPage >= totalPages;
}

function renderNews(newsList, newHashes) {
    const container = document.getElementById('news-container');

    if (!newsList || !newsList.length) {
        container.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">暂无新闻</p>';
        return;
    }

    const existing = new Map();
    container.querySelectorAll('.news-card').forEach(c => existing.set(c.dataset.hash, c));

    const newsHashes = new Set();
    const newHashesSet = new Set(newHashes);

    // 逆序插入：时间早的在列表末尾
    for (let i = newsList.length - 1; i >= 0; i--) {
        const n = newsList[i];
        const h = `${n.title.slice(0, 30)}|${n.source}`;
        newsHashes.add(h);

        if (!existing.has(h)) {
            const card = createNewsCard(n, h, newHashesSet.has(h));
            const first = container.querySelector('.news-card');
            first ? container.insertBefore(card, first) : container.appendChild(card);
        }
    }

    // 移除不在本次列表的卡片
    existing.forEach((card, hash) => {
        if (!newsHashes.has(hash)) card.remove();
    });

    // 统一设置 NEW 标记：只有本次后端返回的 hash 才保留
    container.querySelectorAll('.news-card').forEach(c => {
        const isActuallyNew = newHashesSet.has(c.dataset.hash);
        isActuallyNew ? c.classList.add('news-new') : c.classList.remove('news-new');
    });
}

function createNewsCard(news, hash, isNew) {
    const card = document.createElement('div');
    card.className = `news-card${isNew ? ' news-new' : ''}`;
    card.dataset.hash = hash;
    card.onclick = () => { if (news.url && news.url !== '#') window.open(news.url, '_blank'); };

    const color = SOURCE_COLORS[news.source] || '#3498db';

    card.innerHTML = `
        <h3>📰 ${escapeHtml(news.title)}</h3>
        <div class="meta">
            <span class="source-tag" style="background:${color}">${escapeHtml(news.source)}</span>
            <span>🕐 ${formatTime(news.publish_time)}</span>
        </div>
        <p class="intro">${escapeHtml(news.intro || '暂无摘要')}</p>
    `;
    return card;
}

function formatTime(s) {
    if (!s) return '--';
    try {
        const d = new Date(s);
        return isNaN(d.getTime()) ? s : d.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
    } catch(e) { return s; }
}

function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

window.addEventListener('visibilitychange', function() {
    if (document.hidden) {
        if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
    } else {
        startAutoRefresh();
        loadNews(false);
    }
});
