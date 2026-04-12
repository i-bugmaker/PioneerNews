const API_URL = '/api/news';
const REFRESH_INTERVAL = 60000;

let autoRefreshTimer = null;
let previousNewsIds = [];

document.addEventListener('DOMContentLoaded', function() {
    loadNews();
    setupEventListeners();
    startAutoRefresh();
});

function setupEventListeners() {
    const refreshBtn = document.getElementById('refresh-btn');
    refreshBtn.addEventListener('click', function() {
        loadNews(true, true);
    });
}

function startAutoRefresh() {
    if (autoRefreshTimer) {
        clearInterval(autoRefreshTimer);
    }
    autoRefreshTimer = setInterval(() => {
        loadNews(false);
    }, REFRESH_INTERVAL);
}

async function loadNews(showLoading = true, checkNew = false) {
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
            const newNewsIds = result.data.map(n => n.title + n.publish_time);
            const hasNewNews = checkNew && previousNewsIds.length > 0 && 
                               newNewsIds.some(id => !previousNewsIds.includes(id));
            
            renderNews(result.data, hasNewNews);
            
            if (hasNewNews || !checkNew) {
                previousNewsIds = newNewsIds;
            }
            
            updateTimeEl.textContent = `更新时间：${result.update_time}`;
            errorEl.style.display = 'none';
            containerEl.style.display = 'grid';
        } else {
            showError(result.message || '获取新闻失败');
        }
    } catch (error) {
        console.error('加载新闻失败:', error);
        showError('网络错误，请检查连接');
    } finally {
        if (showLoading) {
            loadingEl.classList.remove('active');
        }
    }
}

function renderNews(newsList, hasNewNews = false) {
    const containerEl = document.getElementById('news-container');
    
    if (!newsList || newsList.length === 0) {
        containerEl.innerHTML = '<p style="text-align:center;color:#999;padding:40px;">暂无新闻</p>';
        return;
    }

    const html = newsList.map((news, index) => {
        const time = formatTime(news.publish_time);
        const source = news.source || '未知来源';
        const intro = news.intro || '暂无摘要';
        const url = news.url || '#';
        const isNew = index < 3 && hasNewNews;
        
        return `
            <div class="news-card ${isNew ? 'news-new' : ''}" onclick="window.open('${url}', '_blank')" style="animation-delay: ${index * 0.1}s">
                <h3>📰 ${isNew ? '<span class="new-badge">[新增]</span> ' : ''}${escapeHtml(news.title)}</h3>
                <div class="meta">
                    <span class="source-tag">${escapeHtml(source)}</span>
                    <span>🕐 ${time}</span>
                </div>
                <p class="intro">${escapeHtml(intro)}</p>
            </div>
        `;
    }).join('');

    containerEl.innerHTML = html;
}

function showError(message) {
    const errorEl = document.getElementById('error-message');
    const containerEl = document.getElementById('news-container');
    
    errorEl.style.display = 'block';
    errorEl.querySelector('p').textContent = `⚠️ ${message}`;
    containerEl.style.display = 'none';
}

function formatTime(timeStr) {
    if (!timeStr) return '--';
    
    try {
        const date = new Date(timeStr);
        if (isNaN(date.getTime())) {
            return timeStr;
        }
        
        return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
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
        if (autoRefreshTimer) {
            clearInterval(autoRefreshTimer);
            autoRefreshTimer = null;
        }
    } else {
        startAutoRefresh();
    }
});
