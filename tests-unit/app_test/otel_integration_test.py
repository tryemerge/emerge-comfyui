"""
Unit tests for OpenTelemetry Integration Module

Tests the OTEL logging initialization which mirrors Node.js @emp/telemetry architecture:
- Dash0 endpoint configuration
- Collector endpoint configuration
- Resource attributes (service.name, machine.id, job.id)
- Graceful degradation when OTEL unavailable
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, call
import os

# Mock OpenTelemetry modules before importing otel_integration
import sys
otel_sdk_logs_mock = MagicMock()
otel_exporter_mock = MagicMock()
otel_logs_mock = MagicMock()
otel_resources_mock = MagicMock()

sys.modules['opentelemetry.sdk._logs'] = otel_sdk_logs_mock
sys.modules['opentelemetry.sdk._logs.export'] = otel_exporter_mock
sys.modules['opentelemetry.exporter.otlp.proto.grpc._log_exporter'] = otel_exporter_mock
sys.modules['opentelemetry._logs'] = otel_logs_mock
sys.modules['opentelemetry.sdk.resources'] = otel_resources_mock


class TestOtelIntegration(unittest.TestCase):
    """Test suite for OTEL integration"""

    def setUp(self):
        """Set up test fixtures"""
        # Reset OTEL availability flag
        import app.otel_integration as otel_module
        otel_module.OTEL_AVAILABLE = True

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        'DASH0_ENDPOINT': 'https://ingress.dash0.com:4317',
        'DASH0_AUTH_TOKEN': 'test-token-123',
        'DASH0_DATASET': 'test-dataset',
        'MACHINE_ID': 'test-machine-456',
        'COMFYUI_JOB_ID': 'job-789'
    })
    @patch('app.otel_integration.LoggerProvider')
    @patch('app.otel_integration.BatchLogRecordProcessor')
    @patch('app.otel_integration.OTLPLogExporter')
    @patch('app.otel_integration.Resource')
    @patch('app.otel_integration.set_logger_provider')
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_with_dash0(
        self, mock_logging, mock_set_provider, mock_resource,
        mock_exporter_class, mock_processor_class, mock_provider_class
    ):
        """Test OTEL initialization with Dash0 configuration"""
        from app.otel_integration import init_otel_logging

        # Mock OTEL components
        mock_exporter = Mock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        mock_provider = Mock()
        mock_provider_class.return_value = mock_provider

        mock_resource_instance = Mock()
        mock_resource.create.return_value = mock_resource_instance

        # Call init
        result = init_otel_logging()

        # Verify success
        self.assertTrue(result)

        # Verify Resource was created with correct attributes
        mock_resource.create.assert_called_once()
        resource_attrs = mock_resource.create.call_args[0][0]
        self.assertEqual(resource_attrs['service.name'], 'comfyui')
        self.assertEqual(resource_attrs['machine.id'], 'test-machine-456')
        self.assertEqual(resource_attrs['job.id'], 'job-789')

        # Verify OTLPLogExporter was created with Dash0 endpoint and auth
        mock_exporter_class.assert_called_once()
        exporter_kwargs = mock_exporter_class.call_args[1]
        self.assertEqual(exporter_kwargs['endpoint'], 'https://ingress.dash0.com:4317')
        self.assertIn('authorization', exporter_kwargs['headers'])
        self.assertIn('Bearer test-token-123', exporter_kwargs['headers']['authorization'])
        self.assertIn('Dash0-Dataset', exporter_kwargs['headers'])
        self.assertEqual(exporter_kwargs['headers']['Dash0-Dataset'], 'test-dataset')

        # Verify LoggerProvider was created and set
        mock_provider_class.assert_called_once_with(resource=mock_resource_instance)
        mock_provider.add_log_record_processor.assert_called_once_with(mock_processor)
        mock_set_provider.assert_called_once_with(mock_provider)

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'collector',
        'OTEL_COLLECTOR_ENDPOINT': 'http://localhost:4317',
        'MACHINE_ID': 'test-machine-456'
    })
    @patch('app.otel_integration.LoggerProvider')
    @patch('app.otel_integration.BatchLogRecordProcessor')
    @patch('app.otel_integration.OTLPLogExporter')
    @patch('app.otel_integration.Resource')
    @patch('app.otel_integration.set_logger_provider')
    def test_init_otel_logging_with_collector(
        self, mock_set_provider, mock_resource, mock_exporter_class,
        mock_processor_class, mock_provider_class
    ):
        """Test OTEL initialization with local collector configuration"""
        from app.otel_integration import init_otel_logging

        # Mock OTEL components
        mock_exporter = Mock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        mock_provider = Mock()
        mock_provider_class.return_value = mock_provider

        mock_resource_instance = Mock()
        mock_resource.create.return_value = mock_resource_instance

        # Call init
        result = init_otel_logging()

        # Verify success
        self.assertTrue(result)

        # Verify OTLPLogExporter was created with collector endpoint
        mock_exporter_class.assert_called_once()
        exporter_kwargs = mock_exporter_class.call_args[1]
        self.assertEqual(exporter_kwargs['endpoint'], 'http://localhost:4317')

        # Verify no auth headers for local collector
        self.assertNotIn('authorization', exporter_kwargs.get('headers', {}))

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'false'
    }, clear=True)
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_disabled_by_env(self, mock_logging):
        """Test OTEL initialization skipped when ENABLE_OTEL_LOGGING=false"""
        from app.otel_integration import init_otel_logging

        result = init_otel_logging()

        self.assertFalse(result)
        # Should log that it's disabled
        mock_logging.info.assert_any_call('[OTEL] OpenTelemetry logging disabled via ENABLE_OTEL_LOGGING=false')

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'disabled'
    })
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_disabled_by_target(self, mock_logging):
        """Test OTEL initialization skipped when TELEMETRY_TARGET=disabled"""
        from app.otel_integration import init_otel_logging

        result = init_otel_logging()

        self.assertFalse(result)

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        # Missing DASH0_ENDPOINT
    })
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_missing_dash0_endpoint(self, mock_logging):
        """Test OTEL initialization fails gracefully with missing Dash0 endpoint"""
        from app.otel_integration import init_otel_logging

        result = init_otel_logging()

        self.assertFalse(result)
        # Should log error about missing endpoint
        mock_logging.error.assert_called()

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        'DASH0_ENDPOINT': 'https://ingress.dash0.com:4317',
        # Missing DASH0_AUTH_TOKEN
    })
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_missing_dash0_auth(self, mock_logging):
        """Test OTEL initialization fails gracefully with missing Dash0 auth"""
        from app.otel_integration import init_otel_logging

        result = init_otel_logging()

        self.assertFalse(result)
        # Should log error about missing auth
        mock_logging.error.assert_called()

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        'DASH0_ENDPOINT': 'https://ingress.dash0.com:4317',
        'DASH0_AUTH_TOKEN': 'test-token-123',
        'DASH0_DATASET': 'test-dataset'
    })
    @patch('app.otel_integration.LoggerProvider')
    def test_init_otel_logging_handles_initialization_error(self, mock_provider_class):
        """Test OTEL initialization handles errors gracefully"""
        from app.otel_integration import init_otel_logging

        # Make LoggerProvider raise an exception
        mock_provider_class.side_effect = Exception("OTEL initialization failed")

        result = init_otel_logging()

        self.assertFalse(result)

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        'DASH0_ENDPOINT': 'https://ingress.dash0.com:4317',
        'DASH0_AUTH_TOKEN': 'test-token-123',
        'DASH0_DATASET': 'test-dataset'
    })
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_handles_otel_unavailable(self, mock_logging):
        """Test OTEL initialization when packages not available"""
        import app.otel_integration as otel_module

        # Set OTEL as unavailable
        otel_module.OTEL_AVAILABLE = False

        result = otel_module.init_otel_logging()

        self.assertFalse(result)
        # Should log that OTEL is not available
        mock_logging.warning.assert_any_call('[OTEL] OpenTelemetry not available - skipping initialization')

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        'DASH0_ENDPOINT': 'https://ingress.dash0.com:4317',
        'DASH0_AUTH_TOKEN': 'test-token-123',
        'DASH0_DATASET': 'test-dataset',
        'MACHINE_ID': 'test-machine-456'
        # No COMFYUI_JOB_ID
    })
    @patch('app.otel_integration.LoggerProvider')
    @patch('app.otel_integration.BatchLogRecordProcessor')
    @patch('app.otel_integration.OTLPLogExporter')
    @patch('app.otel_integration.Resource')
    @patch('app.otel_integration.set_logger_provider')
    def test_init_otel_logging_optional_job_id(
        self, mock_set_provider, mock_resource, mock_exporter_class,
        mock_processor_class, mock_provider_class
    ):
        """Test OTEL initialization works without job_id (optional attribute)"""
        from app.otel_integration import init_otel_logging

        # Mock OTEL components
        mock_exporter = Mock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        mock_provider = Mock()
        mock_provider_class.return_value = mock_provider

        mock_resource_instance = Mock()
        mock_resource.create.return_value = mock_resource_instance

        # Call init
        result = init_otel_logging()

        # Verify success
        self.assertTrue(result)

        # Verify Resource was created without job.id
        mock_resource.create.assert_called_once()
        resource_attrs = mock_resource.create.call_args[0][0]
        self.assertEqual(resource_attrs['service.name'], 'comfyui')
        self.assertEqual(resource_attrs['machine.id'], 'test-machine-456')
        self.assertNotIn('job.id', resource_attrs)

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'remote-collector',
        'OTEL_COLLECTOR_ENDPOINT': 'http://remote-collector.example.com:4317'
    })
    @patch('app.otel_integration.LoggerProvider')
    @patch('app.otel_integration.BatchLogRecordProcessor')
    @patch('app.otel_integration.OTLPLogExporter')
    @patch('app.otel_integration.Resource')
    @patch('app.otel_integration.set_logger_provider')
    def test_init_otel_logging_with_remote_collector(
        self, mock_set_provider, mock_resource, mock_exporter_class,
        mock_processor_class, mock_provider_class
    ):
        """Test OTEL initialization with remote collector configuration"""
        from app.otel_integration import init_otel_logging

        # Mock OTEL components
        mock_exporter = Mock()
        mock_exporter_class.return_value = mock_exporter

        mock_processor = Mock()
        mock_processor_class.return_value = mock_processor

        mock_provider = Mock()
        mock_provider_class.return_value = mock_provider

        mock_resource_instance = Mock()
        mock_resource.create.return_value = mock_resource_instance

        # Call init
        result = init_otel_logging()

        # Verify success
        self.assertTrue(result)

        # Verify OTLPLogExporter was created with remote collector endpoint
        mock_exporter_class.assert_called_once()
        exporter_kwargs = mock_exporter_class.call_args[1]
        self.assertEqual(exporter_kwargs['endpoint'], 'http://remote-collector.example.com:4317')

    @patch.dict(os.environ, {
        'ENABLE_OTEL_LOGGING': 'true',
        'TELEMETRY_TARGET': 'dash0',
        'DASH0_ENDPOINT': 'https://ingress.dash0.com:4317',
        'DASH0_AUTH_TOKEN': 'test-token-123',
        'DASH0_DATASET': 'test-dataset'
    })
    @patch('app.otel_integration.LoggerProvider')
    @patch('app.otel_integration.logging')
    def test_init_otel_logging_debug_output(self, mock_logging, mock_provider_class):
        """Test that initialization logs debug information about configuration"""
        from app.otel_integration import init_otel_logging

        # Mock OTEL components
        mock_provider_class.return_value = Mock()

        with patch('app.otel_integration.BatchLogRecordProcessor'), \
             patch('app.otel_integration.OTLPLogExporter'), \
             patch('app.otel_integration.Resource'), \
             patch('app.otel_integration.set_logger_provider'):

            result = init_otel_logging()

            # Verify debug logging was called
            info_calls = [str(call) for call in mock_logging.info.call_args_list]
            self.assertTrue(any('TELEMETRY_TARGET' in str(call) for call in info_calls))


if __name__ == '__main__':
    unittest.main()
