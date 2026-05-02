import pytest
from datetime import datetime, date
from core.message_fetcher import MessageFetcher, Message


class TestMessageFetcher:
    @pytest.fixture
    def mock_decryptor(self):
        class MockDecryptor:
            def execute_query(self, sql, params=()):
                return [
                    (1, 1714636800, 1, 0, 1, "\U0001f6a95.2 任务\n1\ufe0f\u20e3 订正作文", "张三"),
                    (2, 1714637000, 1, 0, 0, "好的收到", "自己"),
                ]

            def close(self):
                pass

            def connection(self):
                return self

        return MockDecryptor()

    def test_fetch_text_messages(self, mock_decryptor):
        fetcher = MessageFetcher(mock_decryptor)
        messages = fetcher.fetch_messages(
            chat_id="test_chatroom",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 2),
        )
        assert len(messages) == 2
        assert messages[0].content == "\U0001f6a95.2 任务\n1\ufe0f\u20e3 订正作文"
        assert messages[0].sender == "张三"
        assert messages[0].type == 1

    def test_filter_task_messages_only(self, mock_decryptor):
        fetcher = MessageFetcher(mock_decryptor)
        messages = fetcher.fetch_messages(
            chat_id="test_chatroom",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 2),
            task_only=True,
        )
        assert len(messages) == 1
        assert "\U0001f6a9" in messages[0].content

    def test_message_datetime_property(self, mock_decryptor):
        fetcher = MessageFetcher(mock_decryptor)
        messages = fetcher.fetch_messages(
            chat_id="test_chatroom",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 5, 2),
        )
        assert messages[0].datetime == datetime.fromtimestamp(1714636800)

    def test_get_chatrooms_returns_list(self, mock_decryptor):
        """get_chatrooms should return a list even when ChatRoom table doesn't exist."""
        class MockDecryptorNoChatroom:
            def execute_query(self, sql, params=()):
                raise Exception("no such table: ChatRoom")

            def close(self):
                pass

        fetcher = MessageFetcher(MockDecryptorNoChatroom())
        chatrooms = fetcher.get_chatrooms()
        assert chatrooms == []

    def test_fetch_messages_empty_result(self, mock_decryptor):
        class MockDecryptorEmpty:
            def execute_query(self, sql, params=()):
                return []

            def close(self):
                pass

            def connection(self):
                return self

        fetcher = MessageFetcher(MockDecryptorEmpty())
        messages = fetcher.fetch_messages(
            chat_id="nonexistent",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
        )
        assert messages == []
