import os
import json
import time
import pytest
from pydantic import BaseModel

from agent.llm import (
    classify_429,
    load_llm_state,
    save_llm_state,
    record_entry_exhaustion,
    is_entry_exhausted,
    get_next_midnight_pacific,
)

# Realistic Error Fixtures
GOOGLE_PER_DAY_FIXTURE = """
429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '100'}]}]}}
"""

GOOGLE_PER_MINUTE_FIXTURE = """
429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. Please retry in 4.27s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '5'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '4s'}]}}
"""

GROQ_TPD_FIXTURE = """
Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199996, Requested 1849. Please try again in 13m17.04s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
"""

GROQ_TPM_FIXTURE = """
Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01` service tier `on_demand` on tokens per minute (TPM): Limit 6000, Used 5900, Requested 400. Please try again in 5.2s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}
"""

GROQ_RPD_FIXTURE = """
Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` on requests per day (RPD): Limit 14400. Please try again tomorrow.', 'type': 'requests', 'code': 'rate_limit_exceeded'}}
"""

GROQ_RPM_FIXTURE = """
Error code: 429 - {'error': {'message': 'Rate limit reached for model `openai/gpt-oss-120b` on requests per minute (RPM): Limit 30. Please try again in 2s.', 'type': 'requests', 'code': 'rate_limit_exceeded'}}
"""

def test_quota_classification_google_perday():
    err = RuntimeError(GOOGLE_PER_DAY_FIXTURE)
    ex_type, _ra, reason = classify_429("gemini", err)
    assert ex_type == "daily"
    assert "PerDay" in reason

def test_quota_classification_google_perminute():
    err = RuntimeError(GOOGLE_PER_MINUTE_FIXTURE)
    ex_type, ra, reason = classify_429("gemini", err)
    assert ex_type == "per_minute"
    assert "PerMinute" in reason
    assert ra == 4.0 or ra == 4.27

def test_quota_classification_groq_tpd():
    err = RuntimeError(GROQ_TPD_FIXTURE)
    ex_type, _ra, reason = classify_429("groq", err)
    assert ex_type == "daily"
    assert "TPD" in reason

def test_quota_classification_groq_tpm():
    err = RuntimeError(GROQ_TPM_FIXTURE)
    ex_type, ra, reason = classify_429("groq", err)
    assert ex_type == "per_minute"
    assert "TPM" in reason
    assert ra == 5.2

def test_quota_classification_groq_rpd():
    err = RuntimeError(GROQ_RPD_FIXTURE)
    ex_type, _ra, reason = classify_429("groq", err)
    assert ex_type == "daily"
    assert "RPD" in reason

def test_quota_classification_groq_rpm():
    err = RuntimeError(GROQ_RPM_FIXTURE)
    ex_type, ra, reason = classify_429("groq", err)
    assert ex_type == "per_minute"
    assert "RPM" in reason
    assert ra == 2.0

def test_per_model_exhaustion(tmp_path):
    test_state_file = str(tmp_path / "test_state.json")
    
    # Record exhaustion for model-A
    record_entry_exhaustion("gemini", "model-A", "daily", "test daily quota", filepath=test_state_file)
    
    # model-A should be exhausted
    is_ex_a, reason_a, rem_a = is_entry_exhausted("gemini", "model-A", filepath=test_state_file)
    assert is_ex_a is True
    assert "test daily quota" in reason_a
    assert rem_a > 0
    
    # model-B under the same provider must NOT be exhausted
    is_ex_b, _reason_b, rem_b = is_entry_exhausted("gemini", "model-B", filepath=test_state_file)
    assert is_ex_b is False
    assert rem_b == 0.0

def test_persisted_state_loaded(tmp_path):
    test_state_file = str(tmp_path / "test_state.json")
    
    # Save active entry (expires in 1 hour) and expired entry (expired 10 seconds ago)
    now = time.time()
    state = {
        "groq/model-active": {
            "exhausted": True,
            "type": "daily",
            "reason": "active limit",
            "expires_at": now + 3600.0,
            "recorded_at": now
        },
        "groq/model-expired": {
            "exhausted": True,
            "type": "per_minute",
            "reason": "expired limit",
            "expires_at": now - 10.0,
            "recorded_at": now - 70.0
        }
    }
    save_llm_state(state, filepath=test_state_file)
    
    loaded = load_llm_state(filepath=test_state_file)
    assert "groq/model-active" in loaded
    assert "groq/model-expired" not in loaded  # Pruned automatically

def test_next_midnight_pacific():
    midnight_ts = get_next_midnight_pacific()
    now_ts = time.time()
    assert midnight_ts > now_ts
    assert midnight_ts - now_ts <= 86400 * 2
