const API_URL = '/api/news';
const SEARCH_URL = '/api/search';
const REFRESH_INTERVAL = 3000;
const SOURCE_COLORS = {"新浪财经":"#D94A4A","财联社":"#D94A7A","同花顺":"#E08A3A","东方财富":"#E86A2A","GDELT":"#4A8A5A","雅虎财经":"#8A5AC0","Google News":"#4A8AD9","21经济网":"#3AA87A","华尔街见闻":"#5A6ABF","雪球":"#4AA0D9","金十数据":"#E07A4A","格隆汇":"#3A5A8A","法布财经":"#4AC0A0"};

function debounce(fn, delay) {
    let timer = null;
    return function(...args) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

let autoRefreshTimer = null;
let pollAbortController = null;
let latestTimestamp = null;
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
let isLoadingMore = false;
let allLoaded = false;

// 滚动监听自动插入新闻
let scrollHandler = null;
let pendingInsertTimer = null;

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

function customConfirm(message) {
    return new Promise(function(resolve) {
        const overlay = document.getElementById('modal-overlay');
        const msgEl = document.getElementById('modal-message');
        const btnCancel = document.getElementById('modal-cancel');
        const btnConfirm = document.getElementById('modal-confirm');

        msgEl.innerHTML = message;
        overlay.style.display = 'flex';

        function cleanup() {
            overlay.style.display = 'none';
            btnCancel.removeEventListener('click', onCancel);
            btnConfirm.removeEventListener('click', onConfirm);
            overlay.removeEventListener('click', onOverlay);
        }

        function onCancel() {
            cleanup();
            resolve(false);
        }

        function onConfirm() {
            cleanup();
            resolve(true);
        }

        function onOverlay(e) {
            if (e.target === overlay) {
                cleanup();
                resolve(false);
            }
        }

        btnCancel.addEventListener('click', onCancel);
        btnConfirm.addEventListener('click', onConfirm);
        overlay.addEventListener('click', onOverlay);
    });
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
    initTheme();

    initEmojiSystem();
    initNewTagObserver();
    initScrollFloat();
    initScrollAutoInsert();
    initInfiniteScroll();

    const newBar = document.createElement('div');
    newBar.className = 'new-content-bar';
    newBar.id = 'new-content-bar';
    newBar.innerHTML = '<span class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2 L8 12 L8 16 L16 16 L16 12 Z"/><circle cx="12" cy="9" r="1.5"/><path d="M8 13 L5 15 L8 15 Z"/><path d="M16 13 L19 15 L16 15 Z"/><path d="M10 16 L12 19 L14 16"/><line x1="6" y1="19" x2="6" y2="21"/><line x1="12" y1="20" x2="12" y2="22"/><line x1="18" y1="19" x2="18" y2="21"/></svg></span><span id="new-count"></span>';
    newBar.onclick = handleUnreadClick;
    document.body.appendChild(newBar);

    loadNews(true);
    startAutoRefresh();
    connectWebSocket();

    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/static/sw.js').catch(function(e) {
            console.warn('SW registration failed:', e);
        });
    }

    // 主题切换
    document.getElementById('theme-toggle').addEventListener('click', toggleTheme);

    // 搜索功能
    const searchInput = document.getElementById('search-input');
    const searchBtn = document.getElementById('search-btn');
    const searchClear = document.getElementById('search-clear');

    const debouncedSearch = debounce((query) => {
        if (query) performSearch(query);
    }, 300);

    searchInput.addEventListener('input', function() {
        const val = this.value.trim();
        searchClear.classList.toggle('visible', !!val);
        if (!val) {
            if (isSearchMode) exitSearchMode();
        }
    });

    searchInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            performSearch(this.value.trim());
        }
    });

    searchBtn.addEventListener('click', function() {
        performSearch(searchInput.value.trim());
    });

    searchClear.addEventListener('click', function() {
        searchInput.value = '';
        searchClear.classList.remove('visible');
        exitSearchMode();
    });

    // 热度词云
    loadTrending();

    loadTimeline();
    initTimelineToggle();

    // 悬浮导出面板
    initFloatExport();
});

function cancelAndReload() {
    isRefreshing = false;
    pendingNewList = [];
    pendingHashes.clear();
    unreadCount = 0;
    hasLoaded = false;
    latestTimestamp = null;
    allLoaded = false;
    hideNewContentBar();
    loadNews(true);
}

function startAutoRefresh() {
    if (autoRefreshTimer) clearTimeout(autoRefreshTimer);
    doLongPoll();
}

async function doLongPoll() {
    if (isSearchMode) {
        autoRefreshTimer = setTimeout(doLongPoll, 5000);
        return;
    }
    try {
        const response = await fetch('/api/poll?since_ts=' + (latestTimestamp || 0));
        const result = await response.json();
        if (result.success && result.data && result.data.length > 0) {
            if (currentPage === 1 && !isSearchMode) {
                const maxTs = Math.max(...result.data.map(n => n.publish_ts || 0));
                if (maxTs > (latestTimestamp || 0)) latestTimestamp = maxTs;
                loadNews(false);
            }
        }
    } catch (e) {
        // silently retry
    }
    autoRefreshTimer = setTimeout(doLongPoll, 1000);
}

