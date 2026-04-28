"""
性能和端到端测试
"""
import os
import sys
import time
import sqlite3
import threading
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import app, db_insert_news, db_get_news, db_count, db_get_all_for_export
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "perf_test.db")
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
        with TestClient(app) as c:
            yield c


class TestPerformance:

    def test_bulk_insert_performance(self, client):
        news_list = []
        for i in range(1000):
            news_list.append({
                "title": f"新闻{i}",
                "url": f"http://test.com/{i}",
                "source": "新浪财经",
                "publish_time": f"2026-01-{(i%28)+1:02d} {(i%24):02d}:00:00",
                "intro": f"摘要{i}" * 20
            })
        start = time.time()
        _, count = db_insert_news(news_list)
        elapsed = time.time() - start
        assert count == 1000
        assert elapsed < 5.0, f"批量插入1000条耗时{elapsed:.2f}秒，超过5秒限制"

    def test_query_response_time_empty_db(self, client):
        start = time.time()
        response = client.get("/api/news?page=1&page_size=10")
        elapsed = time.time() - start
        assert response.status_code == 200
        assert elapsed < 0.5, f"空库查询耗时{elapsed:.3f}秒，超过0.5秒限制"

    def test_query_response_time_with_data(self, client):
        news_list = [{"title": f"新闻{i}", "url": f"http://test.com/{i}", "source": "新浪财经",
                      "publish_time": f"2026-01-{(i%28)+1:02d} 12:00:00", "intro": f"摘要{i}"}
                     for i in range(500)]
        db_insert_news(news_list)
        start = time.time()
        response = client.get("/api/news?page=1&page_size=10")
        elapsed = time.time() - start
        assert response.status_code == 200
        assert elapsed < 0.5, f"500条数据查询耗时{elapsed:.3f}秒，超过0.5秒限制"

    def test_health_check_response_time(self, client):
        start = time.time()
        response = client.get("/api/health")
        elapsed = time.time() - start
        assert response.status_code == 200
        assert elapsed < 0.5, f"健康检查耗时{elapsed:.3f}秒"


class TestConcurrentAccess:

    def test_concurrent_reads(self, client):
        news_list = [{"title": f"新闻{i}", "url": "#", "source": "新浪财经",
                      "publish_time": f"2026-01-{(i%28)+1:02d} 12:00:00", "intro": f"摘要{i}"}
                     for i in range(100)]
        db_insert_news(news_list)

        results = []
        errors = []

        def read_news():
            try:
                start = time.time()
                response = client.get("/api/news?page=1&page_size=10")
                elapsed = time.time() - start
                results.append((response.status_code, elapsed))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=read_news) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发读取错误: {errors}"
        success_count = sum(1 for r in results if r[0] == 200)
        assert success_count >= len(results) * 0.8, f"成功率过低: {success_count}/{len(results)}"


class TestExportEndpoints:

    def test_json_export_with_data(self, client):
        db_insert_news([{"title": "测试", "url": "http://test.com", "source": "新浪财经",
                         "publish_time": "2026-01-01 12:00:00", "intro": "摘要"}])
        response = client.get("/api/export/json")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    def test_html_export_xss_protection(self, client):
        db_insert_news([{"title": "<script>alert('xss')</script>", "url": "#", "source": "新浪财经",
                         "publish_time": "2026-01-01 12:00:00", "intro": "<b>bold</b>"}])
        response = client.get("/api/export/html")
        assert response.status_code == 200
        html_content = response.text
        assert "<script>" not in html_content
        assert "&lt;script&gt;" in html_content or "&#x3C;script&#x3E;" in html_content


class TestPagination:

    def test_pagination_boundary(self, client):
        for i in range(25):
            db_insert_news([{"title": f"新闻{i}", "url": "#", "source": "新浪财经",
                             "publish_time": f"2026-01-{(i%28)+1:02d} 12:00:00", "intro": f"摘要{i}"}])
        response = client.get("/api/news?page=3&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 5

    def test_pagination_last_page(self, client):
        for i in range(7):
            db_insert_news([{"title": f"新闻{i}", "url": "#", "source": "新浪财经",
                             "publish_time": f"2026-01-{(i+1):02d} 12:00:00", "intro": f"摘要{i}"}])
        response = client.get("/api/news?page=2&page_size=5")
        assert response.status_code == 200
        data = response.json()
        assert len(data["data"]) == 2
        assert data["total"] == 7
