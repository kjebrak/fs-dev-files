#!/usr/bin/env python3
"""
Deep debugging script for JPype/JVM threading issues.

This script investigates:
1. Thread attachment/detachment behavior
2. Class loading race conditions
3. JVM state across multiple TestClient calls
4. Java-side logging
5. Timing of operations

Run with: uv run python scripts/debug_jpype_threading.py

For even deeper debugging, rebuild JPype with tracing:
    pip install --editable git+https://github.com/jpype-project/jpype.git \
        --config-setting="--install-option=--enable-tracing"

Or use Python tracing:
    uv run python -m trace --trace scripts/debug_jpype_threading.py 2>&1 | tee trace.log

Based on Google AI analysis:
- Threads must be explicitly attached/detached
- Race conditions in class loading can occur
- Shared Java objects need synchronization
- Daemon threads don't prevent JVM shutdown

Sources:
- https://github.com/jpype-project/jpype/issues/934 (SIGSEGV with pytest)
- https://github.com/jpype-project/jpype/issues/1169 (Thread detachment)
- https://jpype.readthedocs.io/en/latest/develguide.html (Tracing)
"""

import faulthandler
import gc
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Configure logging FIRST
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s (%(threadName)s): %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("jpype_debug")

# Disable faulthandler to prevent conflicts with JPype signal handlers
faulthandler.disable()
logger.info("Faulthandler disabled")


@dataclass
class ThreadInfo:
    """Track thread state for debugging."""

    thread_id: int
    thread_name: str
    attached_to_jvm: bool = False
    attach_time: float | None = None
    detach_time: float | None = None
    java_calls: int = 0
    errors: list[str] = field(default_factory=list)