async function loadNews(showLoading = true) {
    if (isRefreshing) return;
    isRefreshing = true;

    const containerEl = document.getElementById('news-container');
    const loadingEl = document.getElementById('loading');

    if (showLoading && !hasLoaded) {
        containerEl.style.display = 'none';
        loadingEl.classList.add('active');
    } else if (showLoading) {
        loadingEl.classList.add('active');
        setTimeout(() => loadingEl.classList.remove('active'), 200);
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

            if (result.data && result.data.length > 0) {
                const maxTs = Math.max(...result.data.map(n => n.publish_ts || 0));
                if (maxTs > (latestTimestamp || 0)) latestTimestamp = maxTs;
            }

            // 检查是否需要完全重新渲染（搜索模式变化或者首次加载）
            if (!hasLoaded || isSearchMode !== previousSearchMode) {
                renderNews(result.data, []);
                hasLoaded = true;
                containerEl.style.display = 'grid';
                // IntersectionObserver handles NEW tag visibility
            } else if (currentPage === 1 && !isInsertingNew && !isSearchMode) {
                const domHashes = getDomHashes();
                // 排除已在 DOM 中 dedup_group 的新闻
                const existingGroups = new Set();
                document.querySelectorAll('.news-card').forEach(c => {
                    const g = parseInt(c.dataset.dedupGroup);
                    if (g > 0) existingGroups.add(g);
                });
                const actuallyUnseen = result.data.filter(n => {
                    if (domHashes.has(makeHash(n))) return false;
                    if (n.dedup_group > 0 && existingGroups.has(n.dedup_group)) return false;
                    return true;
                });

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
    
    if (!pendingInsertTimer) {
        pendingInsertTimer = setTimeout(() => {
            if (pendingNewList.length > 0) {
                hideNewContentBar();
                insertPendingNews();
            }
            pendingInsertTimer = null;
        }, 15000);
    }
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

function initScrollAutoInsert() {
    scrollHandler = () => {
        if (window.scrollY <= 200 && pendingNewList.length > 0 && !isInsertingNew) {
            insertPendingNews();
            hideNewContentBar();
            if (pendingInsertTimer) {
                clearTimeout(pendingInsertTimer);
                pendingInsertTimer = null;
            }
        }
    };
    
    window.addEventListener('scroll', scrollHandler, { passive: true });
}

function insertPendingNews() {
    if (pendingNewList.length === 0 || isInsertingNew) return;

    isInsertingNew = true;

    const container = document.getElementById('news-container');
    const domHashes = getDomHashes();

    const toInsert = pendingNewList.filter(n => !domHashes.has(makeHash(n)));

    if (toInsert.length > 0) {
        // 过滤已在 DOM 中存在 dedup_group 的相似新闻
        // 收集 DOM 中已有的 dedup_group
        const existingGroups = new Set();
        container.querySelectorAll('.news-card').forEach(c => {
            const g = parseInt(c.dataset.dedupGroup);
            if (g > 0) existingGroups.add(g);
        });
        // 如果 DOM 中已有该组，不再插入（保留最早的那条）
        const filteredInsert = toInsert.filter(n => {
            if (n.dedup_group > 0 && existingGroups.has(n.dedup_group)) {
                return false;
            }
            return true;
        });
        // 确保最新的新闻优先插入
        filteredInsert.sort((a, b) => (b.publish_ts || 0) - (a.publish_ts || 0));

        if (filteredInsert.length === 0) {
            isInsertingNew = false;
            pendingNewList = [];
            pendingHashes.clear();
            unreadCount = 0;
            return;
        }

        container.querySelectorAll('.empty-msg').forEach(el => el.remove());
        container.querySelectorAll('.scroll-end-msg').forEach(el => el.remove());

        const existingCards = container.querySelectorAll('.news-card');
        existingCards.forEach(card => {
            card.style.transition = 'transform 0.5s cubic-bezier(0.4, 0, 0.2, 1)';
        });

        filteredInsert.reverse().forEach((n, idx) => {
            const h = makeHash(n);
            const card = createNewsCard(n, h, true);
            card.classList.add('card-inserting');
            card.style.animationDelay = `${idx * 0.1}s`;

            const first = container.querySelector('.news-card');
            first ? container.insertBefore(card, first) : container.appendChild(card);
            
            registerCardForNewTag(card);
        });

        // 插入后立即截断底部多余卡片，确保不超过 pageSize
        const allCardsAfterInsert = container.querySelectorAll('.news-card');
        if (allCardsAfterInsert.length > pageSize) {
            const excess = allCardsAfterInsert.length - pageSize;
            for (let i = allCardsAfterInsert.length - 1; i >= allCardsAfterInsert.length - excess; i--) {
                allCardsAfterInsert[i].remove();
            }
        }

        setTimeout(() => {
            existingCards.forEach(card => {
                card.style.transition = '';
                card.style.transform = '';
            });
            container.querySelectorAll('.card-inserting').forEach(card => {
                card.classList.remove('card-inserting');
                card.style.animation = '';
                card.querySelectorAll('h3, .meta, .intro').forEach(el => {
                    el.style.animation = '';
                });
            });
            // IntersectionObserver handles NEW tag visibility
            isInsertingNew = false;
        }, 900);
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

function renderNews(newsList, newHashes) {
    const container = document.getElementById('news-container');

    if (!newsList || !newsList.length) {
        const emptyMsg = isSearchMode ? '没有找到相关结果' : '暂无新闻';
        container.innerHTML = `<p class="empty-msg" style="text-align:center;color:#999;padding:40px;">${emptyMsg}</p>`;
        return;
    }

    container.querySelectorAll('.empty-msg').forEach(el => el.remove());
    container.querySelectorAll('.scroll-end-msg').forEach(el => el.remove());

    // COLLECT existing cards BEFORE clearing
    const existing = new Map();
    container.querySelectorAll('.news-card').forEach(c => existing.set(c.dataset.hash, c));

    // 过滤重复的 dedup_group：只保留每个组最新发布的那条
    // API 返回数据是 publish_ts DESC（最新在前），正向遍历取第一条即最新的
    const seenGroups = new Set();
    const dedupFiltered = [];
    for (let i = 0; i < newsList.length; i++) {
        const n = newsList[i];
        if (n.dedup_group > 0) {
            if (seenGroups.has(n.dedup_group)) continue;
            seenGroups.add(n.dedup_group);
        }
        dedupFiltered.push(n);
    }

    const newsHashes = new Set(dedupFiltered.map(n => makeHash(n)));
    const newHashesSet = new Set(newHashes);

    existing.forEach((card, hash) => {
        if (!newsHashes.has(hash)) {
            card.remove();
        }
    });

    for (let i = dedupFiltered.length - 1; i >= 0; i--) {
        const n = dedupFiltered[i];
        const h = makeHash(n);
        if (!existing.has(h)) {
            const card = createNewsCard(n, h, newHashesSet.has(h));
            const first = container.querySelector('.news-card');
            first ? container.insertBefore(card, first) : container.appendChild(card);
            if (newHashesSet.has(h)) {
                registerCardForNewTag(card);
            }
        }
    }

    existing.forEach((card, hash) => {
        if (newHashesSet.has(hash) && !card.classList.contains('news-new')) {
            card.classList.add('news-new');
            registerCardForNewTag(card);
        } else if (!newHashesSet.has(hash)) {
            card.classList.remove('news-new');
        }
    });
}

function createNewsCard(news, hash, isNew) {
    const card = document.createElement('div');
    card.className = `news-card${isNew ? ' news-new' : ''}`;
    card.dataset.hash = hash;
    card.dataset.dedupGroup = news.dedup_group || '0';
    card.onclick = () => {
        if (news.url && news.url !== '#') {
            window.open(news.url, '_blank');
        }
    };

    const color = SOURCE_COLORS[news.source] || '#3498db';
    const titleContent = news.title_highlight && news.title_highlight.includes('<mark>') 
        ? news.title_highlight 
        : `📰 ${escapeHtml(news.title)}`;
    const introContent = news.intro_highlight && news.intro_highlight.includes('<mark>') 
        ? news.intro_highlight 
        : escapeHtml(news.intro || '暂无摘要');

    const dedupTag = news.dedup_count >= 2
        ? `<span class="dedup-tag" onclick="event.stopPropagation(); toggleDedupExpand(this.closest('.news-card'), ${news.dedup_group})">相似 ${news.dedup_count - 1} 条</span>`
        : '';
    const dedupList = news.dedup_count >= 2
        ? `<div class="dedup-similar-list" style="display:none;"></div>`
        : '';

    card.innerHTML = `
        <h3>${titleContent}</h3>
        <div class="meta">
            <span class="source-tag" style="background:${color}">${escapeHtml(news.source)}</span>
            <span>🕐 ${formatTime(news.publish_time, news.publish_ts)}</span>
        </div>
        <p class="intro">${introContent}</p>
        ${dedupTag}
        ${dedupList}
    `;
    return card;
}

async function toggleDedupExpand(card, groupId) {
    const listEl = card.querySelector('.dedup-similar-list');
    const tagEl = card.querySelector('.dedup-tag');
    if (!listEl) return;

    if (listEl.children.length > 0) {
        const isHidden = listEl.style.display === 'none';
        listEl.style.display = isHidden ? 'block' : 'none';
        tagEl.classList.toggle('expanded', isHidden);
        return;
    }

    tagEl.classList.add('expanded');
    listEl.style.display = 'block';
    listEl.innerHTML = '<div style="text-align:center;padding:8px;color:#7c3aed;">加载中...</div>';

    try {
        const resp = await fetch(`/api/dedup/group/${groupId}`);
        const data = await resp.json();
        if (!data.success || !data.items || data.items.length === 0) {
            listEl.innerHTML = '<div style="text-align:center;padding:8px;color:#94a3b8;">暂无相似新闻</div>';
            return;
        }
        listEl.innerHTML = data.items.map(item => {
            const itemHash = `${item.title.slice(0, 30)}|${item.source}`;
            if (itemHash === card.dataset.hash) return '';
            const srcColor = SOURCE_COLORS[item.source] || '#3498db';
            return `<div class="dedup-similar-item" onclick="event.stopPropagation(); window.open('${item.url}', '_blank')">
                <span class="sim-source" style="background:${srcColor}">${escapeHtml(item.source)}</span>
                <span class="sim-title">${escapeHtml(item.title)}</span>
                <span class="sim-time">${formatTime(item.publish_time, item.publish_ts)}</span>
            </div>`;
        }).filter(Boolean).join('');
        if (!listEl.innerHTML) {
            listEl.innerHTML = '<div style="text-align:center;padding:8px;color:#94a3b8;">暂无其他相似新闻</div>';
        }
    } catch (e) {
        listEl.innerHTML = '<div style="text-align:center;padding:8px;color:#e11d48;">加载失败</div>';
    }
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

function escapeHtml(t) {
    if (typeof t !== 'string') return '';
    return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function hexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
}

// 搜索功能
let isSearching = false;

async function performSearch(query) {
    if (!query) {
        alert('请输入搜索关键词');
        return;
    }

    if (isSearching) return;
    isSearching = true;

    const searchBtn = document.getElementById('search-btn');
    searchBtn.disabled = true;

    window.scrollTo({ top: 0, behavior: 'smooth' });

    currentSearchQuery = query;
    isSearchMode = true;
    currentPage = 1;
    
    try {
        await cancelAndReload();
    } finally {
        searchBtn.disabled = false;
        isSearching = false;
    }
}

function exitSearchMode() {
    currentSearchQuery = '';
    isSearchMode = false;
    currentPage = 1;
    hasLoaded = false;
    cancelAndReload();
}

async function loadSearchResults(query, page, pageSize) {
    const response = await fetch(`${SEARCH_URL}?query=${encodeURIComponent(query)}&page=${page}&page_size=${pageSize}&fuzzy=true`);
    return await response.json();
}

window.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        loadNews(false);
        startClock();  // resume clock when visible
    } else {
        if (clockTimer) clearInterval(clockTimer);  // stop clock when hidden
        clockTimer = null;
    }
});

let newTagObserver = null;

function initNewTagObserver() {
    if (newTagObserver) newTagObserver.disconnect();
    newTagObserver = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            const card = entry.target;
            if (entry.isIntersecting && card.classList.contains('news-new')) {
                // Start fading after 4 seconds of visibility
                setTimeout(() => {
                    if (card.classList.contains('news-new')) {
                        card.classList.add('new-tag-fading');
                    }
                }, 4000);
                // Remove NEW tag after 5 seconds
                setTimeout(() => {
                    card.classList.remove('news-new');
                    card.classList.remove('new-tag-fading');
                }, 5000);
                newTagObserver.unobserve(card);
            }
        });
    }, { threshold: 0.5 });

    document.querySelectorAll('.news-new').forEach(card => newTagObserver.observe(card));
}

