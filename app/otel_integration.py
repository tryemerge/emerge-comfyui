"""
OpenTelemetry integration for ComfyUI
Mirrors Node.js @emp/telemetry architecture for consistent observability

Phase 1 of LOGGING_ARCHITECTURE.md ADR
Sends ALL ComfyUI logs to Dash0/OpenTelemetry for comprehensive monitoring

Dynamic job_id Injection:
- Custom LoggingHandler injects emerge.job_id attribute into every log record
- Gets job_id from executor context (same logic as RedisLogStreamer)
- Falls back to 'unknown' when no job context available
"""
import os
import logging
from typing import Optional

try:
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    logging.warning("OpenTelemetry not available - OTEL logging disabled")


class JobAwareLoggingHandler(LoggingHandler):
    """
    Custom OpenTelemetry LoggingHandler that dynamically injects job context into every log record.

    Adds 'emerge.job_id' and 'emerge.workflow_id' attributes to each log record based on current execution context:
    - During job execution: Gets job_id and workflow_id from executor.extra_data
    - During validation: Gets job_id and workflow_id from server.current_extra_data
    - No job context: Sets to 'unknown'

    This enables filtering logs in Dash0 using: emerge.job_id = "abc123" or emerge.workflow_id = "xyz789"
    """

    def _get_current_job_context(self) -> dict:
        """
        Get the job context (job_id and workflow_id) for the currently executing prompt.

        Mirrors RedisLogStreamer logic to ensure consistency.

        Returns:
            dict: {'job_id': str, 'workflow_id': str} or defaults to 'unknown' for missing fields
        """
        try:
            # Import here to avoid circular dependency
            from server import PromptServer

            # First try executor.extra_data (for runtime errors during execution)
            executor = getattr(PromptServer.instance, 'prompt_executor', None)
            if executor:
                extra_data = getattr(executor, 'extra_data', {})
                job_id = extra_data.get('job_id')
                if job_id:
                    return {
                        'job_id': job_id,
                        'workflow_id': extra_data.get('workflow_id', 'unknown')
                    }

            # Fallback to server.current_extra_data (for validation errors before execution)
            current_extra_data = getattr(PromptServer.instance, 'current_extra_data', {})
            job_id = current_extra_data.get('job_id')
            if job_id:
                return {
                    'job_id': job_id,
                    'workflow_id': current_extra_data.get('workflow_id', 'unknown')
                }

            return {'job_id': 'unknown', 'workflow_id': 'unknown'}

        except Exception:
            # If any error occurs, return defaults - don't let this break logging
            return {'job_id': 'unknown', 'workflow_id': 'unknown'}

    def _translate(self, record: logging.LogRecord) -> dict:
        """
        Override _translate to inject emerge.job_id and emerge.workflow_id into the OpenTelemetry LogRecord.

        This is the proper extension point for adding custom attributes to OTLP logs.
        The parent _translate method converts Python logging.LogRecord to a dict,
        and we enhance it with our dynamic job context.
        """
        # Call parent _translate to get the base OpenTelemetry LogRecord dict
        log_record_dict = super()._translate(record)

        # Get current job context dynamically
        job_context = self._get_current_job_context()

        # Add emerge.job_id and emerge.workflow_id to the attributes dict
        # This will be exported to Dash0 as structured fields
        if 'attributes' not in log_record_dict or log_record_dict['attributes'] is None:
            log_record_dict['attributes'] = {}

        # Add our custom attributes (use dot notation for hierarchical field names)
        log_record_dict['attributes']['emerge.job_id'] = job_context['job_id']
        log_record_dict['attributes']['emerge.workflow_id'] = job_context['workflow_id']

        return log_record_dict