class JPypeDebugger:
    """Comprehensive JPype debugging utility."""

    def __init__(self):
        self.thread_registry: dict[int, ThreadInfo] = {}
        self.jvm_started = False
        self.class_load_times: dict[str, float] = {}
        self.lock = threading.Lock()
        self.start_time = time.time()

    def log_event(self, event: str, details: str = ""):
        """Log a timestamped event."""
        elapsed = time.time() - self.start_time
        thread = threading.current_thread()
        logger.info(f"[{elapsed:.3f}s] {event}: {details} (thread: {thread.name}/{thread.ident})")

    def register_thread(self) -> ThreadInfo:
        """Register current thread for tracking."""
        thread = threading.current_thread()
        with self.lock:
            if thread.ident not in self.thread_registry:
                info = ThreadInfo(
                    thread_id=thread.ident,
                    thread_name=thread.name,
                )
                self.thread_registry[thread.ident] = info
                self.log_event("THREAD_REGISTERED", f"{thread.name}")
            return self.thread_registry[thread.ident]

    def check_thread_attachment(self) -> tuple[bool, str]:
        """Check if current thread is attached to JVM."""
        import jpype

        thread = threading.current_thread()
        info = self.register_thread()

        try:
            is_attached = jpype.isThreadAttachedToJVM()
            info.attached_to_jvm = is_attached
            if is_attached and info.attach_time is None:
                info.attach_time = time.time()
            return is_attached, f"Thread {thread.name} attached: {is_attached}"
        except Exception as e:
            info.errors.append(str(e))
            return False, f"Error checking attachment: {e}"

    def attach_thread(self) -> tuple[bool, str]:
        """Explicitly attach current thread to JVM."""
        import jpype

        thread = threading.current_thread()
        info = self.register_thread()

        try:
            if not jpype.isThreadAttachedToJVM():
                jpype.attachThreadToJVM()
                info.attached_to_jvm = True
                info.attach_time = time.time()
                self.log_event("THREAD_ATTACHED", f"{thread.name}")
                return True, f"Thread {thread.name} attached successfully"
            else:
                return True, f"Thread {thread.name} was already attached"
        except Exception as e:
            info.errors.append(str(e))
            return False, f"Error attaching thread: {e}"

    def detach_thread(self) -> tuple[bool, str]:
        """Explicitly detach current thread from JVM."""
        import jpype

        thread = threading.current_thread()
        info = self.register_thread()

        try:
            if jpype.isJVMStarted():
                jpype.java.lang.Thread.detach()
                info.attached_to_jvm = False
                info.detach_time = time.time()
                self.log_event("THREAD_DETACHED", f"{thread.name}")
                return True, f"Thread {thread.name} detached successfully"
            return True, "JVM not started, no detach needed"
        except Exception as e:
            info.errors.append(str(e))
            return False, f"Error detaching thread: {e}"

    def preload_java_classes(self) -> dict[str, float]:
        """Preload Java classes on main thread and measure load times."""
        self.log_event("PRELOAD_START", "Loading Java classes on main thread")

        from jneqsim import neqsim

        classes_to_load = [
            ("SystemSrkEos", lambda: neqsim.thermo.system.SystemSrkEos),
            ("SystemPrEos", lambda: neqsim.thermo.system.SystemPrEos),
            ("Stream", lambda: neqsim.process.equipment.stream.Stream),
            ("Compressor", lambda: neqsim.process.equipment.compressor.Compressor),
            ("ThrottlingValve", lambda: neqsim.process.equipment.valve.ThrottlingValve),
            ("Mixer", lambda: neqsim.process.equipment.mixer.Mixer),
            ("Splitter", lambda: neqsim.process.equipment.splitter.Splitter),
            ("ProcessSystem", lambda: neqsim.process.processmodel.ProcessSystem),
        ]

        load_times = {}
        for name, loader in classes_to_load:
            start = time.time()
            try:
                _ = loader()
                elapsed = time.time() - start
                load_times[name] = elapsed
                self.class_load_times[name] = elapsed
                self.log_event("CLASS_LOADED", f"{name} in {elapsed:.3f}s")
            except Exception as e:
                self.log_event("CLASS_LOAD_ERROR", f"{name}: {e}")
                load_times[name] = -1

        self.log_event("PRELOAD_COMPLETE", f"Loaded {len(load_times)} classes")
        return load_times

    def get_jvm_info(self) -> dict[str, Any]:
        """Get current JVM state information."""
        import jpype

        info = {
            "jvm_started": jpype.isJVMStarted(),
            "jvm_path": jpype.getDefaultJVMPath() if not jpype.isJVMStarted() else "N/A",
            "thread_attached": False,
            "java_thread_count": "N/A",
            "java_heap_info": "N/A",
        }

        if jpype.isJVMStarted():
            try:
                info["thread_attached"] = jpype.isThreadAttachedToJVM()

                # Get Java thread info
                runtime = jpype.java.lang.Runtime.getRuntime()
                info["java_available_processors"] = runtime.availableProcessors()
                info["java_free_memory"] = runtime.freeMemory()
                info["java_total_memory"] = runtime.totalMemory()
                info["java_max_memory"] = runtime.maxMemory()

                # Get thread count
                thread_group = jpype.java.lang.Thread.currentThread().getThreadGroup()
                info["java_active_threads"] = thread_group.activeCount()

            except Exception as e:
                info["error"] = str(e)

        return info

    def print_summary(self):
        """Print debugging summary."""
        logger.info("=" * 60)
        logger.info("JPYPE DEBUGGING SUMMARY")
        logger.info("=" * 60)

        # Thread summary
        logger.info(f"\nRegistered threads: {len(self.thread_registry)}")
        for tid, info in self.thread_registry.items():
            status = "ATTACHED" if info.attached_to_jvm else "DETACHED"
            errors = f" (errors: {len(info.errors)})" if info.errors else ""
            logger.info(f"  - {info.thread_name} [{tid}]: {status}{errors}")

        # Class load times
        logger.info(f"\nClass load times:")
        for name, time_taken in self.class_load_times.items():
            logger.info(f"  - {name}: {time_taken:.3f}s")

        # JVM info
        jvm_info = self.get_jvm_info()
        logger.info(f"\nJVM State:")
        for key, value in jvm_info.items():
            logger.info(f"  - {key}: {value}")

        logger.info("=" * 60)


# Global debugger instance
debugger = JPypeDebugger()


def enable_java_logging():
    """Enable Java-side logging for debugging."""
    import jpype

    if not jpype.isJVMStarted():
        logger.warning("JVM not started, cannot enable Java logging")
        return

    try:
        # Get Java logging infrastructure
        Logger = jpype.JClass("java.util.logging.Logger")
        Level = jpype.JClass("java.util.logging.Level")
        ConsoleHandler = jpype.JClass("java.util.logging.ConsoleHandler")

        # Create a console handler for Java logs
        handler = ConsoleHandler()
        handler.setLevel(Level.ALL)

        # Get root logger and enable verbose logging
        root_logger = Logger.getLogger("")
        root_logger.setLevel(Level.INFO)
        root_logger.addHandler(handler)

        # Try to enable NeqSim-specific logging if available
        neqsim_logger = Logger.getLogger("neqsim")
        neqsim_logger.setLevel(Level.INFO)

        debugger.log_event("JAVA_LOGGING", "Java logging enabled at INFO level")

    except Exception as e:
        debugger.log_event("JAVA_LOGGING_ERROR", f"Could not enable Java logging: {e}")


