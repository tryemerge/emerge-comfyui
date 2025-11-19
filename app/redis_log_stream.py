"""
Redis Log Streaming Module for ComfyUI

Streams ComfyUI logs to Redis for real-time monitoring and job failure detection.
Uses hybrid approach:
- All logs → Redis Pub/Sub (ephemeral monitoring)
- Error pattern matches → Redis Stream (guaranteed delivery)

Error Detection:
- Loads error patterns from Redis (error:pattern:comfyui:*)
- Matches log messages against patterns using regex
- Writes ERROR events to job:events:{job_id} stream when patterns match

Integration: Hooks into existing app.logger module via on_flush() callback.
"""

import os
import time
import logging
import re
from typing import List, Dict, Any, Optional

# Redis import is optional - module gracefully degrades if not available
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logging.warning("Redis module not available - log streaming disabled")


class RedisLogStreamer:
    """
    Streams ComfyUI logs to Redis using hybrid stream/pub-sub approach.

    - All logs → Redis Pub/Sub (real-time monitoring)
    - Error pattern matches → job:events:{job_id} stream (guaranteed delivery)

    Error Detection:
    - Loads error patterns from Redis on initialization
    - Matches log messages against compiled regex patterns
    - Writes ERROR events to stream when patterns match
    - Tracks jobs to write only ONE error per job
    """

    def __init__(self):
        self.redis_client: Optional[Any] = None
        self.machine_id: Optional[str] = None
        self.worker_id: Optional[str] = None
        self.enabled = False
        self.connector_type = 'comfyui'

        # Error pattern matching
        self.error_patterns: List[Dict[str, Any]] = []
        self.compiled_patterns: List[tuple] = []  # List of (compiled_regex, pattern_info)
        self.error_written_for_jobs = set()  # Track which jobs already have error written

        # Recursion prevention
        self._processing = False

        if not REDIS_AVAILABLE:
            logging.warning("[RedisLogStreamer] Redis not available - log streaming disabled")
            return

        self._init_redis()
        self._init_context()
        self._load_error_patterns()

    def _init_redis(self):
        """Initialize Redis connection using same env vars as EmProps nodes."""
        try:
            # Try HUB_REDIS_URL first (used by workers)
            hub_redis_url = os.getenv('HUB_REDIS_URL')
            if hub_redis_url:
                self.redis_client = redis.from_url(
                    hub_redis_url,
                    decode_responses=True
                )
            else:
                # Fallback to individual env vars
                redis_host = os.getenv('REDIS_HOST', 'localhost')
                redis_port = int(os.getenv('REDIS_PORT', '6379'))
                redis_db = int(os.getenv('REDIS_DB', '0'))
                redis_password = os.getenv('REDIS_PASSWORD')

                self.redis_client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    db=redis_db,
                    password=redis_password,
                    decode_responses=True
                )

            # Test connection
            self.redis_client.ping()
            logging.info("[RedisLogStreamer] ✅ Connected to Redis")
            self.enabled = True

        except Exception as e:
            logging.warning(f"[RedisLogStreamer] Failed to connect to Redis: {e}")
            self.redis_client = None
            self.enabled = False

    def _init_context(self):
        """Get machine_id and worker_id from environment. job_id is retrieved dynamically from executor."""
        # Machine ID for pub/sub channel
        self.machine_id = os.getenv('MACHINE_ID', 'unknown')

        # Worker ID to identify which worker/service instance generated the log
        self.worker_id = os.getenv('WORKER_ID')

        logging.info(f"[RedisLogStreamer] Initialized - machine_id: {self.machine_id}, worker_id: {self.worker_id}, job_id will be retrieved dynamically from executor")

    def _load_error_patterns(self) -> bool:
        """
        Load error patterns from Redis.
        Loads from error_patterns:global and error_patterns:{connector_type} hashes.
        Returns True if patterns loaded successfully, False otherwise.
        """
        logging.info(f"[RedisLogStreamer] 🔍 Loading error patterns from Redis...")

        if not self.enabled or not self.redis_client:
            logging.warning(f"[RedisLogStreamer] ❌ Cannot load patterns - enabled={self.enabled}, has_client={self.redis_client is not None}")
            return False

        try:
            import json

            # Load from global patterns and connector-specific patterns
            redis_keys = [
                f'error_patterns:global',  # Global patterns
                f'error_patterns:{self.connector_type}',  # Connector-specific patterns
            ]

            logging.info(f"[RedisLogStreamer] 🔎 Loading patterns from Redis hashes: {redis_keys}")

            loaded_count = 0
            total_patterns = 0

            for redis_key in redis_keys:
                # Get all patterns from this hash
                pattern_hash = self.redis_client.hgetall(redis_key)
                logging.info(f"[RedisLogStreamer] 📖 Hash '{redis_key}' contains {len(pattern_hash)} pattern(s)")

                if not pattern_hash:
                    logging.info(f"[RedisLogStreamer] ⚪ No patterns in {redis_key}")
                    continue

                # Parse each pattern
                for pattern_id, pattern_json in pattern_hash.items():
                    total_patterns += 1
                    try:
                        # Parse JSON pattern
                        pattern_data = json.loads(pattern_json)
                        logging.info(f"[RedisLogStreamer] 📄 Loaded pattern '{pattern_id}': {pattern_data}")

                        # Extract pattern text and match type
                        pattern_text = pattern_data.get('pattern', '')
                        match_type = pattern_data.get('match_type', 'contains')
                        case_sensitive = pattern_data.get('case_sensitive', False)
                        active = pattern_data.get('active', True)
                        classification = pattern_data.get('classification', 'fatal')

                        # Skip inactive patterns
                        if not active:
                            logging.info(f"[RedisLogStreamer] ⏭️  Skipping inactive pattern '{pattern_id}'")
                            continue

                        if not pattern_text:
                            logging.warning(f"[RedisLogStreamer] ⚠️  Pattern {pattern_id} has no 'pattern' field, skipping")
                            continue

                        # Compile pattern based on match_type
                        compiled_regex = None
                        if match_type == 'regex':
                            try:
                                flags = 0 if case_sensitive else re.IGNORECASE
                                logging.info(f"[RedisLogStreamer] 🔨 Compiling regex pattern '{pattern_id}': {pattern_text}")
                                compiled_regex = re.compile(pattern_text, flags)
                            except re.error as e:
                                logging.error(f"[RedisLogStreamer] ❌ Invalid regex in {pattern_id}: {e}")
                                logging.info(f"[RedisLogStreamer] 🔄 Falling back to 'contains' matching")
                                match_type = 'contains'  # Fallback
                        elif match_type == 'contains' or match_type == 'exact':
                            # For contains/exact, we'll use string matching
                            logging.info(f"[RedisLogStreamer] 📝 Using '{match_type}' matching for pattern '{pattern_id}': {pattern_text}")
                        else:
                            logging.warning(f"[RedisLogStreamer] ⚠️  Unknown match_type '{match_type}' for {pattern_id}, defaulting to 'contains'")
                            match_type = 'contains'

                        # Store compiled pattern with metadata
                        pattern_info = {
                            'id': pattern_id,
                            'pattern': pattern_text,
                            'match_type': match_type,
                            'case_sensitive': case_sensitive,
                            'classification': classification,
                            'regex': compiled_regex,  # None for non-regex patterns
                            'is_log_filter_only': pattern_data.get('is_log_filter_only', False),
                            'human_readable_message': pattern_data.get('human_readable_message'),
                            'call_to_action': pattern_data.get('call_to_action'),
                            'retry': pattern_data.get('retry', False),
                        }

                        self.compiled_patterns.append((compiled_regex, pattern_info))
                        self.error_patterns.append(pattern_info)
                        loaded_count += 1
                        logging.info(f"[RedisLogStreamer] ✅ Successfully loaded pattern '{pattern_id}' ({match_type})")

                    except json.JSONDecodeError as e:
                        logging.error(f"[RedisLogStreamer] ❌ Invalid JSON for pattern {pattern_id}: {e}")
                        continue
                    except Exception as e:
                        logging.error(f"[RedisLogStreamer] ❌ Error loading pattern {pattern_id}: {e}")
                        import traceback
                        logging.error(f"[RedisLogStreamer] Stack trace: {traceback.format_exc()}")
                        continue

            logging.info(f"[RedisLogStreamer] ✅ Successfully loaded {loaded_count}/{total_patterns} active error patterns for {self.connector_type}")

            # Log each loaded pattern for debugging
            if self.error_patterns:
                logging.info(f"[RedisLogStreamer] 📋 Loaded patterns:")
                for pattern_info in self.error_patterns:
                    logging.info(f"[RedisLogStreamer]    - {pattern_info.get('id')}: '{pattern_info.get('pattern')}' ({pattern_info.get('match_type')})")
            else:
                logging.warning(f"[RedisLogStreamer] ⚠️  No active error patterns found")
                logging.warning(f"[RedisLogStreamer] 💡 Error detection will be DISABLED until patterns are added to Redis")

            return len(self.error_patterns) > 0

        except Exception as e:
            logging.error(f"[RedisLogStreamer] ❌ Failed to load error patterns: {e}")
            import traceback
            logging.error(f"[RedisLogStreamer] Stack trace: {traceback.format_exc()}")
            return False

    def _matches_error_pattern(self, message: str) -> Optional[Dict[str, Any]]:
        """
        Check if message matches ANY error pattern.
        Supports match_type: 'contains', 'exact', 'regex'
        Returns pattern info if match found, None otherwise.

        Skips messages containing "META:" or "meta" to prevent recursion from our own logs.
        """
        # Skip meta logs to prevent recursion
        if 'META:' in message or 'meta' in message.lower():
            return None

        if not self.compiled_patterns:
            return None

        for compiled_regex, pattern_info in self.compiled_patterns:
            try:
                pattern_text = pattern_info.get('pattern', '')
                match_type = pattern_info.get('match_type', 'contains')
                case_sensitive = pattern_info.get('case_sensitive', False)
                pattern_name = pattern_info.get('id', 'unknown')  # Use 'id' instead of 'name'

                # Prepare comparison strings based on case sensitivity
                test_message = message if case_sensitive else message.lower()
                test_pattern = pattern_text if case_sensitive else pattern_text.lower()

                matched = False

                if match_type == 'exact':
                    matched = test_message == test_pattern
                elif match_type == 'contains':
                    matched = test_pattern in test_message
                elif match_type == 'regex':
                    if compiled_regex:
                        matched = compiled_regex.search(message) is not None
                    else:
                        # Fallback to contains if regex compilation failed
                        matched = test_pattern in test_message

                if matched:
                    logging.info(f"META: [RedisLogStreamer] ✅ PATTERN MATCHED: '{pattern_name}' on message: {message[:100]}")
                    return pattern_info

            except Exception:
                continue

        return None

    def _get_current_job_id(self) -> Optional[str]:
        """
        Get the job_id for the currently executing prompt.

        Legacy method for backwards compatibility.
        Use _get_current_job_context() for full context including workflow_id.
        """
        context = self._get_current_job_context()
        return context.get('job_id') if context else None

    def _get_current_job_context(self) -> Optional[Dict[str, str]]:
        """
        Get the job context (job_id and workflow_id) for the currently executing prompt.

        Checks two sources in order:
        1. executor.extra_data (set during execution)
        2. server.current_extra_data (set before validation)

        This enables error detection for both runtime errors and validation errors.

        Returns:
            dict: {'job_id': str, 'workflow_id': str} or None if no job context
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
                        'workflow_id': extra_data.get('workflow_id')
                    }

            # Fallback to server.current_extra_data (for validation errors before execution)
            current_extra_data = getattr(PromptServer.instance, 'current_extra_data', {})
            job_id = current_extra_data.get('job_id')
            if job_id:
                return {
                    'job_id': job_id,
                    'workflow_id': current_extra_data.get('workflow_id')
                }

            return None

        except Exception as e:
            logging.error(f"META: [RedisLogStreamer] ❌ Error getting job context: {e}")
            return None

    def _extract_log_level(self, message: str) -> str:
        """Extract log level from message if present."""
        message_upper = message.upper()

        if 'CRITICAL' in message_upper or 'FATAL' in message_upper:
            return 'CRITICAL'
        elif 'ERROR' in message_upper:
            return 'ERROR'
        elif 'WARNING' in message_upper or 'WARN' in message_upper:
            return 'WARNING'
        elif 'INFO' in message_upper:
            return 'INFO'
        elif 'DEBUG' in message_upper:
            return 'DEBUG'
        else:
            return 'UNKNOWN'

    def handle_logs(self, log_entries: List[Dict[str, Any]]):
        """
        Handle batch of log entries from logger flush callback.

        Args:
            log_entries: List of log entries with {t: timestamp, m: message}
        """
        if not self.enabled or not self.redis_client:
            return

        # Prevent recursion from our own logging statements
        if self._processing:
            return

        try:
            self._processing = True

            # Get current job context once for the batch (may be None if no job is executing)
            current_job_context = self._get_current_job_context()
            current_job_id = current_job_context.get('job_id') if current_job_context else None

            # Check if we already wrote error for this job
            if current_job_id and current_job_id in self.error_written_for_jobs:
                logging.debug(f"META: [RedisLogStreamer] ⏭️  Skipping error detection for job {current_job_id} (already wrote error)")
                # Still process logs for pub/sub, but skip error detection
                for entry in log_entries:
                    try:
                        timestamp = entry.get('t', time.time())
                        message = entry.get('m', '')

                        if not message or message.isspace():
                            continue

                        log_level = self._extract_log_level(message)

                        # Always send to pub/sub for real-time monitoring
                        self._publish_to_pubsub(timestamp, message, log_level, current_job_context)

                    except Exception as e:
                        # Don't let log streaming errors crash ComfyUI
                        logging.error(f"META: [RedisLogStreamer] ❌ Error processing log entry: {e}")
                return

            # Process each log entry
            for entry in log_entries:
                try:
                    timestamp = entry.get('t', time.time())
                    message = entry.get('m', '')

                    if not message or message.isspace():
                        continue

                    # Skip our own meta logs to prevent recursion
                    if 'METAMETA' in message or '[RedisLogStreamer]' in message:
                        continue

                    log_level = self._extract_log_level(message)

                    # Always send to pub/sub for real-time monitoring
                    self._publish_to_pubsub(timestamp, message, log_level, current_job_context)

                    # Check if message matches error pattern (only if job_id available and not already written)
                    if current_job_id and current_job_id not in self.error_written_for_jobs:
                        matched_pattern = self._matches_error_pattern(message)
                        if matched_pattern:
                            # Write ERROR event to stream
                            self._write_error_to_stream(current_job_id, message, matched_pattern)
                            self.error_written_for_jobs.add(current_job_id)

                except Exception as e:
                    # Don't let log streaming errors crash ComfyUI
                    logging.error(f"META: [RedisLogStreamer] ❌ Error processing log entry: {e}")
                    import traceback
                    logging.error(f"META: [RedisLogStreamer] Stack trace: {traceback.format_exc()}")
        finally:
            self._processing = False

    def _write_error_to_stream(self, job_id: str, message: str, pattern_info: Dict[str, Any]):
        """
        Write ERROR event to job events stream when error pattern matches.

        Stream: job:events:{job_id}
        Event Type: error
        """
        try:
            stream_key = f"job:events:{job_id}"
            pattern_name = pattern_info.get('id', 'unknown')

            event_data = {
                "event_type": "error",
                "message": message,
                "timestamp": str(time.time()),
                "source": "comfyui",
                "pattern_matched": pattern_name,
                "is_log_filter_only": str(pattern_info.get('is_log_filter_only', False)).lower(),
            }

            # Add human-readable message if available
            human_readable = pattern_info.get('human_readable_message')
            if human_readable:
                event_data["human_readable_message"] = human_readable

            # Add call to action if available
            call_to_action = pattern_info.get('call_to_action')
            if call_to_action:
                event_data["call_to_action"] = call_to_action

            # Add retry flag
            event_data["retry"] = str(pattern_info.get('retry', False)).lower()

            self.redis_client.xadd(stream_key, event_data)

            # Set TTL of 1 hour
            self.redis_client.expire(stream_key, 3600)

        except Exception:
            # Silently fail to avoid recursion
            pass

    def _publish_to_pubsub(self, timestamp: str, message: str, log_level: str, job_context: Optional[Dict[str, str]]):
        """
        Publish log to pub/sub channel for real-time monitoring.

        Channel: machine:{machine_id}:worker:{worker_id}:job:{job_id}:logs
        This allows workers to subscribe only to their own logs for validation.
        """
        try:
            # Build channel with full context - skip if missing critical IDs
            if not job_context or not job_context.get('job_id'):
                # No job context - skip pub/sub (logs without job_id go nowhere)
                return

            job_id = job_context.get('job_id')
            workflow_id = job_context.get('workflow_id')
            worker_id = self.worker_id or 'unknown'
            channel = f"machine:{self.machine_id}:worker:{worker_id}:job:{job_id}:logs"

            # Publish as JSON for structured consumption
            import json
            payload = json.dumps({
                "timestamp": timestamp,
                "level": log_level,
                "message": message,
                "source": "comfyui",
                "job_id": job_id,
                "workflow_id": workflow_id,  # Include workflow_id if available (may be None)
                "worker_id": self.worker_id  # Include worker_id to identify which service instance (may be None)
            })

            self.redis_client.publish(channel, payload)

        except Exception as e:
            logging.error(f"META: [RedisLogStreamer] Failed to publish to pub/sub: {e}")


# Global instance
_streamer: Optional[RedisLogStreamer] = None


def init_redis_log_stream():
    """
    Initialize Redis log streaming.

    Call this after setup_logger() in main.py.
    Hooks into logger's on_flush() callback.
    """
    global _streamer

    if _streamer is not None:
        logging.warning("[RedisLogStreamer] ⚠️  Already initialized")
        return

    logging.info("[RedisLogStreamer] 🚀 Initializing Redis log streaming...")

    _streamer = RedisLogStreamer()

    if _streamer.enabled:
        # Hook into logger's flush callback
        from app import logger
        logger.on_flush(_streamer.handle_logs)
        logging.info("[RedisLogStreamer] ✅ Registered flush callback")

        # Summary
        pattern_count = len(_streamer.error_patterns)
        if pattern_count > 0:
            logging.info(f"[RedisLogStreamer] 🎯 Error detection ENABLED with {pattern_count} patterns")
            logging.info(f"[RedisLogStreamer] 📊 All logs → PubSub, Pattern matches → Stream")
        else:
            logging.warning(f"[RedisLogStreamer] ⚠️  Error detection DISABLED (no patterns loaded)")
            logging.warning(f"[RedisLogStreamer] 💡 Add patterns to Redis at error:pattern:comfyui:* to enable")
    else:
        logging.warning("[RedisLogStreamer] ❌ Not enabled - no logs will be streamed")


def get_streamer() -> Optional[RedisLogStreamer]:
    """Get global streamer instance."""
    return _streamer
