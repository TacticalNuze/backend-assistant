# Elasticsearch Usage Guide

## When is Elasticsearch Used?

Elasticsearch is used in this project for **document indexing and search** in the following scenarios:

### 1. **Chunk-to-File Mappings** (`SaveMappingToDocumentDB`)
   - **Location**: `app/pipelines/pipelines_app.py` - `SaveMappingToDocumentDB` class
   - **Purpose**: Saves per-file chunk embedding mappings into Elasticsearch
   - **When**: After embeddings are generated, this step stores metadata linking:
     - File names to their chunk IDs
     - Chunk IDs to their embeddings
     - Additional metadata (timestamps, client_id, project_id, etc.)
   - **Index Pattern**: `chunk-embeddings-{language}-{client_id}-{project_id}`

### 2. **Chunk ID Resolution** (`GetVectorReference`)
   - **Location**: `app/pipelines/pipelines_app.py` - `GetVectorReference` class
   - **Purpose**: Resolves chunk_ids to filenames when ChromaDB metadata is missing
   - **When**: Used as a **fallback** when ChromaDB doesn't have complete metadata
   - **How**: Queries Elasticsearch by chunk_id to retrieve file_name mappings

### 3. **Document Search & Retrieval**
   - Elasticsearch provides full-text search capabilities
   - Supports complex queries using Elasticsearch Query DSL
   - Enables filtering by client_id and project_id for multi-tenancy

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Application Layer                     │
│  (Pipelines, API Endpoints, Workflows)                  │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              DocumentDatabaseService                      │
│  (Service Layer - Abstraction)                           │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│          ElasticsearchDocProvider                        │
│  (Provider Layer - Implementation)                       │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Elasticsearch (Port 9200)                   │
│  (Docker Container)                                       │
└─────────────────────────────────────────────────────────┘
```

## How to Use the Document Database Service

### 1. **Basic Usage in Code**

```python
from libs.database_service import DocumentDatabaseService

# Initialize the service
doc_service = DocumentDatabaseService()
await doc_service.initialize()

# Save a document
await doc_service.save_document(
    index="my-index",
    doc_id="doc-123",
    data={"title": "My Document", "content": "Document content"},
    client_id="client-1",
    project_id="project-1"
)

# Get a document
document = await doc_service.get_document(
    index="my-index",
    doc_id="doc-123",
    client_id="client-1"
)

# Update a document
await doc_service.update_document(
    index="my-index",
    doc_id="doc-123",
    data={"title": "Updated Title", "content": "Updated content"},
    client_id="client-1"
)

# Delete a document
await doc_service.delete_document(
    index="my-index",
    doc_id="doc-123",
    client_id="client-1"
)

# Search documents
results = await doc_service.search_documents(
    index="my-index",
    query={
        "query": {
            "match": {
                "content": "search term"
            }
        }
    },
    size=10,
    client_id="client-1"
)

# Close the connection
await doc_service.close()
```

### 2. **Using Through DatabaseService**

```python
from libs.database_service import DatabaseService

db_service = DatabaseService()
await db_service.initialize()

# Access document manager
doc_manager = db_service.document_manager

# Use all the same methods as above
await doc_manager.save_document(...)
```

### 3. **Bulk Operations**

```python
# Bulk save
documents = [
    {"doc_id": "doc-1", "data": {"title": "Doc 1"}},
    {"doc_id": "doc-2", "data": {"title": "Doc 2"}},
]
result = await doc_service.bulk_save_documents(
    index="my-index",
    documents=documents,
    client_id="client-1"
)
# Returns: {"saved": 2, "errors": [], "total": 2}

# Bulk delete
doc_ids = ["doc-1", "doc-2"]
result = await doc_service.bulk_delete_documents(
    index="my-index",
    doc_ids=doc_ids,
    client_id="client-1"
)
# Returns: {"deleted": 2, "errors": [], "total": 2}
```

### 4. **Specialized Operations**

```python
# Create document-to-chunks mapping
await doc_service.create_document_mapping(
    index_name="document-mappings",
    document_id="mapping-123",
    storage_object_name="file.pdf",
    vector_chunk_ids=["chunk-1", "chunk-2", "chunk-3"],
    metadata={"upload_timestamp": time.time()},
    client_id="client-1"
)

# Save chunk embeddings (bulk)
chunks = [
    {"chunk_id": "chunk-1", "embedding": [0.1, 0.2, ...]},
    {"chunk_id": "chunk-2", "embedding": [0.3, 0.4, ...]},
]
result = await doc_service.save_chunk_embeddings(
    index_name="chunk-embeddings",
    file_name="file.pdf",
    chunks=chunks,
    client_id="client-1",
    project_id="project-1"
)
```

## API Endpoints (Optional - To Be Created)

If you want to expose Elasticsearch operations via REST API, you can add endpoints to `app/web_server/router.py`:

```python
from fastapi import APIRouter, HTTPException
from libs.database_service import DocumentDatabaseService