def get_java_thread_dump():
    """Get a thread dump from the JVM."""
    import jpype

    if not jpype.isJVMStarted():
        return "JVM not started"

    try:
        Thread = jpype.JClass("java.lang.Thread")
        threads = Thread.getAllStackTraces()

        dump_lines = ["Java Thread Dump:"]
        for thread, stack in threads.entrySet():
            thread_info = f"\n  Thread: {thread.getName()} (daemon={thread.isDaemon()}, state={thread.getState()})"
            dump_lines.append(thread_info)
            for frame in stack:
                dump_lines.append(f"    at {frame}")

        return "\n".join(dump_lines)

    except Exception as e:
        return f"Error getting thread dump: {e}"


def test_basic_jvm_operations():
    """Test basic JVM operations with logging."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 1: Basic JVM Operations")
    logger.info("=" * 60)

    import jpype
    import jpype.config

    # Configure JPype
    jpype.config.destroy_jvm = False
    logger.info("Set jpype.config.destroy_jvm = False")

    # Check initial state
    debugger.log_event("JVM_STATE", f"Started: {jpype.isJVMStarted()}")

    # Import neqsim (this starts JVM)
    from jneqsim import neqsim

    debugger.log_event("JVM_STATE", f"After import: {jpype.isJVMStarted()}")

    # Check thread attachment
    is_attached, msg = debugger.check_thread_attachment()
    debugger.log_event("THREAD_CHECK", msg)

    # Enable Java-side logging
    enable_java_logging()

    # Preload classes
    debugger.preload_java_classes()

    # Get JVM info
    jvm_info = debugger.get_jvm_info()
    for key, value in jvm_info.items():
        debugger.log_event("JVM_INFO", f"{key}: {value}")

    # Get Java thread dump
    thread_dump = get_java_thread_dump()
    logger.debug(thread_dump)

    logger.info("TEST 1: PASSED\n")


def test_testclient_threading():
    """Test TestClient threading behavior with explicit attachment."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: TestClient Threading")
    logger.info("=" * 60)

    from fastapi.testclient import TestClient

    from api.main import app

    # Track thread before TestClient
    debugger.check_thread_attachment()

    def make_request_with_tracking(client, endpoint: str, iteration: int):
        """Make a request and track thread state."""
        debugger.log_event("REQUEST_START", f"Iteration {iteration}: {endpoint}")

        # Check attachment before request
        is_attached, _ = debugger.check_thread_attachment()
        debugger.log_event("PRE_REQUEST", f"Thread attached: {is_attached}")

        try:
            response = client.get(endpoint)
            debugger.log_event("REQUEST_COMPLETE", f"Status: {response.status_code}")
        except Exception as e:
            debugger.log_event("REQUEST_ERROR", str(e))
            raise

        # Check attachment after request
        is_attached, _ = debugger.check_thread_attachment()
        debugger.log_event("POST_REQUEST", f"Thread attached: {is_attached}")

    # Test with TestClient
    for i in range(3):
        debugger.log_event("TESTCLIENT_CREATE", f"Iteration {i}")

        with TestClient(app) as client:
            debugger.log_event("TESTCLIENT_ENTER", f"Iteration {i}")
            make_request_with_tracking(client, "/", i)

        # Explicit detachment after TestClient closes
        import jpype

        if jpype.isJVMStarted():
            success, msg = debugger.detach_thread()
            debugger.log_event("POST_TESTCLIENT_DETACH", msg)

        debugger.log_event("TESTCLIENT_EXIT", f"Iteration {i}")
        time.sleep(0.1)  # Small delay between iterations

    logger.info("TEST 2: PASSED\n")