function removeNewTag(card) {
    if (!card || !card.classList.contains('news-new')) return;
    card.classList.add('new-tag-fading');
    setTimeout(() => {
        card.classList.remove('news-new');
        card.classList.remove('new-tag-fading');
    }, 800);
    if (newTagObserver) newTagObserver.unobserve(card);
}

function registerCardForNewTag(card) {
    if (!card || !card.classList.contains('news-new')) return;
    if (newTagObserver) newTagObserver.observe(card);

    const originalOnClick = card.onclick;
    card.onclick = function(e) {
        removeNewTag(card);
        if (originalOnClick) originalOnClick.call(card, e);
    };
}

let emojiReactionCount = 0;
let userReactions = {};
let lastClickTime = 0;
let comboCount = 0;

const ALL_EMOJIS = [
    { emoji: '❤️', label: '喜欢', weight: 15 },
    { emoji: '👍', label: '赞', weight: 15 },
    { emoji: '🔥', label: '火了', weight: 12 },
    { emoji: '💰', label: '发财', weight: 10 },
    { emoji: '🚀', label: '起飞', weight: 10 },
    { emoji: '📈', label: '涨停', weight: 10 },
    { emoji: '😍', label: '爱了', weight: 8 },
    { emoji: '🤑', label: '暴富', weight: 8 },
    { emoji: '😭', label: '哭了', weight: 5 },
    { emoji: '🐂', label: '牛逼', weight: 8 },
    { emoji: '💪', label: '加油', weight: 6 },
    { emoji: '🎉', label: '庆祝', weight: 6 },
    { emoji: '🦄', label: '神兽', weight: 3 },
    { emoji: '🌈', label: '彩虹', weight: 3 },
    { emoji: '✨', label: '闪光', weight: 5 },
    { emoji: '🎯', label: '精准', weight: 4 },
    { emoji: '💎', label: '钻石', weight: 3 },
    { emoji: '👑', label: '王者', weight: 2 },
];

