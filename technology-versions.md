# Technology Versions - Complete List

This document lists all technology versions used in the VectorRAG Backend Pipeline project.

## Core Runtime & Framework

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Python** | 3.12 | Dockerfile | Base runtime |
| **FastAPI** | 0.119.0 | requirements.txt | Web framework |
| **Uvicorn** | 0.37.0 | requirements.txt | ASGI server |
| **Docker Compose** | 3.8 | docker-compose files | Orchestration format |

## Task Processing & Queue

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Celery** | 5.5.3 | requirements.txt | Task queue framework |
| **Redis** | latest | docker-compose | Message broker (image tag) |
| **Redis Client** | 6.4.0 | requirements.txt | Python Redis client |
| **Flower** | latest | docker-compose | Celery monitoring (image tag) |
| **Gevent** | 25.9.1 | requirements.txt | Async I/O for Celery |

## Vector & Document Databases

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **ChromaDB** | latest | docker-compose | Vector database (image tag) |
| **ChromaDB Client** | 1.1.1 | requirements.txt | Python client |
| **Elasticsearch** | 8.13.0 | docker-compose | Document search engine |
| **Elasticsearch Client** | 8.13.0 | requirements.txt | Python client |
| **Kibana** | 8.13.0 | docker-compose | Elasticsearch UI |
| **Qdrant Client** | 1.15.1 | requirements.txt | Alternative vector DB client |
| **Weaviate Client** | 4.17.0 | requirements.txt | Alternative vector DB client |
| **LanceDB** | 0.25.2 | requirements.txt | Alternative vector DB |

## Storage

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **MinIO** | latest | docker-compose | Object storage (image tag) |
| **MinIO Client** | 7.2.18 | requirements.txt | Python client |

## AI & LLM Services

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **OpenAI SDK** | 2.4.0 | requirements.txt | OpenAI API client |
| **Anthropic SDK** | 0.71.0 | requirements.txt | Claude API client |
| **Google GenAI** | 1.45.0 | requirements.txt | Gemini API client (new SDK) |
| **Google GenerativeAI** | 0.8.5 | requirements.txt | Gemini API client (legacy) |
| **LiteLLM** | 1.78.2 | requirements.txt | Unified LLM interface |
| **FNLLM** | 0.4.1 | requirements.txt | LLM abstraction layer |

## Document Processing

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **LlamaIndex** | 0.9.48 | requirements.txt | Document indexing framework |
| **LlamaIndex Core** | 0.14.5 | requirements.txt | Core LlamaIndex library |
| **LlamaIndex Cloud Services** | 0.6.76 | requirements.txt | Cloud integration |
| **LlamaIndex Cloud** | 0.1.43 | requirements.txt | Cloud API client |
| **Llama Parser** | 0.1.2 | requirements.txt | Document parser |
| **PyPDF2** | 3.0.1 | requirements.txt | PDF processing |
| **Chonkie** | 1.3.0 | requirements.txt | Text chunking library |

## Embeddings & NLP

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Sentence Transformers** | (via dependencies) | embeddings_service | Embedding models |
| **Transformers** | (via dependencies) | embeddings_service | HuggingFace transformers |
| **Spacy** | 3.8.7 | requirements.txt | NLP library |
| **NLTK** | 3.9.1 | requirements.txt | Natural language toolkit |
| **Tokenizers** | 0.22.1 | requirements.txt | Fast tokenization |
| **Tiktoken** | 0.12.0 | requirements.txt | OpenAI tokenizer |

## LLMOps & Prompt Management

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Langfuse** | 3.7.0 | requirements.txt | LLMOps & prompt versioning |
| **LangChain Core** | 0.3.79 | requirements.txt | LangChain core library |
| **LangChain Text Splitters** | 0.3.11 | requirements.txt | Text splitting utilities |
| **LangSmith** | 0.4.37 | requirements.txt | LangChain observability |

## Database & ORM

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **PostgreSQL** | 15-alpine | (mentioned in docs) | SQL database |
| **Psycopg** | 3.2.10 | requirements.txt | PostgreSQL adapter |
| **SQLAlchemy** | 2.0.44 | requirements.txt | SQL toolkit & ORM |

## Memory & Context

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Mem0** | (external service) | - | User memory service |

