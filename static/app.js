const API_URL = '/api/news';
const REFRESH_INTERVAL = 3000;
const SOURCE_COLORS = {"新浪财经":"#E63B2E","财联社":"#DC2626","同花顺":"#F59E0B","东方财富":"#FF6600","金十数据":"#10B981","GDELT":"#6366F1","雅虎财经":"#00B4D8"};

let autoRefreshTimer = null;
let currentPage = 1;
let pageSize = 10;
try {
    const saved = parseInt(localStorage.getItem('pageSize'));
    if (saved && saved >= 5 && saved <= 50) pageSize = saved;
} catch (e) {}
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
    const psEl = document.getElementById('page-size');
    psEl.value = String(pageSize); // 确保 select 显示正确

    loadNews(true);
    startAutoRefresh();

    psEl.addEventListener('change', function() {
        const val = parseInt(this.value);
        if (val >= 5 && val <= 50) {
            pageSize = val;
            localStorage.setItem('pageSize', String(pageSize));
            currentPage = 1;
            cancelAndReload();
        }
    });
    document.getElementById('prev-page').addEventListener('click', function() {
        if (currentPage > 1) { currentPage--; cancelAndReload(); }
    });
    document.getElementById('next-page').addEventListener('click', function() {
        if (currentPage * pageSize < totalNews) { currentPage++; cancelAndReload(); }
    });
    document.getElementById('first-page').addEventListener('click', function() {
        if (currentPage > 1) { currentPage = 1; cancelAndReload(); }
    });

    // 导出功能
    async function doExport(type) {
        const sd = document.getElementById('export-start').value;
        const ed = document.getElementById('export-end').value;
        const params = new URLSearchParams();
        if (sd) params.set('start_date', sd);
        if (ed) params.set('end_date', ed);
        const query = params.toString() ? '?' + params.toString() : '';

        try {
            const res = await fetch(`/api/export/check${query}`);
            const info = await res.json();

            if (!info.success || info.count === 0) {
                alert('⚠️ 该时间段没有新闻数据，请更换日期后重试');
                return;
            }

            if (!confirm(`📥 将导出 ${info.date_range} 的 ${info.count} 条新闻，是否继续？`)) {
                return;
            }

            window.open(`/api/export/${type}${query}`, '_blank');
        } catch (e) {
            alert('导出失败，请重试');
        }
    }

    document.getElementById('btn-json').addEventListener('click', () => doExport('json'));
    document.getElementById('btn-html').addEventListener('click', () => doExport('html'));

    // 初始化日期选择器：只填充数据库中存在的有效日期
    (async function initDatePicker() {
        try {
            const res = await fetch('/api/export/dates');
            const info = await res.json();
            if (!info.success || !info.dates.length) return;

            const sdEl = document.getElementById('export-start');
            const edEl = document.getElementById('export-end');

            // 按时间正序填充选项
            const datesAsc = [...info.dates].sort();
            for (const d of datesAsc) {
                sdEl.innerHTML += `<option value="${d}">${d}</option>`;
                edEl.innerHTML += `<option value="${d}">${d}</option>`;
            }
        } catch (e) { /* 忽略 */ }
    })();
});

// 取消当前请求并重新加载
function cancelAndReload() {
    hasLoaded = false;
    isRefreshing = false;
    loadNews(true);
}

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => {
        if (currentPage === 1) loadNews(false);
    }, REFRESH_INTERVAL);
}

async function loadNews(showLoading = true) {
    if (isRefreshing) return;
    isRefreshing = true;

    const containerEl = document.getElementById('news-container');

    if (showLoading && !hasLoaded) {
        containerEl.style.display = 'none';
        document.getElementById('loading').classList.add('active');
    }
    document.getElementById('error-message').style.display = 'none';

    try {
        const response = await fetch(`${API_URL}?page=${currentPage}&page_size=${pageSize}`);
        const result = await response.json();

        if (result.success) {
            totalNews = result.total;
            const newHashes = result.new_hashes || [];
            const needRender = !hasLoaded || newHashes.length > 0 || currentPage > 1;
            if (needRender) {
                renderNews(result.data, newHashes);
            }
            updatePagination();
            hasLoaded = true;
            containerEl.style.display = 'grid';
        } else {
            handleError(result.message || '获取新闻失败');
        }
    } catch (error) {
        console.error('加载新闻失败:', error);
        handleError('网络错误');
    } finally {
        document.getElementById('loading').classList.remove('active');
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
    document.getElementById('first-page').disabled = currentPage <= 1;
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

    // 清洗和更新现有的状态
    existing.forEach((card, hash) => {
        if (!newsHashes.has(hash)) {
            card.remove();
        } else {
            newHashesSet.has(hash) ? card.classList.add('news-new') : card.classList.remove('news-new');
        }
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