const RAIN_EMOJIS = ['❤️', '👍', '🔥', '💰', '🚀', '📈', '✨', '🌟', '💎', '🎉', '🦄', '🌈'];

const SURPRISE_MESSAGES = {
    1: '感谢支持！',
    5: '连击x5！',
    10: '🔥 十连达成！',
    20: '💎 老粉认证！',
    50: '👑 铁粉之王！',
    100: '🎆 传说级粉丝！'
};

function weightedRandom() {
    const totalWeight = ALL_EMOJIS.reduce((sum, e) => sum + e.weight, 0);
    let r = Math.random() * totalWeight;
    for (const e of ALL_EMOJIS) {
        r -= e.weight;
        if (r <= 0) return e;
    }
    return ALL_EMOJIS[0];
}

function loadEmojiState() {
    try {
        const saved = localStorage.getItem('emojiReactions');
        if (saved) {
            const data = JSON.parse(saved);
            emojiReactionCount = data.total || 0;
            userReactions = data.reactions || {};
        }
    } catch (e) {}
}

function saveEmojiState() {
    try {
        localStorage.setItem('emojiReactions', JSON.stringify({
            total: emojiReactionCount,
            reactions: userReactions
        }));
    } catch (e) {}
}

function bumpBadge() {
    const badge = document.getElementById('author-badge');
    if (!badge) return;
    badge.style.transform = 'scale(0.95)';
    setTimeout(() => { badge.style.transform = ''; }, 150);
}

function showFloatingMessage(text, startX, startY) {
    const el = document.createElement('div');
    const topPos = Math.max(8, startY - 55);
    el.style.cssText = `
        position: fixed;
        left: ${startX}px;
        top: ${topPos}px;
        transform: translateX(-50%);
        background: linear-gradient(135deg, rgba(236, 72, 153, 0.95), rgba(139, 92, 246, 0.95));
        color: white;
        padding: 8px 18px;
        border-radius: 14px;
        font-size: 0.85em;
        font-weight: 700;
        pointer-events: none;
        z-index: 1002;
        animation: floatUp 1.2s ease-out forwards;
        white-space: nowrap;
        box-shadow: 0 6px 20px rgba(139, 92, 246, 0.4);
        text-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    `;
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 1300);
}

function spawnEmojiFly(emoji, startX, startY, count = 1) {
    const container = document.getElementById('emoji-fly-container');
    if (!container) return;
    
    const badge = document.getElementById('author-badge');
    const badgeRect = badge ? badge.getBoundingClientRect() : null;
    
    for (let i = 0; i < count; i++) {
        const el = document.createElement('div');
        el.className = 'emoji-fly';
        el.textContent = emoji;
        
        const baseSize = 1.5 + Math.random() * 1.5;
        el.style.fontSize = baseSize + 'em';
        
        let startX2, startY2;
        if (badgeRect) {
            startX2 = badgeRect.left + Math.random() * badgeRect.width;
            startY2 = badgeRect.top + Math.random() * badgeRect.height;
        } else {
            startX2 = startX + (Math.random() - 0.5) * 40;
            startY2 = startY + (Math.random() - 0.5) * 20;
        }
        el.style.left = startX2 + 'px';
        el.style.top = startY2 + 'px';
        
        const angle = (Math.random() - 0.5) * Math.PI * 1.5;
        const distance = 80 + Math.random() * 120;
        const flyX = Math.cos(angle) * distance * 0.5;
        const flyY = -Math.abs(Math.sin(angle) * distance) - 50;
        const endX = Math.cos(angle) * distance;
        const endY = Math.sin(angle) * distance * 0.5 + 100;
        const rotate = (Math.random() - 0.5) * 120;
        const endRotate = rotate + (Math.random() - 0.5) * 180;
        
        el.style.setProperty('--fly-x', flyX + 'px');
        el.style.setProperty('--fly-y', flyY + 'px');
        el.style.setProperty('--fly-rotate', rotate + 'deg');
        el.style.setProperty('--fly-end-x', endX + 'px');
        el.style.setProperty('--fly-end-y', endY + 'px');
        el.style.setProperty('--fly-end-rotate', endRotate + 'deg');
        el.style.animationDelay = (i * 0.1) + 's';
        
        container.appendChild(el);
        setTimeout(() => el.remove(), 2000);
    }
}

function spawnChaserEmojis(emoji, count = 2) {
    const container = document.getElementById('emoji-fly-container');
    if (!container) return;
    
    const badge = document.getElementById('author-badge');
    const badgeRect = badge ? badge.getBoundingClientRect() : null;
    
    for (let i = 0; i < count; i++) {
        setTimeout(() => {
            const el = document.createElement('div');
            el.className = 'emoji-fly';
            el.textContent = emoji;
            
            const size = 0.8 + Math.random() * 0.6;
            el.style.fontSize = size + 'em';
            el.style.opacity = '0.7';
            
            let startX2, startY2;
            if (badgeRect) {
                startX2 = badgeRect.left + 20 + Math.random() * (badgeRect.width - 40);
                startY2 = badgeRect.top + 5 + Math.random() * (badgeRect.height * 0.3);
            } else {
                startX2 = startX + (Math.random() - 0.5) * 30;
                startY2 = startY - 20 + (Math.random() - 0.5) * 15;
            }
            el.style.left = startX2 + 'px';
            el.style.top = startY2 + 'px';
            
            const flyY = -80 - Math.random() * 60;
            const flyX = (Math.random() - 0.5) * 40;
            const rotate = (Math.random() - 0.5) * 60;
            
            el.style.setProperty('--fly-x', flyX + 'px');
            el.style.setProperty('--fly-y', flyY + 'px');
            el.style.setProperty('--fly-rotate', rotate + 'deg');
            el.style.setProperty('--fly-end-x', flyX * 1.5 + 'px');
            el.style.setProperty('--fly-end-y', flyY * 1.3 + 'px');
            el.style.setProperty('--fly-end-rotate', rotate * 2 + 'deg');
            
            container.appendChild(el);
            setTimeout(() => el.remove(), 1800);
        }, i * 120 + 150);
    }
}

