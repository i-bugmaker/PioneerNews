import os
import sys
import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (
    app, get_db, db_insert_news, db_get_news, db_count,
    db_get_all_for_export, db_cleanup_if_needed, source_last_ts,
    fetch_news_from_source, fetch_new_news, SOURCE_COLORS
)


@pytest.fixture(autouse=True)
def clean_db(tmp_path):
    db_file = str(tmp_path / "test_news.db")
    with patch("main.DB_PATH", db_file):
        conn = sqlite3.connect(db_file)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT,
                source TEXT NOT NULL,
                publish_time TEXT,
                intro TEXT,
                title_hash TEXT UNIQUE,
                created_at TEXT
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_time ON news(publish_time DESC)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_created ON news(created_at ASC)')
        conn.commit()
        conn.close()
        yield db_file
        try:
            os.remove(db_file)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def reset_source_ts():
    for k in source_last_ts:
        source_last_ts[k] = 0
    yield


# ========== 数据库单元测试 ==========
class TestDatabase:

    def test_insert_single_news(self, clean_db):
        news = [{"title": "测试新闻", "url": "http://test.com", "source": "新浪财经",
                 "publish_time": "2026-01-01 12:00:00", "intro": "测试摘要"}]
        hashes, count = db_insert_news(news)
        assert count == 1
        assert len(hashes) == 1

    def test_insert_duplicate_ignored(self, clean_db):
        news = [{"title": "重复标题", "url": "http://test.com", "source": "新浪财经",
                 "publish_time": "2026-01-01 12:00:00", "intro": "摘要"}]
        db_insert_news(news)
        _, count2 = db_insert_news(news)
        assert count2 == 0

    def test_insert_empty_list(self, clean_db):
        hashes, count = db_insert_news([])
        assert count == 0
        assert hashes == []

    def test_get_news(self, clean_db):
        db_insert_news([
            {"title": "新闻1", "url": "http://1.com", "source": "源A", "publish_time": "2026-01-01 12:00:00", "intro": "摘要1"},
            {"title": "新闻2", "url": "http://2.com", "source": "源B", "publish_time": "2026-01-02 12:00:00", "intro": "摘要2"},
        ])
        result = db_get_news(limit=10, offset=0)
        assert len(result) == 2
        assert result[0]["title"] == "新闻2"

    def test_get_news_pagination(self, clean_db):
        for i in range(25):
            db_insert_news([{"title": f"新闻{i}", "url": f"http://{i}.com", "source": "源",
                             "publish_time": f"2026-01-{(i%28)+1:02d} 12:00:00", "intro": f"摘要{i}"}])
        result = db_get_news(limit=5, offset=0)
        assert len(result) == 5

    def test_db_count(self, clean_db):
        assert db_count() == 0
        db_insert_news([{"title": "A", "url": "#", "source": "X", "publish_time": "2026-01-01 00:00:00", "intro": ""}])
        assert db_count() == 1

    def test_export_by_date(self, clean_db):
        db_insert_news([
            {"title": "A", "url": "#", "source": "X", "publish_time": "2026-01-01 12:00:00", "intro": "a"},
            {"title": "B", "url": "#", "source": "X", "publish_time": "2026-01-15 12:00:00", "intro": "b"},
            {"title": "C", "url": "#", "source": "X", "publish_time": "2026-02-01 12:00:00", "intro": "c"},
        ])
        result = db_get_all_for_export("2026-01-01", "2026-01-31")
        assert len(result) == 2
        assert result[0]["title"] == "B"

    def test_export_no_date_filter(self, clean_db):
        db_insert_news([{"title": "A", "url": "#", "source": "X", "publish_time": "2026-01-01 12:00:00", "intro": ""}])
        result = db_get_all_for_export()
        assert len(result) == 1


# ========== API 集成测试 ==========
class TestAPI:

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_root_page(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_favicon(self, client):
        response = client.get("/favicon.ico")
        assert response.status_code == 200

    def test_health_check(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "news_in_db" in data

    def test_news_api_empty_db(self, client):
        response = client.get("/api/news?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"] == []

    def test_news_api_invalid_params(self, client):
        response = client.get("/api/news?page=0")
        assert response.status_code == 422

        response = client.get("/api/news?page_size=3")
        assert response.status_code == 422

        response = client.get("/api/news?page_size=100")
        assert response.status_code == 422

    def test_export_json(self, client):
        response = client.get("/api/export/json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

    def test_export_check(self, client):
        response = client.get("/api/export/check")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_export_dates(self, client):
        response = client.get("/api/export/dates")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_export_html(self, client):
        response = client.get("/api/export/html")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_reset_news(self, client):
        response = client.post("/api/news/reset")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_news_api_with_data(self, client):
        from main import db_insert_news, source_last_ts
        db_insert_news([{"title": "测试", "url": "http://test.com", "source": "新浪财经",
                         "publish_time": "2026-01-01 12:00:00", "intro": "摘要"}])
        response = client.get("/api/news")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) >= 1


# ========== 边缘场景测试 ==========
class TestEdgeCases:

    def test_insert_news_with_empty_fields(self, clean_db):
        news = [{"title": "", "url": "", "source": "",
                 "publish_time": "", "intro": ""}]
        hashes, count = db_insert_news(news)
        assert count == 1

    def test_insert_very_long_title(self, clean_db):
        long_title = "x" * 5000
        news = [{"title": long_title, "url": "#", "source": "新浪财经",
                 "publish_time": "2026-01-01 00:00:00", "intro": "x" * 5000}]
        hashes, count = db_insert_news(news)
        assert count == 1

    def test_insert_special_chars_in_title(self, clean_db):
        news = [{"title": "<script>alert(1)</script>", "url": "#", "source": "新浪财经",
                 "publish_time": "2026-01-01 00:00:00", "intro": "test"}]
        hashes, count = db_insert_news(news)
        assert count == 1

    def test_source_colors(self):
        assert len(SOURCE_COLORS) == 7
        for color in SOURCE_COLORS.values():
            assert color.startswith("#")

    def test_source_last_ts_all_sources_zeroed(self):
        for k in source_last_ts:
            source_last_ts[k] = 1000
        source_last_ts["新浪财经"] = 2000
        for k in source_last_ts:
            source_last_ts[k] = 0
        assert all(v == 0 for v in source_last_ts.values())


# ========== 抓取逻辑模拟测试 ==========
class TestFetchLogic:

    @pytest.mark.asyncio
    async def test_fetch_source_failure(self):
        with patch("main.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.side_effect = Exception("连接失败")
            result = await fetch_news_from_source({"name": "新浪财经", "url": "http://test", "headers": {}})
            assert result == []

    @pytest.mark.asyncio
    async def test_fetch_source_non_200(self):
        mock_response = MagicMock()
        mock_response.status_code = 500

        class MockClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def get(self, **kwargs):
                return mock_response

        with patch("main.httpx.AsyncClient", return_value=MockClient()):
            result = await fetch_news_from_source({"name": "新浪财经", "url": "http://test", "headers": {}})
            assert result == []
