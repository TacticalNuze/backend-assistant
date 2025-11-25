# Guide: FastAPI Endpoints & Adding Python Workflows

This guide explains how FastAPI endpoints are created in this project and how to add a new Python file as a workflow.

## 📋 Table of Contents

1. [How FastAPI Endpoints Are Created](#how-fastapi-endpoints-are-created)
2. [How to Add a New Endpoint](#how-to-add-a-new-endpoint)
3. [How Workflows Work](#how-workflows-work)
4. [How to Add a Python File as a Workflow](#how-to-add-a-python-file-as-a-workflow)
5. [Complete Example](#complete-example)

---

## How FastAPI Endpoints Are Created

### Architecture Overview

The FastAPI application follows this structure:

```
app/
├── main.py                    # FastAPI app initialization
├── web_server/
│   └── router.py             # All API endpoints defined here
├── pipelines/
│   └── pipelines_app.py      # Pipeline operation classes
└── templates/
    ├── vector_preprocessing.yml
    └── vector_inference.yml
```

### Step-by-Step: How Endpoints Work

#### 1. **FastAPI App Initialization** (`app/main.py`)

```python
from fastapi import FastAPI
from app.web_server.router import router

app = FastAPI()
app.include_router(router)  # Register all routes from router.py
```

#### 2. **Router Definition** (`app/web_server/router.py`)

All endpoints are defined using FastAPI's `APIRouter`:

```python
from fastapi import APIRouter, Path, Body
from fastapi.responses import JSONResponse

router = APIRouter()

# Example: Simple GET endpoint
@router.get("/health")
def health():
    return JSONResponse({"api": "ok"})

# Example: POST endpoint with path parameter
@router.post("/api/workflow/{template}")
def start_workflow(
    template: str = Path(..., description="Template name"),
    request: WorkflowRequest = Body(...)
):
    # Endpoint logic here
    pass
```

#### 3. **Current Endpoints in the Project**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check |
| `/api/workflow/{template}` | POST | Start a workflow using a YAML template |
| `/api/chat/{template}` | POST | Start a chat workflow |
| `/api/results/{task_id}` | GET | Get task result by ID |
| `/api/chat-history` | GET | Get chat history for a session |
| `/api/workflow/{workflow_id}/status` | GET | Get workflow status |

---

## How to Add a New Endpoint

### Example: Adding a Simple Endpoint

1. **Open `app/web_server/router.py`**

2. **Add your endpoint function:**

```python
@router.get("/api/my-new-endpoint")
def my_new_endpoint(
    param1: str = Query(..., description="First parameter"),
    param2: int = Query(default=10, description="Second parameter")
):
    """
    Description of what this endpoint does.
    """
    try:
        # Your logic here
        result = {"message": f"Received {param1} and {param2}"}
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        return JSONResponse(
            status_code=500, 
            content={"error": "internal_error", "details": str(e)}
        )
```

3. **The endpoint is automatically available** at `http://localhost:8002/api/my-new-endpoint`

### Example: Adding a POST Endpoint with Request Body

```python
from app.utils.schemas import WorkflowRequest  # Or create your own schema

@router.post("/api/custom-action")
def custom_action(request: WorkflowRequest):
    """
    Process a custom action with input data.
    """
    try:
        input_data = request.input
        # Process input_data
        return JSONResponse({"success": True, "data": input_data})
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )
```

---

## How Workflows Work

### Workflow Architecture

Workflows in this project are **declarative YAML templates** that define a series of steps executed by Celery workers:

```
Client Request
    ↓
FastAPI Endpoint (/api/workflow/{template})
    ↓
Load YAML Template (app/templates/{template}.yml)
    ↓
Parse Template & Generate Task Structure
    ↓
Enqueue Tasks to Redis (Celery)
    ↓
Celery Workers Execute Steps
    ↓
Each Step Executes a Pipeline Operation (Python Class)
```

### Workflow Template Structure

A workflow template (YAML file) has this structure:

```yaml
defaults: &defaults
  template_id: my_workflow
  prompt_config:
    source: langfuse
  database: default

task: my_workflow
<<: *defaults
inputs:
  - client_id
  - project_id
  - custom_param

steps:
  - step: step1_name
    pipeline_key: PipelineOperationClass
    inputs:
      - client_id
      - project_id
    queue: localAPI_queue
    
  - step: step2_name
    pipeline_key: AnotherPipelineOperation
    inputs:
      - step1_name  # Depends on previous step
      - custom_param
    queue: io_queue
```

### Key Concepts

1. **Steps**: Each step is a unit of work
2. **Pipeline Key**: Maps to a Python class in `pipeline_operations` dictionary
3. **Inputs**: Can be from initial request OR from previous step outputs
4. **Queue**: Determines which Celery queue processes the task
5. **Dependencies**: Steps automatically wait for their input dependencies

---

## How to Add a Python File as a Workflow

### Step 1: Create Your Pipeline Operation Class

Create a new Python class in `app/pipelines/pipelines_app.py` (or create a new file and import it):

```python
class MyCustomOperation:
    """
    Custom pipeline operation that does something specific.
    """
    
    def __init__(self, inputs: Dict[str, Any], project_name: str, 
                 prompt_config: Dict[str, Any], pipeline_key: str):
        """
        Initialize the operation.
        
        Args:
            inputs: Dictionary of input data (from previous steps or initial request)
            project_name: Project identifier
            prompt_config: Prompt configuration
            pipeline_key: The pipeline key used to identify this operation
        """
        self.inputs = inputs
        self.project_name = project_name
        self.prompt_config = prompt_config
        self.pipeline_key = pipeline_key
    
    def execute(self) -> Dict[str, Any]:
        """
        Execute the operation logic.
        
        Returns:
            Dictionary with operation results
        """
        # Get inputs
        client_id = self.inputs.get("client_id")
        project_id = self.inputs.get("project_id")
        custom_data = self.inputs.get("custom_param")
        
        # Your custom logic here
        result = {
            "status": "success",
            "processed_data": f"Processed {custom_data} for {client_id}/{project_id}",
            "timestamp": time.time()
        }
        
        return result
```

### Step 2: Register the Operation

Add your class to the `pipeline_operations` dictionary in `app/pipelines/pipelines_app.py`:

```python
# At the end of pipelines_app.py, find pipeline_operations dictionary
pipeline_operations: Dict[str, Any] = {
    "GetFiles": GetFiles,
    "ParseDocuments": ParseDocuments,
    # ... existing operations ...
    
    # Add your new operation here
    "MyCustomOperation": MyCustomOperation,  # pipeline_key -> Class mapping
}
```

### Step 3: Create a YAML Template

Create a new file `app/templates/my_custom_workflow.yml`:

```yaml
defaults: &defaults
  template_id: my_custom_workflow
  prompt_config:
    source: langfuse
    organization_name: dxc
    project_name: my_project
  database: default

task: my_custom_workflow
<<: *defaults
inputs:
  - client_id
  - project_id
  - custom_param

steps:
  # Step 1: Your custom operation
  - step: my_custom_step
    pipeline_key: MyCustomOperation
    inputs:
      - client_id
      - project_id
      - custom_param
    queue: localAPI_queue  # or io_queue, default_queue
    
  # Step 2: Optional - another step that depends on the first
  - step: follow_up_step
    pipeline_key: AnotherOperation  # Must be registered in pipeline_operations
    inputs:
      - my_custom_step  # Uses output from previous step
    queue: localAPI_queue
```

### Step 4: Create an Endpoint (Optional)

If you want a dedicated endpoint, add to `app/web_server/router.py`:

```python
@router.post("/api/my-custom-workflow")
def start_my_custom_workflow(request: WorkflowRequest):
    """
    Start the custom workflow.
    """
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        templates_dir = os.path.join(base_dir, 'templates')
        template_path = os.path.join(templates_dir, "my_custom_workflow.yml")
        
        if not os.path.exists(template_path):
            return JSONResponse(
                status_code=404,
                content={"error": "Template not found"}
            )
        
        with open(template_path, 'r', encoding='utf-8') as f:
            template_config = yaml.safe_load(f) or {}
        
        workflow_id = request.input.get("workflow_id", "default_workflow")
        
        # Generate tasks structure
        tasks_structure = generate_tasks_structure(
            workflow_input={"workflow_id": workflow_id, **request.input},
            template_config=template_config
        )
        
        return WorkflowResponse(
            workflow_id=workflow_id,
            tasks=tasks_structure
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "details": str(e)}
        )
```

**OR** use the generic endpoint:

```bash
POST http://localhost:8002/api/workflow/my_custom_workflow
```

### Step 5: Test Your Workflow

```bash
# Using curl
curl -X POST "http://localhost:8002/api/workflow/my_custom_workflow" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "workflow_id": "test_001",
      "client_id": "testclient",
      "project_id": "testproject",
      "custom_param": "test value"
    }
  }'

# Check task status
curl "http://localhost:8002/api/results/{task_id}"
```

---

## Complete Example

### Example: Creating a "Data Validation" Workflow

#### 1. Create the Pipeline Operation

In `app/pipelines/pipelines_app.py`, add:

```python
class ValidateData:
    """Validate input data and return validation results."""
    
    def __init__(self, inputs: Dict[str, Any], project_name: str,
                 prompt_config: Dict[str, Any], pipeline_key: str):
        self.inputs = inputs
    
    def execute(self) -> Dict[str, Any]:
        """Validate the input data."""
        data = self.inputs.get("data_to_validate", {})
        client_id = self.inputs.get("client_id")
        
        # Validation logic
        errors = []
        if not client_id:
            errors.append("client_id is required")
        if not data:
            errors.append("data_to_validate is required")
        
        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "validated_data": data,
            "client_id": client_id
        }
```

#### 2. Register the Operation

```python
pipeline_operations: Dict[str, Any] = {
    # ... existing operations ...
    "ValidateData": ValidateData,
}
```

#### 3. Create Template: `app/templates/data_validation.yml`

```yaml
defaults: &defaults
  template_id: data_validation
  prompt_config:
    source: langfuse
  database: default

task: data_validation
<<: *defaults
inputs:
  - client_id
  - project_id
  - data_to_validate

steps:
  - step: validate_data
    pipeline_key: ValidateData
    inputs:
      - client_id
      - data_to_validate
    queue: localAPI_queue
```

#### 4. Use the Workflow

```bash
POST /api/workflow/data_validation
{
  "input": {
    "workflow_id": "validation_001",
    "client_id": "testclient",
    "project_id": "testproject",
    "data_to_validate": {"key": "value"}
  }
}
```

---

## Important Notes

### Queue Types

- **`default_queue`**: General purpose tasks
- **`localAPI_queue`**: API-related tasks (higher concurrency: 10 workers)
- **`io_queue`**: I/O-bound tasks (very high concurrency: 1000 workers with gevent)

### Step Dependencies

Steps automatically wait for their dependencies:

```yaml
steps:
  - step: step1
    pipeline_key: Operation1
    inputs:
      - client_id  # From initial request
      
  - step: step2
    pipeline_key: Operation2
    inputs:
      - step1  # Waits for step1 to complete
      - client_id  # Can also use initial inputs
```

### Parallel Processing

Enable parallel processing for steps that can run concurrently:

```yaml
- step: process_items
  pipeline_key: ProcessItems
  parallel_task: true
  parallel_inputs:
    - items_list  # Process each item in parallel
  parallel_merge: true  # Merge results back
  inputs:
    - items_list
```

### Error Handling

Your `execute()` method should handle errors gracefully:

```python
def execute(self) -> Dict[str, Any]:
    try:
        # Your logic
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Error in {self.pipeline_key}: {e}")
        return {"status": "error", "error": str(e)}
```

---

## Summary

1. **FastAPI Endpoints**: Defined in `app/web_server/router.py` using decorators
2. **Workflows**: YAML templates in `app/templates/` that define step sequences
3. **Pipeline Operations**: Python classes in `app/pipelines/pipelines_app.py`
4. **Registration**: Add your class to `pipeline_operations` dictionary
5. **Execution**: Celery workers execute steps based on dependencies and queues

This architecture allows you to:
- ✅ Define complex workflows declaratively (YAML)
- ✅ Implement custom logic in Python classes
- ✅ Handle dependencies automatically
- ✅ Scale with Celery workers
- ✅ Monitor with Flower dashboard