router = APIRouter()

@router.post("/api/documents/{index}")
async def create_document(
    index: str,
    doc_id: str,
    data: dict,
    client_id: str = None,
    project_id: str = None
):
    """Create or update a document in Elasticsearch"""
    doc_service = DocumentDatabaseService()
    await doc_service.initialize()
    try:
        success = await doc_service.save_document(
            index=index,
            doc_id=doc_id,
            data=data,
            client_id=client_id,
            project_id=project_id
        )
        return {"success": success, "doc_id": doc_id}
    finally:
        await doc_service.close()

@router.get("/api/documents/{index}/{doc_id}")
async def get_document(index: str, doc_id: str, client_id: str = None):
    """Get a document from Elasticsearch"""
    doc_service = DocumentDatabaseService()
    await doc_service.initialize()
    try:
        doc = await doc_service.get_document(index, doc_id, client_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc
    finally:
        await doc_service.close()

@router.delete("/api/documents/{index}/{doc_id}")
async def delete_document(index: str, doc_id: str, client_id: str = None):
    """Delete a document from Elasticsearch"""
    doc_service = DocumentDatabaseService()
    await doc_service.initialize()
    try:
        success = await doc_service.delete_document(index, doc_id, client_id)
        return {"success": success, "doc_id": doc_id}
    finally:
        await doc_service.close()

@router.post("/api/documents/{index}/search")
async def search_documents(
    index: str,
    query: dict,
    size: int = 10,
    client_id: str = None,
    project_id: str = None
):
    """Search documents in Elasticsearch"""
    doc_service = DocumentDatabaseService()
    await doc_service.initialize()
    try:
        results = await doc_service.search_documents(
            index=index,
            query=query,
            size=size,
            client_id=client_id,
            project_id=project_id
        )
        return {"results": results, "count": len(results)}
    finally:
        await doc_service.close()
```

## Configuration

Elasticsearch is configured via environment variables:

```env
ELASTICSEARCH_URL=http://localhost:9200
ELASTICSEARCH_USERNAME=  # Optional
ELASTICSEARCH_PASSWORD=  # Optional
DOC_DB_TYPE=elasticsearch  # Default provider type
```

## Docker Setup

Elasticsearch runs in Docker via `docker-compose`:

```yaml
elasticsearch:
  image: docker.elastic.co/elasticsearch/elasticsearch:8.13.0
  container_name: elasticsearch
  ports:
    - "9200:9200"
  environment:
    discovery.type: "single-node"
    xpack.security.enabled: "false"
```

Access Elasticsearch at: `http://localhost:9200`

## Health Check

```python
health = await doc_service.health_check()
# Returns: {"status": "healthy", "provider": "elasticsearch", "initialized": True}
```

## Best Practices

1. **Always initialize** the service before use: `await doc_service.initialize()`
2. **Close connections** when done: `await doc_service.close()`
3. **Use client_id and project_id** for multi-tenant data isolation
4. **Use bulk operations** when processing multiple documents
5. **Handle errors** - operations can raise `RuntimeError` if service is not initialized
6. **Index naming**: Follow the pattern `{base-name}-{language}-{client_id}-{project_id}` for consistency

## Example: Complete Workflow

```python
from libs.database_service import DocumentDatabaseService

async def manage_documents():
    # Initialize
    doc_service = DocumentDatabaseService()
    await doc_service.initialize()
    
    try:
        # Create index and save documents
        index = "my-documents"
        
        # Save a document
        await doc_service.save_document(
            index=index,
            doc_id="doc-1",
            data={
                "title": "Introduction",
                "content": "This is the introduction...",
                "author": "John Doe",
                "tags": ["tutorial", "python"]
            },
            client_id="client-1",
            project_id="project-1"
        )
        
        # Search for documents
        results = await doc_service.search_documents(
            index=index,
            query={
                "query": {
                    "bool": {
                        "must": [
                            {"match": {"content": "introduction"}},
                            {"term": {"tags": "python"}}
                        ]
                    }
                }
            },
            size=10,
            client_id="client-1"
        )
        
        # Update a document
        await doc_service.update_document(
            index=index,
            doc_id="doc-1",
            data={"title": "Updated Introduction", "version": 2},
            client_id="client-1"
        )
        
        # Delete a document
        await doc_service.delete_document(
            index=index,
            doc_id="doc-1",
            client_id="client-1"
        )
        
    finally:
        # Always close
        await doc_service.close()
```

