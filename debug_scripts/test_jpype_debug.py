"""
JPype/JVM threading debug tests for VS Code test runner.

Run these through VS Code's test runner to reproduce the crash.
Compare results with terminal: uv run pytest tests/integration/test_jpype_debug.py -v

This helps identify what VS Code's test runner does differently.
"""

import gc
import logging
import threading
import time

import pytest

logger = logging.getLogger(__name__)


class TestJPypeThreading:
    """Debug tests for JPype threading issues in VS Code."""

    def test_01_jvm_basic_operations(self):
        """Test basic JVM operations - should always pass."""
        import jpype

        from jneqsim import neqsim

        logger.info(f"JVM started: {jpype.isJVMStarted()}")
        logger.info(f"Thread attached: {jpype.java.lang.Thread.isAttached()}")

        # Create a simple thermo system
        system = neqsim.thermo.system.SystemSrkEos(298.15, 50.0)
        system.addComponent("methane", 1.0)
        system.setMixingRule(2)

        logger.info("Basic JVM operations: PASSED")

    def test_02_single_testclient_request(self, api_client):
        """Test single TestClient request."""
        response = api_client.get("/")
        assert response.status_code == 200
        logger.info("Single TestClient request: PASSED")

    def test_03_multiple_testclient_requests(self, api_client):
        """Test multiple requests with same TestClient."""
        for i in range(5):
            response = api_client.get("/")
            assert response.status_code == 200
            logger.info(f"Request {i}: status={response.status_code}")

        logger.info("Multiple TestClient requests: PASSED")

    def test_04_simulate_endpoint_once(self, api_client):
        """Test simulate endpoint once - this is where crashes often occur."""
        simple_graph = {
            "nodes": [
                {
                    "id": "S1",
                    "type": "stream",
                    "input_data": {
                        "temperature": 25.0,
                        "pressure": 50.0,
                        "flow_rate": 1.0,
                        "composition": {"methane": 0.9, "ethane": 0.1},
                        "eos_model": "SRK",
                    },
                },
                {
                    "id": "C1",
                    "type": "compressor",
                    "input_data": {
                        "outlet_pressure": 100.0,
                        "isentropic_efficiency": 0.75,
                    },
                },
                {
                    "id": "S2",
                    "type": "stream",
                    "input_data": None,
                },
            ],
            "edges": [
                {
                    "id": "E1",
                    "source": "S1",
                    "target": "C1",
                    "source_handle": "outlet",
                    "target_handle": "inlet",
                    "type": "material",
                },
                {
                    "id": "E2",
                    "source": "C1",
                    "target": "S2",
                    "source_handle": "outlet",
                    "target_handle": "inlet",
                    "type": "material",
                },
            ],
            "name": "Debug Test",
        }

        request_data = {"graph": simple_graph, "timeout": 30.0}

        response = api_client.post("/api/v1/simulate", json=request_data)
        logger.info(f"Simulate response: {response.status_code}")

        assert response.status_code == 200
        data = response.json()
        assert data["results"]["convergence_status"] is True

        logger.info("Simulate endpoint once: PASSED")

    def test_05_simulate_endpoint_multiple(self, api_client):
        """Test simulate endpoint multiple times."""
        simple_graph = {
            "nodes": [
                {
                    "id": "S1",
                    "type": "stream",
                    "input_data": {
                        "temperature": 25.0,
                        "pressure": 50.0,
                        "flow_rate": 1.0,
                        "composition": {"methane": 1.0},
                        "eos_model": "SRK",
                    },
                },
                {
                    "id": "V1",
                    "type": "valve",
                    "input_data": {
                        "outlet_pressure": 20.0,
                    },
                },
                {
                    "id": "S2",
                    "type": "stream",
                    "input_data": None,
                },
            ],
            "edges": [
                {
                    "id": "E1",
                    "source": "S1",
                    "target": "V1",
                    "source_handle": "outlet",
                    "target_handle": "inlet",
                    "type": "material",
                },
                {
                    "id": "E2",
                    "source": "V1",
                    "target": "S2",
                    "source_handle": "outlet",
                    "target_handle": "inlet",
                    "type": "material",
                },
            ],
            "name": "Valve Test",
        }

        request_data = {"graph": simple_graph, "timeout": 30.0}

        for i in range(3):
            logger.info(f"Simulate iteration {i}")
            response = api_client.post("/api/v1/simulate", json=request_data)
            assert response.status_code == 200
            gc.collect()  # Force GC between iterations

        logger.info("Simulate endpoint multiple: PASSED")

    def test_06_concurrent_java_from_threads(self):
        """Test concurrent Java calls from multiple Python threads."""
        import jpype

        from jneqsim import neqsim

        results = []
        errors = []
        lock = threading.Lock()

        def worker(thread_id: int):
            """Worker that makes Java calls."""
            try:
                # Explicit thread attachment
                if not jpype.java.lang.Thread.isAttached():
                    jpype.java.lang.Thread.attach()

                # Make Java calls
                for i in range(3):
                    system = neqsim.thermo.system.SystemSrkEos(298.15, 50.0)
                    system.addComponent("methane", 1.0)
                    system.setMixingRule(2)
                    time.sleep(0.01)

                with lock:
                    results.append(f"Thread {thread_id}: SUCCESS")

            except Exception as e:
                with lock:
                    errors.append(f"Thread {thread_id}: {e}")

            finally:
                # Explicit detachment
                try:
                    jpype.java.lang.Thread.detach()
                except Exception:
                    pass

        # Start threads
        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(i,), name=f"Worker-{i}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        logger.info(f"Results: {results}")
        logger.info(f"Errors: {errors}")

        assert len(errors) == 0, f"Thread errors: {errors}"
        logger.info("Concurrent Java from threads: PASSED")

    def test_07_thread_state_tracking(self):
        """Track thread attachment state through operations."""
        import jpype

        logger.info(f"Initial: attached={jpype.java.lang.Thread.isAttached()}")

        # Import triggers JVM
        from jneqsim import neqsim

        logger.info(f"After import: attached={jpype.java.lang.Thread.isAttached()}")

        # Make Java call
        _ = neqsim.thermo.system.SystemSrkEos(298.15, 50.0)
        logger.info(f"After Java call: attached={jpype.java.lang.Thread.isAttached()}")

        # Get Java thread info
        java_thread = jpype.java.lang.Thread.currentThread()
        logger.info(f"Java thread: name={java_thread.getName()}, daemon={java_thread.isDaemon()}")

        # Count active Java threads
        thread_group = java_thread.getThreadGroup()
        logger.info(f"Active Java threads: {thread_group.activeCount()}")

        logger.info("Thread state tracking: PASSED")

    def test_08_jvm_memory_state(self):
        """Check JVM memory state."""
        import jpype

        runtime = jpype.java.lang.Runtime.getRuntime()

        free_mb = runtime.freeMemory() / (1024 * 1024)
        total_mb = runtime.totalMemory() / (1024 * 1024)
        max_mb = runtime.maxMemory() / (1024 * 1024)

        logger.info(f"JVM Memory - Free: {free_mb:.1f}MB, Total: {total_mb:.1f}MB, Max: {max_mb:.1f}MB")

        # Force Java GC
        jpype.java.lang.System.gc()
        time.sleep(0.1)

        free_after = runtime.freeMemory() / (1024 * 1024)
        logger.info(f"After Java GC - Free: {free_after:.1f}MB")

        logger.info("JVM memory state: PASSED")