def test_concurrent_java_calls():
    """Test concurrent Java calls from multiple threads."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: Concurrent Java Calls")
    logger.info("=" * 60)

    import jpype

    from jneqsim import neqsim

    results = []
    errors = []
    lock = threading.Lock()

    def worker(thread_id: int):
        """Worker function that makes Java calls."""
        debugger.register_thread()
        debugger.log_event("WORKER_START", f"Thread {thread_id}")

        try:
            # Explicit attachment
            success, msg = debugger.attach_thread()
            debugger.log_event("WORKER_ATTACH", msg)

            # Make Java calls
            for i in range(3):
                debugger.log_event("JAVA_CALL_START", f"Thread {thread_id}, call {i}")

                # Create a thermo system (this is what crashes in production)
                system = neqsim.thermo.system.SystemSrkEos(273.15 + 25, 50.0)
                system.addComponent("methane", 1.0)
                system.setMixingRule(2)

                debugger.log_event("JAVA_CALL_COMPLETE", f"Thread {thread_id}, call {i}")
                time.sleep(0.05)

            with lock:
                results.append(f"Thread {thread_id}: SUCCESS")

        except Exception as e:
            debugger.log_event("WORKER_ERROR", f"Thread {thread_id}: {e}")
            with lock:
                errors.append(f"Thread {thread_id}: {e}")

        finally:
            # Explicit detachment
            success, msg = debugger.detach_thread()
            debugger.log_event("WORKER_DETACH", msg)
            debugger.log_event("WORKER_END", f"Thread {thread_id}")

    # Start multiple threads
    threads = []
    for i in range(3):
        t = threading.Thread(target=worker, args=(i,), name=f"Worker-{i}")
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join()

    logger.info(f"Results: {results}")
    logger.info(f"Errors: {errors}")

    if errors:
        logger.error("TEST 3: FAILED - Some threads had errors")
    else:
        logger.info("TEST 3: PASSED\n")


def test_simulate_endpoint():
    """Test the actual simulate endpoint that crashes."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 4: Simulate Endpoint (Crash Point)")
    logger.info("=" * 60)

    import jpype
    from fastapi.testclient import TestClient

    from api.main import app

    # Simple flowsheet for testing
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

    for iteration in range(5):
        debugger.log_event("SIMULATE_START", f"Iteration {iteration}")

        # Check thread state before
        is_attached, _ = debugger.check_thread_attachment()
        debugger.log_event("PRE_SIMULATE", f"Main thread attached: {is_attached}")

        try:
            with TestClient(app) as client:
                debugger.log_event("TESTCLIENT_READY", f"Iteration {iteration}")

                response = client.post("/api/v1/simulate", json=request_data)

                debugger.log_event(
                    "SIMULATE_RESPONSE",
                    f"Status: {response.status_code}",
                )

                if response.status_code == 200:
                    data = response.json()
                    debugger.log_event(
                        "SIMULATE_SUCCESS",
                        f"Converged: {data.get('results', {}).get('convergence_status')}",
                    )
                else:
                    debugger.log_event("SIMULATE_ERROR", response.text[:200])

        except Exception as e:
            debugger.log_event("SIMULATE_EXCEPTION", str(e))
            raise

        finally:
            # Explicit detachment
            if jpype.isJVMStarted():
                success, msg = debugger.detach_thread()
                debugger.log_event("POST_SIMULATE_DETACH", msg)

        # Small delay between iterations
        time.sleep(0.1)

        # Force GC
        gc.collect()
        debugger.log_event("GC_COMPLETE", f"Iteration {iteration}")

    logger.info("TEST 4: PASSED\n")


def test_rapid_testclient_cycles():
    """Test rapid TestClient creation/destruction cycles."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 5: Rapid TestClient Cycles (Stress Test)")
    logger.info("=" * 60)

    import jpype
    from fastapi.testclient import TestClient

    from api.main import app

    for i in range(10):
        debugger.log_event("CYCLE_START", f"Iteration {i}")

        with TestClient(app) as client:
            response = client.get("/")
            debugger.log_event("CYCLE_REQUEST", f"Status: {response.status_code}")

        # Detach after each cycle
        if jpype.isJVMStarted():
            debugger.detach_thread()

        debugger.log_event("CYCLE_END", f"Iteration {i}")

    logger.info("TEST 5: PASSED\n")


def main():
    """Run all debugging tests."""
    logger.info("=" * 60)
    logger.info("JPYPE/JVM THREADING DEBUG SESSION")
    logger.info(f"Started: {datetime.now().isoformat()}")
    logger.info(f"Python: {sys.version}")
    logger.info(f"PID: {os.getpid()}")
    logger.info("=" * 60)

    try:
        # Test 1: Basic JVM operations
        test_basic_jvm_operations()

        # Test 2: TestClient threading
        test_testclient_threading()

        # Test 3: Concurrent Java calls
        test_concurrent_java_calls()

        # Test 4: Simulate endpoint
        test_simulate_endpoint()

        # Test 5: Rapid cycles
        test_rapid_testclient_cycles()

        # Print summary
        debugger.print_summary()

        logger.info("\n" + "=" * 60)
        logger.info("ALL TESTS COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)

    except Exception as e:
        logger.exception(f"TEST FAILED: {e}")

        # Still print summary on failure
        debugger.print_summary()

        sys.exit(1)


if __name__ == "__main__":
    main()
