---
name: performance_profiler
description: Profiling runtime latency, CPU hotspots, memory utilization, I/O bottlenecks, and algorithm optimization.
triggers: [perf, performance, profile, memory leak, slow, latency, benchmark, optimize]
---
# Performance Profiler for Harness

Guidance for diagnosing latency bottlenecks, memory spikes, and algorithmic efficiency.

## Optimization Workflow
1. **Measure First (No Premature Optimization)**:
   - Profile before changing code (`cProfile`, `timeit`, `tracemalloc`).
   - Identify the specific bottleneck: is it CPU bound, I/O bound, or memory bound?
2. **Common Culprits**:
   - N+1 database queries.
   - Accidental quadratic loops ($O(N^2)$ inside loops over large arrays).
   - Unbuffered I/O or redundant file read/writes.
   - Synchronous network calls blocking the async event loop.
3. **Verification**:
   - Compare before-and-after benchmarks with identical workloads to prove tangible improvement.