function spawnEmojiRain(emoji, count = 12) {
    for (let i = 0; i < count; i++) {
        setTimeout(() => {
            const el = document.createElement('div');
            el.className = 'emoji-rain';
            el.textContent = emoji;
            el.style.left = (5 + Math.random() * 90) + 'vw';
            el.style.top = '-30px';
            el.style.fontSize = (1.2 + Math.random() * 0.8) + 'em';
            el.style.animationDuration = (1.2 + Math.random() * 1.2) + 's';
            document.body.appendChild(el);
            setTimeout(() => el.remove(), 3000);
        }, i * 80);
    }
}

function spawnMultiRain(emojis, each = 8) {
    emojis.forEach((emoji, idx) => {
        setTimeout(() => spawnEmojiRain(emoji, each), idx * 200);
    });
}

function handleBadgeClick() {
    const badge = document.getElementById('author-badge');
    const rect = badge.getBoundingClientRect();
    const startX = rect.left + rect.width / 2;
    const startY = rect.top + rect.height / 2;
    
    const now = Date.now();
    const timeDiff = now - lastClickTime;
    
    if (timeDiff < 800) {
        comboCount++;
    } else {
        comboCount = 1;
    }
    lastClickTime = now;
    
    const picked = weightedRandom();
    const emoji = picked.emoji;
    
    if (!userReactions[emoji]) userReactions[emoji] = 0;
    userReactions[emoji]++;
    emojiReactionCount++;
    
    saveEmojiState();
    bumpBadge();
    
    spawnEmojiFly(emoji, startX, startY, 1);
    
    if (comboCount >= 3 && comboCount < 10) {
        const msg = SURPRISE_MESSAGES[comboCount] || `连击x${comboCount}！`;
        showFloatingMessage(msg, startX, startY);
        spawnChaserEmojis(emoji, 1);
    } else if (comboCount >= 10) {
        const msg = SURPRISE_MESSAGES[comboCount] || `连击x${comboCount}！`;
        showFloatingMessage(msg, startX, startY);
        spawnChaserEmojis(emoji, 2);
        spawnEmojiFly(emoji, startX, startY, 2);
    }
    
    if (emojiReactionCount in SURPRISE_MESSAGES) {
        setTimeout(() => showFloatingMessage(SURPRISE_MESSAGES[emojiReactionCount], startX, startY), 200);
        spawnEmojiRain(emoji, 15);
    }
    
    if (emojiReactionCount % 10 === 0 && emojiReactionCount > 0) {
        const rainEmojis = RAIN_EMOJIS.sort(() => Math.random() - 0.5).slice(0, 4);
        setTimeout(() => spawnMultiRain(rainEmojis, 6), 300);
    }
    
    if (comboCount >= 5) {
        setTimeout(() => spawnEmojiRain('✨', 10), 150);
    }
    
    if (emoji === '👑' && userReactions['👑'] === 1) {
        setTimeout(() => spawnMultiRain(['👑', '✨', '💎', '🌟'], 8), 200);
        setTimeout(() => showFloatingMessage('👑 王者降临！', startX, startY), 400);
    }
    
    if (emoji === '🦄' && userReactions['🦄'] % 3 === 0) {
        setTimeout(() => {
            spawnEmojiRain('🦄', 10);
            spawnEmojiRain('🌈', 8);
        }, 200);
    }
    
    if (emoji === '💰' && comboCount >= 3) {
        setTimeout(() => spawnMultiRain(['💰', '🤑', '📈'], 5), 150);
    }
    
    badge.style.transform = 'scale(0.95)';
    setTimeout(() => { badge.style.transform = ''; }, 150);
}

function initEmojiSystem() {
    loadEmojiState();
    
    if (!document.getElementById('floatUp-style')) {
        const style = document.createElement('style');
        style.id = 'floatUp-style';
        style.textContent = `
            @keyframes floatUp {
                0% { opacity: 1; transform: translateX(-50%) translateY(0) scale(1); }
                100% { opacity: 0; transform: translateX(-50%) translateY(-50px) scale(1.1); }
            }
        `;
        document.head.appendChild(style);
    }
    
    const badge = document.getElementById('author-badge');
    if (badge) {
        badge.addEventListener('click', (e) => {
            e.stopPropagation();
            handleBadgeClick();
        });
    }
}

