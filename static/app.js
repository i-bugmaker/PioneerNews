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

    initEmojiSystem();
    initNewTagObserver();
    initScrollAutoInsert();

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

    const debouncedSearch = debounce((query) => {
        if (query) performSearch(query);
    }, 300);

    searchInput.addEventListener('input', function() {
        const val = this.value.trim();
        searchClear.style.display = val ? 'block' : 'none';
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
        searchClear.style.display = 'none';
        exitSearchMode();
    });

    // 导出功能 - 自定义下拉组件
    const FORMAT_NAMES = { json: 'JSON', html: 'HTML', csv: 'CSV', md: 'Markdown', jsonl: 'JSONL' };
    let currentFormat = 'json';
    let dropdownOpen = false;

    // ----- 自定义下拉交互 -----
    const dd = document.getElementById('fmt-dropdown');
    const trigger = document.getElementById('fmt-trigger');
    const menu = document.getElementById('fmt-menu');
    const triggerIcon = document.getElementById('fmt-trigger-icon');
    const triggerLabel = document.getElementById('fmt-trigger-label');

    function openDropdown() {
        dropdownOpen = true;
        trigger.setAttribute('aria-expanded', 'true');
        menu.classList.add('open');
        dd.classList.add('open');
    }

    function closeDropdown() {
        dropdownOpen = false;
        trigger.setAttribute('aria-expanded', 'false');
        menu.classList.remove('open');
        dd.classList.remove('open');
    }

    function selectFormat(fmt) {
        if (!fmt || fmt === currentFormat) return;
        currentFormat = fmt;

        // Update trigger
        const activeOpt = menu.querySelector('.fmt-option.active');
        const newOpt = menu.querySelector(`.fmt-option[data-format="${fmt}"]`);
        if (activeOpt) {
            activeOpt.classList.remove('active');
            activeOpt.setAttribute('aria-selected', 'false');
        }
        if (newOpt) {
            newOpt.classList.add('active');
            newOpt.setAttribute('aria-selected', 'true');
            // Copy icon SVG from option to trigger
            const optSvg = newOpt.querySelector('.fmt-opt-icon svg');
            if (optSvg) {
                triggerIcon.innerHTML = optSvg.outerHTML;
            }
            triggerLabel.textContent = newOpt.querySelector('.fmt-opt-label').textContent;
        }

        closeDropdown();
    }

    // Toggle on trigger click
    trigger.addEventListener('click', function(e) {
        e.stopPropagation();
        dropdownOpen ? closeDropdown() : openDropdown();
    });

    // Option click
    menu.addEventListener('click', function(e) {
        const option = e.target.closest('.fmt-option');
        if (option) {
            e.stopPropagation();
            selectFormat(option.dataset.format);
        }
    });

    // Close on outside click
    document.addEventListener('click', function(e) {
        if (dropdownOpen && !dd.contains(e.target)) {
            closeDropdown();
        }
    });

    // Close on Escape
    dd.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && dropdownOpen) {
            closeDropdown();
            trigger.focus();
        }
    });

    // Focus management: Enter/Space to toggle
    trigger.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            dropdownOpen ? closeDropdown() : openDropdown();
        }
        if (e.key === 'ArrowDown' && !dropdownOpen) {
            e.preventDefault();
            openDropdown();
        }
    });

    // Keyboard navigation inside menu
    menu.addEventListener('keydown', function(e) {
        const items = [...menu.querySelectorAll('.fmt-option')];
        const idx = items.findIndex(i => i.classList.contains('active'));
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            const next = (idx + 1) % items.length;
            items[next].focus();
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            const prev = (idx - 1 + items.length) % items.length;
            items[prev].focus();
        } else if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            const focused = menu.querySelector('.fmt-option:focus');
            if (focused) selectFormat(focused.dataset.format);
        }
    });

    async function doExport() {
        const format = currentFormat;
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

            if (!confirm(`📥 将以 ${FORMAT_NAMES[format] || format} 格式导出 ${info.date_range} 的 ${info.count} 条新闻，是否继续？`)) {
                return;
            }

            window.open(`/api/export/${format}${query}`, '_blank');
        } catch (e) {
            alert('导出失败，请重试');
        }
    }

    document.getElementById('btn-export').addEventListener('click', doExport);

    // 自定义日期范围选择器：一个日历面板连选开始和结束
    (async function initDateRangePicker() {
        try {
            const res = await fetch('/api/export/dates');
            const info = await res.json();
            if (!info.success || !info.dates.length) return;

            const sdEl = document.getElementById('export-start');
            const edEl = document.getElementById('export-end');
            const trigger = document.getElementById('drp-trigger');
            const label = document.getElementById('drp-label');
            const calendar = document.getElementById('drp-calendar');
            const grid = document.getElementById('drp-cal-grid');
            const titleEl = document.getElementById('drp-cal-title');
            const hintEl = document.getElementById('drp-cal-hint');

            const datesAsc = [...info.dates].sort();
            const minDate = datesAsc[0];
            const maxDate = datesAsc[datesAsc.length - 1];

            sdEl.min = minDate;
            sdEl.max = maxDate;
            edEl.min = minDate;
            edEl.max = maxDate;

            let viewYear, viewMonth;  // 当前日历视图
            let step = 'start';       // 'start' | 'end'
            let startDate = null;     // Date 对象
            let endDate = null;       // Date 对象

            function fmt(n) { return String(n).padStart(2, '0'); }
            function toYmd(d) { return d.getFullYear() + '-' + fmt(d.getMonth() + 1) + '-' + fmt(d.getDate()); }

            function updateLabel() {
                const s = sdEl.value;
                const e = edEl.value;
                if (s && e) {
                    label.textContent = s + ' ~ ' + e;
                } else {
                    label.textContent = '选择日期范围';
                }
            }

            function syncInputs() {
                sdEl.value = startDate ? toYmd(startDate) : '';
                edEl.value = endDate ? toYmd(endDate) : '';
                label.textContent = startDate && endDate
                    ? toYmd(startDate) + ' ~ ' + toYmd(endDate)
                    : '选择日期范围';
            }

            function renderCalendar() {
                const firstDay = new Date(viewYear, viewMonth, 1);
                const lastDay = new Date(viewYear, viewMonth + 1, 0);
                const startDow = firstDay.getDay();  // 0=Sun
                const totalDays = lastDay.getDate();

                titleEl.textContent = viewYear + '年' + (viewMonth + 1) + '月';
                grid.innerHTML = '';

                // 上月填充
                const prevLast = new Date(viewYear, viewMonth, 0).getDate();
                for (let i = startDow - 1; i >= 0; i--) {
                    const day = prevLast - i;
                    const btn = document.createElement('button');
                    btn.className = 'drp-cal-day other';
                    btn.textContent = day;
                    btn.type = 'button';
                    btn.disabled = true;
                    grid.appendChild(btn);
                }

                // 当月
                for (let d = 1; d <= totalDays; d++) {
                    const date = new Date(viewYear, viewMonth, d);
                    const ymd = toYmd(date);
                    const btn = document.createElement('button');
                    btn.className = 'drp-cal-day';
                    btn.textContent = d;
                    btn.type = 'button';
                    btn.dataset.date = ymd;

                    // 超出可用范围
                    if (ymd < minDate || ymd > maxDate) {
                        btn.classList.add('disabled');
                        btn.disabled = true;
                    }

                    // 今天标记
                    const today = new Date();
                    if (d === today.getDate() && viewMonth === today.getMonth() && viewYear === today.getFullYear()) {
                        btn.classList.add('today');
                    }

                    // 日期状态
                    if (startDate && ymd === toYmd(startDate)) {
                        btn.classList.add('start');
                    }
                    if (endDate && ymd === toYmd(endDate)) {
                        btn.classList.add('end');
                    }
                    if (startDate && endDate && ymd > toYmd(startDate) && ymd < toYmd(endDate)) {
                        btn.classList.add('in-range');
                    }
                    // 仅选了开始，中间 < 开始且 > 今天的 disabled
                    if (step === 'end' && startDate) {
                        if (ymd < toYmd(startDate)) {
                            btn.classList.add('disabled');
                            btn.disabled = true;
                        }
                    }

                    btn.addEventListener('click', function(e) {
                        e.stopPropagation();
                        const clicked = new Date(this.dataset.date);
                        if (step === 'start') {
                            startDate = clicked;
                            endDate = null;
                            step = 'end';
                            hintEl.textContent = '请点击选择结束日期';
                            renderCalendar();
                        } else {
                            if (clicked < startDate) {
                                startDate = clicked;
                                endDate = null;
                                hintEl.textContent = '请点击选择结束日期';
                                renderCalendar();
                                return;
                            }
                            endDate = clicked;
                            step = 'start';
                            syncInputs();
                            calendar.classList.remove('open');
                            hintEl.textContent = '请点击选择开始日期';
                        }
                    });

                    grid.appendChild(btn);
                }

                // 下月填充
                const remaining = 42 - (startDow + totalDays);
                for (let d = 1; d <= remaining; d++) {
                    const btn = document.createElement('button');
                    btn.className = 'drp-cal-day other';
                    btn.textContent = d;
                    btn.type = 'button';
                    btn.disabled = true;
                    grid.appendChild(btn);
                }
            }

            function openCalendar() {
                // 重置状态
                startDate = null;
                endDate = null;
                step = 'start';
                hintEl.textContent = '请点击选择开始日期';
                const today = new Date();
                viewYear = today.getFullYear();
                viewMonth = today.getMonth();
                renderCalendar();
                calendar.classList.add('open');
            }

            function closeCalendar() {
                calendar.classList.remove('open');
                // 如果面板关闭时已选完，同步
                if (startDate && endDate) {
                    syncInputs();
                } else {
                    // 未选完则清除
                    startDate = null;
                    endDate = null;
                    sdEl.value = '';
                    edEl.value = '';
                    updateLabel();
                }
            }

            // 点击触发按钮
            trigger.addEventListener('click', function(e) {
                e.stopPropagation();
                if (calendar.classList.contains('open')) {
                    closeCalendar();
                } else {
                    openCalendar();
                }
            });

            // 月导航
            document.querySelectorAll('.drp-cal-nav').forEach(function(btn) {
                btn.addEventListener('click', function(e) {
                    e.stopPropagation();
                    const dir = parseInt(this.dataset.dir);
                    viewMonth += dir;
                    if (viewMonth < 0) { viewMonth = 11; viewYear--; }
                    if (viewMonth > 11) { viewMonth = 0; viewYear++; }
                    renderCalendar();
                });
            });

            // 点击外部关闭
            document.addEventListener('click', function(e) {
                var picker = document.getElementById('date-range-picker');
                if (picker && !picker.contains(e.target) && calendar.classList.contains('open')) {
                    closeCalendar();
                }
            });

            // 关闭日历 → 确认选择
            // 如果面板开着但点外部关闭，未选完就清空
            updateLabel();
        } catch (e) { /* 忽略 */ }
    })();
});

