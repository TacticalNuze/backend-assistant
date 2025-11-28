from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any


# OpenAPI examples for WorkflowRequest
WORKFLOW_REQUEST_EXAMPLES = {
    "vector_preprocessing": {
        "summary": "Vector Preprocessing",
        "description": "Example for vector-based preprocessing workflow",
        "value": {
            "input": {
                "client_id": "dpac",
                "domain_id": "dpac",
                "language": "it",
                "project_id": "dpac_portal_test_1",
                "chunking_method": "semantic_chunker",
                "embedding_model": "text-embedding-3-large",
                "embedding_provider": "azure_openai",
                "embedding_batch_size": 10,
                "workflow_id": "vector_preprocessing_001"
            }
        }
    },
    
    "generate_eval_dataset": {
        "summary": "Generate evaluation dataset",
        "description": "Example for generating the evaluation dataset using modular pipeline operations. The workflow: 1) Retrieves random chunks, 2) Generates queries, 3) Processes queries in parallel, 4) Combines dataset, 5) Saves to file, 6) Saves to Langfuse",
        "value": {
            "input": {
                "client_id": "ragtest",
                "project_id": "rag_evaluation_test",
                "language": "en",
                "num_queries": 20,
                "chunks_per_query": 5,
                "output_path": "eval_dataset.json",
                "query_generation_prompt_key": "generate-query-from-chunk",
                "ground_truth_prompt_key": "generate-ground-truth",
                "embedding_model": "text-embedding-3-large",
                "embedding_provider": "azure_openai",
                "generate_rag_response": True,
                "rag_prompt_key": "run-vector-rag",
                "workflow_id": "generate_evaluation_dataset_001"
            }
        }
    }
}

# OpenAPI examples for ChatRequest
CHAT_REQUEST_EXAMPLES = {
    "vector_inference": {
        "summary": "Vector Inference",
        "description": "Example for vector-based inference workflow",
        "value": {
            "input": {
                "client_id": "dpac",
                "domain_id": "dpac",
                "input_text": "cos'è il D.PaC?",
                "language": "it",
                "project_id": "dpac_portal",
                "session_id": "test_session_001",
                "user_id": "user123",
                "top_k": 5,
                "limit": 10,
                "workflow_id": "vector_inference_001"
            }
        }
    },
    "evaluate_rag": {
        "summary": "Rag evaluation pipeline",
        "description": "Example for evaluating the RAG responses",
        "value": {
            "input": {
                "client_id": "ragtest",
                "domain_id": "ragtest",
                "input_text": "What is DXC's code of conduct main ideas?",
                "project_id": "english_test",
                "language": "en",
                "project_id": "rag_evaluation_test",
                "session_id": "rag_evaluation_session_001",
                "user_id": "user123",
                "num_queries": 20,
                "chunks_per_query": 5,
                "output_path": "eval_dataset.json",
                "generate_rag_response": True,
                "top_k": 5,
                "limit": 10,
                "workflow_id": "rag_evaluation_001"
            }
        }
    }
}
class WorkflowRequest(BaseModel):
    input: Dict[str, Any] = Field(
        ...,
        description="Workflow input parameters"
    )


class ChatRequest(BaseModel):
    input: Dict[str, Any] = Field(
        ...,
        description="Chat workflow input parameters"
    )


class TaskInfo(BaseModel):
    step_name: str = Field(..., description="Name of the pipeline step")
    pipeline_key: str = Field(..., description="Pipeline key for the step")
    task_id: str = Field(..., description="Celery task ID")
    queue: str = Field(..., description="Queue name where the task is running")
    status: str = Field(..., description="Current status of the task")


class WorkflowResponse(BaseModel):
    workflow_id: str = Field(..., description="Unique identifier for the workflow")
    tasks: List[TaskInfo] = Field(..., description="List of tasks in the workflow")