def init_otel_logging() -> bool:
    """
    Initialize OpenTelemetry logging for ComfyUI

    Mirrors @emp/telemetry configuration:
    - Uses same endpoints (DASH0_ENDPOINT or OTEL_COLLECTOR_ENDPOINT)
    - Uses same authentication (DASH0_AUTH_TOKEN)
    - Uses same resource attributes (service.name, machine.id, job.id)

    Environment Variables:
        TELEMETRY_TARGET: 'dash0', 'collector', 'remote-collector', or 'disabled'
        DASH0_ENDPOINT: Dash0 OTLP endpoint (required if TELEMETRY_TARGET=dash0)
        DASH0_AUTH_TOKEN: Dash0 authentication token (required if TELEMETRY_TARGET=dash0)
        DASH0_DATASET: Dash0 dataset name (required if TELEMETRY_TARGET=dash0)
        OTEL_COLLECTOR_ENDPOINT: Collector endpoint (required if TELEMETRY_TARGET=collector)
        MACHINE_ID: Machine identifier (optional, added to resource attributes)
        COMFYUI_JOB_ID: Job identifier (optional, added to resource attributes)

    Returns:
        bool: True if initialization succeeded, False otherwise
    """
    # DEBUG: Log all OTEL-related environment variables BEFORE any logic
    logging.info("=" * 80)
    logging.info("[OTEL-DEBUG] Environment Variable Check:")
    logging.info(f"[OTEL-DEBUG] ENABLE_OTEL_LOGGING = {os.getenv('ENABLE_OTEL_LOGGING')}")
    logging.info(f"[OTEL-DEBUG] TELEMETRY_TARGET = {os.getenv('TELEMETRY_TARGET')}")
    logging.info(f"[OTEL-DEBUG] DASH0_ENDPOINT = {os.getenv('DASH0_ENDPOINT', 'NOT SET')}")
    logging.info(f"[OTEL-DEBUG] DASH0_AUTH_TOKEN = {'SET' if os.getenv('DASH0_AUTH_TOKEN') else 'NOT SET'}")
    logging.info(f"[OTEL-DEBUG] DASH0_DATASET = {os.getenv('DASH0_DATASET', 'NOT SET')}")
    logging.info(f"[OTEL-DEBUG] OTEL_COLLECTOR_ENDPOINT = {os.getenv('OTEL_COLLECTOR_ENDPOINT', 'NOT SET')}")
    logging.info(f"[OTEL-DEBUG] MACHINE_ID = {os.getenv('MACHINE_ID', 'NOT SET')}")
    logging.info(f"[OTEL-DEBUG] COMFYUI_JOB_ID = {os.getenv('COMFYUI_JOB_ID', 'NOT SET')}")
    logging.info(f"[OTEL-DEBUG] OTEL_SERVICE_NAMESPACE = {os.getenv('OTEL_SERVICE_NAMESPACE', 'NOT SET')}")
    logging.info(f"[OTEL-DEBUG] NODE_ENV = {os.getenv('NODE_ENV', 'NOT SET')}")
    logging.info("=" * 80)

    if not OTEL_AVAILABLE:
        logging.warning("[OTEL] OpenTelemetry packages not installed - skipping")
        return False

    # Check if enabled
    telemetry_target = os.getenv('TELEMETRY_TARGET')
    if not telemetry_target or telemetry_target == 'disabled':
        logging.info("[OTEL] Telemetry disabled (TELEMETRY_TARGET not set or 'disabled')")
        return False

    try:
        # Determine endpoint based on TELEMETRY_TARGET (mirrors Node.js logic)
        if telemetry_target == 'dash0':
            endpoint = os.getenv('DASH0_ENDPOINT')
            auth_token = os.getenv('DASH0_AUTH_TOKEN')
            dataset = os.getenv('DASH0_DATASET')

            if not endpoint or not auth_token or not dataset:
                raise ValueError("TELEMETRY_TARGET=dash0 requires DASH0_ENDPOINT, DASH0_AUTH_TOKEN, and DASH0_DATASET")

            headers = {
                'authorization': f'Bearer {auth_token}',
                'dash0-dataset': dataset
            }
        elif telemetry_target in ['collector', 'remote-collector']:
            endpoint = os.getenv('OTEL_COLLECTOR_ENDPOINT')
            if not endpoint:
                raise ValueError(f"TELEMETRY_TARGET={telemetry_target} requires OTEL_COLLECTOR_ENDPOINT")
            headers = {}
        else:
            raise ValueError(f"Unknown TELEMETRY_TARGET: {telemetry_target}")

        # Create resource (mirrors @emp/telemetry resource attributes)
        machine_id = os.getenv('MACHINE_ID')
        worker_id = os.getenv('WORKER_ID')

        resource_attrs = {
            'service.name': 'comfyui',
            'service.namespace': os.getenv('OTEL_SERVICE_NAMESPACE', 'emerge'),
            'service.version': '1.0.0',
            'deployment.environment': os.getenv('NODE_ENV', 'development'),
        }

        # Add service.instance.id for machine identification (required by OTEL semantic conventions)
        if machine_id:
            resource_attrs['service.instance.id'] = machine_id
            resource_attrs['machine.id'] = machine_id  # Also keep custom attribute for filtering

        # Add worker.id for worker identification
        if worker_id:
            resource_attrs['worker.id'] = worker_id

        # Note: job.id is NOT added as a resource attribute (it's static)
        # Instead, emerge.job_id is added dynamically per log record by JobAwareLoggingHandler

        resource = Resource.create(resource_attrs)

        # Create OTLP exporter
        # For local collector, use insecure connection (no TLS)
        # For Dash0, use secure connection (TLS)
        insecure = telemetry_target in ['collector', 'remote-collector']

        exporter = OTLPLogExporter(
            endpoint=endpoint,
            headers=headers,
            insecure=insecure,
        )

        # Create logger provider with batch processor
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(
                exporter,
                max_queue_size=2048,
                max_export_batch_size=512,
                schedule_delay_millis=5000,
            )
        )

        # Set as global logger provider
        set_logger_provider(logger_provider)

        # Attach custom JobAwareLoggingHandler to root logger to capture ALL Python logging
        # This handler dynamically injects emerge.job_id into every log record
        handler = JobAwareLoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        logging.getLogger().addHandler(handler)

        logging.info(f"[OTEL] ✅ OpenTelemetry logging initialized (endpoint: {endpoint})")
        logging.info("[OTEL] JobAwareLoggingHandler attached to root logger")
        logging.info("[OTEL] Resource attributes:")
        logging.info(f"[OTEL]   - service.name: {resource_attrs.get('service.name')}")
        logging.info(f"[OTEL]   - service.namespace: {resource_attrs.get('service.namespace')}")
        logging.info(f"[OTEL]   - service.instance.id: {resource_attrs.get('service.instance.id', 'NOT SET')}")
        logging.info(f"[OTEL]   - machine.id: {resource_attrs.get('machine.id', 'NOT SET')}")
        logging.info(f"[OTEL]   - worker.id: {resource_attrs.get('worker.id', 'NOT SET')}")
        logging.info(f"[OTEL]   - deployment.environment: {resource_attrs.get('deployment.environment')}")
        logging.info("[OTEL] All logs will include emerge.job_id and emerge.workflow_id fields (dynamic per-job tracking)")
        return True

    except Exception as e:
        logging.error(f"[OTEL] Failed to initialize: {e}")
        return False
