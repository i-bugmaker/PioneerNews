const API_URL = '/api/news';
const SEARCH_URL = '/api/search';
const REFRESH_INTERVAL = 3000;
const SOURCE_COLORS = {"新浪财经":"#0891B2","财联社":"#E11D48","同花顺":"#F59E0B","东方财富":"#FF6600","GDELT":"#6366F1","雅虎财经":"#00B4D8","Google News":"#8B5CF6","21经济网":"#DC2626"};

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
let isInsertingNew = false;

// 搜索状态
let currentSearchQuery = '';
let isSearchMode = false;
let previousSearchMode = false;

// 未读新闻追踪
let pendingNewList = [];
let pendingHashes = new Set();
let unreadCount = 0;

function makeHash(n) {
    return `${n.title.slice(0, 30)}|${n.source}`;
}

function getDomHashes() {
    const hashes = new Set();
    document.querySelectorAll('.news-card[data-hash]').forEach(c => hashes.add(c.dataset.hash));
    return hashes;
}

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
    psEl.value = String(pageSize);

    // 创建微博风格的新内容提示条
    const newBar = document.createElement('div');
    newBar.className = 'new-content-bar';
    newBar.id = 'new-content-bar';
    newBar.innerHTML = '<span class="icon"></span><span id="new-count"></span>';
    newBar.onclick = handleUnreadClick;
    document.body.appendChild(newBar);

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

    // 搜索功能
    const searchInput = document.getElementById('search-input');
    const searchBtn = document.getElementById('search-btn');
    const searchClear = document.getElementById('search-clear');

    searchInput.addEventListener('input', function() {
        searchClear.style.display = this.value.trim() ? 'block' : 'none';
    });

    searchInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            performSearch(this.value.trim());
        }
    });

    searchBtn.addEventListener('click', function() {
        performSearch(searchInput.value.trim());
    });

    searchClear.addEventListener('click', function() {
        searchInput.value = '';
        searchClear.style.display = 'none';
        exitSearchMode();
    });

    // 热门搜索标签
    document.querySelectorAll('.search-tag').forEach(tag => {
        tag.addEventListener('click', function() {
            const query = this.dataset.query;
            searchInput.value = query;
            searchClear.style.display = 'block';
            performSearch(query);
        });
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

            const datesAsc = [...info.dates].sort();
            for (const d of datesAsc) {
                sdEl.innerHTML += `<option value="${d}">${d}</option>`;
                edEl.innerHTML += `<option value="${d}">${d}</option>`;
            }
        } catch (e) { /* 忽略 */ }
    })();
});

function cancelAndReload() {
    hasLoaded = false;
    isRefreshing = false;
    pendingNewList = [];
    pendingHashes.clear();
    unreadCount = 0;
    hideNewContentBar();
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
        let response, result;
        if (isSearchMode && currentSearchQuery) {
            response = await fetch(`${SEARCH_URL}?query=${encodeURIComponent(currentSearchQuery)}&page=${currentPage}&page_size=${pageSize}`);
        } else {
            response = await fetch(`${API_URL}?page=${currentPage}&page_size=${pageSize}`);
        }
        result = await response.json();

        if (result.success) {
            totalNews = result.total;

            // 检查是否需要完全重新渲染（搜索模式变化或者首次加载）
            if (!hasLoaded || isSearchMode !== previousSearchMode) {
                renderNews(result.data, []);
                hasLoaded = true;
                containerEl.style.display = 'grid';
            } else if (currentPage === 1 && !isInsertingNew && !isSearchMode) {
                const domHashes = getDomHashes();
                const actuallyUnseen = result.data.filter(n => !domHashes.has(makeHash(n)));

                if (actuallyUnseen.length > 0) {
                    actuallyUnseen.forEach(n => {
                        const h = makeHash(n);
                        if (!pendingHashes.has(h)) {
                            pendingHashes.add(h);
                            pendingNewList.push(n);
                            unreadCount++;
                        }
                    });

                    if (window.scrollY <= 200) {
                        insertPendingNews();
                    } else {
                        showNewContentBar(unreadCount);
                    }
                }
            }

            updatePagination();
        } else {
            handleError(result.message || '获取新闻失败');
        }
    } catch (error) {
        console.error('加载新闻失败:', error);
        handleError('网络错误');
    } finally {
        document.getElementById('loading').classList.remove('active');
        isRefreshing = false;
        // 更新前一次的搜索模式状态
        previousSearchMode = isSearchMode;
    }
}