function cancelAndReload() {
    isRefreshing = false;
    pendingNewList = [];
    pendingHashes.clear();
    unreadCount = 0;
    hasLoaded = false;
    hideNewContentBar();
    loadNews(true);
}

function startAutoRefresh() {
    if (autoRefreshTimer) clearInterval(autoRefreshTimer);
    autoRefreshTimer = setInterval(() => {
        if (currentPage === 1 && !isSearchMode) loadNews(false);
    }, REFRESH_INTERVAL);
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

            // 检查是否需要完全重新渲染（搜索模式变化或者首次加载）
            if (!hasLoaded || isSearchMode !== previousSearchMode) {
                renderNews(result.data, []);
                hasLoaded = true;
                containerEl.style.display = 'grid';
                // 初始渲染后检查NEW标签是否需要开始倒计时
                setTimeout(checkNewTagVisibility, 50);
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
        // 移除空状态提示
        container.querySelectorAll('.empty-msg').forEach(el => el.remove());

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
            
            registerCardForNewTag(card);
        });

        setTimeout(() => {
            existingCards.forEach(card => {
                card.style.transition = '';
                card.style.transform = '';
            });
            container.querySelectorAll('.card-inserting').forEach(card => {
                card.classList.remove('card-inserting');
                card.style.animation = '';
                // 清理内容分层动画
                card.querySelectorAll('h3, .meta, .intro').forEach(el => {
                    el.style.animation = '';
                });
            });
            // 触发NEW标签可见性检查（解决视口在顶部时不触发scroll的问题）
            checkNewTagVisibility();
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

function updatePagination() {
    const totalPages = Math.max(1, Math.ceil(totalNews / pageSize));
    document.getElementById('page-info').textContent = `共 ${totalNews} 条`;
    document.getElementById('page-indicator').textContent = `${currentPage} / ${totalPages}`;
    document.getElementById('first-page').disabled = currentPage <= 1;
    document.getElementById('prev-page').disabled = currentPage <= 1;
    document.getElementById('next-page').disabled = currentPage >= totalPages;
}

function renderNews(newsList, newHashes) {
    const container = document.getElementById('news-container');

    if (!newsList || !newsList.length) {
        const emptyMsg = isSearchMode ? '没有找到相关结果' : '暂无新闻';
        container.innerHTML = `<p class="empty-msg" style="text-align:center;color:#999;padding:40px;">${emptyMsg}</p>`;
        return;
    }

    // 移除空状态提示并清空容器
    container.querySelectorAll('.empty-msg').forEach(el => el.remove());
    container.innerHTML = '';
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
            if (newHashesSet.has(h)) {
                registerCardForNewTag(card);
            }
        }
    }

    existing.forEach((card, hash) => {
        if (!newsHashes.has(hash)) {
            card.remove();
        } else {
            if (newHashesSet.has(hash) && !card.classList.contains('news-new')) {
                card.classList.add('news-new');
                registerCardForNewTag(card);
            } else if (!newHashesSet.has(hash)) {
                card.classList.remove('news-new');
            }
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

    const dedupTag = news.dedup_count >= 2
        ? `<span class="dedup-tag" onclick="event.stopPropagation(); toggleDedupExpand(this.closest('.news-card'), ${news.dedup_group})">相似 ${news.dedup_count} 条</span>`
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
            const srcColor = SOURCE_COLORS[item.source] || '#3498db';
            return `<div class="dedup-similar-item" onclick="event.stopPropagation(); window.open('${item.url}', '_blank')">
                <span class="sim-source" style="background:${srcColor}">${escapeHtml(item.source)}</span>
                <span class="sim-title">${escapeHtml(item.title)}</span>
                <span class="sim-time">${formatTime(item.publish_time, item.publish_ts)}</span>
            </div>`;
        }).join('');
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

function escapeHtml(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

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
    searchBtn.textContent = '搜索中...';

    window.scrollTo({ top: 0, behavior: 'smooth' });

    currentSearchQuery = query;
    isSearchMode = true;
    currentPage = 1;
    
    try {
        await cancelAndReload();
    } finally {
        searchBtn.disabled = false;
        searchBtn.textContent = '搜索';
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
    const response = await fetch(`${SEARCH_URL}?query=${encodeURIComponent(query)}&page=${page}&page_size=${pageSize}`);
    return await response.json();
}

window.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        loadNews(false);
    }
});