function initScrollFloat() {
    const floatWrap = document.getElementById('scroll-float');
    const btnTop = document.getElementById('scroll-to-top');
    const btnBottom = document.getElementById('scroll-to-bottom');
    if (!floatWrap || !btnTop || !btnBottom) return;

    let ticking = false;

    function toggleVisibility() {
        const docHeight = document.documentElement.scrollHeight;
        const winHeight = window.innerHeight;

        if (docHeight <= winHeight) {
            floatWrap.classList.remove('visible');
            return;
        }

        floatWrap.classList.add('visible');
    }

    function onScroll() {
        if (!ticking) {
            requestAnimationFrame(() => {
                toggleVisibility();
                ticking = false;
            });
            ticking = true;
        }
    }

    window.addEventListener('scroll', onScroll, { passive: true });

    const ro = new ResizeObserver(() => toggleVisibility());
    ro.observe(document.body);

    toggleVisibility();

    btnTop.addEventListener('click', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    btnBottom.addEventListener('click', () => {
        window.scrollTo({
            top: document.documentElement.scrollHeight,
            behavior: 'smooth'
        });
    });
}

// ===== 暗色模式 =====
function applyTheme(theme) {
    if (theme === 'dark') {
        document.documentElement.setAttribute('data-theme', 'dark');
    } else {
        document.documentElement.removeAttribute('data-theme');
    }
}
function toggleTheme() {
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const newTheme = isDark ? 'light' : 'dark';

    document.documentElement.classList.add('theme-transitioning');

    localStorage.setItem('theme', newTheme);
    applyTheme(newTheme);

    setTimeout(() => {
        document.documentElement.classList.remove('theme-transitioning');
    }, 600);
}
function initTheme() {
    const saved = localStorage.getItem('theme');
    if (saved) {
        applyTheme(saved);
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
        applyTheme('dark');
    } else {
        applyTheme('light');
    }
}

// ===== 无限滚动 =====
function initInfiniteScroll() {
    const sentinel = document.createElement('div');
    sentinel.id = 'scroll-sentinel';
    sentinel.style.height = '1px';
    document.getElementById('news-container').after(sentinel);

    const observer = new IntersectionObserver((entries) => {
        if (entries[0].isIntersecting && !isLoadingMore && !allLoaded) {
            loadMoreNews();
        }
    }, { rootMargin: '200px' });
    observer.observe(sentinel);
}

async function loadMoreNews() {
    if (isLoadingMore || allLoaded) return;
    isLoadingMore = true;

    const container = document.getElementById('news-container');
    for (let i = 0; i < 3; i++) {
        const sk = document.createElement('div');
        sk.className = 'skeleton';
        sk.id = 'skeleton-' + i;
        sk.innerHTML = '<div class="skeleton-line skeleton-title"></div><div class="skeleton-line skeleton-meta"></div><div class="skeleton-line"></div>';
        container.appendChild(sk);
    }

    try {
        const nextPage = currentPage + 1;
        let url = `${API_URL}?page=${nextPage}&page_size=${pageSize}`;
        if (isSearchMode && currentSearchQuery) {
            url += `&search=${encodeURIComponent(currentSearchQuery)}`;
        }
        const resp = await fetch(url);
        const result = await resp.json();

        document.querySelectorAll('.skeleton').forEach(el => el.remove());

        if (result.success && result.data && result.data.length > 0) {
            currentPage = nextPage;
            totalNews = result.total;
            const existingHashes = getDomHashes();
            const seenGroups = new Set();
            container.querySelectorAll('.news-card').forEach(c => {
                const g = parseInt(c.dataset.dedupGroup);
                if (g > 0) seenGroups.add(g);
            });

            const toAppend = result.data.filter(n => {
                if (existingHashes.has(makeHash(n))) return false;
                if (n.dedup_group > 0 && seenGroups.has(n.dedup_group)) return false;
                return true;
            });

            toAppend.forEach(n => {
                const h = makeHash(n);
                const card = createNewsCard(n, h, false);
                container.appendChild(card);
            });

        } else {
            document.querySelectorAll('.skeleton').forEach(el => el.remove());
            if (currentPage * pageSize >= totalNews) {
                allLoaded = true;
                const endMsg = document.createElement('div');
                endMsg.className = 'scroll-end-msg';
                endMsg.textContent = '— 已显示全部新闻 —';
                container.appendChild(endMsg);
            }
        }
    } catch (e) {
        document.querySelectorAll('.skeleton').forEach(el => el.remove());
        console.error('Infinite scroll error:', e);
    } finally {
        isLoadingMore = false;
    }
}

// ===== 悬浮导出面板 =====
let fpFormat = 'json';
let fpStartDate = null;
let fpEndDate = null;
let fpCalYear = null;
let fpCalMonth = null;
let fpSelectMode = 'start';
let fpCalStartRow = -1;
let fpCalStartCol = -1;
const FP_WEEK_DAYS = ['日', '一', '二', '三', '四', '五', '六'];

function initFloatExport() {
    document.getElementById('sfb-export').addEventListener('click', function(e) {
        e.stopPropagation();
        const panel = document.getElementById('export-float-panel');
        const isOpen = panel.style.display !== 'none';
        if (!isOpen) {
            panel.style.display = '';
            fpCalYear = null;
            fpCalMonth = null;
            fpSelectMode = 'start';
            document.getElementById('fp-drp-cal-hint').textContent = '📌 点击日期选择开始';
        } else {
            panel.style.display = 'none';
        }
    });

    document.addEventListener('click', function(e) {
        const panel = document.getElementById('export-float-panel');
        const btn = document.getElementById('sfb-export');
        if (panel.style.display !== 'none' && !panel.contains(e.target) && !btn.contains(e.target)) {
            closeFloatPanel();
        }
    });

    document.querySelectorAll('.fp-fmt').forEach(btn => {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.fp-fmt').forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            fpFormat = this.dataset.format;
        });
    });

    document.getElementById('fp-btn-export').addEventListener('click', function() {
        const start = document.getElementById('fp-export-start').value;
        const end = document.getElementById('fp-export-end').value;
        const baseUrl = `/api/export/${fpFormat}`;
        const params = new URLSearchParams();
        if (start) params.set('start_date', start);
        if (end) params.set('end_date', end);
        const url = params.toString() ? `${baseUrl}?${params.toString()}` : baseUrl;
        window.open(url, '_blank');
        closeFloatPanel();
    });

    document.getElementById('export-float-panel').addEventListener('click', function(e) {
        e.stopPropagation();
    });

    initDrpCalendar();
}

function closeFloatPanel() {
    document.getElementById('export-float-panel').style.display = 'none';
    const cal = document.getElementById('fp-drp-calendar');
    if (cal) cal.classList.remove('open');
}

function initDrpCalendar() {
    const trigger = document.getElementById('fp-drp-trigger');
    const calendar = document.getElementById('fp-drp-calendar');

    trigger.addEventListener('click', function(e) {
        e.stopPropagation();
        const isOpen = calendar.classList.contains('open');
        if (!isOpen) {
            const now = new Date();
            fpCalYear = fpCalYear || now.getFullYear();
            fpCalMonth = fpCalMonth || now.getMonth();
            renderCalendar();
            calendar.classList.add('open');
        } else {
            calendar.classList.remove('open');
        }
    });

    document.addEventListener('click', function(e) {
        if (calendar.classList.contains('open') && !calendar.contains(e.target) && !trigger.contains(e.target)) {
            calendar.classList.remove('open');
        }
    });

    document.getElementById('fp-drp-cal-grid').addEventListener('click', function(e) {
        const td = e.target.closest('td');
        if (!td) return;
        const day = parseInt(td.dataset.day);
        if (!day) return;
        if (td.classList.contains('disabled') || td.classList.contains('other')) return;
        handleDayClick(day);
    });

    document.querySelectorAll('.fp-drp-cal-nav').forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const dir = parseInt(this.dataset.dir);
            fpCalMonth += dir;
            if (fpCalMonth < 0) { fpCalMonth = 11; fpCalYear--; }
            if (fpCalMonth > 11) { fpCalMonth = 0; fpCalYear++; }
            renderCalendar();
        });
    });
}