function showNewContentBar(count) {
    const bar = document.getElementById('new-content-bar');
    const countEl = document.getElementById('new-count');
    countEl.textContent = `有 ${count} 条未读新闻`;
    bar.classList.add('visible');
}

function hideNewContentBar() {
    const bar = document.getElementById('new-content-bar');
    bar.classList.remove('visible');
}

// 点击悬浮按钮：滚到顶部 + 插入所有未读新闻
function handleUnreadClick() {
    hideNewContentBar();
    window.scrollTo({ top: 0, behavior: 'smooth' });
    // 等滚动动画结束后插入（300ms足够）
    setTimeout(() => {
        if (pendingNewList.length > 0) {
            insertPendingNews();
        }
    }, 350);
}

function insertPendingNews() {
    if (pendingNewList.length === 0 || isInsertingNew) return;

    isInsertingNew = true;

    const container = document.getElementById('news-container');
    const domHashes = getDomHashes();

    const toInsert = pendingNewList.filter(n => !domHashes.has(makeHash(n)));

    if (toInsert.length > 0) {
        const existingCards = container.querySelectorAll('.news-card');
        existingCards.forEach(card => {
            card.style.transition = 'transform 0.5s cubic-bezier(0.4, 0, 0.2, 1)';
        });

        toInsert.reverse().forEach((n, idx) => {
            const h = makeHash(n);
            const card = createNewsCard(n, h, true);
            card.classList.add('card-inserting');
            card.style.animationDelay = `${idx * 0.1}s`;

            const first = container.querySelector('.news-card');
            first ? container.insertBefore(card, first) : container.appendChild(card);
        });

        setTimeout(() => {
            existingCards.forEach(card => {
                card.style.transition = '';
                card.style.transform = '';
            });
            container.querySelectorAll('.card-inserting').forEach(card => {
                card.classList.remove('card-inserting');
                card.style.animation = '';
            });
            isInsertingNew = false;
        }, 600);
    } else {
        isInsertingNew = false;
    }

    pendingNewList = [];
    pendingHashes.clear();
    unreadCount = 0;
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
        const h = makeHash(n);
        newsHashes.add(h);
        if (!existing.has(h)) {
            const card = createNewsCard(n, h, newHashesSet.has(h));
            const first = container.querySelector('.news-card');
            first ? container.insertBefore(card, first) : container.appendChild(card);
        }
    }

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
    const titleContent = news.title_highlight && news.title_highlight.includes('<mark>') 
        ? news.title_highlight 
        : `📰 ${escapeHtml(news.title)}`;
    const introContent = news.intro_highlight && news.intro_highlight.includes('<mark>') 
        ? news.intro_highlight 
        : escapeHtml(news.intro || '暂无摘要');

    card.innerHTML = `
        <h3>${titleContent}</h3>
        <div class="meta">
            <span class="source-tag" style="background:${color}">${escapeHtml(news.source)}</span>
            <span>🕐 ${formatTime(news.publish_time, news.publish_ts)}</span>
        </div>
        <p class="intro">${introContent}</p>
    `;
    return card;
}

function formatTime(s, ts) {
    if (ts && ts > 0) {
        try {
            const d = new Date(ts * 1000);
            return d.toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});
        } catch(e) {}
    }
    if (!s) return '--';
    try {
        const d = new Date(s);
        return isNaN(d.getTime()) ? s : d.toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
    } catch(e) { return s; }
}

function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

// 搜索功能
async function performSearch(query) {
    if (!query || query.length < 2) {
        alert('请输入至少2个字符进行搜索');
        return;
    }

    currentSearchQuery = query;
    isSearchMode = true;
    currentPage = 1;
    cancelAndReload();
}

function exitSearchMode() {
    currentSearchQuery = '';
    isSearchMode = false;
    currentPage = 1;
    cancelAndReload();
}

async function loadSearchResults(query, page, pageSize) {
    const response = await fetch(`${SEARCH_URL}?query=${encodeURIComponent(query)}&page=${page}&page_size=${pageSize}`);
    return await response.json();
}

window.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        loadNews(false);
    }
});
