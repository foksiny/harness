#!/usr/bin/env python3
"""
Harness Benchmark Suite.
Measures cold-start time, RAM usage, tool registration, and skill loading.
Run: python3 benchmarks/bench_startup.py
"""
import time
import os
import sys
import importlib

def measure_rss_mb():
    """Current process RSS in MB (cross-platform)."""
    from harness.sysinfo import get_ram_usage_mb
    mb = get_ram_usage_mb()
    return mb if mb > 0 else -1.0

def bench_cold_start():
    """Time a fresh import of the core harness modules."""
    t0 = time.perf_counter()
    import harness
    import harness.core.agent
    import harness.tools
    import harness.providers
    import harness.skills
    t1 = time.perf_counter()
    return t1 - t0

def bench_tool_registration():
    """Time ToolRegistry construction (includes all default tools)."""
    from harness.tools import ToolRegistry
    t0 = time.perf_counter()
    r = ToolRegistry()
    t1 = time.perf_counter()
    return t1 - t0, len(r.list_tools())

def bench_skill_loading():
    """Time SkillsManager discovery."""
    from harness.skills.loader import SkillsManager
    t0 = time.perf_counter()
    sm = SkillsManager()
    t1 = time.perf_counter()
    return t1 - t0, len(sm.list_skills())

def bench_model_detection():
    """Time model spec resolution for a batch of popular models."""
    from harness.providers.detector import inspect_model
    models = [
        ("claude-sonnet-4-5", "anthropic"),
        ("gpt-4o", "openai"),
        ("gemini-2.5-flash", "gemini"),
        ("deepseek-r1", "deepseek"),
        ("llama-3.3-70b-instruct", "groq"),
        ("grok-4.5", "xai"),
        ("mistral-large-latest", "mistral"),
        ("qwen-3-235b-a22b", "openrouter"),
    ]
    t0 = time.perf_counter()
    for name, prov in models:
        inspect_model(name, prov)
    t1 = time.perf_counter()
    return t1 - t0, len(models)

def main():
    print("=" * 60)
    print("  Harness Benchmark Suite")
    print("=" * 60)

    # Baseline RSS
    rss_before = measure_rss_mb()
    print(f"\n  RSS before imports:     {rss_before:.1f} MB")

    # Cold start
    t_cold = bench_cold_start()
    rss_after = measure_rss_mb()
    print(f"  Cold start time:        {t_cold * 1000:.1f} ms")
    print(f"  RSS after imports:      {rss_after:.1f} MB")
    print(f"  RSS delta:              {rss_after - rss_before:.1f} MB")

    # Tool registration
    t_tools, n_tools = bench_tool_registration()
    print(f"\n  Tool registration:      {t_tools * 1000:.1f} ms ({n_tools} tools)")

    # Skill loading
    t_skills, n_skills = bench_skill_loading()
    print(f"  Skill loading:          {t_skills * 1000:.1f} ms ({n_skills} skills)")

    # Model detection
    t_models, n_models = bench_model_detection()
    print(f"  Model detection:        {t_models * 1000:.1f} ms ({n_models} models)")

    # Full session init (simulated)
    rss_before_full = measure_rss_mb()
    t0 = time.perf_counter()
    from harness.tools import ToolRegistry
    from harness.skills.loader import SkillsManager
    from harness.core.todo import TodoManager
    from harness.core.subagents import SubagentOrchestrator
    from harness.core.learning import LearningManager
    from harness.core.permissions import PermissionManager
    r = ToolRegistry()
    sm = SkillsManager()
    tm = TodoManager()
    lm = LearningManager()
    pm = PermissionManager()
    t1 = time.perf_counter()
    rss_after_full = measure_rss_mb()
    print(f"\n  Full init (registry+skills+todos+perms):")
    print(f"    Time:                 {(t1 - t0) * 1000:.1f} ms")
    print(f"    RSS delta:            {rss_after_full - rss_before_full:.1f} MB")

    print(f"\n{'=' * 60}")
    print(f"  FINAL RSS:              {measure_rss_mb():.1f} MB")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