## Data Processing & ML

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **NumPy** | 1.26.4 | requirements.txt | Numerical computing |
| **Pandas** | 2.3.3 | requirements.txt | Data manipulation |
| **Scikit-learn** | 1.7.2 | requirements.txt | Machine learning |
| **SciPy** | 1.13.1 | requirements.txt | Scientific computing |
| **Gensim** | 4.3.3 | requirements.txt | Topic modeling |
| **Graspologic** | 3.4.4 | requirements.txt | Graph analysis |

## HTTP & Networking

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **HTTPX** | 0.28.1 | requirements.txt | Async HTTP client |
| **Aiohttp** | 3.13.0 | requirements.txt | Async HTTP framework |
| **Requests** | 2.32.5 | requirements.txt | HTTP library |
| **Starlette** | 0.48.0 | requirements.txt | ASGI framework (FastAPI dependency) |

## Azure Services

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Azure Core** | 1.36.0 | requirements.txt | Azure SDK core |
| **Azure Identity** | 1.25.1 | requirements.txt | Azure authentication |
| **Azure Storage Blob** | 12.27.0 | requirements.txt | Azure blob storage |
| **Azure Search Documents** | 11.6.0 | requirements.txt | Azure Cognitive Search |
| **Azure Cosmos** | 4.14.0 | requirements.txt | Azure Cosmos DB |

## Utilities & Configuration

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Pydantic** | 2.12.2 | requirements.txt | Data validation |
| **Pydantic Settings** | 2.11.0 | requirements.txt | Settings management |
| **PyYAML** | 6.0.3 | requirements.txt | YAML parser |
| **Python Dotenv** | 1.1.1 | requirements.txt | Environment variables |
| **Click** | 8.3.0 | requirements.txt | CLI framework |
| **Rich** | 14.2.0 | requirements.txt | Terminal formatting |

## Testing

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Pytest** | 8.4.2 | requirements.txt | Testing framework |
| **Pytest Asyncio** | 1.2.0 | requirements.txt | Async test support |

## Development Tools

| Technology | Version | Source | Notes |
|------------|---------|--------|-------|
| **Devtools** | 0.12.2 | requirements.txt | Development utilities |
| **Watchfiles** | 1.1.1 | requirements.txt | File watching (Uvicorn reload) |

## Docker Images (from docker-compose)

| Service | Image | Version Tag | Notes |
|---------|-------|-------------|-------|
| **ChromaDB** | chromadb/chroma | latest | Vector database |
| **MinIO** | minio/minio | latest | Object storage |
| **Redis** | redis | latest | Message broker |
| **Elasticsearch** | docker.elastic.co/elasticsearch/elasticsearch | 8.13.0 | Search engine |
| **Kibana** | docker.elastic.co/kibana/kibana | 8.13.0 | Elasticsearch UI |
| **Flower** | mher/flower | latest | Celery monitoring |
| **Python Base** | python | 3.12-slim | Application container |

## API Versions

| Service | API Version | Source | Notes |
|---------|-------------|--------|-------|
| **Azure OpenAI** | 2025-04-01-preview | llm_client.py | Default API version |
| **Docker Compose** | 3.8 | docker-compose files | Compose file format |

## Summary Statistics

- **Total Python Packages**: ~200+ dependencies
- **Python Version**: 3.12
- **Docker Services**: 7 containers
- **Major Frameworks**: FastAPI, Celery, LangChain, LlamaIndex
- **Vector Databases**: ChromaDB (primary), Qdrant, Weaviate, LanceDB (alternatives)
- **LLM Providers**: Azure OpenAI, OpenAI, Anthropic, Google Gemini

## Notes

1. **Latest Tags**: Some Docker images use `latest` tag, which means the actual version may change. For production, consider pinning specific versions.

2. **Python 3.12**: The project requires Python 3.12+ as specified in the Dockerfile.

3. **Elasticsearch 8.13.0**: Both Elasticsearch and Kibana are pinned to version 8.13.0 for compatibility.

4. **LangChain vs LangGraph**: While LangGraph is mentioned in documentation, it's not explicitly listed in requirements.txt. It may be included via LangChain dependencies or used as an external service.

5. **Next.js**: Mentioned in infrastructure but not part of this backend repository. Version would be in the frontend repository.

6. **PostgreSQL**: Version 15-alpine is mentioned in documentation but not directly specified in docker-compose files (likely external service).

7. **Mem0**: External service, version managed by Mem0 provider.

## Version Update Recommendations

For production deployments, consider:
- Pinning Docker image versions instead of using `latest`
- Regularly updating dependencies for security patches
- Testing version upgrades in staging before production
- Maintaining a changelog for version updates