// ========== 表情符号互动系统 ==========
// NEW标签优雅消失系统
const newTagTimers = new Map();
const newTagVisibility = new Map();
let newTagCleanupInterval = null;

function initNewTagObserver() {
    window.addEventListener('scroll', checkNewTagVisibility, { passive: true });
    // 兜底：每15秒扫描一次未处理的NEW标签（防scroll不触发）
    newTagCleanupInterval = setInterval(checkNewTagVisibility, 15000);
}

function checkNewTagVisibility() {
    const cards = document.querySelectorAll('.news-new');
    const viewportTop = window.scrollY;
    const viewportBottom = viewportTop + window.innerHeight;

    cards.forEach(card => {
        if (!card.classList.contains('news-new')) return;
        
        const rect = card.getBoundingClientRect();
        const cardTop = viewportTop + rect.top;
        const cardBottom = cardTop + rect.height;

        const visibleInViewport = Math.min(cardBottom, viewportBottom) - Math.max(cardTop, viewportTop);
        
        if (visibleInViewport >= 100) {
            if (!newTagVisibility.has(card)) {
                newTagVisibility.set(card, Date.now());
                
                // 4秒后开始渐变消失动画
                const startFadeTimer = setTimeout(() => {
                    card.classList.add('new-tag-fading');
                }, 4000);
                newTagTimers.set(card, startFadeTimer);
                
                // 5秒后完全移除NEW标签类
                const removeTimer = setTimeout(() => {
                    card.classList.remove('news-new');
                    newTagVisibility.delete(card);
                    newTagTimers.delete(card);
                }, 5000);
                newTagTimers.set(card + '_remove', removeTimer);
            }
        }
    });
}

function removeNewTag(card) {
    if (!card || !card.classList.contains('news-new')) return;
    
    card.classList.add('new-tag-fading');
    
    setTimeout(() => {
        card.classList.remove('news-new');
        card.classList.remove('new-tag-fading');
    }, 800);
    
    newTagTimers.forEach((timer, key) => {
        if (key === card || key === card + '_remove') {
            clearTimeout(timer);
            newTagTimers.delete(key);
        }
    });
    newTagVisibility.delete(card);
}

function registerCardForNewTag(card) {
    if (!card || !card.classList.contains('news-new')) return;

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