function renderCalendar() {
    const titleEl = document.getElementById('fp-drp-cal-title');
    const gridEl = document.getElementById('fp-drp-cal-grid');
    const months = ['一月', '二月', '三月', '四月', '五月', '六月', '七月', '八月', '九月', '十月', '十一月', '十二月'];
    titleEl.textContent = `${fpCalYear} ${months[fpCalMonth]}`;

    const firstDay = new Date(fpCalYear, fpCalMonth, 1).getDay();
    const daysInMonth = new Date(fpCalYear, fpCalMonth + 1, 0).getDate();
    const today = new Date();
    const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

    let html = '<table><tr>';
    for (let i = 0; i < firstDay; i++) {
        html += '<td></td>';
    }

    fpCalStartRow = Math.floor(firstDay / 7);
    fpCalStartCol = firstDay % 7;

    for (let d = 1; d <= daysInMonth; d++) {
        const cellDate = `${fpCalYear}-${String(fpCalMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
        const isToday = cellDate === todayStr;
        const isStart = cellDate === fpStartDate;
        const isEnd = cellDate === fpEndDate;
        const isInRange = fpStartDate && fpEndDate && cellDate > fpStartDate && cellDate < fpEndDate;
        const classes = [];
        if (isToday && !isStart && !isEnd) classes.push('today');
        if (isStart) classes.push('range-start');
        if (isEnd) classes.push('range-end');
        if (isInRange && !isStart && !isEnd) classes.push('in-range');
        if (cellDate < todayStr) classes.push('disabled');

        let badge = '';
        if (isStart && isEnd) {
            badge = '<span class="range-badge both">始/终</span>';
        } else if (isStart) {
            badge = '<span class="range-badge start-badge">始</span>';
        } else if (isEnd) {
            badge = '<span class="range-badge end-badge">终</span>';
        }
        html += `<td class="${classes.join(' ')}" data-day="${d}" data-date="${cellDate}">${d}${badge}</td>`;
        if ((firstDay + d) % 7 === 0) html += '</tr><tr>';
    }

    const lastCol = (firstDay + daysInMonth) % 7;
    if (lastCol !== 0) {
        for (let i = lastCol; i < 7; i++) {
            html += '<td></td>';
        }
    }
    html += '</tr></table>';
    gridEl.innerHTML = html;
}

function handleDayClick(day) {
    const cellDate = `${fpCalYear}-${String(fpCalMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
    const today = new Date();
    const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
    if (cellDate > todayStr) return;

    const hintEl = document.getElementById('fp-drp-cal-hint');
    const labelEl = document.getElementById('fp-drp-label');
    const startInput = document.getElementById('fp-export-start');
    const endInput = document.getElementById('fp-export-end');

    if (fpSelectMode === 'start') {
        fpStartDate = cellDate;
        fpEndDate = null;
        fpSelectMode = 'end';
        startInput.value = cellDate;
        endInput.value = '';
        hintEl.innerHTML = `📌 开始：${cellDate}，请选择结束日期`;
        renderCalendar();
    } else {
        if (cellDate < fpStartDate) {
            fpStartDate = cellDate;
            fpEndDate = null;
            startInput.value = cellDate;
            endInput.value = '';
            hintEl.innerHTML = `📌 开始：${cellDate}，请选择结束日期`;
            renderCalendar();
            return;
        }
        fpEndDate = cellDate;
        fpSelectMode = 'start';
        endInput.value = cellDate;
        hintEl.textContent = '✅ 选择完成，可继续调整';
        labelEl.textContent = `${fpStartDate} ~ ${fpEndDate}`;
        renderCalendar();
        setTimeout(() => {
            document.getElementById('fp-drp-calendar').classList.remove('open');
        }, 400);
    }
}

// ===== 热度词云 =====
let trendingData = null;
let trendingCacheTime = 0;

async function loadTrending() {
    const now = Date.now();
    if (trendingData && now - trendingCacheTime < 5 * 60 * 1000) {
        renderTrending(trendingData);
        return;
    }
    try {
        const resp = await fetch('/api/trending');
        const result = await resp.json();
        if (result.success && result.data && result.data.length > 0) {
            trendingData = result.data;
            trendingData._aiGenerated = result.ai_generated || false;
            trendingCacheTime = now;
            renderTrending(result.data, result.ai_generated);
            document.getElementById('trending-section').style.display = '';
        } else {
            document.getElementById('trending-section').style.display = 'none';
        }
    } catch (e) {
        document.getElementById('trending-section').style.display = 'none';
    }
}

function renderTrending(items, aiGenerated) {
    const cloud = document.getElementById('trending-cloud');
    const header = document.getElementById('trending-header');
    const titleEl = header.querySelector('.trending-title');

    if (aiGenerated && items.length > 0 && items[0].topic) {
        titleEl.innerHTML = `
            <svg class="trending-title-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2C8 2 4 5 4 9c0 2.5 1.5 4.5 3 6l-1 3c0 1 1 2 2 2h8c1 0 2-1 2-2l-1-3c1.5-1.5 3-3.5 3-6 0-4-4-7-8-7z"/>
                <path d="M9 13c0 0 1-1 1-2 0-1-1-2-1-2s1 1 1 2c0 1-1 2-1 2z"/>
                <path d="M13 13c0 0 1-1 1-2 0-1-1-2-1-2s1 1 1 2c0 1-1 2-1 2z"/>
                <path d="M11 15c-1 0-2-1-2-2h4c0 1-1 2-2 2z"/>
            </svg>
            24小时热点
            <span class="trending-ai-badge">AI</span>
        `;
        const maxCount = Math.max(...items.map(t => t.count || 1));
        cloud.innerHTML = items.slice(0, 8).map(t => {
            const size = 0.7 + (t.count / maxCount) * 0.65;
            const opacity = 0.5 + (t.count / maxCount) * 0.5;
            const color = `hsl(${220 + (1 - t.count / maxCount) * 60}, 70%, ${45 + (t.count / maxCount) * 20}%)`;
            return `<span class="trending-word" style="font-size:${size}em;opacity:${opacity};color:${color}"
                     data-word="${escapeHtml(t.topic)}"
                     title="${escapeHtml(t.description)} — ${t.count}条相关">${escapeHtml(t.topic)}</span>`;
        }).join('');
    } else {
        titleEl.innerHTML = `
            <svg class="trending-title-icon" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2C8 2 4 5 4 9c0 2.5 1.5 4.5 3 6l-1 3c0 1 1 2 2 2h8c1 0 2-1 2-2l-1-3c1.5-1.5 3-3.5 3-6 0-4-4-7-8-7z"/>
                <path d="M9 13c0 0 1-1 1-2 0-1-1-2-1-2s1 1 1 2c0 1-1 2-1 2z"/>
                <path d="M13 13c0 0 1-1 1-2 0-1-1-2-1-2s1 1 1 2c0 1-1 2-1 2z"/>
                <path d="M11 15c-1 0-2-1-2-2h4c0 1-1 2-2 2z"/>
            </svg>
            24小时热点
        `;
        const maxCount = items[0].count || 1;
        cloud.innerHTML = items.slice(0, 6).map(w => {
            const size = 0.7 + (w.count / maxCount) * 0.65;
            const opacity = 0.5 + (w.count / maxCount) * 0.5;
            const color = `hsl(${220 + (1 - w.count / maxCount) * 60}, 70%, ${45 + (w.count / maxCount) * 20}%)`;
            return `<span class="trending-word" style="font-size:${size}em;opacity:${opacity};color:${color}" data-word="${escapeHtml(w.word)}">${escapeHtml(w.word)}</span>`;
        }).join('');
    }
    cloud.querySelectorAll('.trending-word').forEach(el => {
        el.addEventListener('click', function() {
            const word = this.dataset.word;
            document.getElementById('search-input').value = word;
            document.getElementById('search-clear').classList.add('visible');
            performSearch(word);
        });
    });
}

// ===== WebSocket =====
let ws = null;
let wsReconnectDelay = 1000;

function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws`;
    
    try {
        ws = new WebSocket(url);
    } catch(e) {
        scheduleReconnect();
        return;
    }
    
    ws.onopen = function() {
        wsReconnectDelay = 1000;
    };
    
    ws.onmessage = function(event) {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === 'new_news' && msg.count > 0 && currentPage === 1 && !isSearchMode && !currentSearchQuery) {
                loadNews(false);
            }
        } catch(e) {}
    };
    
    ws.onclose = function() {
        scheduleReconnect();
    };
    
    ws.onerror = function() {
        ws.close();
    };
}

function scheduleReconnect() {
    setTimeout(function() {
        if (document.visibilityState !== 'hidden') {
            connectWebSocket();
        }
        wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
    }, wsReconnectDelay);
}

const TIMELINE_WEEKDAYS = ['周日','周一','周二','周三','周四','周五','周六'];
let timelineData = [];

function initTimelineToggle() {
    const btn = document.getElementById('timeline-toggle-btn');
    const panel = document.getElementById('timeline-panel');
    if (!btn || !panel) return;
    btn.addEventListener('click', function() {
        panel.classList.toggle('open');
    });
    document.addEventListener('click', function(e) {
        if (window.innerWidth > 1200) return;
        if (!panel.classList.contains('open')) return;
        if (!panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
            panel.classList.remove('open');
        }
    });
}

async function loadTimeline() {
    const scroll = document.getElementById('timeline-scroll');
    if (!scroll) return;
    scroll.innerHTML = '<div class="tl-loading"><div class="spinner"></div>加载中...</div>';
    try {
        const resp = await fetch('/api/events');
        const json = await resp.json();
        if (!json.success || !json.data || !json.data.length) {
            scroll.innerHTML = '<div class="tl-empty">暂无事件数据</div>';
            return;
        }
        timelineData = json.data;
        renderTimeline();
        const updateEl = document.getElementById('timeline-update-time');
        if (updateEl && json.updated_at) {
            updateEl.textContent = '更新: ' + json.updated_at;
        }
    } catch (e) {
        scroll.innerHTML = '<div class="tl-empty">加载失败，请刷新重试</div>';
    }
}

function renderTimeline() {
    const scroll = document.getElementById('timeline-scroll');
    if (!scroll) return;
    if (!timelineData.length) {
        scroll.innerHTML = '<div class="tl-empty">暂无事件数据</div>';
        return;
    }
    const today = new Date();
    const todayStr = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');
    const grouped = {};
    timelineData.forEach(function(ev) {
        if (!grouped[ev.date]) grouped[ev.date] = [];
        grouped[ev.date].push(ev);
    });
    const dates = Object.keys(grouped).sort();
    let html = '';
    let todayInserted = false;
    dates.forEach(function(date) {
        if (!todayInserted && date >= todayStr) {
            html += '<div class="tl-today-marker"><span class="tl-today-label">● 今天</span></div>';
            todayInserted = true;
        }
        const d = new Date(date + 'T00:00:00');
        const month = d.getMonth() + 1;
        const day = d.getDate();
        const weekday = TIMELINE_WEEKDAYS[d.getDay()];
        html += '<div class="tl-date-group"><div class="tl-date-node">' + month + '月' + day + '日 <span class="tl-date-weekday">' + weekday + '</span></div>';
        grouped[date].forEach(function(ev) {
            html += '<div class="tl-event-card" data-url="' + escapeHtml(ev.source_url || '') + '" data-importance="' + (ev.importance || 2) + '" data-source="' + escapeHtml(ev.source || '') + '">'
                + '<div class="tl-event-title">' + escapeHtml(ev.title) + '</div>'
                + (ev.description ? '<div class="tl-event-desc">' + escapeHtml(ev.description) + '</div>' : '')
                + '</div>';
        });
        html += '</div>';
    });
    if (!todayInserted) {
        html = '<div class="tl-today-marker"><span class="tl-today-label">● 今天</span></div>' + html;
    }
    scroll.innerHTML = html;
    scroll.querySelectorAll('.tl-event-card').forEach(function(card) {
        card.addEventListener('click', function() {
            const url = this.dataset.url;
            if (url) {
                window.open(url, '_blank');
            }
        });
    });
    scrollToToday();
}

function scrollToToday() {
    const scroll = document.getElementById('timeline-scroll');
    const marker = scroll ? scroll.querySelector('.tl-today-marker') : null;
    if (marker && scroll) {
        setTimeout(function() {
            marker.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 300);
    }
}


