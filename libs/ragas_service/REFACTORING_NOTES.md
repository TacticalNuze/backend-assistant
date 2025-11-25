# RAGAS Service Refactoring Notes

## Overview

The RAGAS evaluation service has been refactored to follow the same patterns as other services in the `libs` folder (e.g., `chunking_service`, `embeddings_service`).

## Changes Made

### 1. Created Service Class (`service.py`)

- **New**: `RagasEvaluationService` class following the interface pattern
- **Features**:
  - Configurable initialization via constructor parameters or environment variables
  - Proper logging using Python's logging module (instead of print statements)
  - Lazy initialization of components
  - Support for enabling/disabling Langfuse tracing
  - Better error handling and validation

### 2. Refactored Module (`langfuse_tracing.py`)

- **Maintained**: Backward compatibility with existing function signatures
- **Changed**: Functions now use the service class internally
- **Benefits**:
  - Same API for existing code
  - Better maintainability
  - Consistent with other services

### 3. Updated Package Exports (`__init__.py`)

- Exports both the service class and backward-compatible functions
- Proper error handling if RAGAS is not installed

## Usage Patterns

### Pattern 1: Service Class (Recommended for new code)

```python
from libs.ragas_service import RagasEvaluationService

# Initialize service
service = RagasEvaluationService(
    max_tokens=2000,
    enable_langfuse=True
)

# Use service methods
scores = await service.score(
    user_input="What is AI?",
    retrieved_contexts=["AI is..."],
    response="AI stands for..."
)

# Or evaluate with automatic field mapping
result = await service.evaluate({
    "user_input": "What is AI?",
    "response": "AI stands for...",
    "retrieved_contexts": ["AI is..."]
})
```

### Pattern 2: Backward-Compatible Functions (Existing code)

```python
from libs.ragas_service.langfuse_tracing import score_with_ragas

# Works exactly as before
scores = await score_with_ragas(
    user_input="What is AI?",
    retrieved_contexts=["AI is..."],
    response="AI stands for..."
)
```

## Configuration

The service can be configured via:

1. **Constructor parameters** (highest priority)
2. **Environment variables** (fallback)
3. **Default values** (last resort)

### Environment Variables

- `AZURE_OPENAI_API_KEY` - Azure OpenAI API key
- `AZURE_OPENAI_ENDPOINT` - Azure OpenAI endpoint
- `AZURE_OPENAI_CHAT_DEPLOYMENT` - Deployment name
- `OPENAI_API_VERSION` - API version (default: "2024-02-15-preview")
- `MAX_TOKENS` - Maximum tokens for LLM responses (default: 1000)

### Constructor Parameters

All environment variables can be overridden via constructor parameters, plus:
- `embedding_model` - Embedding model name (default: "text-embedding-3-large")
- `llm_model` - LLM model name (default: "gpt-4")
- `enable_langfuse` - Enable/disable Langfuse tracing (default: True)

## Benefits

1. **Consistency**: Follows the same patterns as other services
2. **Maintainability**: Centralized configuration and initialization
3. **Testability**: Service class can be easily mocked and tested
4. **Flexibility**: Can create multiple service instances with different configs
5. **Backward Compatibility**: Existing code continues to work

## Migration Guide

### No Changes Required

Existing code using `score_with_ragas()` and `map_rag_output_to_score_fields()` will continue to work without any changes.

### Optional: Migrate to Service Class

If you want to take advantage of the new features, you can migrate to using the service class:

**Before:**
```python
from libs.ragas_service.langfuse_tracing import score_with_ragas

scores = await score_with_ragas(...)
```

**After:**
```python
from libs.ragas_service import RagasEvaluationService

service = RagasEvaluationService()
scores = await service.score(...)
```

## Testing

The refactored code maintains the same functionality, so existing tests should continue to work. The service class can be tested independently:

```python
import pytest
from libs.ragas_service import RagasEvaluationService

@pytest.fixture
def ragas_service():
    return RagasEvaluationService(enable_langfuse=False)

async def test_score(ragas_service):
    scores = await ragas_service.score(
        user_input="test",
        retrieved_contexts=["context"],
        response="response"
    )
    assert "ContextPrecision" in scores
```

