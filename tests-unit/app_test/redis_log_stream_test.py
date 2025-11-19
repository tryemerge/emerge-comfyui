"""
Unit tests for Redis Log Streaming Module

Tests the RedisLogStreamer class which routes ComfyUI logs to Redis:
- Critical logs → job:events:{job_id} stream (guaranteed delivery)
- All logs → machine:{machine_id}:logs pub/sub (ephemeral monitoring)
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call
import time
import os

# Mock redis module before importing redis_log_stream
import sys
redis_mock = MagicMock()
sys.modules['redis'] = redis_mock


class TestRedisLogStreamer(unittest.TestCase):
    """Test suite for RedisLogStreamer"""

    def setUp(self):
        """Set up test fixtures"""
        # Reset redis availability flag
        import app.redis_log_stream as rls_module
        rls_module.REDIS_AVAILABLE = True

        # Create fresh instance for each test
        self.streamer = None

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    def test_init_with_hub_redis_url(self, mock_redis):
        """Test initialization with HUB_REDIS_URL environment variable"""
        from app.redis_log_stream import RedisLogStreamer

        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Verify Redis connection was created with HUB_REDIS_URL
        mock_redis.from_url.assert_called_once_with(
            'redis://localhost:6379',
            decode_responses=True
        )
        mock_client.ping.assert_called_once()
        self.assertTrue(streamer.enabled)
        self.assertEqual(streamer.machine_id, 'test-machine-123')

    @patch.dict(os.environ, {
        'REDIS_HOST': 'redis.example.com',
        'REDIS_PORT': '6380',
        'REDIS_DB': '1',
        'REDIS_PASSWORD': 'secret',
        'MACHINE_ID': 'test-machine-456'
    }, clear=True)
    @patch('app.redis_log_stream.redis')
    def test_init_with_individual_env_vars(self, mock_redis):
        """Test initialization with individual Redis environment variables"""
        from app.redis_log_stream import RedisLogStreamer

        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.Redis.return_value = mock_client

        streamer = RedisLogStreamer()

        # Verify Redis connection was created with individual env vars
        mock_redis.Redis.assert_called_once_with(
            host='redis.example.com',
            port=6380,
            db=1,
            password='secret',
            decode_responses=True
        )
        mock_client.ping.assert_called_once()
        self.assertTrue(streamer.enabled)

    @patch('app.redis_log_stream.redis')
    def test_init_redis_connection_failure(self, mock_redis):
        """Test graceful degradation when Redis connection fails"""
        from app.redis_log_stream import RedisLogStreamer

        mock_redis.from_url.side_effect = Exception("Connection refused")

        streamer = RedisLogStreamer()

        self.assertFalse(streamer.enabled)
        self.assertIsNone(streamer.redis_client)

    @patch('app.redis_log_stream.redis')
    def test_get_current_job_id_from_executor(self, mock_redis):
        """Test dynamic job_id extraction from executor's extra_data"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Mock PromptServer and executor
        mock_executor = Mock()
        mock_executor.extra_data = {'job_id': 'job-789'}

        mock_server = Mock()
        mock_server.prompt_executor = mock_executor

        with patch('app.redis_log_stream.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            job_id = streamer._get_current_job_id()

            self.assertEqual(job_id, 'job-789')

    @patch('app.redis_log_stream.redis')
    def test_get_current_job_id_no_executor(self, mock_redis):
        """Test job_id extraction returns None when no executor"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Mock PromptServer with no executor
        mock_server = Mock()
        mock_server.prompt_executor = None

        with patch('app.redis_log_stream.PromptServer') as MockPromptServer:
            MockPromptServer.instance = mock_server

            job_id = streamer._get_current_job_id()

            self.assertIsNone(job_id)

    @patch('app.redis_log_stream.redis')
    def test_is_critical_detects_error_patterns(self, mock_redis):
        """Test critical pattern detection for various error messages"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Test critical patterns
        critical_cases = [
            {'m': 'ERROR: Model not found'},
            {'m': 'Exception in workflow execution'},
            {'m': 'Failed to load checkpoint'},
            {'m': 'Traceback (most recent call last)'},
            {'m': 'CUDA error: out of memory'},
            {'m': 'fatal error occurred'},
            {'m': 'Segmentation fault (core dumped)'},
        ]

        for case in critical_cases:
            self.assertTrue(
                streamer._is_critical(case),
                f"Should detect '{case['m']}' as critical"
            )

        # Test non-critical patterns
        non_critical_cases = [
            {'m': 'INFO: Model loaded successfully'},
            {'m': 'DEBUG: Processing node 5/10'},
            {'m': 'Workflow completed'},
        ]

        for case in non_critical_cases:
            self.assertFalse(
                streamer._is_critical(case),
                f"Should NOT detect '{case['m']}' as critical"
            )

    @patch('app.redis_log_stream.redis')
    def test_extract_log_level(self, mock_redis):
        """Test log level extraction from messages"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        test_cases = [
            ('CRITICAL: System failure', 'CRITICAL'),
            ('ERROR: File not found', 'ERROR'),
            ('WARNING: Deprecated API', 'WARNING'),
            ('INFO: Process started', 'INFO'),
            ('DEBUG: Variable value = 42', 'DEBUG'),
            ('Some message without level', 'UNKNOWN'),
        ]

        for message, expected_level in test_cases:
            level = streamer._extract_log_level(message)
            self.assertEqual(
                level, expected_level,
                f"Should extract '{expected_level}' from '{message}'"
            )

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    @patch('app.redis_log_stream.time')
    def test_publish_to_stream(self, mock_time, mock_redis):
        """Test publishing critical logs to job events stream"""
        from app.redis_log_stream import RedisLogStreamer

        mock_time.time.return_value = 1234567890.123

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_client.xadd = Mock()
        mock_client.expire = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Test publishing to stream
        streamer._publish_to_stream(
            timestamp='1234567890.123',
            message='ERROR: Model not found',
            log_level='ERROR',
            job_id='job-789'
        )

        # Verify stream write
        mock_client.xadd.assert_called_once()
        call_args = mock_client.xadd.call_args

        self.assertEqual(call_args[0][0], 'job:events:job-789')
        message_data = call_args[0][1]
        self.assertEqual(message_data['event_type'], 'log')
        self.assertEqual(message_data['level'], 'ERROR')
        self.assertEqual(message_data['message'], 'ERROR: Model not found')
        self.assertEqual(message_data['source'], 'comfyui')

        # Verify TTL set
        mock_client.expire.assert_called_once_with('job:events:job-789', 3600)

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    def test_publish_to_pubsub(self, mock_redis):
        """Test publishing all logs to pub/sub channel"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_client.publish = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Test publishing to pub/sub
        streamer._publish_to_pubsub(
            timestamp='1234567890.123',
            message='INFO: Processing node 5/10',
            log_level='INFO',
            job_id='job-789'
        )

        # Verify pub/sub publish
        mock_client.publish.assert_called_once()
        call_args = mock_client.publish.call_args

        self.assertEqual(call_args[0][0], 'machine:test-machine-123:logs')

        # Verify JSON payload
        import json
        payload = json.loads(call_args[0][1])
        self.assertEqual(payload['level'], 'INFO')
        self.assertEqual(payload['message'], 'INFO: Processing node 5/10')
        self.assertEqual(payload['source'], 'comfyui')
        self.assertEqual(payload['job_id'], 'job-789')

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    def test_handle_logs_routes_critical_to_stream(self, mock_redis):
        """Test that critical logs are routed to both stream and pub/sub"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_client.xadd = Mock()
        mock_client.expire = Mock()
        mock_client.publish = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Mock job_id extraction
        with patch.object(streamer, '_get_current_job_id', return_value='job-789'):
            # Test handling critical log
            log_entries = [
                {'t': 1234567890.123, 'm': 'ERROR: Critical failure'}
            ]

            streamer.handle_logs(log_entries)

            # Verify both stream and pub/sub were called
            mock_client.xadd.assert_called_once()  # Stream
            mock_client.publish.assert_called_once()  # Pub/sub

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    def test_handle_logs_routes_non_critical_to_pubsub_only(self, mock_redis):
        """Test that non-critical logs only go to pub/sub"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_client.xadd = Mock()
        mock_client.publish = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Mock job_id extraction
        with patch.object(streamer, '_get_current_job_id', return_value='job-789'):
            # Test handling non-critical log
            log_entries = [
                {'t': 1234567890.123, 'm': 'INFO: Processing normally'}
            ]

            streamer.handle_logs(log_entries)

            # Verify only pub/sub was called
            mock_client.xadd.assert_not_called()  # No stream
            mock_client.publish.assert_called_once()  # Pub/sub only

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    def test_handle_logs_skips_empty_messages(self, mock_redis):
        """Test that empty or whitespace-only messages are skipped"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_client.publish = Mock()
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Test handling empty messages
        log_entries = [
            {'t': 1234567890.123, 'm': ''},
            {'t': 1234567890.124, 'm': '   '},
            {'t': 1234567890.125, 'm': '\n\t  '},
        ]

        streamer.handle_logs(log_entries)

        # Verify nothing was published
        mock_client.publish.assert_not_called()

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    def test_handle_logs_continues_on_individual_errors(self, mock_redis):
        """Test that errors in individual log processing don't crash entire batch"""
        from app.redis_log_stream import RedisLogStreamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_client.publish = Mock(side_effect=[Exception("Network error"), None])
        mock_redis.from_url.return_value = mock_client

        streamer = RedisLogStreamer()

        # Test handling batch with error in first message
        log_entries = [
            {'t': 1234567890.123, 'm': 'First message'},
            {'t': 1234567890.124, 'm': 'Second message'},
        ]

        with patch.object(streamer, '_get_current_job_id', return_value='job-789'):
            # Should not raise exception
            streamer.handle_logs(log_entries)

            # Verify both messages were attempted
            self.assertEqual(mock_client.publish.call_count, 2)


class TestRedisLogStreamerInitialization(unittest.TestCase):
    """Test suite for init_redis_log_stream() function"""

    @patch.dict(os.environ, {
        'HUB_REDIS_URL': 'redis://localhost:6379',
        'MACHINE_ID': 'test-machine-123'
    })
    @patch('app.redis_log_stream.redis')
    @patch('app.logger')
    def test_init_redis_log_stream_registers_callback(self, mock_logger_module, mock_redis):
        """Test that init_redis_log_stream() registers flush callback"""
        from app.redis_log_stream import init_redis_log_stream, _streamer

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        # Mock logger's on_flush
        mock_logger = Mock()
        mock_logger.on_flush = Mock()
        mock_logger_module.on_flush = Mock()

        # Initialize
        init_redis_log_stream()

        # Verify callback was registered
        mock_logger_module.on_flush.assert_called_once()

    @patch('app.redis_log_stream.redis')
    def test_init_redis_log_stream_idempotent(self, mock_redis):
        """Test that init_redis_log_stream() can only be called once"""
        from app.redis_log_stream import init_redis_log_stream
        import app.redis_log_stream as rls_module

        # Mock Redis client
        mock_client = Mock()
        mock_client.ping = Mock()
        mock_redis.from_url.return_value = mock_client

        # First call should succeed
        init_redis_log_stream()
        first_streamer = rls_module._streamer

        # Second call should warn and return early
        init_redis_log_stream()
        second_streamer = rls_module._streamer

        # Should be same instance
        self.assertIs(first_streamer, second_streamer)


if __name__ == '__main__':
    unittest.main()
