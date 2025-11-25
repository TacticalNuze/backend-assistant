# RAGAS Evaluation Service

This service provides RAG (Retrieval Augmented Generation) evaluation metrics using RAGAS.

## Integration with Pipelines

The RAGAS evaluation has been integrated into the pipeline system. You can use it in your pipeline configurations.

### Pipeline Operation: `evaluate_rag_with_ragas`

This operation evaluates RAG responses using RAGAS metrics (ContextPrecision, ContextRecall, ContextRelevance) with Langfuse tracing.

#### Required Inputs:
- `input_text`: The user query/question
- `run_vector_rag`: The LLM response
- `search_relevant_chunks`: Object containing `relevant_chunks` list

#### Optional Inputs:
- `reference` or `ground_truth`: Ground truth answer for evaluation (optional)

#### Output:
```json
{
  "ragas_scores": {
    "ContextPrecision": 0.85,
    "ContextRecall": 0.92,
    "ContextRelevance": 0.88
  },
  "evaluation_metadata": {
    "num_contexts": 5,
    "has_reference": true,
    "user_input": "What is AI?",
    "response_length": 150
  }
}
```

### Example Pipeline Configuration

Add `evaluate_rag_with_ragas` to your pipeline after generating the response:

```json
{
  "pipeline_steps": [
    "search_relevant_chunks",
    "run_vector_rag",
    "GetVectorReference",
    "combine_vector_response_and_references",
    "evaluate_rag_with_ragas",
    "save_vector_llm_message"
  ]
}
```

### Usage in Code

The operation is automatically available in the pipeline system:

```python
# In your pipeline execution
result = execute_pipeline_step(
    inputs={
        "input_text": "What is machine learning?",
        "run_vector_rag": "Machine learning is...",
        "search_relevant_chunks": {
            "relevant_chunks": [...]
        },
        "reference": "Optional ground truth answer"
    },
    pipeline_key="evaluate_rag_with_ragas",
    project_name="my_project",
    prompt_config={}
)

# Access scores
scores = result.get("ragas_scores", {})
print(f"Context Precision: {scores.get('ContextPrecision')}")
print(f"Context Recall: {scores.get('ContextRecall')}")
print(f"Context Relevance: {scores.get('ContextRelevance')}")
```

## Metrics Explained

- **ContextPrecision**: Measures how many of the retrieved contexts are relevant to the query
- **ContextRecall**: Measures how many relevant contexts were retrieved
- **ContextRelevance**: Measures the relevance of retrieved contexts to the query

## Requirements

Make sure `ragas` is installed:
```bash
pip install ragas
```

Note: On Windows, you may need Microsoft Visual C++ Build Tools to install ragas (see INSTALL_WINDOWS.md).

## Langfuse Integration

All evaluations are automatically traced in Langfuse for monitoring and analysis.

